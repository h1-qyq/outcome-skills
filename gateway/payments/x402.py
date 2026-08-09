"""Safe, lower-level x402 v2 boundary for a one-cent USD order.

The FastAPI middleware is intentionally not used to unlock gateway work: its
released ordering executes the handler before settlement.  This adapter uses
the same v2 resource-server/facilitator primitives, but settles before calling
``OrderStore.mark_paid``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import hashlib
import re
from typing import Any, Protocol

from httpx import HTTPError
from x402.schemas import PaymentError as X402SdkPaymentError

from gateway.contracts import Order, PaymentRequirement, X402PaymentProviderData
from gateway.orders import InvalidOrderState, OrderExpired, OrderStore
from gateway.payments.base import (
    InvalidPaymentProof,
    PaymentAdapter,
    PaymentAlreadySettled,
    PaymentProofExpired,
    PaymentProcessing,
    PaymentRequirementExpired,
    PaymentRequirementUnavailable,
)


BASE_SEPOLIA = "eip155:84532"
BASE_MAINNET = "eip155:8453"
PUBLIC_TEST_FACILITATOR = "https://x402.org/facilitator"
CDP_FACILITATOR = "https://api.cdp.coinbase.com/platform/v2/x402"
PAYMENT_REQUIRED_HEADER = "PAYMENT-REQUIRED"
PAYMENT_SIGNATURE_HEADER = "PAYMENT-SIGNATURE"
PAYMENT_RESPONSE_HEADER = "PAYMENT-RESPONSE"
_ADDRESS = re.compile(r"^0x[a-fA-F0-9]{40}$")


class X402Facilitator(Protocol):
    """Injected seam for offline tests and the official SDK bridge."""

    def verify(self, requirement: X402PaymentProviderData, proof: str) -> bool: ...

    def settle(self, requirement: X402PaymentProviderData, proof: str) -> bool | "X402SettlementResult": ...


PaymentRequiredBuilder = Callable[["X402Settings", Order], str]


@dataclass(frozen=True)
class AuthenticatedCdpProvider:
    """Explicit evidence that a CDP auth provider passed integration testing.

    ``provider`` is deliberately opaque: the current official Python docs do
    not publish a release-pinned CDP JWT/header-construction example.  An
    operator supplies a verified official-compatible provider at deployment.
    """

    provider: Any = field(repr=False)
    integration_tested: bool = False


@dataclass(frozen=True)
class X402SettlementResult:
    success: bool
    payment_response: str | None = None


@dataclass(frozen=True)
class X402Settings:
    pay_to: str
    network: str
    facilitator_url: str
    auth_provider: AuthenticatedCdpProvider | None = field(default=None, repr=False)
    resource_base_url: str = "https://gateway.invalid"

    def __post_init__(self) -> None:
        if not _ADDRESS.fullmatch(self.pay_to):
            raise ValueError("x402 receiving address must be a 0x-prefixed EVM address")
        if self.network not in {BASE_SEPOLIA, BASE_MAINNET}:
            raise ValueError("x402 network must be a current CAIP-2 Base network")
        if not self.facilitator_url.startswith("https://"):
            raise ValueError("x402 facilitator URL must use https")
        if not self.resource_base_url.startswith("https://"):
            raise ValueError("x402 resource base URL must use https")
        if self.network == BASE_SEPOLIA:
            if self.facilitator_url != PUBLIC_TEST_FACILITATOR:
                raise ValueError("Base Sepolia must use the public test facilitator")
            if self.auth_provider is not None:
                raise ValueError("Base Sepolia public facilitator does not use CDP authentication")
        else:
            if self.facilitator_url != CDP_FACILITATOR:
                raise ValueError("Base mainnet requires the CDP facilitator")
            if self.auth_provider is None or not self.auth_provider.integration_tested:
                raise ValueError("Base mainnet requires an explicitly authenticated CDP provider")

    @classmethod
    def testnet(cls, *, pay_to: str, resource_base_url: str = "https://gateway.invalid") -> "X402Settings":
        return cls(
            pay_to=pay_to,
            network=BASE_SEPOLIA,
            facilitator_url=PUBLIC_TEST_FACILITATOR,
            resource_base_url=resource_base_url,
        )

    @classmethod
    def production(
        cls,
        *,
        pay_to: str,
        auth_provider: AuthenticatedCdpProvider | None,
        resource_base_url: str = "https://gateway.invalid",
    ) -> "X402Settings":
        return cls(
            pay_to=pay_to,
            network=BASE_MAINNET,
            facilitator_url=CDP_FACILITATOR,
            auth_provider=auth_provider,
            resource_base_url=resource_base_url,
        )


class OfficialX402Facilitator:
    """Official-SDK bridge, imported lazily so offline tests never load EVM code."""

    def __init__(self, settings: X402Settings) -> None:
        self._settings = settings

    def payment_required(self, order: Order) -> str:
        server, requirements = self._server_and_requirements(order)
        try:
            from x402.http import encode_payment_required_header
            from x402.schemas import ResourceInfo

            response = server.create_payment_required_response(
                requirements,
                ResourceInfo(
                    url=self._resource_url(order),
                    description="One Cent Outcomes result",
                    mime_type="application/json",
                ),
            )
            return encode_payment_required_header(response)
        except (ImportError, ValueError, TypeError, RuntimeError, HTTPError, TimeoutError) as error:
            raise PaymentRequirementUnavailable("x402 payment requirement is unavailable") from error

    def verify_order(self, order: Order, proof: str) -> bool:
        try:
            from x402.http import decode_payment_signature_header

            server, requirements = self._server_and_requirements(order)
            payload = decode_payment_signature_header(proof)
            matched = server.find_matching_requirements(requirements, payload)
            if matched is None:
                return False
            return bool(server.verify_payment(payload, matched).is_valid)
        except (RuntimeError, HTTPError, TimeoutError) as error:
            raise PaymentRequirementUnavailable("x402 verification is unavailable") from error
        except (ImportError, ValueError, TypeError):
            return False

    def settle_order(self, order: Order, proof: str) -> X402SettlementResult:
        try:
            from x402.http import decode_payment_signature_header, encode_payment_response_header

            server, requirements = self._server_and_requirements(order)
            payload = decode_payment_signature_header(proof)
            matched = server.find_matching_requirements(requirements, payload)
            if matched is None:
                return X402SettlementResult(success=False)
            response = server.settle_payment(payload, matched)
            return X402SettlementResult(
                success=bool(response.success) or response.error_reason == "duplicate_settlement",
                payment_response=encode_payment_response_header(response),
            )
        except (ImportError, ValueError, TypeError):
            return X402SettlementResult(success=False)

    def _server_and_requirements(self, order: Order) -> tuple[Any, list[Any]]:
        try:
            from x402.http import FacilitatorConfig, HTTPFacilitatorClientSync
            from x402.mechanisms.evm.exact import ExactEvmServerScheme
            from x402.schemas import ResourceConfig
            from x402.server import x402ResourceServerSync

            auth = self._settings.auth_provider.provider if self._settings.auth_provider else None
            facilitator = HTTPFacilitatorClientSync(
                FacilitatorConfig(url=self._settings.facilitator_url, auth_provider=auth)
            )
            server = x402ResourceServerSync(facilitator)
            server.register(self._settings.network, ExactEvmServerScheme())
            server.initialize()
            requirements = server.build_payment_requirements(
                ResourceConfig(
                    scheme="exact",
                    pay_to=self._settings.pay_to,
                    price="$0.01",
                    network=self._settings.network,
                )
            )
            return server, requirements
        except (
            ImportError,
            ValueError,
            TypeError,
            RuntimeError,
            HTTPError,
            TimeoutError,
            X402SdkPaymentError,
        ) as error:
            raise PaymentRequirementUnavailable("x402 SDK configuration is unavailable") from error

    def _resource_url(self, order: Order) -> str:
        return f"{self._settings.resource_base_url.rstrip('/')}/v1/orders/{order.order_id}/result"

class X402PaymentAdapter(PaymentAdapter):
    """Settle x402 first, then atomically transition the local order to paid."""

    def __init__(
        self,
        store: OrderStore,
        settings: X402Settings,
        *,
        facilitator: X402Facilitator | None = None,
        payment_required_builder: PaymentRequiredBuilder | None = None,
    ) -> None:
        self._store = store
        self.settings = settings
        self._official = OfficialX402Facilitator(settings)
        self._uses_official_bridge = facilitator is None and payment_required_builder is None
        self._facilitator = facilitator or self._official
        self._payment_required_builder = payment_required_builder or (
            lambda _settings, current: self._official.payment_required(current)
        )
        self._payment_responses: dict[str, str] = {}

    @property
    def production_ready(self) -> bool:
        return (
            self._uses_official_bridge
            and self._facilitator is self._official
            and self.settings.network == BASE_MAINNET
            and self.settings.auth_provider is not None
            and self.settings.auth_provider.integration_tested
        )

    def requirement(self, order: Order) -> PaymentRequirement:
        current = self._payable(order)
        data = X402PaymentProviderData(
            network=self.settings.network,
            pay_to=self.settings.pay_to,
            facilitator_url=self.settings.facilitator_url,
            payment_required=self._payment_required_builder(self.settings, current),
        )
        return PaymentRequirement(
            order_id=current.order_id,
            currency="USD",
            amount_minor=1,
            expires_at=current.expires_at,
            provider_data=data,
        )

    def verify(self, order: Order, proof: str) -> bool:
        data = self._provider_data(order)
        try:
            if self._facilitator is self._official:
                return self._official.verify_order(order, proof)
            return bool(self._facilitator.verify(data, proof))
        except (RuntimeError, TimeoutError, HTTPError) as error:
            raise PaymentRequirementUnavailable("x402 verification is unavailable") from error
        except PaymentRequirementUnavailable:
            raise
        except (ValueError, TypeError):
            return False

    def settle(self, order: Order, proof: str) -> Order:
        if self._store.get(order.order_id).status == "processing":
            return self.resume_settlement(order, proof)
        current = self._payable(order)
        data = self._provider_data(current)
        if not self.verify(current, proof):
            raise InvalidPaymentProof("x402 payment proof was not verified")
        try:
            settling = self._store.begin_settlement(current.order_id, self._proof_digest(proof))
        except OrderExpired as error:
            raise PaymentProofExpired("x402 payment proof expired") from error
        except Exception as error:
            raise PaymentAlreadySettled("x402 order is no longer payable") from error
        try:
            settlement = (
                self._official.settle_order(settling, proof)
                if self._facilitator is self._official
                else self._facilitator.settle(data, proof)
            )
        except (RuntimeError, TimeoutError, HTTPError) as error:
            # The provider may have settled after timing out locally. Keep the
            # durable reservation and require same-proof reconciliation.
            raise PaymentProcessing("x402 settlement outcome is unknown; retry the same proof") from error
        except PaymentRequirementUnavailable:
            self._store.cancel_settlement(settling.order_id)
            raise
        except (ValueError, TypeError) as error:
            self._store.cancel_settlement(settling.order_id)
            raise InvalidPaymentProof("x402 settlement was rejected") from error
        result = settlement if isinstance(settlement, X402SettlementResult) else X402SettlementResult(bool(settlement))
        if not result.success:
            self._store.cancel_settlement(settling.order_id)
            raise InvalidPaymentProof("x402 settlement was rejected")
        try:
            paid = self._store.complete_settlement(settling.order_id)
            if result.payment_response:
                self._payment_responses[paid.order_id] = result.payment_response
            return paid
        except Exception as error:
            raise PaymentAlreadySettled("x402 payment proof already settled") from error

    def _provider_data(self, order: Order) -> X402PaymentProviderData:
        return X402PaymentProviderData(
            network=self.settings.network,
            pay_to=self.settings.pay_to,
            facilitator_url=self.settings.facilitator_url,
            payment_required=self._payment_required_builder(self.settings, order),
        )

    def payment_response(self, order_id: str) -> str | None:
        """Return the official v2 settlement header for the settlement request."""
        return self._payment_responses.get(order_id)

    def resume_settlement(self, order: Order, proof: str) -> Order:
        """Retry the same proof after a crash without generating while pending."""
        current = self._store.get(order.order_id)
        digest = self._proof_digest(proof)
        if current.status in {"paid", "generating", "delivered", "failed"}:
            if self._store.settlement_proof_matches(current.order_id, digest):
                return current
            raise InvalidPaymentProof("x402 recovery proof does not match the original proof")
        if current.status != "processing":
            raise PaymentAlreadySettled("x402 order is not awaiting settlement recovery")
        if not self._store.settlement_proof_matches(current.order_id, digest):
            raise InvalidPaymentProof("x402 recovery proof does not match the original proof")
        try:
            if self._facilitator is self._official:
                settlement = self._official.settle_order(current, proof)
            else:
                settlement = self._facilitator.settle(self._provider_data(current), proof)
        except PaymentRequirementUnavailable as error:
            raise PaymentProcessing("x402 settlement recovery is still processing") from error
        except (RuntimeError, TimeoutError, HTTPError) as error:
            raise PaymentProcessing("x402 settlement recovery is still processing") from error
        except (ValueError, TypeError) as error:
            raise InvalidPaymentProof("x402 settlement recovery was rejected") from error
        result = settlement if isinstance(settlement, X402SettlementResult) else X402SettlementResult(bool(settlement))
        if not result.success:
            raise PaymentProcessing("x402 settlement recovery is still processing")
        try:
            paid = self._store.complete_settlement(current.order_id)
        except InvalidOrderState as error:
            paid = self._store.get(current.order_id)
            if (
                paid.status not in {"paid", "generating", "delivered", "failed"}
                or not self._store.settlement_proof_matches(paid.order_id, digest)
            ):
                raise PaymentAlreadySettled("x402 settlement recovery lost its reservation") from error
        if result.payment_response:
            self._payment_responses[paid.order_id] = result.payment_response
        return paid

    @staticmethod
    def _proof_digest(proof: str) -> str:
        return hashlib.sha256(proof.encode("utf-8")).hexdigest()

    def _payable(self, order: Order) -> Order:
        current = self._store.get(order.order_id)
        if current.currency != "USD" or current.amount_minor != 1:
            raise PaymentRequirementUnavailable("x402 only accepts the server-owned USD one-cent order")
        if current.status == "expired" or self._store.now() >= current.expires_at:
            raise PaymentRequirementExpired("x402 payment requirement is expired")
        if current.status != "payment-required":
            raise PaymentAlreadySettled("x402 order is not payable")
        return current

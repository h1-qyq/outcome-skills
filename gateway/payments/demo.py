"""Development-only payment adapter; it never contacts a payment network."""

from __future__ import annotations

import hmac
from typing import Literal

from gateway.contracts import Order, PaymentRequirement
from gateway.orders import (
    InvalidOrderState,
    OrderAlreadySettled,
    OrderExpired,
    OrderStore,
)
from gateway.payments.base import (
    InvalidPaymentProof,
    PaymentAdapter,
    PaymentAlreadySettled,
    PaymentProofExpired,
    PaymentRequirementExpired,
    PaymentRequirementUnavailable,
)


class DemoPaymentAdapter(PaymentAdapter):
    """Accept the explicit test proof only outside production."""

    def __init__(
        self,
        store: OrderStore,
        *,
        environment: Literal["development", "test", "production"],
    ) -> None:
        if environment == "production":
            raise ValueError("demo payment adapter is forbidden in production")
        self._store = store

    @property
    def production_ready(self) -> bool:
        return False

    def requirement(self, order: Order) -> PaymentRequirement:
        current = self._store.get(order.order_id)
        if current.status == "expired" or self._store.now() >= current.expires_at:
            raise PaymentRequirementExpired("payment requirement is expired")
        if current.status != "payment-required":
            raise PaymentRequirementUnavailable("order is not payable")
        return PaymentRequirement(
            order_id=current.order_id,
            currency=current.currency,
            amount_minor=current.amount_minor,
            expires_at=current.expires_at,
        )

    def verify(self, order: Order, proof: str) -> bool:
        """Check a receipt without changing persistent order state."""

        current = self._store.get(order.order_id)
        return (
            current.status == "payment-required"
            and self._store.now() < current.expires_at
            and hmac.compare_digest(proof, self._expected_proof(current))
        )

    def settle(self, order: Order, proof: str) -> Order:
        current = self._store.get(order.order_id)
        if current.status in {"paid", "generating", "delivered", "failed"}:
            raise PaymentAlreadySettled("payment proof already used")
        if current.status == "expired":
            raise PaymentProofExpired("payment proof is expired")
        if not hmac.compare_digest(proof, self._expected_proof(current)):
            raise InvalidPaymentProof("invalid payment proof")
        try:
            return self._store.mark_paid(current.order_id)
        except OrderExpired as error:
            raise PaymentProofExpired("payment proof is expired") from error
        except OrderAlreadySettled as error:
            raise PaymentAlreadySettled("payment proof already used") from error
        except InvalidOrderState as error:
            latest = self._store.get(current.order_id)
            if latest.status in {"paid", "generating", "delivered", "failed"}:
                raise PaymentAlreadySettled("payment proof already used") from error
            if latest.status == "expired":
                raise PaymentProofExpired("payment proof is expired") from error
            raise

    @staticmethod
    def _expected_proof(order: Order) -> str:
        return f"demo:{order.order_id}:paid"

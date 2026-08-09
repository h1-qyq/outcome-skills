import pytest
from x402.http import decode_payment_required_header, encode_payment_required_header
from x402.schemas import PaymentRequired, PaymentRequirements, ResourceInfo

from gateway.contracts import OrderRequest
from gateway.orders import OrderStore
from gateway.payments.base import InvalidPaymentProof, PaymentProcessing
from gateway.payments.x402 import (
    BASE_MAINNET,
    BASE_SEPOLIA,
    PAYMENT_REQUIRED_HEADER,
    PAYMENT_RESPONSE_HEADER,
    PAYMENT_SIGNATURE_HEADER,
    PUBLIC_TEST_FACILITATOR,
    X402PaymentAdapter,
    X402Settings,
    AuthenticatedCdpProvider,
    OfficialX402Facilitator,
)


class OfflineFacilitator:
    def __init__(self, *, verified: bool = True, settled: bool = True) -> None:
        self.verified = verified
        self.settled = settled
        self.calls: list[str] = []

    def verify(self, requirement, proof):
        self.calls.append("verify")
        return self.verified

    def settle(self, requirement, proof):
        self.calls.append("settle")
        return self.settled


def official_sdk_shaped_payment_required(settings, order):
    """Actual pinned-SDK V2 schema, encoded with its public helper."""
    return encode_payment_required_header(PaymentRequired(
        resource=ResourceInfo(
            url=f"https://gateway.invalid/v1/orders/{order.order_id}/result",
            description="One Cent Outcomes result",
            mime_type="application/json",
        ),
        accepts=[PaymentRequirements(
            scheme="exact",
            network=settings.network,
            asset="0x036CbD53842c5426634e7929541eC2318f3dCF7e",
            amount="10000",
            pay_to=settings.pay_to,
            max_timeout_seconds=300,
            extra={"name": "USDC", "version": "2"},
        )],
    ))


def make_order(tmp_path):
    store = OrderStore(tmp_path / "orders.sqlite3")
    return store, store.create_or_reuse(
        OrderRequest(
            skill_id="outcome-offer",
            input_text="A private offer request.",
            currency="USD",
            locale="en-US",
            idempotency_key="x402-contract-001",
        )
    )


def test_testnet_requirement_uses_exact_v2_one_cent_shape(tmp_path):
    store, order = make_order(tmp_path)
    adapter = X402PaymentAdapter(
        store,
        X402Settings.testnet(pay_to="0x0000000000000000000000000000000000000001"),
        facilitator=OfflineFacilitator(),
        payment_required_builder=official_sdk_shaped_payment_required,
    )

    requirement = adapter.requirement(order)

    assert requirement.amount_minor == 1
    assert requirement.provider_data.model_dump() == {
        "provider": "x402",
        "x402_version": 2,
        "scheme": "exact",
        "network": BASE_SEPOLIA,
        "pay_to": "0x0000000000000000000000000000000000000001",
        "price": "$0.01",
        "atomic_amount": "10000",
        "facilitator_url": PUBLIC_TEST_FACILITATOR,
        "required_header": PAYMENT_REQUIRED_HEADER,
        "signature_header": PAYMENT_SIGNATURE_HEADER,
        "response_header": PAYMENT_RESPONSE_HEADER,
        "payment_required": official_sdk_shaped_payment_required(adapter.settings, order),
    }
    decoded = decode_payment_required_header(requirement.provider_data.payment_required)
    assert decoded.x402_version == 2
    assert decoded.resource.url.endswith(f"/{order.order_id}/result")
    assert decoded.accepts[0].model_dump(by_alias=True) == {
        "scheme": "exact", "network": BASE_SEPOLIA,
        "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        "amount": "10000", "payTo": "0x0000000000000000000000000000000000000001",
        "maxTimeoutSeconds": 300, "extra": {"name": "USDC", "version": "2"},
    }


def test_x402_settles_before_persisting_paid_state(tmp_path):
    store, order = make_order(tmp_path)
    facilitator = OfflineFacilitator()
    adapter = X402PaymentAdapter(
        store,
        X402Settings.testnet(pay_to="0x0000000000000000000000000000000000000001"),
        facilitator=facilitator,
        payment_required_builder=official_sdk_shaped_payment_required,
    )

    paid = adapter.settle(order, "signed-payment")

    assert paid.status == "paid"
    assert facilitator.calls == ["verify", "settle"]
    assert store.get(order.order_id).status == "paid"


def test_default_payment_required_builder_has_official_bridge_arity(tmp_path, monkeypatch):
    store, order = make_order(tmp_path)
    monkeypatch.setattr(
        OfficialX402Facilitator,
        "payment_required",
        lambda _bridge, current: official_sdk_shaped_payment_required(
            X402Settings.testnet(pay_to="0x0000000000000000000000000000000000000001"), current
        ),
    )
    adapter = X402PaymentAdapter(
        store,
        X402Settings.testnet(pay_to="0x0000000000000000000000000000000000000001"),
        facilitator=OfflineFacilitator(),
    )

    requirement = adapter.requirement(order)

    assert decode_payment_required_header(requirement.provider_data.payment_required).x402_version == 2


def test_x402_does_not_mark_order_paid_when_settlement_fails(tmp_path):
    store, order = make_order(tmp_path)
    adapter = X402PaymentAdapter(
        store,
        X402Settings.testnet(pay_to="0x0000000000000000000000000000000000000001"),
        facilitator=OfflineFacilitator(settled=False),
        payment_required_builder=official_sdk_shaped_payment_required,
    )

    with pytest.raises(InvalidPaymentProof):
        adapter.settle(order, "signed-payment")

    assert store.get(order.order_id).status == "payment-required"


def test_x402_timeout_after_reservation_stays_processing_for_same_proof_recovery(tmp_path):
    class TimeoutThenSuccess(OfflineFacilitator):
        def __init__(self):
            super().__init__()
            self.timed_out = True

        def settle(self, requirement, proof):
            if self.timed_out:
                self.timed_out = False
                raise TimeoutError("facilitator timeout")
            return True

    store, order = make_order(tmp_path)
    adapter = X402PaymentAdapter(
        store,
        X402Settings.testnet(pay_to="0x0000000000000000000000000000000000000001"),
        facilitator=TimeoutThenSuccess(),
        payment_required_builder=official_sdk_shaped_payment_required,
    )

    with pytest.raises(PaymentProcessing):
        adapter.settle(order, "signed-payment")
    assert store.get(order.order_id).status == "processing"

    with pytest.raises(InvalidPaymentProof, match="original proof"):
        adapter.settle(order, "different-signed-payment")

    paid = adapter.settle(order, "signed-payment")

    assert paid.status == "paid"


@pytest.mark.parametrize("pay_to", ["", "not-an-address"])
def test_x402_rejects_missing_or_invalid_receiver(pay_to):
    with pytest.raises(ValueError, match="receiving address"):
        X402Settings.testnet(pay_to=pay_to)


def test_mainnet_requires_explicit_authenticated_provider_not_environment_credentials():
    with pytest.raises(ValueError, match="authenticated CDP"):
        X402Settings.production(
            pay_to="0x0000000000000000000000000000000000000001",
            auth_provider=None,
        )

    auth_provider = AuthenticatedCdpProvider(provider=object(), integration_tested=True)
    settings = X402Settings.production(
        pay_to="0x0000000000000000000000000000000000000001",
        auth_provider=auth_provider,
    )

    assert settings.network == BASE_MAINNET
    assert settings.auth_provider is auth_provider

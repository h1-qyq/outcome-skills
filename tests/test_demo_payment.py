from datetime import UTC, datetime, timedelta

import pytest

from gateway.contracts import OrderRequest
from gateway.orders import OrderStore
from gateway.payments.base import (
    InvalidPaymentProof,
    PaymentAlreadySettled,
    PaymentRequirementExpired,
    PaymentRequirementUnavailable,
)
from gateway.payments.demo import DemoPaymentAdapter


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def test_demo_proof_settles_once_without_network_activity(tmp_path):
    store = OrderStore(tmp_path / "orders.sqlite3")
    order = store.create_or_reuse(
        OrderRequest(
            skill_id="proof-pack",
            input_text="Proof based on the buyer's figures.",
            currency="CNY",
            locale="zh-CN",
            idempotency_key="proof-001",
        )
    )
    adapter = DemoPaymentAdapter(store, environment="test")

    requirement = adapter.requirement(order)
    paid = adapter.settle(order, f"demo:{order.order_id}:paid")

    assert requirement.amount_minor == 6
    assert paid.status == "paid"
    assert adapter.verify(paid, f"demo:{order.order_id}:paid") is False
    with pytest.raises(PaymentAlreadySettled, match="already used"):
        adapter.settle(paid, f"demo:{order.order_id}:paid")


def test_demo_adapter_rejects_production_and_mismatched_proofs(tmp_path):
    with pytest.raises(ValueError, match="production"):
        DemoPaymentAdapter(store=OrderStore(tmp_path / "production.sqlite3"), environment="production")

    store = OrderStore(tmp_path / "orders.sqlite3")
    order = store.create_or_reuse(
        OrderRequest(
            skill_id="reply-to-close",
            input_text="Reply to the proposal.",
            currency="USD",
            locale="en-US",
            idempotency_key="reply-001",
        )
    )
    adapter = DemoPaymentAdapter(store, environment="development")

    assert adapter.verify(order, "demo:other:paid") is False
    with pytest.raises(InvalidPaymentProof, match="invalid"):
        adapter.settle(order, "demo:other:paid")


def test_valid_verify_is_read_only(tmp_path):
    store = OrderStore(tmp_path / "orders.sqlite3")
    order = store.create_or_reuse(
        OrderRequest(
            skill_id="outcome-offer",
            input_text="Keep the buyer wording traceable.",
            currency="USD",
            locale="en-US",
            idempotency_key="verify-001",
        )
    )
    adapter = DemoPaymentAdapter(store, environment="test")

    assert adapter.verify(order, f"demo:{order.order_id}:paid") is True
    assert store.get(order.order_id).status == "payment-required"


def test_requirement_rejects_expired_order(tmp_path):
    clock = Clock(datetime(2026, 7, 30, tzinfo=UTC))
    store = OrderStore(tmp_path / "orders.sqlite3", clock=clock, payment_ttl=timedelta(minutes=1))
    order = store.create_or_reuse(
        OrderRequest(
            skill_id="outcome-offer",
            input_text="Expired quote.",
            currency="USD",
            locale="en-US",
            idempotency_key="requirement-expired",
        )
    )
    adapter = DemoPaymentAdapter(store, environment="test")
    clock.now += timedelta(minutes=2)

    with pytest.raises(PaymentRequirementExpired, match="expired"):
        adapter.requirement(order)


def test_requirement_rejects_non_payable_order(tmp_path):
    store = OrderStore(tmp_path / "orders.sqlite3")
    order = store.create_or_reuse(
        OrderRequest(
            skill_id="outcome-offer",
            input_text="Already paid quote.",
            currency="USD",
            locale="en-US",
            idempotency_key="requirement-paid",
        )
    )
    adapter = DemoPaymentAdapter(store, environment="test")
    paid = adapter.settle(order, f"demo:{order.order_id}:paid")

    with pytest.raises(PaymentRequirementUnavailable, match="not payable"):
        adapter.requirement(paid)


@pytest.mark.parametrize("advanced_status", ["generating", "failed", "delivered"])
def test_stale_demo_settlement_classifies_advanced_order_as_replay(
    tmp_path, advanced_status
):
    store = OrderStore(tmp_path / f"{advanced_status}.sqlite3")
    order = store.create_or_reuse(
        OrderRequest(
            skill_id="outcome-offer",
            input_text=f"Concurrent {advanced_status} order.",
            currency="USD",
            locale="en-US",
            idempotency_key=f"advanced-{advanced_status}",
        )
    )
    adapter = DemoPaymentAdapter(store, environment="test")
    store.mark_paid(order.order_id)
    store.begin_generation(order.order_id)
    if advanced_status == "failed":
        store.mark_generation_failed(order.order_id)
    elif advanced_status == "delivered":
        store.deliver_result(order.order_id, "result", "a" * 64)

    with pytest.raises(PaymentAlreadySettled):
        adapter.settle(order, f"demo:{order.order_id}:paid")

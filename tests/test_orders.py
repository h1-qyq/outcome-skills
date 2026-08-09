from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from gateway.contracts import OrderRequest
from gateway.orders import IdempotencyConflict, OrderStore
from gateway.payments.base import PaymentProofExpired
from gateway.payments.demo import DemoPaymentAdapter


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class LockAdvancingConnection:
    def __init__(self, connection, clock: Clock, advance_to: datetime) -> None:
        self._connection = connection
        self._clock = clock
        self._advance_to = advance_to

    def __enter__(self):
        self._connection.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return self._connection.__exit__(exc_type, exc_value, traceback)

    def execute(self, statement: str, parameters=()):
        cursor = self._connection.execute(statement, parameters)
        if statement == "BEGIN IMMEDIATE":
            self._clock.now = self._advance_to
        return cursor


class LockAdvancingOrderStore(OrderStore):
    def __init__(self, database_path, *, clock: Clock, advance_to: datetime, payment_ttl: timedelta):
        self._test_clock = clock
        self._advance_to = advance_to
        super().__init__(database_path, clock=clock, payment_ttl=payment_ttl)

    def _connect(self):
        return LockAdvancingConnection(super()._connect(), self._test_clock, self._advance_to)


def request(**overrides: object) -> OrderRequest:
    values: dict[str, object] = {
        "skill_id": "outcome-offer",
        "input_text": "  Buyer wording\r\nwith  figures  ",
        "currency": "USD",
        "locale": "en-US",
        "idempotency_key": "request-001",
    }
    values.update(overrides)
    return OrderRequest(**values)


def test_create_or_reuse_binds_server_owned_price_and_input_hash(tmp_path):
    store = OrderStore(tmp_path / "orders.sqlite3")

    first = store.create_or_reuse(request())
    again = store.create_or_reuse(request())

    assert first.order_id == again.order_id
    assert first.amount_minor == 1
    assert first.input_hash != ""
    assert first.status == "payment-required"


def test_idempotency_key_rejects_different_normalized_input(tmp_path):
    store = OrderStore(tmp_path / "orders.sqlite3")
    store.create_or_reuse(request())

    with pytest.raises(IdempotencyConflict):
        store.create_or_reuse(request(input_text="different buyer wording"))


@pytest.mark.parametrize(
    "changed_binding",
    [
        {"skill_id": "proof-pack"},
        {"currency": "CNY"},
        {"locale": "zh-CN"},
    ],
    ids=["skill-id", "currency", "locale"],
)
def test_idempotency_key_rejects_changed_non_input_binding(tmp_path, changed_binding):
    store = OrderStore(tmp_path / "orders.sqlite3")
    store.create_or_reuse(request())

    with pytest.raises(IdempotencyConflict):
        store.create_or_reuse(request(**changed_binding))


def test_order_request_rejects_client_supplied_amount():
    with pytest.raises(ValidationError):
        request(amount=1)


def test_input_size_error_applies_only_to_input_text():
    with pytest.raises(ValidationError) as error:
        request(input_text="a" * 12_001)

    assert error.value.errors()[0]["loc"] == ("input_text",)
    assert "12,000 Unicode code points" in error.value.errors()[0]["msg"]
    assert request(skill_id="x" * 12_001).skill_id == "x" * 12_001


def test_input_normalization_is_deterministic_but_preserves_interior_wording(tmp_path):
    store = OrderStore(tmp_path / "orders.sqlite3")
    first = store.create_or_reuse(request(input_text="  caf\u00e9\r\nQuote  12  "))
    repeated = store.create_or_reuse(request(input_text="caf\u00e9\nQuote  12", idempotency_key="request-002"))
    different_case = store.create_or_reuse(request(input_text="caf\u00e9\nquote  12", idempotency_key="request-003"))

    assert first.input_hash == repeated.input_hash
    assert first.input_hash != different_case.input_hash


def test_expired_receipt_cannot_change_order_to_paid(tmp_path):
    clock = Clock(datetime(2026, 7, 30, tzinfo=UTC))
    store = OrderStore(tmp_path / "orders.sqlite3", clock=clock, payment_ttl=timedelta(minutes=1))
    order = store.create_or_reuse(request())
    adapter = DemoPaymentAdapter(store, environment="development")

    clock.now += timedelta(minutes=2)

    assert adapter.verify(order, f"demo:{order.order_id}:paid") is False
    with pytest.raises(PaymentProofExpired, match="expired"):
        adapter.settle(order, f"demo:{order.order_id}:paid")
    assert store.get(order.order_id).status == "expired"


def test_settlement_rechecks_time_after_acquiring_write_lock(tmp_path):
    clock = Clock(datetime(2026, 7, 30, tzinfo=UTC))
    store = LockAdvancingOrderStore(
        tmp_path / "orders.sqlite3",
        clock=clock,
        advance_to=clock.now + timedelta(minutes=2),
        payment_ttl=timedelta(minutes=1),
    )
    order = store.create_or_reuse(request())
    adapter = DemoPaymentAdapter(store, environment="test")

    with pytest.raises(PaymentProofExpired, match="expired"):
        adapter.settle(order, f"demo:{order.order_id}:paid")

    assert store.get(order.order_id).status == "expired"


def test_settlement_started_before_expiry_can_complete_after_expiry(tmp_path):
    clock = Clock(datetime(2026, 7, 30, tzinfo=UTC))
    store = OrderStore(tmp_path / "orders.sqlite3", clock=clock, payment_ttl=timedelta(minutes=1))
    order = store.create_or_reuse(request())

    settling = store.begin_settlement(order.order_id, "a" * 64)
    clock.now += timedelta(minutes=2)
    paid = store.complete_settlement(settling.order_id)

    assert settling.status == "processing"
    assert paid.status == "paid"

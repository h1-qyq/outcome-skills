from datetime import UTC, datetime, timedelta
import hashlib
import sqlite3

from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest

from gateway.app import create_app
from gateway.contracts import PaymentRequirement
from gateway.orders import OrderStore, input_hash
from gateway.payments.base import (
    InvalidPaymentProof,
    PaymentAdapter,
    PaymentAlreadySettled,
    PaymentProofExpired,
    PaymentRequirementUnavailable,
)
from gateway.payments.demo import DemoPaymentAdapter
from gateway.results import (
    BuyerPayloadStore,
    FixtureResultEngine,
    RetryableGenerationError,
    SQLiteBuyerPayloadStore,
)


class ProductionReadyPaymentAdapter(PaymentAdapter):
    def __init__(self, store):
        self._store = store

    @property
    def production_ready(self):
        return True

    def requirement(self, order):
        return PaymentRequirement(
            order_id=order.order_id,
            currency=order.currency,
            amount_minor=order.amount_minor,
            expires_at=order.expires_at,
        )

    def verify(self, order, proof):
        return proof == f"live:{order.order_id}:paid"

    def settle(self, order, proof):
        if not self.verify(order, proof):
            raise InvalidPaymentProof("invalid payment proof")
        return self._store.mark_paid(order.order_id)


class ProductionReadyPayloadStore(BuyerPayloadStore):
    def __init__(self):
        self._payloads = {}

    @property
    def production_ready(self):
        return True

    def put(self, order, input_text):
        if input_hash(input_text) != order.input_hash:
            raise ValueError("payload integrity mismatch")
        self._payloads[order.order_id] = input_text

    def get_verified(self, order):
        return self._payloads[order.order_id]


class DishonestDemoPaymentAdapter(DemoPaymentAdapter):
    @property
    def production_ready(self):
        return True


class DishonestSQLitePayloadStore(SQLiteBuyerPayloadStore):
    @property
    def production_ready(self):
        return True

class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


class AuthenticatedTestClient(TestClient):
    """Carry the create-only result capability through legacy API scenarios."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._result_access_tokens: dict[str, str] = {}

    def request(self, method, url, *args, **kwargs):
        if isinstance(url, str) and url.startswith("/v1/orders/"):
            order_id = url.split("/", 4)[3]
            token = self._result_access_tokens.get(order_id)
            headers = dict(kwargs.get("headers") or {})
            if token and "Result-Access-Token" not in headers:
                headers["Result-Access-Token"] = token
                kwargs["headers"] = headers
        response = super().request(method, url, *args, **kwargs)
        if method.upper() == "POST" and url == "/v1/orders" and response.status_code == 201:
            payload = response.json()
            if isinstance(payload, dict) and isinstance(payload.get("order_id"), str):
                token = payload.get("result_access_token")
                if isinstance(token, str):
                    self._result_access_tokens[payload["order_id"]] = token
        return response


def make_client(tmp_path, engine=None, *, auto_auth=True):
    store = OrderStore(tmp_path / "orders.sqlite3")
    adapter = DemoPaymentAdapter(store, environment="test")
    client_type = AuthenticatedTestClient if auto_auth else TestClient
    return client_type(
        create_app(store=store, payment_adapter=adapter, result_engine=engine or FixtureResultEngine())
    )


def request(**overrides):
    value = {
        "skill_id": "outcome-offer",
        "input_text": "Need a clearer offer for a consulting service.",
        "currency": "USD",
        "locale": "en-US",
        "idempotency_key": "api-request-001",
    }
    value.update(overrides)
    return value


def result_access_headers(created, **extra):
    return {"Result-Access-Token": created["result_access_token"], **extra}


def test_create_returns_stable_high_entropy_result_access_token_but_stores_only_hash(tmp_path):
    database = tmp_path / "orders.sqlite3"
    store = OrderStore(database)
    client = AuthenticatedTestClient(
        create_app(
            store=store,
            payment_adapter=DemoPaymentAdapter(store, environment="test"),
            result_access_token_key=SecretStr("stable-test-key-" + "x" * 32),
        )
    )

    created = client.post("/v1/orders", json=request()).json()
    repeated = client.post("/v1/orders", json=request()).json()
    token = created["result_access_token"]

    assert repeated["result_access_token"] == token
    assert len(token) >= 43
    assert token not in str(created["order"])
    with sqlite3.connect(database) as connection:
        stored_hash = connection.execute(
            "SELECT result_access_token_sha256 FROM orders WHERE order_id = ?",
            (created["order_id"],),
        ).fetchone()[0]
    assert stored_hash == hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert token.encode("utf-8") not in database.read_bytes()


def test_status_and_result_require_the_create_only_access_capability(tmp_path):
    client = make_client(tmp_path, auto_auth=False)
    created = client.post("/v1/orders", json=request()).json()
    status_url = f"/v1/orders/{created['order_id']}"
    result_url = f"{status_url}/result"

    for headers in ({}, {"Result-Access-Token": "wrong"}):
        status_response = client.get(status_url, headers=headers)
        result_response = client.post(result_url, headers=headers)
        assert status_response.status_code == 404
        assert result_response.status_code == 404
        assert status_response.json() == result_response.json() == {"detail": "unknown order"}

    assert client.get(status_url, headers=result_access_headers(created)).status_code == 200
    assert client.post(result_url, headers=result_access_headers(created)).status_code == 402


def test_same_persistent_access_key_reproduces_capability_after_restart(tmp_path):
    database = tmp_path / "orders.sqlite3"
    key = SecretStr("persistent-result-access-key-" + "x" * 32)
    first_store = OrderStore(database)
    first_client = AuthenticatedTestClient(
        create_app(
            store=first_store,
            payment_adapter=DemoPaymentAdapter(first_store, environment="test"),
            result_access_token_key=key,
        )
    )
    created = first_client.post("/v1/orders", json=request()).json()

    restarted_store = OrderStore(database)
    restarted_client = AuthenticatedTestClient(
        create_app(
            store=restarted_store,
            payment_adapter=DemoPaymentAdapter(restarted_store, environment="test"),
            result_access_token_key=key,
        )
    )
    repeated = restarted_client.post("/v1/orders", json=request()).json()

    assert repeated["result_access_token"] == created["result_access_token"]
    assert restarted_client.get(
        f"/v1/orders/{created['order_id']}",
        headers=result_access_headers(created),
    ).status_code == 200


def test_payment_response_survives_generation_failure_and_app_restart_without_public_leak(tmp_path):
    class ReceiptAdapter(DemoPaymentAdapter):
        def __init__(self, store, receipt=None):
            super().__init__(store, environment="test")
            self.receipt = receipt

        def payment_response(self, _order_id):
            return self.receipt

    class FailsOnceEngine(FixtureResultEngine):
        def __init__(self):
            self.calls = 0

        def generate(self, skill_id, input_text, locale):
            self.calls += 1
            if self.calls == 1:
                raise RetryableGenerationError("provider unavailable")
            return super().generate(skill_id, input_text, locale)

    database = tmp_path / "orders.sqlite3"
    receipt = "private-x402-settlement-response"
    key = SecretStr("persistent-result-access-key-" + "y" * 32)
    first_store = OrderStore(database)
    first_client = AuthenticatedTestClient(
        create_app(
            store=first_store,
            payment_adapter=ReceiptAdapter(first_store, receipt),
            result_engine=FailsOnceEngine(),
            result_access_token_key=key,
        )
    )
    created = first_client.post("/v1/orders", json=request()).json()
    failed = first_client.post(
        f"/v1/orders/{created['order_id']}/result",
        headers=result_access_headers(
            created, **{"Payment-Proof": f"demo:{created['order_id']}:paid"}
        ),
    )

    assert failed.status_code == 503
    assert failed.headers["PAYMENT-RESPONSE"] == receipt
    assert receipt not in failed.text

    restarted_store = OrderStore(database)
    restarted_client = AuthenticatedTestClient(
        create_app(
            store=restarted_store,
            payment_adapter=ReceiptAdapter(restarted_store),
            result_engine=FixtureResultEngine(),
            result_access_token_key=key,
        )
    )
    retried = restarted_client.post(
        f"/v1/orders/{created['order_id']}/result",
        headers=result_access_headers(created),
    )
    status_response = restarted_client.get(
        f"/v1/orders/{created['order_id']}",
        headers=result_access_headers(created),
    )

    assert retried.status_code == 200
    assert retried.headers["PAYMENT-RESPONSE"] == receipt
    assert status_response.status_code == 200
    assert receipt not in retried.text
    assert receipt not in status_response.text


def test_result_is_blocked_until_a_valid_payment_proof_then_delivered(tmp_path):
    client = make_client(tmp_path)
    created = client.post("/v1/orders", json=request()).json()

    blocked = client.post(f"/v1/orders/{created['order_id']}/result")
    assert blocked.status_code == 402
    assert blocked.json()["detail"] == created["payment_requirement"]

    invalid = client.post(
        f"/v1/orders/{created['order_id']}/result",
        headers={"Payment-Proof": "invalid-proof"},
    )
    assert invalid.status_code == 402
    assert invalid.json()["detail"] == created["payment_requirement"]

    delivered = client.post(
        f"/v1/orders/{created['order_id']}/result",
        headers={"Payment-Proof": f"demo:{created['order_id']}:paid"},
    )
    assert delivered.status_code == 200
    assert delivered.json()["result"]
    assert len(delivered.json()["result_sha256"]) == 64


def test_unknown_product_and_order_return_not_found(tmp_path):
    client = make_client(tmp_path)

    assert client.post("/v1/orders", json=request(skill_id="not-a-product")).status_code == 404
    assert client.get("/v1/orders/missing").status_code == 404


def test_oversized_input_is_rejected_at_api_boundary(tmp_path):
    client = make_client(tmp_path)

    response = client.post("/v1/orders", json=request(input_text="a" * 12_001))

    assert response.status_code == 422
    assert "12,000" in response.text


def test_generation_failure_retries_without_a_second_settlement_or_generation(tmp_path):
    class FailsOnceEngine:
        def __init__(self):
            self.calls = 0
            self.fixture = FixtureResultEngine()

        def generate(self, skill_id, input_text, locale):
            self.calls += 1
            if self.calls == 1:
                raise RetryableGenerationError("provider unavailable")
            return self.fixture.generate(skill_id, input_text, locale)

    engine = FailsOnceEngine()
    client = make_client(tmp_path, engine)
    created = client.post("/v1/orders", json=request()).json()
    proof = {"Payment-Proof": f"demo:{created['order_id']}:paid"}

    failed = client.post(f"/v1/orders/{created['order_id']}/result", headers=proof)
    retried = client.post(f"/v1/orders/{created['order_id']}/result")
    replay = client.post(f"/v1/orders/{created['order_id']}/result")

    assert failed.status_code == 503
    assert retried.status_code == 200
    assert replay.json() == retried.json()
    assert engine.calls == 2


def test_programming_error_is_not_mislabeled_as_retryable_generation_failure(tmp_path):
    class BrokenEngine:
        def generate(self, skill_id, input_text, locale):
            raise RuntimeError("programming defect")

    store = OrderStore(tmp_path / "orders.sqlite3")
    adapter = DemoPaymentAdapter(store, environment="test")
    app = create_app(store=store, payment_adapter=adapter, result_engine=BrokenEngine())
    client = AuthenticatedTestClient(app, raise_server_exceptions=False)
    created = client.post("/v1/orders", json=request()).json()

    response = client.post(
        f"/v1/orders/{created['order_id']}/result",
        headers={"Payment-Proof": f"demo:{created['order_id']}:paid"},
    )

    assert response.status_code == 500
    assert store.get(created["order_id"]).status == "generating"


def test_production_startup_requires_live_model_configuration(tmp_path, monkeypatch):
    monkeypatch.delenv("MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("MODEL_NAME", raising=False)
    store = OrderStore(tmp_path / "orders.sqlite3")
    adapter = ProductionReadyPaymentAdapter(store)
    payload_store = ProductionReadyPayloadStore()

    with pytest.raises(ValueError, match="model configuration"):
        create_app(
            store=store,
            payment_adapter=adapter,
            payload_store=payload_store,
            environment="production",
        )


def set_live_model_environment(monkeypatch):
    monkeypatch.setenv("MODEL_BASE_URL", "https://model.example/v1")
    monkeypatch.setenv("MODEL_API_KEY", "server-secret")
    monkeypatch.setenv("MODEL_NAME", "server-model")


def test_production_rejects_demo_payment_adapter_created_for_test(tmp_path, monkeypatch):
    monkeypatch.delenv("MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("MODEL_NAME", raising=False)
    store = OrderStore(tmp_path / "orders.sqlite3")
    adapter = DemoPaymentAdapter(store, environment="test")
    payload_store = ProductionReadyPayloadStore()

    with pytest.raises(ValueError, match="demo payment adapter"):
        create_app(
            store=store,
            payment_adapter=adapter,
            payload_store=payload_store,
            environment="production",
        )


def test_production_rejects_demo_subclass_that_lies_about_readiness(tmp_path, monkeypatch):
    set_live_model_environment(monkeypatch)
    store = OrderStore(tmp_path / "orders.sqlite3")
    adapter = DishonestDemoPaymentAdapter(store, environment="test")

    with pytest.raises(ValueError, match="demo payment adapter"):
        create_app(
            store=store,
            payment_adapter=adapter,
            payload_store=ProductionReadyPayloadStore(),
            environment="production",
        )


def test_production_rejects_default_plaintext_payload_store(tmp_path, monkeypatch):
    set_live_model_environment(monkeypatch)
    store = OrderStore(tmp_path / "orders.sqlite3")
    adapter = ProductionReadyPaymentAdapter(store)

    with pytest.raises(ValueError, match="production-ready payload store"):
        create_app(store=store, payment_adapter=adapter, environment="production")
    assert not (tmp_path / "orders.sqlite3.payloads.sqlite3").exists()


def test_production_rejects_sqlite_subclass_that_lies_about_readiness(tmp_path, monkeypatch):
    set_live_model_environment(monkeypatch)
    store = OrderStore(tmp_path / "orders.sqlite3")
    payload_store = DishonestSQLitePayloadStore(tmp_path / "dishonest.sqlite3")

    with pytest.raises(ValueError, match="SQLite payload store"):
        create_app(
            store=store,
            payment_adapter=ProductionReadyPaymentAdapter(store),
            payload_store=payload_store,
            environment="production",
        )


def test_production_accepts_explicitly_ready_payment_and_payload_capabilities(tmp_path, monkeypatch):
    set_live_model_environment(monkeypatch)
    prompts_dir = tmp_path / "private-prompts"
    prompts_dir.mkdir()
    for skill_id in ("outcome-offer", "proof-pack", "reply-to-close"):
        (prompts_dir / f"{skill_id}.md").write_text("operator-private contract", encoding="utf-8")
    monkeypatch.setenv("MODEL_PROMPTS_DIR", str(prompts_dir))
    monkeypatch.setenv("RESULT_ACCESS_TOKEN_KEY", "production-result-access-key-" + "x" * 32)
    store = OrderStore(tmp_path / "orders.sqlite3")
    adapter = ProductionReadyPaymentAdapter(store)
    payload_store = ProductionReadyPayloadStore()

    app = create_app(
        store=store,
        payment_adapter=adapter,
        payload_store=payload_store,
        environment="production",
    )

    assert app is not None


@pytest.mark.parametrize("environment", ["prod", "Production", "staging", "", None])
def test_unknown_environment_fails_before_fixture_payload_side_effect(
    tmp_path, environment
):
    store = OrderStore(tmp_path / "orders.sqlite3")

    with pytest.raises(ValueError, match="environment"):
        create_app(
            store=store,
            payment_adapter=DemoPaymentAdapter(store, environment="test"),
            environment=environment,
        )

    assert not (tmp_path / "orders.sqlite3.payloads.sqlite3").exists()


def test_expired_requirement_returns_structured_non_payable_response(tmp_path):
    clock = Clock(datetime(2026, 7, 30, tzinfo=UTC))
    store = OrderStore(tmp_path / "orders.sqlite3", clock=clock, payment_ttl=timedelta(minutes=1))
    adapter = DemoPaymentAdapter(store, environment="test")
    client = AuthenticatedTestClient(create_app(store=store, payment_adapter=adapter))
    created = client.post("/v1/orders", json=request()).json()
    clock.now += timedelta(minutes=2)

    response = client.post(f"/v1/orders/{created['order_id']}/result")

    assert response.status_code == 410
    assert response.json()["detail"] == {
        "code": "payment_requirement_expired",
        "order_id": created["order_id"],
        "payable": False,
        "payment_requirement": created["payment_requirement"],
    }


def test_gateway_rejects_expiry_before_calling_permissive_adapter_requirement(tmp_path):
    class PermissiveAdapter(DemoPaymentAdapter):
        def __init__(self, store):
            super().__init__(store, environment="test")
            self.requirement_calls = 0

        def requirement(self, order):
            self.requirement_calls += 1
            return PaymentRequirement(
                order_id=order.order_id,
                currency=order.currency,
                amount_minor=order.amount_minor,
                expires_at=order.expires_at,
            )

    clock = Clock(datetime(2026, 7, 30, tzinfo=UTC))
    store = OrderStore(
        tmp_path / "orders.sqlite3",
        clock=clock,
        payment_ttl=timedelta(minutes=1),
    )
    adapter = PermissiveAdapter(store)
    client = AuthenticatedTestClient(create_app(store=store, payment_adapter=adapter))
    created = client.post("/v1/orders", json=request()).json()
    assert adapter.requirement_calls == 1
    clock.now += timedelta(minutes=2)

    response = client.post(f"/v1/orders/{created['order_id']}/result")

    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "payment_requirement_expired"
    assert adapter.requirement_calls == 1


def test_unrelated_requirement_value_error_remains_server_error_on_create(tmp_path):
    class BrokenRequirementAdapter(DemoPaymentAdapter):
        def requirement(self, order):
            raise ValueError("programming defect")

    store = OrderStore(tmp_path / "orders.sqlite3")
    adapter = BrokenRequirementAdapter(store, environment="test")
    client = AuthenticatedTestClient(
        create_app(store=store, payment_adapter=adapter),
        raise_server_exceptions=False,
    )

    response = client.post("/v1/orders", json=request())

    assert response.status_code == 500


def test_unrelated_requirement_value_error_remains_server_error_on_result(tmp_path):
    class BreaksAfterQuoteAdapter(DemoPaymentAdapter):
        def __init__(self, store):
            super().__init__(store, environment="test")
            self.calls = 0

        def requirement(self, order):
            self.calls += 1
            if self.calls > 1:
                raise ValueError("programming defect")
            return super().requirement(order)

    store = OrderStore(tmp_path / "orders.sqlite3")
    adapter = BreaksAfterQuoteAdapter(store)
    client = AuthenticatedTestClient(
        create_app(store=store, payment_adapter=adapter),
        raise_server_exceptions=False,
    )
    created = client.post("/v1/orders", json=request()).json()

    response = client.post(f"/v1/orders/{created['order_id']}/result")

    assert response.status_code == 500


def test_unrelated_settlement_value_error_remains_server_error(tmp_path):
    class BrokenSettlementAdapter(DemoPaymentAdapter):
        def settle(self, order, proof):
            raise ValueError("programming defect")

    store = OrderStore(tmp_path / "orders.sqlite3")
    adapter = BrokenSettlementAdapter(store, environment="test")
    client = AuthenticatedTestClient(
        create_app(store=store, payment_adapter=adapter),
        raise_server_exceptions=False,
    )
    created = client.post("/v1/orders", json=request()).json()

    response = client.post(
        f"/v1/orders/{created['order_id']}/result",
        headers={"Payment-Proof": f"demo:{created['order_id']}:paid"},
    )

    assert response.status_code == 500


def test_typed_payment_replay_reloads_paid_order_and_continues(tmp_path):
    class RaceAdapter(DemoPaymentAdapter):
        def settle(self, order, proof):
            self._store.mark_paid(order.order_id)
            raise PaymentAlreadySettled("payment proof already used")

    store = OrderStore(tmp_path / "orders.sqlite3")
    adapter = RaceAdapter(store, environment="test")
    client = AuthenticatedTestClient(create_app(store=store, payment_adapter=adapter))
    created = client.post("/v1/orders", json=request()).json()

    response = client.post(
        f"/v1/orders/{created['order_id']}/result",
        headers={"Payment-Proof": f"demo:{created['order_id']}:paid"},
    )

    assert response.status_code == 200
    assert response.json()["result"]


def test_typed_expired_settlement_returns_structured_gone(tmp_path):
    class ExpiredSettlementAdapter(DemoPaymentAdapter):
        def settle(self, order, proof):
            raise PaymentProofExpired("payment proof is expired")

    store = OrderStore(tmp_path / "orders.sqlite3")
    adapter = ExpiredSettlementAdapter(store, environment="test")
    client = AuthenticatedTestClient(create_app(store=store, payment_adapter=adapter))
    created = client.post("/v1/orders", json=request()).json()

    response = client.post(
        f"/v1/orders/{created['order_id']}/result",
        headers={"Payment-Proof": f"demo:{created['order_id']}:paid"},
    )

    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "payment_proof_expired"
    assert response.json()["detail"]["payable"] is False


def test_typed_invalid_settlement_returns_authoritative_payment_requirement(tmp_path):
    class InvalidSettlementAdapter(DemoPaymentAdapter):
        def settle(self, order, proof):
            raise InvalidPaymentProof("invalid payment proof")

    store = OrderStore(tmp_path / "orders.sqlite3")
    adapter = InvalidSettlementAdapter(store, environment="test")
    client = AuthenticatedTestClient(create_app(store=store, payment_adapter=adapter))
    created = client.post("/v1/orders", json=request()).json()

    response = client.post(
        f"/v1/orders/{created['order_id']}/result",
        headers={"Payment-Proof": f"demo:{created['order_id']}:paid"},
    )

    assert response.status_code == 402
    assert response.json()["detail"] == created["payment_requirement"]


def test_conflicting_legacy_and_v2_payment_headers_are_rejected_before_settlement(tmp_path):
    store = OrderStore(tmp_path / "orders.sqlite3")
    adapter = DemoPaymentAdapter(store, environment="test")
    client = AuthenticatedTestClient(create_app(store=store, payment_adapter=adapter))
    created = client.post("/v1/orders", json=request()).json()

    response = client.post(
        f"/v1/orders/{created['order_id']}/result",
        headers={
            "Payment-Proof": f"demo:{created['order_id']}:paid",
            "PAYMENT-SIGNATURE": "conflicting-proof",
        },
    )

    assert response.status_code == 400
    assert store.get(created["order_id"]).status == "payment-required"


def test_typed_unavailable_requirement_returns_structured_gone(tmp_path):
    class UnavailableAfterQuoteAdapter(DemoPaymentAdapter):
        def __init__(self, store):
            super().__init__(store, environment="test")
            self.calls = 0

        def requirement(self, order):
            self.calls += 1
            if self.calls > 1:
                raise PaymentRequirementUnavailable("provider unavailable")
            return super().requirement(order)

    store = OrderStore(tmp_path / "orders.sqlite3")
    adapter = UnavailableAfterQuoteAdapter(store)
    client = AuthenticatedTestClient(create_app(store=store, payment_adapter=adapter))
    created = client.post("/v1/orders", json=request()).json()

    response = client.post(f"/v1/orders/{created['order_id']}/result")

    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "payment_requirement_unavailable"


def test_concurrent_payment_during_requirement_resumes_without_false_410(tmp_path):
    class ConcurrentRequirementAdapter(DemoPaymentAdapter):
        def __init__(self, store):
            super().__init__(store, environment="test")
            self.calls = 0

        def requirement(self, order):
            self.calls += 1
            if self.calls > 1:
                self._store.mark_paid(order.order_id)
            return super().requirement(order)

    store = OrderStore(tmp_path / "orders.sqlite3")
    adapter = ConcurrentRequirementAdapter(store)
    client = AuthenticatedTestClient(create_app(store=store, payment_adapter=adapter))
    created = client.post("/v1/orders", json=request()).json()

    response = client.post(f"/v1/orders/{created['order_id']}/result")

    assert response.status_code == 200
    assert response.json()["result"]


def test_concurrent_payment_before_false_verify_resumes_without_false_402(tmp_path):
    class ConcurrentVerifyAdapter(DemoPaymentAdapter):
        def requirement(self, order):
            return PaymentRequirement(
                order_id=order.order_id,
                currency=order.currency,
                amount_minor=order.amount_minor,
                expires_at=order.expires_at,
            )

        def verify(self, order, proof):
            self._store.mark_paid(order.order_id)
            return False

    store = OrderStore(tmp_path / "orders.sqlite3")
    adapter = ConcurrentVerifyAdapter(store, environment="test")
    client = AuthenticatedTestClient(create_app(store=store, payment_adapter=adapter))
    created = client.post("/v1/orders", json=request()).json()

    response = client.post(
        f"/v1/orders/{created['order_id']}/result",
        headers={"Payment-Proof": f"demo:{created['order_id']}:paid"},
    )

    assert response.status_code == 200
    assert response.json()["result"]


@pytest.mark.parametrize("include_proof", [False, True])
def test_clock_crossing_expiry_during_requirement_returns_gone(
    tmp_path, include_proof
):
    clock = Clock(datetime(2026, 7, 30, tzinfo=UTC))

    class ExpiringAfterRequirementAdapter(DemoPaymentAdapter):
        def __init__(self, store):
            super().__init__(store, environment="test")
            self.calls = 0

        def requirement(self, order):
            requirement = super().requirement(order)
            self.calls += 1
            if self.calls > 1:
                clock.now = order.expires_at
            return requirement

    store = OrderStore(
        tmp_path / "orders.sqlite3",
        clock=clock,
        payment_ttl=timedelta(minutes=1),
    )
    adapter = ExpiringAfterRequirementAdapter(store)
    client = AuthenticatedTestClient(create_app(store=store, payment_adapter=adapter))
    created = client.post("/v1/orders", json=request()).json()

    headers = (
        {"Payment-Proof": f"demo:{created['order_id']}:paid"}
        if include_proof
        else {}
    )
    response = client.post(
        f"/v1/orders/{created['order_id']}/result",
        headers=headers,
    )

    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "payment_requirement_expired"


def test_exact_expiry_after_successful_verification_skips_settlement(tmp_path):
    clock = Clock(datetime(2026, 7, 30, tzinfo=UTC))

    class ExpiringVerifyAdapter(PaymentAdapter):
        def __init__(self):
            self.settle_calls = 0

        @property
        def production_ready(self):
            return False

        def requirement(self, order):
            return PaymentRequirement(
                order_id=order.order_id,
                currency=order.currency,
                amount_minor=order.amount_minor,
                expires_at=order.expires_at,
            )

        def verify(self, order, proof):
            clock.now = order.expires_at
            return True

        def settle(self, order, proof):
            self.settle_calls += 1
            return order.model_copy(update={"status": "paid"})

    store = OrderStore(
        tmp_path / "orders.sqlite3",
        clock=clock,
        payment_ttl=timedelta(minutes=1),
    )
    adapter = ExpiringVerifyAdapter()
    client = AuthenticatedTestClient(create_app(store=store, payment_adapter=adapter))
    created = client.post("/v1/orders", json=request()).json()

    response = client.post(
        f"/v1/orders/{created['order_id']}/result",
        headers={"Payment-Proof": "provider-proof"},
    )

    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "payment_requirement_expired"
    assert response.json()["detail"]["payable"] is False
    assert adapter.settle_calls == 0


@pytest.mark.parametrize(
    ("advanced_status", "expected_status"),
    [
        ("paid", 200),
        ("failed", 200),
        ("generating", 409),
        ("delivered", 200),
    ],
)
def test_successful_verification_reloads_advanced_state_before_settlement(
    tmp_path, advanced_status, expected_status
):
    class AdvancingVerifyAdapter(PaymentAdapter):
        def __init__(self, store):
            self.store = store
            self.settle_calls = 0

        @property
        def production_ready(self):
            return False

        def requirement(self, order):
            return PaymentRequirement(
                order_id=order.order_id,
                currency=order.currency,
                amount_minor=order.amount_minor,
                expires_at=order.expires_at,
            )

        def verify(self, order, proof):
            paid = self.store.mark_paid(order.order_id)
            if advanced_status != "paid":
                generating = self.store.begin_generation(paid.order_id)
                if advanced_status == "failed":
                    self.store.mark_generation_failed(generating.order_id)
                elif advanced_status == "delivered":
                    self.store.deliver_result(
                        generating.order_id,
                        "concurrent delivery",
                        "a" * 64,
                    )
            return True

        def settle(self, order, proof):
            self.settle_calls += 1
            return self.store.get(order.order_id)

    store = OrderStore(tmp_path / "orders.sqlite3")
    adapter = AdvancingVerifyAdapter(store)
    client = AuthenticatedTestClient(create_app(store=store, payment_adapter=adapter))
    created = client.post("/v1/orders", json=request()).json()

    response = client.post(
        f"/v1/orders/{created['order_id']}/result",
        headers={"Payment-Proof": "provider-proof"},
    )

    assert response.status_code == expected_status
    assert adapter.settle_calls == 0
    if advanced_status == "delivered":
        assert response.json() == {
            "result": "concurrent delivery",
            "result_sha256": "a" * 64,
        }


def test_public_order_responses_omit_private_buyer_and_server_data(tmp_path):
    client = make_client(tmp_path)
    raw_input = "PRIVATE buyer message 83902"
    created = client.post("/v1/orders", json=request(input_text=raw_input)).json()

    fetched = client.get(f"/v1/orders/{created['order_id']}")

    serialized = fetched.text
    assert fetched.status_code == 200
    assert raw_input not in str(created)
    assert raw_input not in serialized
    for forbidden_field in ("input_text", "input_hash", "idempotency_key", "prompt", "api_key"):
        assert forbidden_field not in fetched.json()

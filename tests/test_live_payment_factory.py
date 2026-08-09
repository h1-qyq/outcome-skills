import hashlib

import pytest
from fastapi.testclient import TestClient

from gateway.app import create_app
from gateway.config import GatewayConfig, build_payment_adapter, load_config
from gateway.contracts import OrderRequest
from gateway.orders import OrderStore
from gateway.payments.router import CurrencyPaymentRouter
from gateway.payments.clawtip import ClawTipActivation
from gateway.payments.x402 import AuthenticatedCdpProvider


TEST_KEY = "ASNFZ4mrze/+3LqYdlQyEA=="


def live_config(tmp_path, *, environment="test"):
    return GatewayConfig(
        environment=environment,
        payment_adapter="live",
        x402_pay_to="0x0000000000000000000000000000000000000001",
        x402_resource_base_url="https://gateway.example",
        clawtip_pay_to="merchant-pay-to",
        clawtip_slug="one-cent-outcomes",
        clawtip_sm4_key=TEST_KEY,
        clawtip_amount_representation="string",
        clawtip_order_records_dir=str(tmp_path / "openclaw" / "skills" / "orders"),
        clawtip_resource_url="https://gateway.example/v1/results",
    )


def test_production_factory_fails_closed_without_injected_tested_cdp_auth(tmp_path):
    with pytest.raises(ValueError, match="authenticated CDP"):
        build_payment_adapter(OrderStore(tmp_path / "orders.sqlite3"), live_config(tmp_path, environment="production"))


def test_environment_cdp_variables_do_not_count_as_authenticated_provider(tmp_path):
    config = load_config({
        "GATEWAY_ENVIRONMENT": "production",
        "PAYMENT_ADAPTER": "live",
        "X402_PAY_TO": "0x0000000000000000000000000000000000000001",
        "X402_CDP_API_KEY_ID": "not-proof",
        "X402_CDP_API_KEY_SECRET": "not-proof",
    })

    with pytest.raises(ValueError, match="authenticated CDP"):
        build_payment_adapter(OrderStore(tmp_path / "orders.sqlite3"), config)


def test_factory_routes_only_server_owned_usd_and_cny_orders(tmp_path):
    store = OrderStore(tmp_path / "orders.sqlite3")
    adapter = build_payment_adapter(
        store,
        live_config(tmp_path),
        x402_payment_required_builder=lambda _settings, _order: "offline-payment-required",
    )
    usd = store.create_or_reuse(OrderRequest(
        skill_id="outcome-offer", input_text="usd", currency="USD", locale="en-US", idempotency_key="usd-router",
    ))
    cny = store.create_or_reuse(OrderRequest(
        skill_id="proof-pack", input_text="cny", currency="CNY", locale="zh-CN", idempotency_key="cny-router",
    ))

    assert isinstance(adapter, CurrencyPaymentRouter)
    assert adapter.requirement(usd).provider_data.provider == "x402"
    assert adapter.requirement(cny).provider_data.provider == "clawtip"


def test_app_keeps_clawtip_handoff_public_and_never_writes_buyer_input(tmp_path):
    store = OrderStore(tmp_path / "orders.sqlite3")
    adapter = build_payment_adapter(
        store,
        live_config(tmp_path),
        x402_payment_required_builder=lambda _settings, _order: "offline-payment-required",
    )
    client = TestClient(create_app(store=store, payment_adapter=adapter, environment="test"))
    private_question = "This is the buyer's private proof source."

    created = client.post("/v1/orders", json={
        "skill_id": "proof-pack", "input_text": private_question, "currency": "CNY",
        "locale": "en-US", "idempotency_key": "app-question-source",
    })

    assert created.status_code == 201
    provider = created.json()["payment_requirement"]["provider_data"]
    assert provider["skill_id"] == "proof-pack"
    assert provider["resource_url"] == "https://gateway.example/v1/results"
    assert "question" not in provider
    assert "payCredential" not in provider
    assert private_question not in created.text
    assert not (tmp_path / "openclaw" / "skills" / "orders").exists()


def test_app_recovers_reserved_clawtip_settlement_only_after_same_credential(tmp_path):
    store = OrderStore(tmp_path / "orders.sqlite3")
    adapter = build_payment_adapter(
        store, live_config(tmp_path),
        x402_payment_required_builder=lambda _settings, _order: "offline-payment-required",
    )
    client = TestClient(create_app(store=store, payment_adapter=adapter, environment="test"))
    created = client.post("/v1/orders", json={
        "skill_id": "proof-pack", "input_text": "private", "currency": "CNY",
        "locale": "en-US", "idempotency_key": "recover-clawtip",
    }).json()
    order_id = created["order_id"]
    provider = created["payment_requirement"]["provider_data"]
    clawtip = adapter._cny
    proof = clawtip.codec.encrypt(__import__("json").dumps({
        "orderNo": provider["order_no"], "amount": "6", "payTo": provider["pay_to"], "payStatus": "SUCCESS",
    }, separators=(",", ":")))
    store.begin_settlement(order_id, hashlib.sha256(proof.encode("utf-8")).hexdigest())

    access = {"Result-Access-Token": created["result_access_token"]}
    waiting = client.post(f"/v1/orders/{order_id}/result", headers=access)
    recovered = client.post(
        f"/v1/orders/{order_id}/result",
        headers={**access, "Payment-Proof": proof},
    )

    assert waiting.status_code == 409
    assert recovered.status_code == 200
    assert store.get(order_id).status == "delivered"


def test_production_factory_accepts_only_explicitly_tested_cdp_provider(tmp_path):
    with pytest.raises(ValueError, match="human ClawTip activation"):
        build_payment_adapter(
            OrderStore(tmp_path / "missing-activation.sqlite3"),
            live_config(tmp_path, environment="production"),
            cdp_auth_provider=AuthenticatedCdpProvider(provider=object(), integration_tested=True),
        )
    adapter = build_payment_adapter(
        OrderStore(tmp_path / "orders.sqlite3"),
        live_config(tmp_path, environment="production"),
        cdp_auth_provider=AuthenticatedCdpProvider(provider=object(), integration_tested=True),
        clawtip_activation=ClawTipActivation(onboarding_completed=True, sandbox_interop_confirmed=True),
    )

    assert adapter.production_ready is True

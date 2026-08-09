import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import sqlite3
import sys
from threading import Barrier
from types import ModuleType, SimpleNamespace

from httpx import HTTPError
import pytest
from x402.schemas import PaymentRequired, PaymentRequirements, SupportedKind, SupportedResponse

from gateway.app import create_app
from gateway.config import GatewayConfig, build_payment_adapter
from gateway.contracts import OrderRequest, PaymentRequirement
from gateway.orders import InvalidOrderState, OrderStore
from gateway.payments.base import (
    InvalidPaymentProof,
    PaymentAdapter,
    PaymentProcessing,
    PaymentRequirementUnavailable,
)
from gateway.payments.clawtip import (
    ClawTipActivation,
    ClawTipPaymentAdapter,
    ClawTipSettings,
    Sm4EcbPkcs7Codec,
)
from gateway.payments.x402 import (
    AuthenticatedCdpProvider,
    OfficialX402Facilitator,
    X402PaymentAdapter,
    X402Settings,
)


OTHER_VALID_KEY = base64.b64encode(bytes.fromhex("00112233445566778899aabbccddeeff")).decode("ascii")


def make_order(tmp_path, *, currency="USD"):
    store = OrderStore(tmp_path / f"{currency}.sqlite3")
    order = store.create_or_reuse(OrderRequest(
        skill_id="outcome-offer",
        input_text="private input",
        currency=currency,
        locale="en-US",
        idempotency_key=f"hardening-{currency}",
    ))
    return store, order


def install_offline_official_sdk(monkeypatch, *, initialize_error=None, requirements_match=True):
    import x402.http as http_module
    import x402.server as server_module

    events = []

    class FakeServer:
        def __init__(self, _client):
            self.initialized = False

        def register(self, network, _scheme):
            events.append(("register", network))

        def initialize(self):
            events.append(("initialize", None))
            if initialize_error is not None:
                raise initialize_error
            self.initialized = True

        def build_payment_requirements(self, config):
            assert self.initialized, "build called before initialize"
            events.append(("build", config.network))
            return [PaymentRequirements(
                scheme="exact",
                network=config.network,
                asset="0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                amount="10000",
                pay_to=config.pay_to,
                max_timeout_seconds=300,
                extra={"name": "USDC", "version": "2"},
            )]

        def create_payment_required_response(self, requirements, resource):
            assert self.initialized
            events.append(("required", None))
            return PaymentRequired(accepts=requirements, resource=resource)

        def find_matching_requirements(self, requirements, _payload):
            assert self.initialized
            events.append(("match", None))
            return requirements[0] if requirements_match else None

        def verify_payment(self, _payload, _requirements):
            assert self.initialized
            events.append(("verify", None))
            return SimpleNamespace(is_valid=True)

        def settle_payment(self, _payload, _requirements):
            assert self.initialized
            events.append(("settle", None))
            return SimpleNamespace(success=True, error_reason=None)

    exact_module = ModuleType("x402.mechanisms.evm.exact")
    exact_module.ExactEvmServerScheme = type("ExactEvmServerScheme", (), {})
    monkeypatch.setitem(sys.modules, "x402.mechanisms.evm.exact", exact_module)
    monkeypatch.setattr(server_module, "x402ResourceServerSync", FakeServer)
    monkeypatch.setattr(http_module, "HTTPFacilitatorClientSync", lambda _config: object())
    monkeypatch.setattr(http_module, "decode_payment_signature_header", lambda _proof: object())
    monkeypatch.setattr(http_module, "encode_payment_response_header", lambda _response: "encoded-response")
    return events


def test_official_x402_bridge_initializes_before_build_verify_and_settle(tmp_path, monkeypatch):
    events = install_offline_official_sdk(monkeypatch)
    _, order = make_order(tmp_path)
    bridge = OfficialX402Facilitator(
        X402Settings.testnet(pay_to="0x0000000000000000000000000000000000000001")
    )

    assert bridge.payment_required(order)
    assert bridge.verify_order(order, "proof") is True
    assert bridge.settle_order(order, "proof").success is True

    assert [event[0] for event in events] == [
        "register", "initialize", "build", "required",
        "register", "initialize", "build", "match", "verify",
        "register", "initialize", "build", "match", "settle",
    ]


@pytest.mark.parametrize("error", [RuntimeError("not initialized"), HTTPError("facilitator offline")])
def test_official_x402_initialization_failures_are_typed_unavailable(tmp_path, monkeypatch, error):
    install_offline_official_sdk(monkeypatch, initialize_error=error)
    store, order = make_order(tmp_path)
    adapter = X402PaymentAdapter(
        store,
        X402Settings.testnet(pay_to="0x0000000000000000000000000000000000000001"),
    )

    with pytest.raises(PaymentRequirementUnavailable, match="SDK configuration"):
        adapter.requirement(order)


def test_real_x402_server_missing_network_capability_is_typed_unavailable(tmp_path, monkeypatch):
    import x402.http as http_module

    class InertFacilitator:
        def get_supported(self):
            return SupportedResponse(kinds=[SupportedKind(
                x402Version=2,
                scheme="exact",
                network="eip155:1",
            )])

    class InertExactScheme:
        scheme = "exact"

    exact_module = ModuleType("x402.mechanisms.evm.exact")
    exact_module.ExactEvmServerScheme = InertExactScheme
    monkeypatch.setitem(sys.modules, "x402.mechanisms.evm.exact", exact_module)
    monkeypatch.setattr(http_module, "HTTPFacilitatorClientSync", lambda _config: InertFacilitator())
    store, order = make_order(tmp_path)
    adapter = X402PaymentAdapter(
        store,
        X402Settings.testnet(pay_to="0x0000000000000000000000000000000000000001"),
    )

    with pytest.raises(PaymentRequirementUnavailable, match="SDK configuration"):
        adapter.requirement(order)


def test_official_x402_unmatched_proof_is_exactly_false_through_adapter(tmp_path, monkeypatch):
    install_offline_official_sdk(monkeypatch, requirements_match=False)
    store, order = make_order(tmp_path)
    adapter = X402PaymentAdapter(
        store,
        X402Settings.testnet(pay_to="0x0000000000000000000000000000000000000001"),
    )

    assert adapter.verify(order, "unmatched-proof") is False


def test_x402_recovery_maps_sdk_reinitialization_failure_to_processing(tmp_path, monkeypatch):
    install_offline_official_sdk(monkeypatch, initialize_error=RuntimeError("facilitator unavailable"))
    store, order = make_order(tmp_path)
    adapter = X402PaymentAdapter(
        store,
        X402Settings.testnet(pay_to="0x0000000000000000000000000000000000000001"),
    )
    proof = "same-signed-proof"
    store.begin_settlement(order.order_id, hashlib.sha256(proof.encode("utf-8")).hexdigest())

    with pytest.raises(PaymentProcessing, match="still processing"):
        adapter.resume_settlement(order, proof)

    assert store.get(order.order_id).status == "processing"


def test_sm4_known_vector_is_independent_of_issued_merchant_key():
    codec = Sm4EcbPkcs7Codec(OTHER_VALID_KEY)
    plaintext = '{"orderNo":"merchant-order","amount":6,"payTo":"merchant"}'

    encrypted = codec.encrypt(plaintext)

    assert codec.decrypt(encrypted) == plaintext
    assert OTHER_VALID_KEY not in repr(codec)


class AlwaysTrueFacilitator:
    def verify(self, _requirement, _proof):
        return True

    def settle(self, _requirement, _proof):
        return True


def production_inputs(tmp_path):
    config = GatewayConfig(
        environment="production",
        payment_adapter="live",
        x402_pay_to="0x0000000000000000000000000000000000000001",
        x402_resource_base_url="https://gateway.example",
        clawtip_pay_to="merchant-pay-to",
        clawtip_slug="one-cent-outcomes",
        clawtip_sm4_key=OTHER_VALID_KEY,
        clawtip_amount_representation="string",
        clawtip_order_records_dir=str(tmp_path / "openclaw" / "skills" / "orders"),
        clawtip_resource_url="https://gateway.example/v1/results",
    )
    auth = AuthenticatedCdpProvider(provider={"secret": "cdp-super-secret"}, integration_tested=True)
    activation = ClawTipActivation(onboarding_completed=True, sandbox_interop_confirmed=True)
    return config, auth, activation


@pytest.mark.parametrize("seam", ["facilitator", "builder"])
def test_production_factory_rejects_all_injected_x402_test_seams(tmp_path, seam):
    config, auth, activation = production_inputs(tmp_path)
    kwargs = {
        "cdp_auth_provider": auth,
        "clawtip_activation": activation,
    }
    if seam == "facilitator":
        kwargs["x402_facilitator"] = AlwaysTrueFacilitator()
    else:
        kwargs["x402_payment_required_builder"] = lambda _settings, _order: "fake"

    with pytest.raises(ValueError, match="test seams"):
        build_payment_adapter(OrderStore(tmp_path / f"{seam}.sqlite3"), config, **kwargs)


def test_direct_mainnet_x402_adapter_with_fake_seam_is_not_production_ready(tmp_path):
    _, auth, _ = production_inputs(tmp_path)
    settings = X402Settings.production(
        pay_to="0x0000000000000000000000000000000000000001",
        auth_provider=auth,
        resource_base_url="https://gateway.example",
    )
    store = OrderStore(tmp_path / "direct.sqlite3")

    assert X402PaymentAdapter(store, settings, facilitator=AlwaysTrueFacilitator()).production_ready is False
    assert X402PaymentAdapter(store, settings, payment_required_builder=lambda _s, _o: "fake").production_ready is False


class ReadyAdapter(PaymentAdapter):
    @property
    def production_ready(self):
        return True

    def requirement(self, order):
        return PaymentRequirement(
            order_id=order.order_id, currency=order.currency,
            amount_minor=order.amount_minor, expires_at=order.expires_at,
        )

    def verify(self, _order, _proof):
        return True

    def settle(self, order, _proof):
        return order


def test_create_app_rejects_production_ready_adapter_outside_production(tmp_path):
    with pytest.raises(ValueError, match="production-ready payment adapter"):
        create_app(store=OrderStore(tmp_path / "app.sqlite3"), payment_adapter=ReadyAdapter())


def test_private_ledger_binds_processing_recovery_to_original_proof_digest(tmp_path):
    store, order = make_order(tmp_path)
    original = "original-proof"
    digest = hashlib.sha256(original.encode("utf-8")).hexdigest()

    store.begin_settlement(order.order_id, digest)

    database = (tmp_path / "USD.sqlite3").read_bytes()
    assert original.encode("utf-8") not in database
    assert digest.encode("ascii") in database
    assert store.settlement_proof_matches(order.order_id, digest) is True
    assert store.settlement_proof_matches(order.order_id, hashlib.sha256(b"other").hexdigest()) is False


def test_concurrent_settlement_claim_atomically_binds_only_the_winning_digest(tmp_path):
    database_path = tmp_path / "concurrent.sqlite3"
    store = OrderStore(database_path)
    order = store.create_or_reuse(OrderRequest(
        skill_id="outcome-offer", input_text="private", currency="USD", locale="en-US",
        idempotency_key="concurrent-proof-binding",
    ))
    digests = [hashlib.sha256(value).hexdigest() for value in (b"first", b"second")]
    barrier = Barrier(2)

    def claim(digest):
        contender = OrderStore(database_path)
        barrier.wait()
        try:
            contender.begin_settlement(order.order_id, digest)
            return digest
        except InvalidOrderState:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, digests))

    winner = next(claim for claim in claims if claim is not None)
    loser = next(digest for digest in digests if digest != winner)
    assert sum(claim is not None for claim in claims) == 1
    assert store.settlement_proof_matches(order.order_id, winner) is True
    assert store.settlement_proof_matches(order.order_id, loser) is False


def test_order_store_migrates_existing_ledger_with_private_proof_digest_column(tmp_path):
    database_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("""
            CREATE TABLE orders (
                order_id TEXT PRIMARY KEY, skill_id TEXT NOT NULL, input_hash TEXT NOT NULL,
                currency TEXT NOT NULL, locale TEXT NOT NULL, amount_minor INTEGER NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE, status TEXT NOT NULL,
                created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                result_body TEXT, result_sha256 TEXT
            )
        """)

    OrderStore(database_path)

    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(orders)")}
    assert "settlement_proof_sha256" in columns


def test_clawtip_processing_recovery_rejects_different_valid_credential(tmp_path):
    store, order = make_order(tmp_path, currency="CNY")
    settings = ClawTipSettings(
        pay_to="merchant-pay-to", slug="one-cent-outcomes", sm4_key=OTHER_VALID_KEY,
        amount_representation="string", order_records_dir=tmp_path / "records",
        payment_skill="clawtip-sandbox", environment="test",
        description="One Cent Outcomes result",
        resource_url="https://example.invalid/result",
    )
    adapter = ClawTipPaymentAdapter(store, settings)
    requirement = adapter.requirement(order)
    base = {
        "orderNo": requirement.provider_data.order_no,
        "amount": "6",
        "payTo": "merchant-pay-to",
        "payStatus": "SUCCESS",
    }
    original = adapter.codec.encrypt(json.dumps(base, separators=(",", ":")))
    different = adapter.codec.encrypt(json.dumps({**base, "finishTime": "later"}, separators=(",", ":")))
    store.begin_settlement(order.order_id, hashlib.sha256(original.encode("utf-8")).hexdigest())

    with pytest.raises(InvalidPaymentProof, match="original"):
        adapter.resume_settlement(order, different)

    assert adapter.resume_settlement(order, original).status == "paid"


class PeerCompletesSettlementStore(OrderStore):
    """Simulate another request winning completion after this request settles."""

    def __init__(self, database_path):
        super().__init__(database_path)
        self._peer_won = False

    def complete_settlement(self, order_id):
        if not self._peer_won:
            self._peer_won = True
            super().complete_settlement(order_id)
            raise InvalidOrderState("peer request completed settlement")
        return super().complete_settlement(order_id)


def test_same_clawtip_credential_recovery_is_idempotent_when_peer_completes(tmp_path):
    store = PeerCompletesSettlementStore(tmp_path / "race.sqlite3")
    order = store.create_or_reuse(OrderRequest(
        skill_id="proof-pack", input_text="private", currency="CNY", locale="en-US",
        idempotency_key="same-proof-race",
    ))
    settings = ClawTipSettings(
        pay_to="merchant-pay-to", slug="one-cent-outcomes", sm4_key=OTHER_VALID_KEY,
        amount_representation="string", order_records_dir=tmp_path / "race-records",
        payment_skill="clawtip-sandbox", environment="test",
        description="One Cent Outcomes result",
        resource_url="https://example.invalid/result",
    )
    adapter = ClawTipPaymentAdapter(store, settings)
    requirement = adapter.requirement(order)
    proof = adapter.codec.encrypt(json.dumps({
        "orderNo": requirement.provider_data.order_no, "amount": "6",
        "payTo": "merchant-pay-to", "payStatus": "SUCCESS",
    }, separators=(",", ":")))
    store.begin_settlement(order.order_id, hashlib.sha256(proof.encode("utf-8")).hexdigest())

    recovered = adapter.resume_settlement(order, proof)
    repeated = adapter.resume_settlement(order, proof)

    assert recovered.status == "paid"
    assert repeated.status == "paid"


def test_configuration_and_runtime_repr_dump_redact_payment_secrets(tmp_path):
    config, auth, activation = production_inputs(tmp_path)
    settings = ClawTipSettings(
        pay_to="merchant-pay-to", slug="one-cent-outcomes", sm4_key=OTHER_VALID_KEY,
        amount_representation="string", order_records_dir=tmp_path,
        payment_skill="clawtip", environment="production", activation=activation,
        resource_url="https://gateway.example/v1/results",
    )
    x402_settings = X402Settings.production(
        pay_to="0x0000000000000000000000000000000000000001",
        auth_provider=auth,
    )

    for rendered in (
        repr(config), str(config.model_dump()), config.model_dump_json(),
        repr(auth), repr(settings), repr(x402_settings),
    ):
        assert OTHER_VALID_KEY not in rendered
        assert "cdp-super-secret" not in rendered

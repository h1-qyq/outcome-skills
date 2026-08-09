import base64
import hashlib
import json

import pytest

from gateway.contracts import OrderRequest
from gateway.orders import OrderStore
from gateway.payments.base import InvalidPaymentProof, PaymentAlreadySettled, PaymentProcessing
from gateway.payments.clawtip import (
    SM4_VECTOR_A_CIPHERTEXT,
    ClawTipPaymentAdapter,
    ClawTipSettings,
    Sm4EcbPkcs7Codec,
)


TEST_KEY = "ASNFZ4mrze/+3LqYdlQyEA=="


def make_order(tmp_path):
    store = OrderStore(tmp_path / "orders.sqlite3")
    return store, store.create_or_reuse(
        OrderRequest(
            skill_id="proof-pack",
            input_text="Private proof source.",
            currency="CNY",
            locale="zh-CN",
            idempotency_key="clawtip-contract-001",
        )
    )


def make_adapter(tmp_path, store, *, amount_representation="string"):
    return ClawTipPaymentAdapter(
        store,
        ClawTipSettings(
            pay_to="merchant-pay-to",
            slug="one-cent-outcomes",
            sm4_key=TEST_KEY,
            amount_representation=amount_representation,
            order_records_dir=tmp_path / "orders",
            payment_skill="clawtip-sandbox",
            environment="test",
            description="One Cent Outcomes result",
            resource_url="https://example.invalid/result",
        ),
    )


def test_sm4_codec_matches_published_vector_and_rejects_bad_key():
    codec = Sm4EcbPkcs7Codec(TEST_KEY)
    plaintext = '{"orderNo":"PP-20260802-0001","amount":"6","payTo":"0x0123456789abcdef"}'

    assert codec.encrypt(plaintext) == SM4_VECTOR_A_CIPHERTEXT
    assert codec.decrypt(SM4_VECTOR_A_CIPHERTEXT) == plaintext
    with pytest.raises(ValueError, match="16 bytes"):
        Sm4EcbPkcs7Codec(base64.b64encode(b"too-short").decode("ascii"))


def test_clawtip_returns_only_documented_safe_client_companion_data(tmp_path):
    store, order = make_order(tmp_path)
    adapter = make_adapter(tmp_path, store)

    requirement = adapter.requirement(order)
    provider = requirement.provider_data.model_dump()

    assert requirement.amount_minor == 6
    assert len(provider["order_no"]) <= 32
    assert provider["payment_skill"] == "clawtip-sandbox"
    assert provider["skill_id"] == "proof-pack"
    assert provider["description"] == "One Cent Outcomes result"
    assert provider["resource_url"] == "https://example.invalid/result"
    assert set(provider) == {
        "provider", "order_no", "indicator", "amount", "pay_to", "slug",
        "encrypted_data", "payment_skill", "skill_id", "description", "resource_url",
    }
    assert not (tmp_path / "orders").exists()
    serialized = json.dumps(requirement.model_dump(mode="json"))
    assert TEST_KEY not in serialized
    assert "sm4_key" not in serialized
    assert "question" not in serialized
    assert "payCredential" not in serialized


def test_clawtip_requires_no_protected_question_source(tmp_path):
    store, order = make_order(tmp_path)
    records_root = tmp_path / "orders"
    adapter = ClawTipPaymentAdapter(
        store,
        ClawTipSettings(
            pay_to="merchant-pay-to", slug="one-cent-outcomes", sm4_key=TEST_KEY,
            amount_representation="string", order_records_dir=records_root,
            payment_skill="clawtip-sandbox", environment="test",
            description="One Cent Outcomes result",
            resource_url="https://example.invalid/result",
        ),
    )

    assert adapter.requirement(order).provider_data.skill_id == "proof-pack"
    assert not records_root.exists()


def test_clawtip_gateway_requirement_needs_no_shared_filesystem_or_question_reader(tmp_path):
    store, order = make_order(tmp_path)
    records_root = tmp_path / "gateway-must-not-write-here"
    adapter = ClawTipPaymentAdapter(
        store,
        ClawTipSettings(
            pay_to="merchant-pay-to",
            slug="one-cent-outcomes",
            sm4_key=TEST_KEY,
            amount_representation="string",
            order_records_dir=records_root,
            payment_skill="clawtip-sandbox",
            environment="test",
            description="One Cent Outcomes result",
            resource_url="https://example.invalid/result",
        ),
    )

    requirement = adapter.requirement(order)

    provider = requirement.provider_data.model_dump()
    assert provider["skill_id"] == "proof-pack"
    assert provider["description"] == "One Cent Outcomes result"
    assert provider["resource_url"] == "https://example.invalid/result"
    assert "question" not in provider
    assert "payCredential" not in provider
    assert not records_root.exists()


def test_clawtip_gateway_never_writes_the_legacy_configured_record_path(tmp_path):
    store, order = make_order(tmp_path)
    configured_root = tmp_path / "openclaw" / "skills" / "orders"
    adapter = ClawTipPaymentAdapter(
        store,
        ClawTipSettings(
            pay_to="merchant-pay-to", slug="one-cent-outcomes", sm4_key=TEST_KEY,
            amount_representation="string", order_records_dir=configured_root,
            payment_skill="clawtip-sandbox", environment="test",
            description="One Cent Outcomes result",
            resource_url="https://example.invalid/result",
        ),
    )

    adapter.requirement(order)

    assert not configured_root.exists()


@pytest.mark.parametrize(
    "payload, expected_exception",
    [
        ({"orderNo": "different", "amount": "6", "payTo": "merchant-pay-to", "payStatus": "SUCCESS"}, InvalidPaymentProof),
        ({"orderNo": "{order_no}", "amount": 5, "payTo": "merchant-pay-to", "payStatus": "SUCCESS"}, InvalidPaymentProof),
        ({"orderNo": "{order_no}", "amount": True, "payTo": "merchant-pay-to", "payStatus": "SUCCESS"}, InvalidPaymentProof),
        ({"orderNo": "{order_no}", "amount": "6", "payTo": "other", "payStatus": "SUCCESS"}, InvalidPaymentProof),
        ({"orderNo": "{order_no}", "amount": "6", "payTo": "merchant-pay-to", "payStatus": "FAIL"}, InvalidPaymentProof),
        ({"orderNo": "{order_no}", "amount": "6", "payTo": "merchant-pay-to", "payStatus": "PROCESSING"}, PaymentProcessing),
        ({"orderNo": "{order_no}", "amount": "6", "payTo": "merchant-pay-to", "payStatus": "UNKNOWN"}, InvalidPaymentProof),
    ],
)
def test_clawtip_credential_only_unlocks_exact_success(tmp_path, payload, expected_exception):
    store, order = make_order(tmp_path)
    adapter = make_adapter(tmp_path, store)
    requirement = adapter.requirement(order)
    order_no = requirement.provider_data.order_no
    payload = {key: (value.format(order_no=order_no) if isinstance(value, str) else value) for key, value in payload.items()}
    proof = adapter.codec.encrypt(json.dumps(payload, separators=(",", ":")))

    with pytest.raises(expected_exception):
        adapter.settle(order, proof)

    assert store.get(order.order_id).status == "payment-required"


@pytest.mark.parametrize("amount_representation", ["string", "number"])
def test_clawtip_encrypted_order_amount_representation_is_explicit(tmp_path, amount_representation):
    store, order = make_order(tmp_path)
    adapter = make_adapter(tmp_path, store, amount_representation=amount_representation)
    requirement = adapter.requirement(order)
    encrypted = requirement.provider_data.encrypted_data
    payload = json.loads(adapter.codec.decrypt(encrypted))

    assert payload["amount"] == ("6" if amount_representation == "string" else 6)


def test_clawtip_success_is_idempotent_and_never_serializes_credential(tmp_path):
    store, order = make_order(tmp_path)
    adapter = make_adapter(tmp_path, store)
    requirement = adapter.requirement(order)
    proof = adapter.codec.encrypt(json.dumps({
        "orderNo": requirement.provider_data.order_no,
        "amount": "6",
        "payTo": "merchant-pay-to",
        "payStatus": "SUCCESS",
    }, separators=(",", ":")))

    paid = adapter.settle(order, proof)

    assert paid.status == "paid"
    assert proof not in json.dumps(requirement.model_dump(mode="json"))
    with pytest.raises(PaymentAlreadySettled):
        adapter.settle(paid, proof)


def test_clawtip_same_credential_recovers_a_crash_reserved_settlement(tmp_path):
    store, order = make_order(tmp_path)
    adapter = make_adapter(tmp_path, store)
    requirement = adapter.requirement(order)
    proof = adapter.codec.encrypt(json.dumps({
        "orderNo": requirement.provider_data.order_no, "amount": "6",
        "payTo": "merchant-pay-to", "payStatus": "SUCCESS",
    }, separators=(",", ":")))
    store.begin_settlement(order.order_id, hashlib.sha256(proof.encode("utf-8")).hexdigest())

    paid = adapter.settle(order, proof)

    assert paid.status == "paid"


def test_clawtip_production_requires_explicit_amount_representation_and_live_skill(tmp_path):
    with pytest.raises(ValueError, match="amount representation"):
        ClawTipSettings(
            pay_to="merchant-pay-to", slug="one-cent-outcomes", sm4_key=TEST_KEY,
            amount_representation=None, order_records_dir=tmp_path, payment_skill="clawtip",
            environment="production",
        )
    with pytest.raises(ValueError, match="clawtip"):
        ClawTipSettings(
            pay_to="merchant-pay-to", slug="one-cent-outcomes", sm4_key=TEST_KEY,
            amount_representation="string", order_records_dir=tmp_path, payment_skill="clawtip-sandbox",
            environment="production",
        )

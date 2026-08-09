"""JD ClawTip verifier; the payer-side client owns the official local handoff."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from gateway.contracts import ClawTipPaymentProviderData, Order, PaymentRequirement
from gateway.orders import InvalidOrderState, OrderExpired, OrderStore
from gateway.payments.base import (
    InvalidPaymentProof,
    PaymentAdapter,
    PaymentAlreadySettled,
    PaymentProcessing,
    PaymentProofExpired,
    PaymentRequirementExpired,
    PaymentRequirementUnavailable,
)


SM4_VECTOR_A_CIPHERTEXT = (
    "aej5m1e1IfGqdisRc9uIVxVPXP0THJa4V89SjB11UDFzh6iBLYV+ImlFenyLZ6b51"
    "wSV3+NgnTvsYwyxXU3U/Fplf+1z43rp5vTtjlaW/So="
)
_VECTOR_A_PLAINTEXT = '{"orderNo":"PP-20260802-0001","amount":"6","payTo":"0x0123456789abcdef"}'
_VECTOR_KEY = base64.b64decode("ASNFZ4mrze/+3LqYdlQyEA==")
_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class Sm4EcbPkcs7Codec:
    """The source-proven JD/Hutool SM4/ECB/PKCS7 UTF-8/Base64 transform."""

    def __init__(self, sm4_key: str) -> None:
        self._run_known_vector_self_test()
        try:
            self._key = base64.b64decode(sm4_key.encode("ascii"), validate=True)
        except (UnicodeEncodeError, ValueError) as error:
            raise ValueError("ClawTip SM4 key must be standard Base64") from error
        if len(self._key) != 16:
            raise ValueError("ClawTip SM4 key must decode to exactly 16 bytes")

    def encrypt(self, plaintext: str) -> str:
        return self._encrypt_with_key(self._key, plaintext)

    @staticmethod
    def _encrypt_with_key(key: bytes, plaintext: str) -> str:
        raw = plaintext.encode("utf-8")
        padder = padding.PKCS7(128).padder()
        padded = padder.update(raw) + padder.finalize()
        encryptor = Cipher(algorithms.SM4(key), modes.ECB()).encryptor()
        ciphertext = encryptor.update(padded) + encryptor.finalize()
        return base64.b64encode(ciphertext).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        return self._decrypt_with_key(self._key, ciphertext)

    @staticmethod
    def _decrypt_with_key(key: bytes, ciphertext: str) -> str:
        try:
            raw = base64.b64decode(ciphertext.encode("ascii"), validate=True)
            if not raw or len(raw) % 16:
                raise ValueError("invalid encrypted payload")
            decryptor = Cipher(algorithms.SM4(key), modes.ECB()).decryptor()
            padded = decryptor.update(raw) + decryptor.finalize()
            unpadder = padding.PKCS7(128).unpadder()
            plaintext = unpadder.update(padded) + unpadder.finalize()
            return plaintext.decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError, ValueError) as error:
            raise ValueError("invalid ClawTip encrypted payload") from error

    @classmethod
    def _run_known_vector_self_test(cls) -> None:
        if cls._encrypt_with_key(_VECTOR_KEY, _VECTOR_A_PLAINTEXT) != SM4_VECTOR_A_CIPHERTEXT:
            raise ValueError("ClawTip SM4 codec self-test failed")
        if cls._decrypt_with_key(_VECTOR_KEY, SM4_VECTOR_A_CIPHERTEXT) != _VECTOR_A_PLAINTEXT:
            raise ValueError("ClawTip SM4 codec self-test failed")


@dataclass(frozen=True)
class ClawTipActivation:
    """Human/on-sandbox attestation injected at deployment, never read from env."""

    onboarding_completed: bool = False
    sandbox_interop_confirmed: bool = False


@dataclass(frozen=True)
class ClawTipSettings:
    pay_to: str
    slug: str
    sm4_key: str = field(repr=False)
    amount_representation: str | None
    order_records_dir: Path
    payment_skill: str
    environment: str
    activation: ClawTipActivation | None = None
    description: str = "One Cent Outcomes result"
    resource_url: str = ""

    def __post_init__(self) -> None:
        if not self.pay_to.strip():
            raise ValueError("ClawTip pay_to is required")
        if not _SLUG.fullmatch(self.slug):
            raise ValueError("ClawTip slug is unsafe")
        if self.amount_representation not in {"string", "number"}:
            raise ValueError("ClawTip encrypted amount representation must be explicit")
        if self.payment_skill not in {"clawtip", "clawtip-sandbox"}:
            raise ValueError("ClawTip payment skill must be clawtip or clawtip-sandbox")
        if self.environment not in {"development", "test", "production"}:
            raise ValueError("ClawTip environment is invalid")
        if self.environment == "production" and self.payment_skill != "clawtip":
            raise ValueError("production requires the official clawtip Skill")
        if self.environment == "production" and (
            self.activation is None
            or not self.activation.onboarding_completed
            or not self.activation.sandbox_interop_confirmed
        ):
            raise ValueError("production requires human ClawTip activation and sandbox interop confirmation")
        if len(self.description) > 128 or len(self.resource_url) > 128:
            raise ValueError("ClawTip description and resource URL must be at most 128 characters")


class ClawTipPaymentAdapter(PaymentAdapter):
    """Build public handoff data and verify returned credentials without shared disk."""

    def __init__(
        self,
        store: OrderStore,
        settings: ClawTipSettings,
        *,
        codec: Sm4EcbPkcs7Codec | None = None,
    ) -> None:
        self._store = store
        self.settings = settings
        self.codec = codec or Sm4EcbPkcs7Codec(settings.sm4_key)

    @property
    def production_ready(self) -> bool:
        return (
            self.settings.environment == "production"
            and self.settings.payment_skill == "clawtip"
            and self.settings.activation is not None
            and self.settings.activation.onboarding_completed
            and self.settings.activation.sandbox_interop_confirmed
        )

    def requirement(self, order: Order) -> PaymentRequirement:
        current = self._payable(order)
        return PaymentRequirement(
            order_id=current.order_id,
            currency="CNY",
            amount_minor=6,
            expires_at=current.expires_at,
            provider_data=self._provider_data(current),
        )

    def verify(self, order: Order, proof: str) -> bool:
        current = self._payable(order)
        self._validate_credential(current, proof)
        return True

    def settle(self, order: Order, proof: str) -> Order:
        if self._store.get(order.order_id).status == "processing":
            return self.resume_settlement(order, proof)
        current = self._payable(order)
        self._validate_credential(current, proof)
        try:
            settling = self._store.begin_settlement(current.order_id, self._proof_digest(proof))
        except OrderExpired as error:
            raise PaymentProofExpired("ClawTip credential expired") from error
        except Exception as error:
            raise PaymentAlreadySettled("ClawTip credential already used") from error
        try:
            return self._store.complete_settlement(settling.order_id)
        except Exception as error:
            raise PaymentAlreadySettled("ClawTip credential already used") from error

    def resume_settlement(self, order: Order, proof: str) -> Order:
        """A same-credential retry completes a crash-reserved local settlement."""
        current = self._store.get(order.order_id)
        digest = self._proof_digest(proof)
        if current.status in {"paid", "generating", "delivered", "failed"}:
            if self._store.settlement_proof_matches(current.order_id, digest):
                return current
            raise InvalidPaymentProof("ClawTip recovery credential does not match the original credential")
        if current.status != "processing":
            raise PaymentAlreadySettled("ClawTip order is not awaiting settlement recovery")
        if not self._store.settlement_proof_matches(current.order_id, digest):
            raise InvalidPaymentProof("ClawTip recovery credential does not match the original credential")
        self._validate_credential(current, proof)
        try:
            return self._store.complete_settlement(current.order_id)
        except InvalidOrderState as error:
            recovered = self._store.get(current.order_id)
            if (
                recovered.status in {"paid", "generating", "delivered", "failed"}
                and self._store.settlement_proof_matches(recovered.order_id, digest)
            ):
                return recovered
            raise PaymentAlreadySettled("ClawTip credential already used") from error

    @staticmethod
    def _proof_digest(proof: str) -> str:
        return hashlib.sha256(proof.encode("utf-8")).hexdigest()

    def _provider_data(self, order: Order) -> ClawTipPaymentProviderData:
        order_no = self._order_no(order)
        indicator = hashlib.md5(self.settings.slug.encode("utf-8"), usedforsecurity=False).hexdigest()
        amount: str | int = "6" if self.settings.amount_representation == "string" else 6
        encrypted_data = self.codec.encrypt(json.dumps({
            "orderNo": order_no,
            "amount": amount,
            "payTo": self.settings.pay_to,
        }, ensure_ascii=False, separators=(",", ":")))
        return ClawTipPaymentProviderData(
            order_no=order_no,
            indicator=indicator,
            skill_id=order.skill_id,
            pay_to=self.settings.pay_to,
            slug=self.settings.slug,
            encrypted_data=encrypted_data,
            payment_skill=self.settings.payment_skill,
            description=self.settings.description,
            resource_url=self.settings.resource_url,
        )

    def _validate_credential(self, order: Order, proof: str) -> None:
        expected = self._provider_data(order)
        try:
            payload = json.loads(self.codec.decrypt(proof))
        except (ValueError, json.JSONDecodeError) as error:
            raise InvalidPaymentProof("invalid ClawTip credential") from error
        if not isinstance(payload, dict):
            raise InvalidPaymentProof("invalid ClawTip credential")
        status = payload.get("payStatus")
        if status == "PROCESSING":
            raise PaymentProcessing("ClawTip payment is still processing")
        if status != "SUCCESS":
            raise InvalidPaymentProof("ClawTip payment did not succeed")
        amount = payload.get("amount")
        valid_amount = (type(amount) is int and amount == 6) or (type(amount) is str and amount == "6")
        if not valid_amount or payload.get("orderNo") != expected.order_no or payload.get("payTo") != expected.pay_to:
            raise InvalidPaymentProof("ClawTip credential does not match this order")

    @staticmethod
    def _order_no(order: Order) -> str:
        # Two fixed ASCII bytes plus 30 hex chars: fresh per UUID-backed order,
        # deterministic for retries, and always within the official 32-char cap.
        return "CT" + hashlib.sha256(order.order_id.encode("ascii")).hexdigest()[:30]

    def _payable(self, order: Order) -> Order:
        current = self._store.get(order.order_id)
        if current.currency != "CNY" or current.amount_minor != 6:
            raise PaymentRequirementUnavailable("ClawTip only accepts the server-owned six-fen order")
        if current.status == "expired" or self._store.now() >= current.expires_at:
            raise PaymentRequirementExpired("ClawTip order is expired")
        if current.status != "payment-required":
            raise PaymentAlreadySettled("ClawTip order is not payable")
        return current

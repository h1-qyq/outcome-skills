"""HTTP gateway that releases results under the configured access policy."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Response, status
from pydantic import SecretStr

from gateway.catalog import get_product
from gateway.contracts import Order, OrderRequest, PaymentRequirement, X402PaymentProviderData
from gateway.orders import (
    IdempotencyConflict,
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
    PaymentProcessing,
    PaymentRequirementExpired,
    PaymentRequirementUnavailable,
)
from gateway.payments.demo import DemoPaymentAdapter
from gateway.results import (
    BuyerPayloadStore,
    FixtureResultEngine,
    OpenAICompatibleResultEngine,
    ResultEngine,
    RetryableGenerationError,
    RetryablePayloadError,
    SQLiteBuyerPayloadStore,
)
from gateway.result_validation import ResultValidationError, validate_result


def create_app(
    *,
    store: OrderStore,
    payment_adapter: PaymentAdapter,
    result_engine: ResultEngine | None = None,
    payload_store: BuyerPayloadStore | None = None,
    environment: str = "development",
    result_access_token_key: SecretStr | None = None,
    free_access: bool = False,
) -> FastAPI:
    """Create an injectable gateway; ``free_access`` is reserved for public tests."""

    if environment not in {"development", "test", "production"}:
        raise ValueError(
            "environment must be exactly development, test, or production"
        )
    if environment != "production" and payment_adapter.production_ready:
        raise ValueError("production-ready payment adapter requires the production environment")
    if environment == "production":
        if isinstance(payment_adapter, DemoPaymentAdapter):
            raise ValueError("demo payment adapter is forbidden in production")
        if not payment_adapter.production_ready:
            raise ValueError("production requires a production-ready payment adapter")
        if payload_store is None:
            raise ValueError("production requires a production-ready payload store")
        if isinstance(payload_store, SQLiteBuyerPayloadStore):
            raise ValueError("SQLite payload store is forbidden in production")
        if not payload_store.production_ready:
            raise ValueError("production requires a production-ready payload store")
        result_engine = _production_engine_from_environment()
    elif result_engine is None:
        result_engine = FixtureResultEngine()
    access_key = _result_access_key(environment, result_access_token_key)
    if payload_store is None:
        payload_store = SQLiteBuyerPayloadStore(store.payload_database_path)
    app = FastAPI()

    @app.post("/v1/orders", status_code=status.HTTP_201_CREATED)
    def create_order(request: OrderRequest) -> dict[str, object]:
        try:
            get_product(request.skill_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="unknown product") from error
        try:
            order = store.create_or_reuse(request)
        except IdempotencyConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        result_access_token = _derive_result_access_token(access_key, order)
        store.bind_result_access_token_hash(
            order.order_id,
            hashlib.sha256(result_access_token.encode("utf-8")).hexdigest(),
        )
        payload_store.put(order, request.input_text)
        if free_access:
            order = _grant_free_access(store, order)
            return {
                "order": _public_order(order),
                "order_id": order.order_id,
                "access_mode": "free",
                "payment_requirement": None,
                "result_access_token": result_access_token,
            }
        try:
            requirement = _require_payment(payment_adapter, store, order)
        except PaymentRequirementExpired:
            _raise_payment_gone(order, "payment_requirement_expired")
        except PaymentRequirementUnavailable:
            _raise_payment_gone(order, "payment_requirement_unavailable")
        except PaymentAlreadySettled:
            _raise_payment_gone(order, "payment_requirement_unavailable")
        return {
            "order": _public_order(order),
            "order_id": order.order_id,
            "payment_requirement": requirement.model_dump(mode="json"),
            "result_access_token": result_access_token,
        }

    @app.get("/v1/orders/{order_id}")
    def get_order(
        order_id: str,
        result_access_token: Annotated[
            str | None, Header(alias="Result-Access-Token")
        ] = None,
    ) -> dict[str, object]:
        order = _authorized_order(store, order_id, result_access_token)
        return _public_order(order)

    @app.post("/v1/orders/{order_id}/result")
    def deliver_result(
        order_id: str,
        response: Response,
        result_access_token: Annotated[
            str | None, Header(alias="Result-Access-Token")
        ] = None,
        payment_proof: Annotated[str | None, Header(alias="Payment-Proof")] = None,
        payment_signature: Annotated[str | None, Header(alias="PAYMENT-SIGNATURE")] = None,
    ) -> dict[str, str]:
        order = _authorized_order(store, order_id, result_access_token)
        _restore_payment_response_header(store, order_id, response)

        if free_access and order.status == "payment-required":
            order = _grant_free_access(store, order)

        if order.status == "delivered":
            return _result_response(store, order_id)
        if order.status == "generating":
            raise HTTPException(status_code=409, detail="result generation is already in progress")
        if order.status == "processing":
            if payment_signature and payment_proof and not hmac.compare_digest(payment_signature, payment_proof):
                raise HTTPException(status_code=400, detail="conflicting payment proof headers")
            submitted_proof = payment_signature or payment_proof
            resume = getattr(payment_adapter, "resume_settlement", None)
            if not submitted_proof or not callable(resume):
                raise HTTPException(status_code=409, detail="payment settlement is in progress; retry with the same proof")
            try:
                order = resume(order, submitted_proof)
            except PaymentProcessing as error:
                raise HTTPException(status_code=409, detail="payment settlement is in progress; retry with the same proof") from error
            except InvalidPaymentProof as error:
                raise HTTPException(status_code=402, detail="payment proof is invalid") from error
            except PaymentAlreadySettled:
                order = _reload_payment_state(store, order_id)
            if order.status == "delivered":
                return _result_response(store, order_id)
            if order.status == "generating":
                raise HTTPException(status_code=409, detail="result generation is already in progress")
            _capture_payment_response(payment_adapter, store, order.order_id, response)
        if order.status in {"payment-required", "expired"}:
            requirement: PaymentRequirement | None = None
            try:
                requirement = _require_payment(payment_adapter, store, order)
            except PaymentAlreadySettled:
                order = _reload_payment_state(store, order_id)
            except PaymentRequirementExpired:
                order = _reload_payment_state(store, order_id)
                if order.status == "payment-required":
                    _raise_payment_gone(order, "payment_requirement_expired")
            except PaymentRequirementUnavailable:
                order = _reload_payment_state(store, order_id)
                if order.status == "payment-required":
                    _raise_payment_gone(order, "payment_requirement_unavailable")

            if order.status == "delivered":
                return _result_response(store, order_id)
            if order.status == "payment-required":
                if requirement is None:
                    raise HTTPException(
                        status_code=409,
                        detail="payment state changed without a usable requirement",
                    )
                if payment_signature and payment_proof and not hmac.compare_digest(payment_signature, payment_proof):
                    raise HTTPException(status_code=400, detail="conflicting payment proof headers")
                submitted_proof = payment_signature or payment_proof
                if not submitted_proof:
                    raise HTTPException(
                        status_code=402,
                        detail=requirement.model_dump(mode="json"),
                        headers=_payment_required_headers(requirement),
                    )
                try:
                    verified = payment_adapter.verify(order, submitted_proof)
                except InvalidPaymentProof:
                    verified = False
                except PaymentProofExpired:
                    _raise_payment_gone(order, "payment_proof_expired")
                except PaymentRequirementExpired:
                    _raise_payment_gone(order, "payment_requirement_expired")
                except PaymentRequirementUnavailable:
                    _raise_payment_gone(order, "payment_requirement_unavailable")
                except PaymentProcessing as error:
                    raise HTTPException(status_code=409, detail="payment is still processing; retry with the same order") from error
                if not verified:
                    order = _reload_payment_state(store, order_id)
                    if order.status == "delivered":
                        return _result_response(store, order_id)
                    if order.status == "payment-required":
                        raise HTTPException(
                            status_code=402,
                            detail=requirement.model_dump(mode="json"),
                            headers=_payment_required_headers(requirement),
                        )
                else:
                    order = _reload_payment_state(store, order_id)
                    if order.status == "delivered":
                        return _result_response(store, order_id)
                if order.status == "payment-required":
                    try:
                        order = payment_adapter.settle(order, submitted_proof)
                        _capture_payment_response(payment_adapter, store, order.order_id, response)
                    except InvalidPaymentProof as error:
                        raise HTTPException(
                            status_code=402,
                            detail=requirement.model_dump(mode="json"),
                            headers=_payment_required_headers(requirement),
                        ) from error
                    except PaymentProofExpired:
                        _raise_payment_gone(order, "payment_proof_expired")
                    except PaymentRequirementExpired:
                        _raise_payment_gone(order, "payment_requirement_expired")
                    except PaymentRequirementUnavailable:
                        _raise_payment_gone(order, "payment_requirement_unavailable")
                    except PaymentProcessing as error:
                        raise HTTPException(status_code=409, detail="payment is still processing; retry with the same order") from error
                    except PaymentAlreadySettled as error:
                        order = _reload_payment_state(store, order_id)
                        if order.status == "delivered":
                            return _result_response(store, order_id)
                        if order.status == "payment-required":
                            raise HTTPException(
                                status_code=409,
                                detail=(
                                    "payment was already settled but the order "
                                    "is not paid"
                                ),
                            ) from error

        if order.status not in {"paid", "failed"}:
            raise HTTPException(status_code=409, detail=f"order cannot generate from {order.status}")
        try:
            generating = store.begin_generation(order_id)
            input_text = payload_store.get_verified(generating)
            body = result_engine.generate(generating.skill_id, input_text, generating.locale)
            validate_result(generating.skill_id, input_text, generating.locale, body)
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            stored = store.deliver_result(order_id, body, digest)
        except InvalidOrderState as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except (RetryableGenerationError, RetryablePayloadError, ResultValidationError) as error:
            # Provider, network, and invalid-response failures remain retryable.
            try:
                store.mark_generation_failed(order_id)
            except InvalidOrderState:
                pass
            raise HTTPException(
                status_code=503,
                detail="result generation failed; retry without repaying",
                headers=_stored_payment_response_headers(store, order_id),
            ) from error
        return {"result": stored.body, "result_sha256": stored.sha256}

    return app


def _production_engine_from_environment() -> OpenAICompatibleResultEngine:
    return OpenAICompatibleResultEngine(
        base_url=os.environ.get("MODEL_BASE_URL", ""),
        api_key=os.environ.get("MODEL_API_KEY", ""),
        model_name=os.environ.get("MODEL_NAME", ""),
        prompts_dir=os.environ.get("MODEL_PROMPTS_DIR") or None,
    )


def _grant_free_access(store: OrderStore, order: Order) -> Order:
    """Grant only the explicitly configured public-test access path."""

    if order.status == "payment-required":
        try:
            return store.mark_paid(order.order_id)
        except (InvalidOrderState, OrderAlreadySettled, OrderExpired):
            return store.get(order.order_id)
    return order


def _result_access_key(environment: str, configured: SecretStr | None) -> bytes:
    if configured is not None:
        value = configured.get_secret_value()
    elif environment == "production":
        value = os.environ.get("RESULT_ACCESS_TOKEN_KEY", "")
    else:
        return secrets.token_bytes(32)
    encoded = value.encode("utf-8")
    if len(encoded) < 32:
        raise ValueError("result access token key must contain at least 32 bytes")
    return encoded


def _derive_result_access_token(key: bytes, order: Order) -> str:
    binding = json.dumps(
        [
            "one-cent-outcomes/result-access/v1",
            order.order_id,
            order.input_hash,
            order.skill_id,
            order.currency,
            order.locale,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hmac.new(key, binding, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _authorized_order(
    store: OrderStore, order_id: str, presented_token: str | None
) -> Order:
    candidate = presented_token or ""
    candidate_hash = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
    try:
        order = store.get(order_id)
    except KeyError as error:
        hmac.compare_digest(candidate_hash, "0" * 64)
        raise HTTPException(status_code=404, detail="unknown order") from error
    if (
        not presented_token
        or len(presented_token) > 4_096
        or not store.result_access_token_hash_matches(order_id, candidate_hash)
    ):
        raise HTTPException(status_code=404, detail="unknown order")
    return order


def _capture_payment_response(
    adapter: PaymentAdapter, store: OrderStore, order_id: str, response: Response
) -> None:
    payment_response = getattr(adapter, "payment_response", None)
    encoded_response = payment_response(order_id) if callable(payment_response) else None
    if isinstance(encoded_response, str) and encoded_response:
        store.set_payment_response(order_id, encoded_response)
    _restore_payment_response_header(store, order_id, response)


def _restore_payment_response_header(
    store: OrderStore, order_id: str, response: Response
) -> None:
    encoded_response = store.get_payment_response(order_id)
    if encoded_response:
        response.headers["PAYMENT-RESPONSE"] = encoded_response


def _stored_payment_response_headers(
    store: OrderStore, order_id: str
) -> dict[str, str] | None:
    encoded_response = store.get_payment_response(order_id)
    return {"PAYMENT-RESPONSE": encoded_response} if encoded_response else None


def _require_payment(
    adapter: PaymentAdapter, store: OrderStore, order: Order
) -> PaymentRequirement:
    current = store.get(order.order_id)
    if current.status in {"paid", "generating", "delivered", "failed"}:
        raise PaymentAlreadySettled("payment state already advanced")
    if current.status == "expired" or store.now() >= current.expires_at:
        raise PaymentRequirementExpired("payment requirement is expired")
    requirement = adapter.requirement(current)
    latest = store.get(order.order_id)
    if latest.status in {"paid", "generating", "delivered", "failed"}:
        raise PaymentAlreadySettled("payment state already advanced")
    if latest.status == "expired" or store.now() >= latest.expires_at:
        raise PaymentRequirementExpired("payment requirement is expired")
    return requirement


def _reload_payment_state(store: OrderStore, order_id: str) -> Order:
    order = store.get(order_id)
    if order.status == "generating":
        raise HTTPException(
            status_code=409,
            detail="result generation is already in progress",
        )
    if order.status in {"payment-required", "expired"} and (
        order.status == "expired" or store.now() >= order.expires_at
    ):
        _raise_payment_gone(order, "payment_requirement_expired")
    if order.status not in {"payment-required", "paid", "failed", "delivered"}:
        raise HTTPException(
            status_code=409,
            detail=f"order cannot resume payment from {order.status}",
        )
    return order


def _raise_payment_gone(order: Order, code: str) -> None:
    authoritative = PaymentRequirement(
        order_id=order.order_id,
        currency=order.currency,
        amount_minor=order.amount_minor,
        expires_at=order.expires_at,
    )
    raise HTTPException(
        status_code=410,
        detail={
            "code": code,
            "order_id": order.order_id,
            "payable": False,
            "payment_requirement": authoritative.model_dump(mode="json"),
        },
    )


def _payment_required_headers(requirement: PaymentRequirement) -> dict[str, str] | None:
    """Emit the v2 header for x402 while retaining the JSON client contract."""

    if isinstance(requirement.provider_data, X402PaymentProviderData):
        return {"PAYMENT-REQUIRED": requirement.provider_data.payment_required}
    return None


def _public_order(order: Order) -> dict[str, object]:
    """Deliberately omit buyer input/hash, provider data, and internal secrets."""

    return {
        "order_id": order.order_id,
        "skill_id": order.skill_id,
        "currency": order.currency,
        "locale": order.locale,
        "amount_minor": order.amount_minor,
        "status": order.status,
        "created_at": order.created_at.isoformat(),
        "expires_at": order.expires_at.isoformat(),
    }


def _result_response(store: OrderStore, order_id: str) -> dict[str, str]:
    try:
        result = store.get_result(order_id)
    except KeyError as error:
        raise HTTPException(status_code=409, detail="delivered result is unavailable") from error
    return {"result": result.body, "result_sha256": result.sha256}

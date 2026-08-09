"""Small, amount-free client for the Outcome Offer gateway product."""

from __future__ import annotations

import argparse
from ipaddress import ip_address
import json
import os
import re
import sys
from typing import Any
from pathlib import Path
from uuid import uuid4
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


SKILL_ID = "outcome-offer"
TIMEOUT_SECONDS = 10.0
MAX_BUYER_INPUT_BYTES = 64 * 1024
MAX_PAYMENT_PROOF_BYTES = 8 * 1024
PRICE_MINOR = {"USD": 1, "CNY": 6}
ORDER_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
ORDER_STATUSES = frozenset(
    {"created", "payment-required", "processing", "paid", "generating", "delivered", "failed", "expired"}
)


class SecretFreeArgumentParser(argparse.ArgumentParser):
    """Never reflect rejected argv values into shell history or captured output."""

    def error(self, message: str) -> None:
        self.exit(2, "ERROR=invalid command\n")


class NoRedirect(HTTPRedirectHandler):
    """Treat redirects as rejected gateway responses before credentials can move."""

    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def gateway_url() -> str:
    value = os.environ.get("OUTCOMES_GATEWAY_URL", "")
    if not value:
        raise ValueError("OUTCOMES_GATEWAY_URL is required")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ValueError("invalid OUTCOMES_GATEWAY_URL") from None
    if (
        value != value.strip()
        or parsed.scheme not in {"https", "http"}
        or not parsed.netloc
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port is not None and not 0 < port <= 65535
    ):
        raise ValueError("invalid OUTCOMES_GATEWAY_URL")
    if parsed.scheme == "http":
        try:
            is_loopback = ip_address(host).is_loopback
        except ValueError:
            is_loopback = False
        if not is_loopback:
            raise ValueError("invalid OUTCOMES_GATEWAY_URL")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def protected_stdin(label: str, *, maximum_bytes: int, single_line: bool = False) -> str:
    stream = getattr(sys.stdin, "buffer", None)
    raw = stream.read(maximum_bytes + 1) if stream is not None else sys.stdin.read().encode("utf-8")
    if not raw or len(raw) > maximum_bytes:
        raise ValueError(f"valid {label} is required on protected stdin")
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"valid {label} is required on protected stdin") from None
    if single_line:
        value = value.rstrip("\r\n")
    if not value or "\x00" in value or (single_line and ("\r" in value or "\n" in value)):
        raise ValueError(f"valid {label} is required on protected stdin")
    return value


def _state_path(order_id: str) -> Path:
    root = Path(os.environ.get("OUTCOMES_CLIENT_STATE_DIR", Path.home() / ".one-cent-outcomes"))
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root / f"{validated_order_id(order_id)}.json"


def _write_private_json(path: Path, state: dict[str, object]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink():
        raise RuntimeError("local order state is unavailable")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(state, file, ensure_ascii=False, separators=(",", ":"))
        os.replace(temporary, path)
    except OSError:
        try: temporary.unlink(missing_ok=True)
        except OSError: pass
        raise RuntimeError("local order state is unavailable") from None


def save_order_state(order_id: str, access_token: object, provider_data: object, access_mode: object = "paid") -> None:
    if not isinstance(access_token, str) or not access_token or len(access_token) > 4096 or any(c in access_token for c in "\r\n\x00"):
        raise RuntimeError("gateway returned an incomplete quote")
    provider = "free" if access_mode == "free" else (provider_data.get("provider") if isinstance(provider_data, dict) else "demo")
    state = {"result_access_token": access_token, "provider": provider if isinstance(provider, str) else "demo"}
    if isinstance(provider_data, dict): state["provider_data"] = provider_data
    path = _state_path(order_id)
    _write_private_json(path, state)


def load_order_state(order_id: str) -> dict[str, object]:
    try:
        state = json.loads(_state_path(order_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise RuntimeError("local order state is unavailable") from None
    token = state.get("result_access_token") if isinstance(state, dict) else None
    provider = state.get("provider") if isinstance(state, dict) else None
    if not isinstance(token, str) or not token or not isinstance(provider, str):
        raise RuntimeError("local order state is unavailable")
    return state


def _clawtip_orders_root() -> Path:
    return Path(os.environ.get("OUTCOMES_CLAWTIP_ORDERS_DIR", Path.home() / "openclaw" / "skills" / "orders"))


def write_clawtip_record(provider_data: object, question: str) -> None:
    if not isinstance(provider_data, dict): raise RuntimeError("gateway returned an incomplete quote")
    required = ("order_no", "indicator", "pay_to", "encrypted_data", "description", "slug", "skill_id", "resource_url")
    if any(not isinstance(provider_data.get(field), str) or not provider_data[field] for field in required) or provider_data.get("amount") != 6:
        raise RuntimeError("gateway returned an incomplete quote")
    order_no, indicator = provider_data["order_no"], provider_data["indicator"]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", order_no) or not re.fullmatch(r"[0-9a-f]{32}", indicator):
        raise RuntimeError("gateway returned an incomplete quote")
    record = {field: provider_data[field] for field in required if field != "indicator"}
    record.update({"amount": 6, "question": question})
    _write_private_json(_clawtip_orders_root() / indicator / f"{order_no}.json", record)


def clawtip_credential(state: dict[str, object]) -> str:
    provider_data = state.get("provider_data")
    if not isinstance(provider_data, dict) or not isinstance(provider_data.get("order_no"), str) or not isinstance(provider_data.get("indicator"), str):
        raise RuntimeError("local ClawTip order record is unavailable")
    path = _clawtip_orders_root() / provider_data["indicator"] / f"{provider_data['order_no']}.json"
    try: record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): raise RuntimeError("local ClawTip order record is unavailable") from None
    credential = record.get("payCredential") if isinstance(record, dict) else None
    if not isinstance(credential, str) or not credential or len(credential) > MAX_PAYMENT_PROOF_BYTES or any(c in credential for c in "\r\n\x00"):
        raise RuntimeError("local ClawTip order record is unavailable")
    record.pop("payCredential", None); _write_private_json(path, record)
    return credential


def save_payment_response(order_id: str, payment_response: str | None) -> None:
    if not payment_response or len(payment_response) > 16384 or any(c in payment_response for c in "\r\n\x00"): return
    state = load_order_state(order_id); state["payment_response"] = payment_response; _write_private_json(_state_path(order_id), state)


def validated_order_id(value: object) -> str:
    if not isinstance(value, str) or not ORDER_ID_PATTERN.fullmatch(value):
        raise ValueError("invalid order id")
    return value


def request_json(method: str, path: str, body: dict[str, Any] | None = None, proof: str | None = None, access_token: str | None = None, proof_header: str = "Payment-Proof", order_id: str | None = None) -> dict[str, Any]:
    base_url = gateway_url()
    try:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if proof is not None:
            headers[proof_header] = proof
        if access_token is not None:
            headers["Result-Access-Token"] = access_token
        request = Request(f"{base_url}{path}", data=data, headers=headers, method=method)
        with build_opener(NoRedirect()).open(request, timeout=TIMEOUT_SECONDS) as response:
            if order_id: save_payment_response(order_id, response.headers.get("PAYMENT-RESPONSE"))
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        if order_id: save_payment_response(order_id, error.headers.get("PAYMENT-RESPONSE"))
        next_step = {402: "complete the stored payment handoff and retry", 409: "retry with the same local order state", 410: "create a new quote"}.get(error.code, "retry or contact the gateway operator")
        raise RuntimeError(f"gateway response {error.code}; {next_step}") from None
    except (URLError, TimeoutError, OSError, TypeError, ValueError, UnicodeError):
        raise RuntimeError("gateway request failed") from None
    except json.JSONDecodeError:
        raise RuntimeError("gateway returned an invalid response") from None
    if not isinstance(payload, dict):
        raise RuntimeError("gateway returned an invalid response")
    return payload


def quote(arguments: argparse.Namespace) -> int:
    input_text = protected_stdin("buyer input", maximum_bytes=MAX_BUYER_INPUT_BYTES)
    payload = request_json(
        "POST",
        "/v1/orders",
        {
            "skill_id": SKILL_ID,
            "input_text": input_text,
            "currency": arguments.currency,
            "locale": arguments.locale,
            "idempotency_key": arguments.idempotency_key,
        },
    )
    order_id = validated_order_id(payload.get("order_id"))
    access_mode = payload.get("access_mode", "paid")
    requirement = payload.get("payment_requirement")
    if access_mode == "free":
        save_order_state(order_id, payload.get("result_access_token"), None, access_mode)
        print("ACCESS_MODE=FREE")
        print("RESULT=FREE_PUBLIC_TEST")
        print(f"ORDER_ID={order_id}")
        return 0
    if not isinstance(requirement, dict):
        raise RuntimeError("gateway returned an incomplete quote")
    provider_data = requirement.get("provider_data")
    save_order_state(order_id, payload.get("result_access_token"), provider_data, access_mode)
    expires_at = requirement.get("expires_at")
    if (
        not isinstance(expires_at, str)
        or "\r" in expires_at
        or "\n" in expires_at
        or requirement.get("order_id") != order_id
        or requirement.get("currency") != arguments.currency
        or type(requirement.get("amount_minor")) is not int
        or requirement.get("amount_minor") != PRICE_MINOR[arguments.currency]
    ):
        raise RuntimeError("gateway returned an inconsistent quote")
    if isinstance(provider_data, dict) and provider_data.get("provider") == "clawtip": write_clawtip_record(provider_data, input_text)
    print("ACCESS_MODE=RESTRICTED_FUTURE_PAYMENT")
    print("RESULT=OFFER_CARD")
    print(f"ORDER_ID={order_id}")
    print(f"EXPIRES_AT={expires_at}")
    print("PAYMENT_REQUIREMENT=" + json.dumps(requirement, ensure_ascii=False, separators=(",", ":")))
    return 0


def status(arguments: argparse.Namespace) -> int:
    order_id = validated_order_id(arguments.order_id)
    gateway_url()
    state = load_order_state(order_id)
    payload = request_json("GET", f"/v1/orders/{order_id}", access_token=state["result_access_token"])
    status_value = payload.get("status")
    if status_value not in ORDER_STATUSES or payload.get("order_id") != order_id:
        raise RuntimeError("gateway returned an incomplete order status")
    print(f"ORDER_ID={order_id}")
    print(f"STATUS={status_value}")
    return 0


def fulfill(arguments: argparse.Namespace) -> int:
    order_id = validated_order_id(arguments.order_id)
    gateway_url()
    state = load_order_state(order_id)
    if state["provider"] != "free" and not arguments.authorize_payment:
        raise ValueError("explicit --authorize-payment is required")
    payment_proof = None if state["provider"] == "free" else (clawtip_credential(state) if state["provider"] == "clawtip" else protected_stdin("payment proof", maximum_bytes=MAX_PAYMENT_PROOF_BYTES, single_line=True))
    payload = request_json(
        "POST",
        f"/v1/orders/{order_id}/result",
        proof=payment_proof, access_token=state["result_access_token"],
        proof_header="PAYMENT-SIGNATURE" if state["provider"] == "x402" else "Payment-Proof", order_id=order_id,
    )
    result = payload.get("result")
    if not isinstance(result, str):
        raise RuntimeError("gateway returned no result artifact")
    sys.stdout.write(result)
    if not result.endswith("\n"):
        sys.stdout.write("\n")
    digest = payload.get("result_sha256")
    if isinstance(digest, str):
        print(f"RESULT_SHA256={digest}")
    return 0


def parser() -> argparse.ArgumentParser:
    command = SecretFreeArgumentParser(description="Client for the Outcome Offer public-test result.")
    commands = command.add_subparsers(dest="command", required=True)

    quote_command = commands.add_parser("quote")
    quote_command.add_argument("--input-stdin", action="store_true", required=True)
    quote_command.add_argument("--currency", choices=("USD", "CNY"), default="USD")
    quote_command.add_argument("--locale", default="en-US")
    quote_command.add_argument("--idempotency-key", required=True)
    quote_command.set_defaults(handler=quote)

    status_command = commands.add_parser("status")
    status_command.add_argument("--order-id", required=True)
    status_command.set_defaults(handler=status)

    fulfill_command = commands.add_parser("fulfill")
    fulfill_command.add_argument("--order-id", required=True)
    fulfill_command.add_argument("--payment-proof-stdin", action="store_true")
    fulfill_command.add_argument("--authorize-payment", action="store_true")
    fulfill_command.set_defaults(handler=fulfill)
    return command


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        return arguments.handler(arguments)
    except (ValueError, RuntimeError) as error:
        print(f"ERROR={error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

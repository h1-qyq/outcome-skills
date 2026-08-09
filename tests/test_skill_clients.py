import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess as _subprocess
import sys
import tempfile
from threading import Thread
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).parents[1]
TEST_STATE_ROOT = Path(tempfile.gettempdir()) / "one-cent-outcomes-client-tests"
OUTCOME_OFFER_CLIENT = ROOT / "skills" / "outcome-offer" / "scripts" / "client.py"
PROOF_PACK_CLIENT = ROOT / "skills" / "proof-pack" / "scripts" / "client.py"
PROOF_PACK_SKILL = ROOT / "skills" / "proof-pack" / "SKILL.md"
PROOF_PACK_EXAMPLE = ROOT / "examples" / "proof-pack.md"
PROOF_PACK_METADATA = ROOT / "skills" / "proof-pack" / "agents" / "openai.yaml"
REPLY_TO_CLOSE_CLIENT = ROOT / "skills" / "reply-to-close" / "scripts" / "client.py"
REPLY_TO_CLOSE_SKILL = ROOT / "skills" / "reply-to-close" / "SKILL.md"
REPLY_TO_CLOSE_EXAMPLE = ROOT / "examples" / "reply-to-close.md"
REPLY_TO_CLOSE_METADATA = ROOT / "skills" / "reply-to-close" / "agents" / "openai.yaml"


def _run_client_with_protected_stdin(command, *args, **kwargs):
    """Keep legacy behavioral tests on the public protected-stdin interface."""

    rewritten = list(command)
    secret: str | None = None
    for legacy, protected in (("--input", "--input-stdin"), ("--payment-proof", "--payment-proof-stdin")):
        if legacy in rewritten:
            index = rewritten.index(legacy)
            secret = rewritten[index + 1]
            rewritten[index : index + 2] = [protected]
    if secret is not None:
        assert "input" not in kwargs
        kwargs["input"] = secret
        kwargs.setdefault("encoding", "utf-8")
    environment = dict(kwargs.get("env", os.environ))
    environment["OUTCOMES_CLIENT_STATE_DIR"] = str(TEST_STATE_ROOT)
    kwargs["env"] = environment
    if "fulfill" in rewritten or "status" in rewritten:
        try:
            order_id = rewritten[rewritten.index("--order-id") + 1]
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", order_id):
                TEST_STATE_ROOT.mkdir(parents=True, exist_ok=True)
                (TEST_STATE_ROOT / f"{order_id}.json").write_text(
                    json.dumps({"result_access_token": "test-access-token", "provider": "demo"}), encoding="utf-8"
                )
        except (ValueError, IndexError):
            pass
    return _subprocess.run(rewritten, *args, **kwargs)


subprocess = SimpleNamespace(run=_run_client_with_protected_stdin)

_json_dumps = json.dumps


def _json_dumps_with_test_access_token(value, *args, **kwargs):
    if isinstance(value, dict) and "order_id" in value and "payment_requirement" in value and "result_access_token" not in value:
        value = {**value, "result_access_token": "test-access-token"}
    return _json_dumps(value, *args, **kwargs)


json.dumps = _json_dumps_with_test_access_token
PUBLIC_PROOF_PACK_HEADINGS = {
    "# Proof Pack",
    "## Result contract",
    "## Workflow",
    "## Guardrails",
    "## Example",
}
PRIVATE_PROOF_PACK_HEADINGS = {
    "## PROOF HEADLINE",
    "## PROPOSAL BLURB",
    "## CASE STORY",
    "## EVIDENCE BULLETS",
    "## SOCIAL POST",
    "## SALES-CONVERSATION VERSION",
    "## CLAIM TRACEABILITY",
    "## MISSING EVIDENCE",
    "## QUALITY CHECK",
}
PRIVATE_PROOF_PACK_MARKERS = (
    "18%",
    "9%",
    "六周",
    "WhatsApp 到店提醒",
    "我们没有改变服务价格",
    "“",
    "”",
)

PUBLIC_REPLY_TO_CLOSE_HEADINGS = {
    "# Reply to Close",
    "## Result contract",
    "## Workflow",
    "## Guardrails",
    "## Example",
}
PRIVATE_REPLY_TO_CLOSE_HEADINGS = {
    "## COPY-PASTE REPLY",
    "## SHORT REPLY",
    "## OBJECTION CLASSIFICATION",
    "## LOW-FRICTION NEXT STEP",
    "## ASSUMPTIONS AND TRACEABILITY",
    "## QUALITY CHECK",
}
PRIVATE_REPLY_TO_CLOSE_MARKERS = (
    "每月 49 美元",
    "看起来不错",
    "大约每月有多少预约",
)


def test_all_public_skill_instructions_are_ascii_for_default_windows_validator():
    skill_files = sorted(ROOT.glob("skills/*/SKILL.md"))

    assert {path.parent.name for path in skill_files} == {
        "outcome-offer",
        "proof-pack",
        "reply-to-close",
    }
    for path in skill_files:
        non_ascii = [value for value in path.read_bytes() if value > 127]
        assert non_ascii == [], f"{path.parent.name}/SKILL.md has non-ASCII bytes: {non_ascii}"


def assert_public_reply_to_close_is_thin(content: str) -> None:
    headings = {line for line in content.splitlines() if line.startswith("#")}
    assert headings == PUBLIC_REPLY_TO_CLOSE_HEADINGS
    assert not any(heading in content for heading in PRIVATE_REPLY_TO_CLOSE_HEADINGS)
    assert not any(marker in content for marker in PRIVATE_REPLY_TO_CLOSE_MARKERS)
    assert "gateway artifact" in content
    assert "one primary reply" in content


def assert_public_proof_pack_is_thin(content: str) -> None:
    headings = {line for line in content.splitlines() if line.startswith("#")}
    assert headings == PUBLIC_PROOF_PACK_HEADINGS
    assert not any(heading in content for heading in PRIVATE_PROOF_PACK_HEADINGS)
    assert not any(marker in content for marker in PRIVATE_PROOF_PACK_MARKERS)
    assert "gateway artifact" in content
    assert "exactly one headline" in content


def test_proof_pack_quote_posts_exact_product_without_an_amount_and_preserves_input():
    source_input = "给一家美容店加了 WhatsApp 到店提醒，六周内爽约率从 18% 降到 9%。"
    received: dict[str, object] = {}

    class QuoteGateway(BaseHTTPRequestHandler):
        def do_POST(self):
            received["path"] = self.path
            received["body"] = json.loads(
                self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8")
            )
            response = {
                "order_id": "order-proof-001",
                "payment_requirement": {
                    "order_id": "order-proof-001",
                    "currency": "USD",
                    "amount_minor": 1,
                    "expires_at": "2030-01-02T03:04:05+00:00",
                },
            }
            payload = json.dumps(response).encode("utf-8")
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), QuoteGateway)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(PROOF_PACK_CLIENT),
                "quote",
                "--input",
                source_input,
                "--currency",
                "USD",
                "--locale",
                "zh-CN",
                "--idempotency-key",
                "proof-quote-001",
            ],
            cwd=ROOT,
            env={**os.environ, "OUTCOMES_GATEWAY_URL": f"http://127.0.0.1:{server.server_port}"},
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert completed.returncode == 0, completed.stderr
    assert received["path"] == "/v1/orders"
    assert received["body"] == {
        "skill_id": "proof-pack",
        "input_text": source_input,
        "currency": "USD",
        "locale": "zh-CN",
        "idempotency_key": "proof-quote-001",
    }
    assert "ACCESS_MODE=RESTRICTED_FUTURE_PAYMENT" in completed.stdout
    assert "ORDER_ID=order-proof-001" in completed.stdout
    assert "RESULT=PROOF_PACK" in completed.stdout
    assert "18%" not in completed.stdout
    assert "9%" not in completed.stdout


def test_proof_pack_quote_reuses_idempotency_key_and_accepts_authoritative_cny_amount():
    received: list[dict[str, object]] = []

    class QuoteGateway(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8")))
            response = {
                "order_id": "order-proof-002",
                "payment_requirement": {
                    "order_id": "order-proof-002",
                    "currency": "CNY",
                    "amount_minor": 6,
                    "expires_at": "2030-01-02T03:04:05+00:00",
                },
            }
            payload = json.dumps(response).encode("utf-8")
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), QuoteGateway)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        command = [
            sys.executable,
            str(PROOF_PACK_CLIENT),
            "quote",
            "--input",
            "One verified metric.",
            "--currency",
            "CNY",
            "--idempotency-key",
            "reuse-proof-quote",
        ]
        environment = {**os.environ, "OUTCOMES_GATEWAY_URL": f"http://127.0.0.1:{server.server_port}"}
        first = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True, check=False)
        second = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True, check=False)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert first.returncode == second.returncode == 0
    assert [body["idempotency_key"] for body in received] == ["reuse-proof-quote"] * 2
    assert all("amount" not in body and "price" not in body for body in received)
    assert "ACCESS_MODE=RESTRICTED_FUTURE_PAYMENT" in first.stdout


@pytest.mark.parametrize("order_id", ["order?override=1", "order#fragment", "../../admin"])
@pytest.mark.parametrize("command", ["status", "fulfill"])
def test_proof_pack_rejects_unsafe_order_id_before_any_gateway_request(order_id, command):
    received: list[str] = []

    class Gateway(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.path)
            self.send_response(200)
            self.end_headers()

        def do_POST(self):
            received.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        arguments = [sys.executable, str(PROOF_PACK_CLIENT), command, "--order-id", order_id]
        if command == "fulfill":
            arguments.extend(["--payment-proof", "demo:unsafe:paid", "--authorize-payment"])
        completed = subprocess.run(
            arguments,
            cwd=ROOT,
            env={**os.environ, "OUTCOMES_GATEWAY_URL": f"http://127.0.0.1:{server.server_port}"},
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert completed.returncode == 1
    assert completed.stderr == "ERROR=invalid order id\n"
    assert received == []


def test_proof_pack_fulfill_redacts_crlf_payment_proof_and_returns_gateway_artifact_unchanged():
    proof = "bad\r\nInjected: yes"
    completed = subprocess.run(
        [
            sys.executable,
            str(PROOF_PACK_CLIENT),
            "fulfill",
            "--order-id",
            "order-proof-003",
            "--payment-proof",
            proof,
            "--authorize-payment",
        ],
        cwd=ROOT,
        env={**os.environ, "OUTCOMES_GATEWAY_URL": "http://127.0.0.1:1"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    assert completed.stderr.startswith("ERROR=")
    assert proof not in completed.stdout
    assert proof not in completed.stderr
    assert "Injected" not in completed.stderr


@pytest.mark.parametrize(
    ("currency", "requirement"),
    [
        ("USD", {"order_id": "other-order", "currency": "USD", "amount_minor": 1}),
        ("USD", {"order_id": "order-proof-004", "currency": "CNY", "amount_minor": 6}),
        ("USD", {"order_id": "order-proof-004", "currency": "USD", "amount_minor": 6}),
        ("USD", {"order_id": "order-proof-004", "currency": "USD", "amount_minor": 1.0}),
        ("CNY", {"order_id": "order-proof-004", "currency": "CNY", "amount_minor": 6.0}),
        ("USD", {"order_id": "order-proof-004", "currency": "USD", "amount_minor": True}),
        ("CNY", {"order_id": "order-proof-004", "currency": "CNY", "amount_minor": "6"}),
        ("USD", {"order_id": "order-proof-004", "currency": "USD", "amount_minor": 2}),
        ("CNY", {"order_id": "order-proof-004", "currency": "CNY", "amount_minor": 1}),
    ],
)
def test_proof_pack_quote_rejects_mismatched_gateway_requirement(currency, requirement):
    class QuoteGateway(BaseHTTPRequestHandler):
        def do_POST(self):
            requirement["expires_at"] = "2030-01-02T03:04:05+00:00"
            payload = json.dumps({"order_id": "order-proof-004", "payment_requirement": requirement}).encode("utf-8")
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), QuoteGateway)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(PROOF_PACK_CLIENT),
                "quote",
                "--input",
                "One verified metric.",
                "--currency",
                currency,
                "--idempotency-key",
                "proof-quote-004",
            ],
            cwd=ROOT,
            env={**os.environ, "OUTCOMES_GATEWAY_URL": f"http://127.0.0.1:{server.server_port}"},
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert "inconsistent" in completed.stderr


def test_proof_pack_quote_rejects_unsafe_top_level_order_id_without_echoing_it():
    unsafe_order_id = "../../admin"

    class QuoteGateway(BaseHTTPRequestHandler):
        def do_POST(self):
            response = {
                "order_id": unsafe_order_id,
                "payment_requirement": {
                    "order_id": unsafe_order_id,
                    "currency": "USD",
                    "amount_minor": 1,
                    "expires_at": "2030-01-02T03:04:05+00:00",
                },
            }
            payload = json.dumps(response).encode("utf-8")
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), QuoteGateway)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(PROOF_PACK_CLIENT),
                "quote",
                "--input",
                "One verified metric.",
                "--idempotency-key",
                "proof-quote-unsafe-order",
            ],
            cwd=ROOT,
            env={**os.environ, "OUTCOMES_GATEWAY_URL": f"http://127.0.0.1:{server.server_port}"},
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr == "ERROR=invalid order id\n"
    assert unsafe_order_id not in completed.stderr


def test_proof_pack_fulfill_returns_verified_gateway_artifact_first_and_unchanged():
    artifact = "## HEADLINE\n\nVerified artifact only."
    received: dict[str, object] = {}

    class FulfillGateway(BaseHTTPRequestHandler):
        def do_POST(self):
            received["path"] = self.path
            received["proof"] = self.headers.get("Payment-Proof")
            payload = json.dumps({"result": artifact, "result_sha256": "b" * 64}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), FulfillGateway)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(PROOF_PACK_CLIENT),
                "fulfill",
                "--order-id",
                "order-proof-005",
                "--payment-proof",
                "demo:order-proof-005:paid",
                "--authorize-payment",
            ],
            cwd=ROOT,
            env={**os.environ, "OUTCOMES_GATEWAY_URL": f"http://127.0.0.1:{server.server_port}"},
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert completed.returncode == 0, completed.stderr
    assert received == {
        "path": "/v1/orders/order-proof-005/result",
        "proof": "demo:order-proof-005:paid",
    }
    assert completed.stdout.startswith(artifact)
    assert "RESULT_SHA256=" + "b" * 64 in completed.stdout


def test_proof_pack_public_skill_keeps_paid_artifact_gateway_only():
    content = PROOF_PACK_SKILL.read_text(encoding="utf-8")

    assert_public_proof_pack_is_thin(content)
    assert "do not draft, reconstruct, preview, or reveal" in content
    assert "exactly two proposal-blurb sentences" in content
    assert "120-180" in content
    assert "exactly three evidence bullets" in content
    assert "quotation marks only" in content.lower()
    assert "Derived" in content
    assert "guarantee the named proof pack artifact only" in content.lower()


def test_proof_pack_public_skill_checker_rejects_an_appended_private_artifact():
    adversarial_content = PROOF_PACK_SKILL.read_text(encoding="utf-8") + "\n## PROOF HEADLINE\n美容店：18% 到 9%\n"

    with pytest.raises(AssertionError):
        assert_public_proof_pack_is_thin(adversarial_content)


def test_proof_pack_metadata_matches_the_exact_public_interface_contract():
    normalized_lines = [line.strip() for line in PROOF_PACK_METADATA.read_text(encoding="utf-8").splitlines() if line]

    assert normalized_lines == [
        "interface:",
        'display_name: "Proof Pack"',
        'short_description: "Turn verified results into a free, sales-ready proof pack during public testing"',
        'default_prompt: "Use $proof-pack to quote and fulfill a proof pack from my verified result, metric, or testimonial."',
    ]


def test_proof_pack_demo_fixture_uses_the_exact_private_output_contract():
    content = PROOF_PACK_EXAMPLE.read_text(encoding="utf-8")
    headings = [line for line in content.splitlines() if line.startswith("## ")]

    assert headings[2:11] == [
        "## PROOF HEADLINE",
        "## PROPOSAL BLURB",
        "## CASE STORY",
        "## EVIDENCE BULLETS",
        "## SOCIAL POST",
        "## SALES-CONVERSATION VERSION",
        "## CLAIM TRACEABILITY",
        "## MISSING EVIDENCE",
        "## QUALITY CHECK",
    ]
    assert "| Output claim | Input support | Status |" in content
    assert "| Source |" not in content
    assert "| Derived |" not in content
    assert "| Not claimed |" not in content
    assert "| Supported |" in content


def test_outcome_offer_quote_posts_product_without_an_amount_and_prints_price_contract():
    source_input = "I help independent consultants build a Notion client system in two days."
    received: dict[str, object] = {}

    class QuoteGateway(BaseHTTPRequestHandler):
        def do_POST(self):
            received["path"] = self.path
            received["body"] = json.loads(
                self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8")
            )
            response = {
                "order_id": "order-outcome-001",
                "payment_requirement": {
                    "order_id": "order-outcome-001",
                    "currency": "USD",
                    "amount_minor": 1,
                    "expires_at": "2030-01-02T03:04:05+00:00",
                },
            }
            payload = json.dumps(response).encode("utf-8")
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), QuoteGateway)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(OUTCOME_OFFER_CLIENT),
                "quote",
                "--input",
                source_input,
                "--currency",
                "USD",
                "--locale",
                "en-US",
                "--idempotency-key",
                "quote-attempt-001",
            ],
            cwd=ROOT,
            env={**os.environ, "OUTCOMES_GATEWAY_URL": f"http://127.0.0.1:{server.server_port}"},
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert completed.returncode == 0, completed.stderr
    assert received["path"] == "/v1/orders"
    assert received["body"]["skill_id"] == "outcome-offer"
    assert received["body"]["input_text"] == source_input
    assert received["body"]["idempotency_key"] == "quote-attempt-001"
    assert "amount" not in received["body"]
    assert "price" not in received["body"]
    assert "ACCESS_MODE=RESTRICTED_FUTURE_PAYMENT" in completed.stdout
    assert "ORDER_ID=order-outcome-001" in completed.stdout
    assert "RESULT=OFFER_CARD" in completed.stdout
    assert source_input not in completed.stdout
    assert source_input not in completed.stderr


def test_outcome_offer_fulfill_keeps_payment_proof_out_of_output_and_returns_artifact_first():
    proof = "demo:order-outcome-002:paid"
    received: dict[str, object] = {}

    class FulfillGateway(BaseHTTPRequestHandler):
        def do_POST(self):
            received["path"] = self.path
            received["proof"] = self.headers.get("Payment-Proof")
            response = {"result": "# Offer card\n\nPaste-ready sales block.", "result_sha256": "a" * 64}
            payload = json.dumps(response).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), FulfillGateway)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(OUTCOME_OFFER_CLIENT),
                "fulfill",
                "--order-id",
                "order-outcome-002",
                "--payment-proof",
                proof,
                "--authorize-payment",
            ],
            cwd=ROOT,
            env={**os.environ, "OUTCOMES_GATEWAY_URL": f"http://127.0.0.1:{server.server_port}"},
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert completed.returncode == 0, completed.stderr
    assert received == {"path": "/v1/orders/order-outcome-002/result", "proof": proof}
    assert completed.stdout.startswith("# Offer card\n\nPaste-ready sales block.")
    assert "RESULT_SHA256=" + "a" * 64 in completed.stdout
    assert proof not in completed.stdout
    assert proof not in completed.stderr


def test_outcome_offer_quote_reuses_a_caller_supplied_idempotency_key():
    received: list[dict[str, object]] = []

    class QuoteGateway(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8")))
            response = {
                "order_id": "order-outcome-003",
                "payment_requirement": {
                    "order_id": "order-outcome-003",
                    "currency": "USD",
                    "amount_minor": 1,
                    "expires_at": "2030-01-02T03:04:05+00:00",
                },
            }
            payload = json.dumps(response).encode("utf-8")
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), QuoteGateway)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        command = [
            sys.executable,
            str(OUTCOME_OFFER_CLIENT),
            "quote",
            "--input",
            "A rough service description.",
            "--idempotency-key",
            "reuse-this-quote-attempt",
        ]
        environment = {**os.environ, "OUTCOMES_GATEWAY_URL": f"http://127.0.0.1:{server.server_port}"}
        first = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True, check=False)
        second = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True, check=False)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert first.returncode == second.returncode == 0
    assert [body["idempotency_key"] for body in received] == ["reuse-this-quote-attempt"] * 2


def test_outcome_offer_fulfill_redacts_adversarial_payment_proof_on_request_construction_failure():
    proof = "bad\r\nInjected: yes"

    completed = subprocess.run(
        [
            sys.executable,
            str(OUTCOME_OFFER_CLIENT),
            "fulfill",
            "--order-id",
            "order-outcome-004",
            "--payment-proof",
            proof,
            "--authorize-payment",
        ],
        cwd=ROOT,
        env={**os.environ, "OUTCOMES_GATEWAY_URL": "http://127.0.0.1:1"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    assert completed.stderr.startswith("ERROR=")
    assert proof not in completed.stdout
    assert proof not in completed.stderr
    assert "Injected" not in completed.stderr


@pytest.mark.parametrize(
    ("currency", "requirement", "expected_error"),
    [
        ("USD", {"order_id": "other-order", "currency": "USD", "amount_minor": 1}, "inconsistent"),
        ("USD", {"order_id": "order-outcome-005", "currency": "CNY", "amount_minor": 6}, "inconsistent"),
        ("USD", {"order_id": "order-outcome-005", "currency": "USD", "amount_minor": 6}, "inconsistent"),
        ("USD", {"order_id": "order-outcome-005", "currency": "USD", "amount_minor": 1.0}, "inconsistent"),
        ("CNY", {"order_id": "order-outcome-005", "currency": "CNY", "amount_minor": 6.0}, "inconsistent"),
        ("USD", {"order_id": "order-outcome-005", "currency": "USD", "amount_minor": True}, "inconsistent"),
        ("CNY", {"order_id": "order-outcome-005", "currency": "CNY", "amount_minor": "6"}, "inconsistent"),
        ("USD", {"order_id": "order-outcome-005", "currency": "USD", "amount_minor": 2}, "inconsistent"),
        ("CNY", {"order_id": "order-outcome-005", "currency": "CNY", "amount_minor": 1}, "inconsistent"),
    ],
)
def test_outcome_offer_quote_rejects_mismatched_gateway_requirement(currency, requirement, expected_error):
    class QuoteGateway(BaseHTTPRequestHandler):
        def do_POST(self):
            requirement["expires_at"] = "2030-01-02T03:04:05+00:00"
            payload = json.dumps({"order_id": "order-outcome-005", "payment_requirement": requirement}).encode("utf-8")
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), QuoteGateway)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(OUTCOME_OFFER_CLIENT),
                "quote",
                "--input",
                "A rough service description.",
                "--currency",
                currency,
                "--idempotency-key",
                "quote-attempt-005",
            ],
            cwd=ROOT,
            env={**os.environ, "OUTCOMES_GATEWAY_URL": f"http://127.0.0.1:{server.server_port}"},
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert expected_error in completed.stderr


def test_outcome_offer_quote_accepts_authoritative_cny_requirement():
    received: dict[str, object] = {}

    class QuoteGateway(BaseHTTPRequestHandler):
        def do_POST(self):
            received.update(json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8")))
            response = {
                "order_id": "order-outcome-006",
                "payment_requirement": {
                    "order_id": "order-outcome-006",
                    "currency": "CNY",
                    "amount_minor": 6,
                    "expires_at": "2030-01-02T03:04:05+00:00",
                },
            }
            payload = json.dumps(response).encode("utf-8")
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), QuoteGateway)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(OUTCOME_OFFER_CLIENT),
                "quote",
                "--input",
                "A rough service description.",
                "--currency",
                "CNY",
                "--idempotency-key",
                "quote-attempt-006",
            ],
            cwd=ROOT,
            env={**os.environ, "OUTCOMES_GATEWAY_URL": f"http://127.0.0.1:{server.server_port}"},
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert completed.returncode == 0, completed.stderr
    assert received["currency"] == "CNY"
    assert "ACCESS_MODE=RESTRICTED_FUTURE_PAYMENT" in completed.stdout


def test_outcome_offer_quote_rejects_an_unsafe_top_level_order_id_without_echoing_it():
    unsafe_order_id = "../../admin"

    class QuoteGateway(BaseHTTPRequestHandler):
        def do_POST(self):
            response = {
                "order_id": unsafe_order_id,
                "payment_requirement": {
                    "order_id": unsafe_order_id,
                    "currency": "USD",
                    "amount_minor": 1,
                    "expires_at": "2030-01-02T03:04:05+00:00",
                },
            }
            payload = json.dumps(response).encode("utf-8")
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), QuoteGateway)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(OUTCOME_OFFER_CLIENT),
                "quote",
                "--input",
                "A rough service description.",
                "--idempotency-key",
                "quote-attempt-unsafe-order",
            ],
            cwd=ROOT,
            env={**os.environ, "OUTCOMES_GATEWAY_URL": f"http://127.0.0.1:{server.server_port}"},
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr == "ERROR=invalid order id\n"
    assert unsafe_order_id not in completed.stderr


def test_outcome_offer_status_rejects_unsafe_gateway_status_without_echoing_response_content():
    unsafe_status = "delivered\nINJECTED=response"

    class Gateway(BaseHTTPRequestHandler):
        def do_GET(self):
            payload = json.dumps({"order_id": "order-outcome-007", "status": unsafe_status}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        completed = subprocess.run(
            [sys.executable, str(OUTCOME_OFFER_CLIENT), "status", "--order-id", "order-outcome-007"],
            cwd=ROOT,
            env={**os.environ, "OUTCOMES_GATEWAY_URL": f"http://127.0.0.1:{server.server_port}"},
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert unsafe_status not in completed.stderr


@pytest.mark.parametrize("order_id", ["order?override=1", "order#fragment", "../../admin"])
@pytest.mark.parametrize("command", ["status", "fulfill"])
def test_outcome_offer_rejects_unsafe_order_id_before_any_gateway_request(order_id, command):
    received: list[tuple[str, str | None]] = []

    class Gateway(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append((self.path, self.headers.get("Payment-Proof")))
            self.send_response(200)
            self.end_headers()

        def do_POST(self):
            received.append((self.path, self.headers.get("Payment-Proof")))
            self.send_response(200)
            self.end_headers()

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        arguments = [sys.executable, str(OUTCOME_OFFER_CLIENT), command, "--order-id", order_id]
        proof = "demo:unsafe-route:paid"
        if command == "fulfill":
            arguments.extend(["--payment-proof", proof, "--authorize-payment"])
        completed = subprocess.run(
            arguments,
            cwd=ROOT,
            env={**os.environ, "OUTCOMES_GATEWAY_URL": f"http://127.0.0.1:{server.server_port}"},
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert completed.returncode == 1
    assert completed.stderr == "ERROR=invalid order id\n"
    assert received == []
    if command == "fulfill":
        assert proof not in completed.stdout
        assert proof not in completed.stderr


def test_reply_to_close_quote_posts_exact_product_without_amount_and_preserves_input():
    source_input = "我卖给美容店一款每月 49 美元的预约提醒工具。客户说：“看起来不错，但我们店太小了，价格也有点高，可能下季度再说。”客户这样说我怎么回？"
    received: dict[str, object] = {}

    class QuoteGateway(BaseHTTPRequestHandler):
        def do_POST(self):
            received["path"] = self.path
            received["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
            payload = json.dumps(
                {
                    "order_id": "order-reply-001",
                    "payment_requirement": {
                        "order_id": "order-reply-001",
                        "currency": "USD",
                        "amount_minor": 1,
                        "expires_at": "2030-01-02T03:04:05+00:00",
                    },
                }
            ).encode("utf-8")
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), QuoteGateway)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        completed = subprocess.run(
            [
                sys.executable, str(REPLY_TO_CLOSE_CLIENT), "quote", "--input", source_input,
                "--currency", "USD", "--locale", "zh-CN", "--idempotency-key", "reply-quote-001",
            ],
            cwd=ROOT,
            env={**os.environ, "OUTCOMES_GATEWAY_URL": f"http://127.0.0.1:{server.server_port}"},
            capture_output=True, text=True, check=False,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert completed.returncode == 0, completed.stderr
    assert received == {
        "path": "/v1/orders",
        "body": {
            "skill_id": "reply-to-close", "input_text": source_input, "currency": "USD",
            "locale": "zh-CN", "idempotency_key": "reply-quote-001",
        },
    }
    assert "ACCESS_MODE=RESTRICTED_FUTURE_PAYMENT" in completed.stdout
    assert "RESULT=REPLY_TO_CLOSE" in completed.stdout
    assert "ORDER_ID=order-reply-001" in completed.stdout
    assert source_input not in completed.stdout
    assert source_input not in completed.stderr


def test_reply_to_close_quote_reuses_caller_key_and_accepts_authoritative_cny_amount():
    received: list[dict[str, object]] = []

    class QuoteGateway(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8")))
            payload = json.dumps(
                {
                    "order_id": "order-reply-cny-001",
                    "payment_requirement": {
                        "order_id": "order-reply-cny-001",
                        "currency": "CNY",
                        "amount_minor": 6,
                        "expires_at": "2030-01-02T03:04:05+00:00",
                    },
                }
            ).encode("utf-8")
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), QuoteGateway)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        command = [
            sys.executable, str(REPLY_TO_CLOSE_CLIENT), "quote", "--input", "private inbound objection",
            "--currency", "CNY", "--idempotency-key", "reply-cny-reuse-001",
        ]
        environment = {**os.environ, "OUTCOMES_GATEWAY_URL": f"http://127.0.0.1:{server.server_port}"}
        first = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True, check=False)
        second = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True, check=False)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert first.returncode == second.returncode == 0
    assert [body["idempotency_key"] for body in received] == ["reply-cny-reuse-001"] * 2
    assert all("amount" not in body and "price" not in body for body in received)
    assert "ACCESS_MODE=RESTRICTED_FUTURE_PAYMENT" in first.stdout
    assert "private inbound objection" not in first.stdout
    assert "private inbound objection" not in first.stderr


def test_reply_to_close_quote_rejects_unsafe_returned_order_id_without_printing_it():
    unsafe_order_id = "../../admin"

    class QuoteGateway(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.dumps(
                {
                    "order_id": unsafe_order_id,
                    "payment_requirement": {
                        "order_id": unsafe_order_id,
                        "currency": "USD",
                        "amount_minor": 1,
                        "expires_at": "2030-01-02T03:04:05+00:00",
                    },
                }
            ).encode("utf-8")
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), QuoteGateway)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        completed = subprocess.run(
            [
                sys.executable, str(REPLY_TO_CLOSE_CLIENT), "quote", "--input", "private inbound objection",
                "--idempotency-key", "reply-unsafe-returned-order",
            ],
            cwd=ROOT,
            env={**os.environ, "OUTCOMES_GATEWAY_URL": f"http://127.0.0.1:{server.server_port}"},
            capture_output=True, text=True, check=False,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr == "ERROR=invalid order id\n"
    assert unsafe_order_id not in completed.stderr


@pytest.mark.parametrize(
    ("currency", "requirement"),
    [
        ("USD", {"order_id": "other", "currency": "USD", "amount_minor": 1}),
        ("USD", {"order_id": "order-reply-002", "currency": "CNY", "amount_minor": 6}),
        ("USD", {"order_id": "order-reply-002", "currency": "USD", "amount_minor": 1.0}),
        ("CNY", {"order_id": "order-reply-002", "currency": "CNY", "amount_minor": True}),
        ("CNY", {"order_id": "order-reply-002", "currency": "CNY", "amount_minor": "6"}),
        ("CNY", {"order_id": "order-reply-002", "currency": "CNY", "amount_minor": 1}),
    ],
)
def test_reply_to_close_quote_rejects_inconsistent_requirements(currency, requirement):
    class QuoteGateway(BaseHTTPRequestHandler):
        def do_POST(self):
            requirement["expires_at"] = "2030-01-02T03:04:05+00:00"
            payload = json.dumps({"order_id": "order-reply-002", "payment_requirement": requirement}).encode("utf-8")
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), QuoteGateway)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        completed = subprocess.run(
            [sys.executable, str(REPLY_TO_CLOSE_CLIENT), "quote", "--input", "private input", "--currency", currency, "--idempotency-key", "reply-quote-002"],
            cwd=ROOT, env={**os.environ, "OUTCOMES_GATEWAY_URL": f"http://127.0.0.1:{server.server_port}"},
            capture_output=True, text=True, check=False,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr == "ERROR=gateway returned an inconsistent quote\n"
    assert "private input" not in completed.stderr


@pytest.mark.parametrize("order_id", ["order?override=1", "order#fragment", "../../admin"])
@pytest.mark.parametrize("command", ["status", "fulfill"])
def test_reply_to_close_rejects_unsafe_order_ids_and_redacts_crlf_proofs(order_id, command):
    received: list[str] = []

    class Gateway(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.path)
            self.send_response(200)
            self.end_headers()

        def do_POST(self):
            received.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    proof = "bad\r\nInjected: yes"
    try:
        arguments = [sys.executable, str(REPLY_TO_CLOSE_CLIENT), command, "--order-id", order_id]
        if command == "fulfill":
            arguments.extend(["--payment-proof", proof, "--authorize-payment"])
        completed = subprocess.run(
            arguments, cwd=ROOT,
            env={**os.environ, "OUTCOMES_GATEWAY_URL": f"http://127.0.0.1:{server.server_port}"},
            capture_output=True, text=True, check=False,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert completed.returncode == 1
    assert completed.stderr == "ERROR=invalid order id\n"
    assert received == []
    assert proof not in completed.stdout
    assert proof not in completed.stderr
    assert "Injected" not in completed.stderr


def test_reply_to_close_redacts_crlf_payment_proof_when_safe_request_construction_fails():
    proof = "bad\r\nInjected: yes"
    completed = subprocess.run(
        [
            sys.executable, str(REPLY_TO_CLOSE_CLIENT), "fulfill", "--order-id", "order-reply-safe-004",
            "--payment-proof", proof, "--authorize-payment",
        ],
        cwd=ROOT,
        env={**os.environ, "OUTCOMES_GATEWAY_URL": "http://127.0.0.1:1"},
        capture_output=True, text=True, check=False,
    )

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr == "ERROR=valid payment proof is required on protected stdin\n"
    assert proof not in completed.stdout
    assert proof not in completed.stderr
    assert "Injected" not in completed.stderr


def test_reply_to_close_fulfill_requires_authorization_and_returns_artifact_first_unchanged():
    artifact = "## COPY-PASTE REPLY\n\nVerified artifact only."
    received: dict[str, object] = {}

    class FulfillGateway(BaseHTTPRequestHandler):
        def do_POST(self):
            received["path"] = self.path
            received["proof"] = self.headers.get("Payment-Proof")
            payload = json.dumps({"result": artifact, "result_sha256": "c" * 64}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), FulfillGateway)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    proof = "demo:order-reply-003:paid"
    try:
        no_auth = subprocess.run(
            [sys.executable, str(REPLY_TO_CLOSE_CLIENT), "fulfill", "--order-id", "order-reply-003", "--payment-proof", proof],
            cwd=ROOT, env={**os.environ, "OUTCOMES_GATEWAY_URL": f"http://127.0.0.1:{server.server_port}"},
            capture_output=True, text=True, check=False,
        )
        completed = subprocess.run(
            [sys.executable, str(REPLY_TO_CLOSE_CLIENT), "fulfill", "--order-id", "order-reply-003", "--payment-proof", proof, "--authorize-payment"],
            cwd=ROOT, env={**os.environ, "OUTCOMES_GATEWAY_URL": f"http://127.0.0.1:{server.server_port}"},
            capture_output=True, text=True, check=False,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert no_auth.returncode == 1
    assert no_auth.stderr == "ERROR=explicit --authorize-payment is required\n"
    assert received == {"path": "/v1/orders/order-reply-003/result", "proof": proof}
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith(artifact)
    assert "RESULT_SHA256=" + "c" * 64 in completed.stdout
    assert proof not in completed.stdout
    assert proof not in completed.stderr


def test_reply_to_close_public_skill_metadata_and_demo_fixture_enforce_the_paid_contract():
    skill = REPLY_TO_CLOSE_SKILL.read_text(encoding="utf-8")
    assert_public_reply_to_close_is_thin(skill)
    assert "do not draft, reconstruct, preview, or reveal" in skill
    assert "cold outreach" in skill.lower()
    assert "mass-spam" in skill.lower()
    assert "guarantee the named Reply to Close artifact only" in skill
    assert [line.strip() for line in REPLY_TO_CLOSE_METADATA.read_text(encoding="utf-8").splitlines() if line] == [
        "interface:",
        'display_name: "Reply to Close"',
        'short_description: "Turn a live objection into a free, ready-to-send reply during public testing"',
        'default_prompt: "Use $reply-to-close to quote and fulfill one grounded reply to this inbound objection."',
    ]

    example = REPLY_TO_CLOSE_EXAMPLE.read_text(encoding="utf-8")
    headings = [line for line in example.splitlines() if line.startswith("## ")]
    assert headings == [
        "## Source input", "## Artifact returned by the controlled demo gateway",
        "## COPY-PASTE REPLY", "## SHORT REPLY", "## OBJECTION CLASSIFICATION",
        "## LOW-FRICTION NEXT STEP", "## ASSUMPTIONS AND TRACEABILITY", "## QUALITY CHECK",
    ]
    question = "方便告诉我你们大约每月有多少预约吗？"
    assert example.count(question) == 3
    primary_reply = example.split("## COPY-PASTE REPLY\n\n", 1)[1].split("\n\n## SHORT REPLY", 1)[0]
    short_reply = example.split("## SHORT REPLY\n\n", 1)[1].split("\n\n## OBJECTION CLASSIFICATION", 1)[0]
    assert len(primary_reply) < 90
    assert len(short_reply) < 40
    assert question in primary_reply
    assert question in short_reply
    assert "每月 49 美元" in example
    assert "客户说：“看起来不错，但我们店太小了，价格也有点高，可能下季度再说。”客户这样说我怎么回？" in example
    assert "$49/month" not in example
    assert "| Reply claim | Input support | Status |" in example
    assert "| Supported |" in example
    assert "| Placeholder |" in example
    status_cells = [
        line.split("|")[-2].strip()
        for line in example.splitlines()
        if line.startswith("|") and line.count("|") == 4 and not line.startswith("| ---")
    ][1:]
    assert status_cells
    assert set(status_cells) <= {"Supported", "Assumption", "Placeholder"}
    assert "trial" not in example.lower()
    assert "discount" not in example.lower()
    assert "refund" not in example.lower()
    assert not any(term in example for term in ("试用", "折扣", "退款", "取消", "无合同", "功能", "证明", "回报", "节省"))
    assert "no real money moved" in example.lower()


def test_reply_to_close_public_skill_checker_rejects_an_appended_private_artifact():
    adversarial_content = REPLY_TO_CLOSE_SKILL.read_text(encoding="utf-8") + "\n## COPY-PASTE REPLY\nunsafe local reply\n"

    with pytest.raises(AssertionError):
        assert_public_reply_to_close_is_thin(adversarial_content)

import os
import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
from threading import Thread

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).parents[1]
CLIENTS = tuple(sorted(ROOT.glob("skills/*/scripts/client.py")))


def run_client(
    client: Path,
    arguments: list[str],
    *,
    gateway_url: str,
    stdin: str = "",
    state_dir: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(client), *arguments],
        cwd=ROOT,
        env={
            **os.environ,
            "OUTCOMES_GATEWAY_URL": gateway_url,
            **({"OUTCOMES_CLIENT_STATE_DIR": str(state_dir)} if state_dir else {}),
            **(extra_env or {}),
        },
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("client", CLIENTS, ids=lambda path: path.parents[1].name)
@pytest.mark.parametrize(
    "base_url",
    (
        "http://example.invalid",
        "ftp://example.invalid",
        "https://buyer:secret@example.invalid",
        "https://gateway.invalid?replace=1",
        "https://gateway.invalid#fragment",
        "not-a-url",
    ),
)
def test_client_rejects_unsafe_gateway_base_urls(client: Path, base_url: str):
    completed = run_client(
        client,
        ["status", "--order-id", "safe-order-001"],
        gateway_url=base_url,
    )

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr == "ERROR=invalid OUTCOMES_GATEWAY_URL\n"


@pytest.mark.parametrize("client", CLIENTS, ids=lambda path: path.parents[1].name)
def test_client_reads_buyer_input_only_from_protected_stdin(client: Path):
    received: list[bytes] = []

    class Gateway(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(self.rfile.read(int(self.headers["Content-Length"])))
            payload = b'{"order_id":"safe-order-001","result_access_token":"test-access-token","payment_requirement":{"order_id":"safe-order-001","currency":"USD","amount_minor":1,"expires_at":"2030-01-02T03:04:05+00:00"}}'
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    gateway_url = f"http://127.0.0.1:{server.server_port}"
    try:
        completed = run_client(
            client,
            ["quote", "--input-stdin", "--idempotency-key", "safe-input-001"],
            gateway_url=gateway_url,
            stdin="private buyer input",
        )
        legacy = run_client(
            client,
            ["quote", "--input", "private buyer input", "--idempotency-key", "safe-input-002"],
            gateway_url=gateway_url,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert completed.returncode == 0, completed.stderr
    assert b"private buyer input" in received[0]
    assert legacy.returncode == 2
    assert "private buyer input" not in legacy.stderr


@pytest.mark.parametrize("client", CLIENTS, ids=lambda path: path.parents[1].name)
def test_redirect_never_forwards_payment_proof_to_second_server(client: Path):
    received_by_second_server: list[str | None] = []

    class SecondServer(BaseHTTPRequestHandler):
        def do_GET(self):
            received_by_second_server.append(self.headers.get("Payment-Proof"))
            self.send_response(200)
            self.end_headers()

        def do_POST(self):
            received_by_second_server.append(self.headers.get("Payment-Proof"))
            self.send_response(200)
            self.end_headers()

        def log_message(self, format, *args):
            return

    second = ThreadingHTTPServer(("127.0.0.1", 0), SecondServer)
    second_thread = Thread(target=second.serve_forever, daemon=True)
    second_thread.start()

    class FirstServer(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{second.server_port}/capture")
            self.end_headers()

        def log_message(self, format, *args):
            return

    first = ThreadingHTTPServer(("127.0.0.1", 0), FirstServer)
    first_thread = Thread(target=first.serve_forever, daemon=True)
    first_thread.start()
    proof = "demo:safe-order-001:paid\n"
    try:
        completed = run_client(
            client,
            ["fulfill", "--order-id", "safe-order-001", "--payment-proof-stdin", "--authorize-payment"],
            gateway_url=f"http://127.0.0.1:{first.server_port}",
            stdin=proof,
        )
    finally:
        first.shutdown()
        first_thread.join()
        first.server_close()
        second.shutdown()
        second_thread.join()
        second.server_close()

    assert completed.returncode == 1
    assert completed.stderr == "ERROR=gateway response 302; retry or contact the gateway operator\n"
    assert received_by_second_server == []
    assert proof.strip() not in completed.stdout
    assert proof.strip() not in completed.stderr


def test_loopback_demo_gateway_completes_a_free_public_test(tmp_path: Path):
    entrypoint = ROOT / "scripts" / "run_demo_gateway.py"
    assert entrypoint.is_file(), "the loopback demo gateway entrypoint is missing"
    specification = importlib.util.spec_from_file_location("run_demo_gateway", entrypoint)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    application = module.create_demo_app(tmp_path)
    client = TestClient(application)

    quote = client.post(
        "/v1/orders",
        json={
            "skill_id": "outcome-offer",
            "input_text": "safe local demonstration input",
            "currency": "USD",
            "locale": "en-US",
            "idempotency_key": "demo-gateway-001",
        },
    )

    assert quote.status_code == 201
    order_id = quote.json()["order_id"]
    assert quote.json()["access_mode"] == "free"
    assert quote.json()["payment_requirement"] is None
    result = client.post(
        f"/v1/orders/{order_id}/result",
        headers={"Result-Access-Token": quote.json()["result_access_token"]},
    )
    assert result.status_code == 200
    assert "DEMO" in module.DEMO_NOTICE
    assert "NO MONEY" in module.DEMO_NOTICE


@pytest.mark.parametrize("client", CLIENTS, ids=lambda path: path.parents[1].name)
def test_each_public_skill_runs_without_payment_during_free_test(client: Path, tmp_path: Path):
    token = "free-test-access-token"
    received: dict[str, object] = {}

    class Gateway(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path == "/v1/orders":
                self.rfile.read(int(self.headers["Content-Length"]))
                payload = json.dumps({
                    "order_id": "free-order-001",
                    "access_mode": "free",
                    "payment_requirement": None,
                    "result_access_token": token,
                }).encode()
                self.send_response(201)
            else:
                received["access_token"] = self.headers.get("Result-Access-Token")
                received["payment_proof"] = self.headers.get("Payment-Proof")
                payload = b'{"result":"free result","result_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}'
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
        base_url = f"http://127.0.0.1:{server.server_port}"
        quoted = run_client(
            client,
            ["quote", "--input-stdin", "--idempotency-key", "free-001"],
            gateway_url=base_url,
            stdin="public test input",
            state_dir=tmp_path,
        )
        fulfilled = run_client(
            client,
            ["fulfill", "--order-id", "free-order-001"],
            gateway_url=base_url,
            state_dir=tmp_path,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert quoted.returncode == 0, quoted.stderr
    assert "ACCESS_MODE=FREE" in quoted.stdout
    assert fulfilled.returncode == 0, fulfilled.stderr
    assert fulfilled.stdout.startswith("free result")
    assert received == {"access_token": token, "payment_proof": None}


@pytest.mark.parametrize("client", CLIENTS, ids=lambda path: path.parents[1].name)
def test_client_persists_result_access_token_without_printing_it_and_reuses_it_for_status(
    client: Path, tmp_path: Path
):
    access_token = "token-that-must-not-be-printed"
    observed_headers: list[str | None] = []

    class Gateway(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            payload = (
                b'{"order_id":"safe-order-token-001","result_access_token":"'
                + access_token.encode("ascii")
                + b'","payment_requirement":{"order_id":"safe-order-token-001","currency":"USD","amount_minor":1,"expires_at":"2030-01-02T03:04:05+00:00"}}'
            )
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            observed_headers.append(self.headers.get("Result-Access-Token"))
            payload = b'{"order_id":"safe-order-token-001","status":"payment-required"}'
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
    gateway_url = f"http://127.0.0.1:{server.server_port}"
    try:
        quote = run_client(
            client,
            ["quote", "--input-stdin", "--idempotency-key", "safe-token-001"],
            gateway_url=gateway_url,
            stdin="private input",
            state_dir=tmp_path,
        )
        status = run_client(
            client,
            ["status", "--order-id", "safe-order-token-001"],
            gateway_url=gateway_url,
            state_dir=tmp_path,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert quote.returncode == status.returncode == 0
    assert access_token not in quote.stdout
    assert access_token not in quote.stderr
    assert observed_headers == [access_token]
    state_files = list(tmp_path.glob("*.json"))
    assert len(state_files) == 1
    assert access_token in state_files[0].read_text(encoding="utf-8")


@pytest.mark.parametrize("client", CLIENTS, ids=lambda path: path.parents[1].name)
def test_clawtip_client_writes_official_companion_record_and_reads_its_credential(
    client: Path, tmp_path: Path
):
    token = "private-access-token"
    credential = "private-pay-credential"
    provider_data = {
        "provider": "clawtip", "order_no": "claw-order-001", "indicator": "a" * 32,
        "amount": 6, "pay_to": "merchant", "encrypted_data": "encrypted", "slug": "one-cent-outcomes",
        "payment_skill": "clawtip-sandbox", "skill_id": client.parents[1].name,
        "description": "A local test order", "resource_url": "https://gateway.example/result",
    }
    received: list[str | None] = []

    class Gateway(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path == "/v1/orders":
                self.rfile.read(int(self.headers["Content-Length"]))
                payload = json.dumps({
                    "order_id": "safe-claw-001", "result_access_token": token,
                    "payment_requirement": {"order_id": "safe-claw-001", "currency": "CNY", "amount_minor": 6,
                    "expires_at": "2030-01-02T03:04:05+00:00", "provider_data": provider_data},
                }).encode()
                self.send_response(201)
            else:
                received.append(self.headers.get("Payment-Proof"))
                payload = b'{"result":"paid artifact"}'
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
    orders_root = tmp_path / "openclaw-orders"
    environment = {"OUTCOMES_CLAWTIP_ORDERS_DIR": str(orders_root)}
    try:
        quote = run_client(client, ["quote", "--input-stdin", "--currency", "CNY", "--idempotency-key", "claw-001"], gateway_url=f"http://127.0.0.1:{server.server_port}", stdin="private question", state_dir=tmp_path / "state", extra_env=environment)
        record = orders_root / ("a" * 32) / "claw-order-001.json"
        data = json.loads(record.read_text(encoding="utf-8"))
        data["payCredential"] = credential
        record.write_text(json.dumps(data), encoding="utf-8")
        fulfilled = run_client(client, ["fulfill", "--order-id", "safe-claw-001", "--authorize-payment"], gateway_url=f"http://127.0.0.1:{server.server_port}", state_dir=tmp_path / "state", extra_env=environment)
    finally:
        server.shutdown(); thread.join(); server.server_close()

    assert quote.returncode == fulfilled.returncode == 0
    assert data["skill_id"] == client.parents[1].name
    assert data["question"] == "private question"
    assert received == [credential]
    assert credential not in fulfilled.stdout + fulfilled.stderr


@pytest.mark.parametrize("client", CLIENTS, ids=lambda path: path.parents[1].name)
def test_client_saves_payment_response_from_503_without_printing_it(client: Path, tmp_path: Path):
    token, payment_response = "private-token", "private-payment-response"

    class Gateway(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path == "/v1/orders":
                self.rfile.read(int(self.headers["Content-Length"]))
                payload = json.dumps({"order_id": "safe-503-001", "result_access_token": token, "payment_requirement": {"order_id": "safe-503-001", "currency": "USD", "amount_minor": 1, "expires_at": "2030-01-02T03:04:05+00:00", "provider_data": {"provider": "x402"}}}).encode()
                self.send_response(201)
            else:
                payload = b'{"detail":{"code":"processing"}}'
                self.send_response(503)
                self.send_header("PAYMENT-RESPONSE", payment_response)
            self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)

        def log_message(self, format, *args): return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Gateway); thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        quote = run_client(client, ["quote", "--input-stdin", "--idempotency-key", "response-001"], gateway_url=f"http://127.0.0.1:{server.server_port}", stdin="private input", state_dir=tmp_path)
        failed = run_client(client, ["fulfill", "--order-id", "safe-503-001", "--payment-proof-stdin", "--authorize-payment"], gateway_url=f"http://127.0.0.1:{server.server_port}", stdin="private proof", state_dir=tmp_path)
    finally:
        server.shutdown(); thread.join(); server.server_close()

    assert quote.returncode == 0
    assert failed.returncode == 1
    assert payment_response not in failed.stdout + failed.stderr
    assert payment_response in next(tmp_path.glob("*.json")).read_text(encoding="utf-8")
    assert "retry" in failed.stderr

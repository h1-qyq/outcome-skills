"""Run a loopback-only, ephemeral, no-money gateway for local client checks."""

from __future__ import annotations

import argparse
from ipaddress import ip_address
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

from gateway.app import create_app
from gateway.orders import OrderStore
from gateway.payments.demo import DemoPaymentAdapter


DEMO_NOTICE = "DEMO / NO MONEY: this loopback gateway grants free public-test access and never moves money."


def create_demo_app(storage_dir: str | Path):
    """Compose the development-only app with caller-provided temporary storage."""

    directory = Path(storage_dir)
    directory.mkdir(parents=True, exist_ok=True)
    store = OrderStore(directory / "orders.sqlite3")
    return create_app(
        store=store,
        payment_adapter=DemoPaymentAdapter(store, environment="development"),
        environment="development",
        free_access=True,
    )


def loopback_host(value: str) -> str:
    try:
        if not ip_address(value).is_loopback:
            raise ValueError
    except ValueError:
        raise argparse.ArgumentTypeError("--host must be an explicit loopback IP address") from None
    return value


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=DEMO_NOTICE)
    command.add_argument("--host", type=loopback_host, default="127.0.0.1")
    command.add_argument("--port", type=int, default=8000)
    return command


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    if not 0 < arguments.port <= 65535:
        parser().error("--port must be between 1 and 65535")
    with TemporaryDirectory(prefix="one-cent-outcomes-demo-") as storage_dir:
        print(DEMO_NOTICE)
        print(f"Set OUTCOMES_GATEWAY_URL=http://{arguments.host}:{arguments.port}")
        print("Temporary order and buyer-payload storage is outside this repository and is removed on exit.")
        uvicorn.run(
            create_demo_app(storage_dir),
            host=arguments.host,
            port=arguments.port,
            access_log=False,
            log_level="warning",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

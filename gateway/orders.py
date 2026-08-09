"""SQLite-backed, idempotent order ledger with server-owned prices."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
from pathlib import Path
import re
import sqlite3
from unicodedata import normalize
from uuid import uuid4

from gateway.catalog import get_product
from gateway.contracts import Order, OrderRequest


class IdempotencyConflict(ValueError):
    """An idempotency key was already bound to another buyer request."""


class OrderExpired(ValueError):
    """Payment was attempted after the order's expiry timestamp."""


class OrderAlreadySettled(ValueError):
    """A receipt was replayed against a paid order."""


class InvalidOrderState(ValueError):
    """The requested state transition is not allowed."""


@dataclass(frozen=True)
class StoredResult:
    """The only model artifact retained for a delivered order."""

    body: str
    sha256: str


Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


def normalize_input(input_text: str) -> str:
    """Normalize only representation differences that are not buyer wording."""

    return normalize("NFKC", input_text).replace("\r\n", "\n").replace("\r", "\n").strip()


def input_hash(input_text: str) -> str:
    return hashlib.sha256(normalize_input(input_text).encode("utf-8")).hexdigest()


class OrderStore:
    """Persistent ledger that does not retain the buyer's raw input."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        clock: Clock = utc_now,
        payment_ttl: timedelta = timedelta(minutes=15),
    ) -> None:
        if payment_ttl <= timedelta():
            raise ValueError("payment_ttl must be positive")
        self._database_path = str(database_path)
        self._clock = clock
        self._payment_ttl = payment_ttl
        self._initialize()

    def now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware timestamp")
        return now.astimezone(UTC)

    def create_or_reuse(self, request: OrderRequest) -> Order:
        product = get_product(request.skill_id)
        amount_minor = product.prices[request.currency].minor_units
        binding = (input_hash(request.input_text), request.skill_id, request.currency, request.locale)

        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM orders WHERE idempotency_key = ?", (request.idempotency_key,)
            ).fetchone()
            if existing is not None:
                order = self._row_to_order(existing)
                self._assert_same_binding(order, binding)
                return order

            created_at = self.now()
            expires_at = created_at + self._payment_ttl
            order = Order(
                order_id=str(uuid4()),
                skill_id=request.skill_id,
                input_hash=binding[0],
                currency=request.currency,
                locale=request.locale,
                amount_minor=amount_minor,
                idempotency_key=request.idempotency_key,
                status="payment-required",
                created_at=created_at,
                expires_at=expires_at,
            )
            try:
                connection.execute(
                    """
                    INSERT INTO orders (
                        order_id, skill_id, input_hash, currency, locale, amount_minor,
                        idempotency_key, status, created_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._order_values(order),
                )
            except sqlite3.IntegrityError:
                existing = connection.execute(
                    "SELECT * FROM orders WHERE idempotency_key = ?", (request.idempotency_key,)
                ).fetchone()
                if existing is None:
                    raise
                order = self._row_to_order(existing)
                self._assert_same_binding(order, binding)
                return order
            return order

    def get(self, order_id: str) -> Order:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        if row is None:
            raise KeyError(order_id)
        return self._row_to_order(row)

    def bind_result_access_token_hash(self, order_id: str, token_sha256: str) -> None:
        """Persist only the capability digest, binding it once to the order."""

        if not re.fullmatch(r"[0-9a-f]{64}", token_sha256):
            raise ValueError("result access token digest must be a lowercase SHA-256 hex digest")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT result_access_token_sha256 FROM orders WHERE order_id = ?",
                (order_id,),
            ).fetchone()
            if row is None:
                raise KeyError(order_id)
            existing = row["result_access_token_sha256"]
            if existing is None:
                connection.execute(
                    "UPDATE orders SET result_access_token_sha256 = ? WHERE order_id = ?",
                    (token_sha256, order_id),
                )
            elif not hmac.compare_digest(existing, token_sha256):
                raise InvalidOrderState("result access capability is already bound")

    def result_access_token_hash_matches(self, order_id: str, token_sha256: str) -> bool:
        """Constant-time compare a presented capability digest with private state."""

        candidate = token_sha256 if re.fullmatch(r"[0-9a-f]{64}", token_sha256) else "0" * 64
        with self._connect() as connection:
            row = connection.execute(
                "SELECT result_access_token_sha256 FROM orders WHERE order_id = ?",
                (order_id,),
            ).fetchone()
        stored = row["result_access_token_sha256"] if row is not None else None
        return hmac.compare_digest(stored if isinstance(stored, str) else "0" * 64, candidate)

    def set_payment_response(self, order_id: str, payment_response: str) -> None:
        """Persist a private provider settlement response for result retries."""

        if (
            not payment_response
            or len(payment_response) > 16_384
            or not payment_response.isascii()
            or any(ord(character) < 32 or ord(character) == 127 for character in payment_response)
        ):
            raise ValueError("payment response must contain at most 16,384 characters")
        with self._connect() as connection:
            updated = connection.execute(
                "UPDATE orders SET payment_response = ? WHERE order_id = ?",
                (payment_response, order_id),
            )
        if updated.rowcount != 1:
            raise KeyError(order_id)

    def get_payment_response(self, order_id: str) -> str | None:
        """Return private settlement transport state without adding it to Order."""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT payment_response FROM orders WHERE order_id = ?",
                (order_id,),
            ).fetchone()
        if row is None:
            raise KeyError(order_id)
        value = row["payment_response"]
        return value if isinstance(value, str) and value else None

    @property
    def payload_database_path(self) -> str:
        """A separate database location for the buyer payload responsibility."""

        return f"{self._database_path}.payloads.sqlite3"

    def begin_generation(self, order_id: str) -> Order:
        """Claim the single generation slot, allowing failed work to retry."""

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
            if row is None:
                raise KeyError(order_id)
            order = self._row_to_order(row)
            if order.status not in {"paid", "failed"}:
                raise InvalidOrderState(f"order is {order.status}")
            updated = connection.execute(
                "UPDATE orders SET status = 'generating' WHERE order_id = ? AND status IN ('paid', 'failed')",
                (order_id,),
            )
            if updated.rowcount != 1:
                raise InvalidOrderState("generation slot could not be claimed")
            generating = connection.execute(
                "SELECT * FROM orders WHERE order_id = ?", (order_id,)
            ).fetchone()
        return self._row_to_order(generating)

    def mark_generation_failed(self, order_id: str) -> None:
        with self._connect() as connection:
            updated = connection.execute(
                "UPDATE orders SET status = 'failed' WHERE order_id = ? AND status = 'generating'", (order_id,)
            )
        if updated.rowcount != 1:
            raise InvalidOrderState("generation failure could not be recorded")

    def deliver_result(self, order_id: str, body: str, sha256: str) -> StoredResult:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """
                UPDATE orders SET status = 'delivered', result_body = ?, result_sha256 = ?
                WHERE order_id = ? AND status = 'generating'
                """,
                (body, sha256, order_id),
            )
        if updated.rowcount != 1:
            raise InvalidOrderState("result could not be delivered")
        return StoredResult(body=body, sha256=sha256)

    def get_result(self, order_id: str) -> StoredResult:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT result_body, result_sha256 FROM orders WHERE order_id = ? AND status = 'delivered'",
                (order_id,),
            ).fetchone()
        if row is None or row["result_body"] is None or row["result_sha256"] is None:
            raise KeyError(order_id)
        return StoredResult(body=row["result_body"], sha256=row["result_sha256"])

    def mark_paid(self, order_id: str) -> Order:
        """Atomically transition a currently payable order, or fail closed."""

        expired = False
        paid_row: sqlite3.Row | None = None
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
            if row is None:
                raise KeyError(order_id)
            order = self._row_to_order(row)
            current_time = self.now()
            if order.status == "paid":
                raise OrderAlreadySettled("payment proof already used")
            if order.status != "payment-required":
                raise InvalidOrderState(f"order is {order.status}")
            if current_time >= order.expires_at:
                connection.execute(
                    "UPDATE orders SET status = 'expired' WHERE order_id = ? AND status = 'payment-required'",
                    (order_id,),
                )
                expired = True
            else:
                updated = connection.execute(
                    "UPDATE orders SET status = 'paid' WHERE order_id = ? AND status = 'payment-required'",
                    (order_id,),
                )
                if updated.rowcount != 1:
                    raise InvalidOrderState("order could not be paid")
                paid_row = connection.execute(
                    "SELECT * FROM orders WHERE order_id = ?", (order_id,)
                ).fetchone()
        if expired:
            raise OrderExpired("order payment window expired")
        if paid_row is None:
            raise InvalidOrderState("order could not be paid")
        return self._row_to_order(paid_row)

    def begin_settlement(self, order_id: str, proof_sha256: str) -> Order:
        """Reserve a payable order before an irreversible provider settlement."""

        if not re.fullmatch(r"[0-9a-f]{64}", proof_sha256):
            raise ValueError("settlement proof digest must be a lowercase SHA-256 hex digest")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
            if row is None:
                raise KeyError(order_id)
            order = self._row_to_order(row)
            if order.status != "payment-required":
                raise InvalidOrderState(f"order is {order.status}")
            if self.now() >= order.expires_at:
                connection.execute(
                    "UPDATE orders SET status = 'expired' WHERE order_id = ? AND status = 'payment-required'",
                    (order_id,),
                )
                raise OrderExpired("order payment window expired")
            updated = connection.execute(
                """
                UPDATE orders SET status = 'processing', settlement_proof_sha256 = ?
                WHERE order_id = ? AND status = 'payment-required'
                """,
                (proof_sha256, order_id),
            )
            if updated.rowcount != 1:
                raise InvalidOrderState("settlement slot could not be claimed")
            settled = connection.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        return self._row_to_order(settled)

    def settlement_proof_matches(self, order_id: str, proof_sha256: str) -> bool:
        """Compare a recovery proof digest without exposing the stored binding."""

        if not re.fullmatch(r"[0-9a-f]{64}", proof_sha256):
            return False
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT status, settlement_proof_sha256 FROM orders
                WHERE order_id = ?
                """,
                (order_id,),
            ).fetchone()
        if row is None:
            raise KeyError(order_id)
        stored = row["settlement_proof_sha256"]
        return isinstance(stored, str) and hmac.compare_digest(stored, proof_sha256)

    def complete_settlement(self, order_id: str) -> Order:
        """Commit a settlement that was reserved while the quote was live."""

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """
                UPDATE orders SET status = 'paid'
                WHERE order_id = ? AND status = 'processing'
                """,
                (order_id,),
            )
            if updated.rowcount != 1:
                raise InvalidOrderState("settlement could not be completed")
            row = connection.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        return self._row_to_order(row)

    def cancel_settlement(self, order_id: str) -> Order:
        """Release a failed provider settlement without extending its expiry."""

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
            if row is None:
                raise KeyError(order_id)
            order = self._row_to_order(row)
            if order.status != "processing":
                raise InvalidOrderState(f"order is {order.status}")
            new_status = "expired" if self.now() >= order.expires_at else "payment-required"
            connection.execute(
                """
                UPDATE orders SET status = ?, settlement_proof_sha256 = NULL
                WHERE order_id = ? AND status = 'processing'
                """,
                (new_status, order_id),
            )
            updated = connection.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        return self._row_to_order(updated)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    skill_id TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    currency TEXT NOT NULL CHECK (currency IN ('USD', 'CNY')),
                    locale TEXT NOT NULL,
                    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
                    idempotency_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL CHECK (status IN (
                        'created', 'payment-required', 'processing', 'paid',
                        'generating', 'delivered', 'failed', 'expired'
                    )),
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    result_body TEXT,
                    result_sha256 TEXT,
                    settlement_proof_sha256 TEXT,
                    result_access_token_sha256 TEXT,
                    payment_response TEXT
                )
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(orders)")}
            if "result_body" not in columns:
                connection.execute("ALTER TABLE orders ADD COLUMN result_body TEXT")
            if "result_sha256" not in columns:
                connection.execute("ALTER TABLE orders ADD COLUMN result_sha256 TEXT")
            if "settlement_proof_sha256" not in columns:
                connection.execute("ALTER TABLE orders ADD COLUMN settlement_proof_sha256 TEXT")
            if "result_access_token_sha256" not in columns:
                connection.execute("ALTER TABLE orders ADD COLUMN result_access_token_sha256 TEXT")
            if "payment_response" not in columns:
                connection.execute("ALTER TABLE orders ADD COLUMN payment_response TEXT")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _order_values(order: Order) -> tuple[object, ...]:
        return (
            order.order_id,
            order.skill_id,
            order.input_hash,
            order.currency,
            order.locale,
            order.amount_minor,
            order.idempotency_key,
            order.status,
            order.created_at.isoformat(),
            order.expires_at.isoformat(),
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value.astimezone(UTC)

    @classmethod
    def _row_to_order(cls, row: sqlite3.Row) -> Order:
        return Order(
            order_id=row["order_id"],
            skill_id=row["skill_id"],
            input_hash=row["input_hash"],
            currency=row["currency"],
            locale=row["locale"],
            amount_minor=row["amount_minor"],
            idempotency_key=row["idempotency_key"],
            status=row["status"],
            created_at=cls._as_utc(datetime.fromisoformat(row["created_at"])),
            expires_at=cls._as_utc(datetime.fromisoformat(row["expires_at"])),
        )

    @staticmethod
    def _assert_same_binding(order: Order, binding: tuple[str, str, str, str]) -> None:
        if (order.input_hash, order.skill_id, order.currency, order.locale) != binding:
            raise IdempotencyConflict("idempotency key is already bound to a different order")

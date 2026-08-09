"""Private result engines and the separately stored buyer-input payload."""

from __future__ import annotations

from abc import ABC, abstractmethod
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import sqlite3
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from gateway.catalog import PRODUCTS, get_product
from gateway.contracts import Order
from gateway.orders import input_hash


class RetryableGenerationError(RuntimeError):
    """A provider failure that may be retried without another payment."""


class RetryablePayloadError(RuntimeError):
    """Protected buyer payload is temporarily unavailable or failed integrity."""


class _NoRedirectHandler(HTTPRedirectHandler):
    """Keep provider credentials and buyer content on the configured origin."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT_OPENER = build_opener(_NoRedirectHandler())


def urlopen(request: Request, timeout: float):
    """Open a provider request without urllib's credential-bearing redirects."""

    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


def _is_loopback_host(hostname: str) -> bool:
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _provider_endpoint(base_url: str) -> str:
    """Validate a provider origin and append the fixed chat-completions path."""

    if not base_url or any(character.isspace() or ord(character) < 32 for character in base_url):
        raise ValueError("invalid model base URL")
    if "\\" in base_url:
        raise ValueError("invalid model base URL")
    try:
        parsed = urlsplit(base_url)
        hostname = parsed.hostname
        parsed.port
    except ValueError as error:
        raise ValueError("invalid model base URL") from error
    if (
        parsed.scheme not in {"https", "http"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (parsed.scheme == "http" and not _is_loopback_host(hostname))
    ):
        raise ValueError("invalid model base URL")
    path_segments = [segment for segment in parsed.path.split("/") if segment]
    if any(segment in {".", ".."} for segment in path_segments):
        raise ValueError("invalid model base URL")
    base_path = "/" + "/".join(path_segments) if path_segments else ""
    endpoint_path = f"{base_path}/chat/completions"
    return urlunsplit((parsed.scheme, parsed.netloc, endpoint_path, "", ""))


class ResultEngine(ABC):
    @abstractmethod
    def generate(self, skill_id: str, input_text: str, locale: str) -> str:
        """Create the finished result from a server-selected skill."""


class BuyerPayloadStore(ABC):
    """Protected raw-input storage, separate from the order ledger.

    The SQLite implementation is a local fixture only. Production deployments
    must replace it with protected, access-controlled, encrypted payload storage.
    """

    @property
    @abstractmethod
    def production_ready(self) -> bool:
        """Whether this store protects raw payloads for production use."""

    @abstractmethod
    def put(self, order: Order, input_text: str) -> None:
        """Persist raw input only when it matches the ledger's normalized hash."""

    @abstractmethod
    def get_verified(self, order: Order) -> str:
        """Return input only after rechecking its normalized ledger hash."""


class SQLiteBuyerPayloadStore(BuyerPayloadStore):
    """Minimal local fixture payload store; never expose this data publicly."""

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = str(database_path)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS buyer_payloads (
                    order_id TEXT PRIMARY KEY,
                    input_text TEXT NOT NULL,
                    normalized_sha256 TEXT NOT NULL
                )
                """
            )

    @property
    def production_ready(self) -> bool:
        return False

    def put(self, order: Order, input_text: str) -> None:
        digest = input_hash(input_text)
        if digest != order.input_hash:
            raise ValueError("payload does not match order input hash")
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT normalized_sha256 FROM buyer_payloads WHERE order_id = ?", (order.order_id,)
            ).fetchone()
            if existing is not None:
                if existing["normalized_sha256"] != digest:
                    raise ValueError("payload already belongs to a different input")
                return
            connection.execute(
                "INSERT INTO buyer_payloads (order_id, input_text, normalized_sha256) VALUES (?, ?, ?)",
                (order.order_id, input_text, digest),
            )

    def get_verified(self, order: Order) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT input_text, normalized_sha256 FROM buyer_payloads WHERE order_id = ?", (order.order_id,)
            ).fetchone()
        if row is None or row["normalized_sha256"] != order.input_hash:
            raise RetryablePayloadError("protected buyer payload is unavailable")
        if input_hash(row["input_text"]) != order.input_hash:
            raise RetryablePayloadError("protected buyer payload failed integrity verification")
        return row["input_text"]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection


class FixtureResultEngine(ResultEngine):
    """Deterministic, no-network engine used only by fixture/test deployments."""

    def generate(self, skill_id: str, input_text: str, locale: str) -> str:
        get_product(skill_id)
        fingerprint = hashlib.sha256(f"{skill_id}\0{locale}\0{input_text}".encode("utf-8")).hexdigest()[:12]
        support = _fixture_support(input_text)
        if skill_id == "outcome-offer":
            return _fixture_outcome_offer(support, fingerprint)
        if skill_id == "proof-pack":
            return _fixture_proof_pack(support, locale, fingerprint)
        return _fixture_reply_to_close(support, locale, fingerprint)


def _fixture_support(input_text: str) -> str:
    """Return a short exact buyer fragment safe for a Markdown table cell."""

    collapsed = " ".join(input_text.split())
    match = re.search(r"[A-Za-z0-9\u3400-\u9fff]{2,}", collapsed)
    if match:
        return match.group(0)
    return collapsed[:2] or "in"


def _fixture_outcome_offer(support: str, fingerprint: str) -> str:
    return f"""## FROM-TO OUTCOME

From supplied context to a named outcome asset.

## PRODUCT NAME

Outcome Offer fixture

## TARGET BUYER

The buyer described in the supplied context.

## BUYING MOMENT

When the supplied context needs a usable sales asset.

## DELIVERABLES

- A clear outcome statement
- A review-ready offer description
- A paste-ready sales block

## RESULT-LED BENEFITS

- Keeps the requested outcome visible
- Gives the buyer a clear starting point
- Creates a review-ready handoff

## RISK REVERSAL

Unknown commercial terms remain assumptions for review.

## HEADLINES

- Make the intended outcome clear
- Turn context into a usable offer
- Start with a review-ready result

## PASTE-READY SALES BLOCK

Turn the supplied context into a review-ready outcome asset without inventing missing facts.

## ASSUMPTIONS AND TRACEABILITY

| Output claim | Input support | Status |
| --- | --- | --- |
| The buyer supplied source context | {support} | Supported |
| Commercial terms | Not supplied | Unverified |

## QUALITY CHECK

The fixture includes every required section, three benefits, and three headlines. Reference {fingerprint}.
"""


def _fixture_proof_pack(support: str, locale: str, fingerprint: str) -> str:
    if locale.casefold().split("-", 1)[0] in {"zh", "ja", "ko"}:
        story = "提供的上下文记录了一个待审阅的情况。此演示只保留来源边界，不添加因果、承诺或商业结论。" * 6
        proposal = "提供的上下文描述了一个待审阅的情况。现有信息没有证明其原因。"
        headline = "把已提供的情况整理成可审阅证据"
        bullet_items = ("保留已提供的事实", "标出尚未提供的证据", "不添加因果或商业结论")
        social = "已提供的情况可以整理为证据，同时保留未验证的边界。"
        sales = "这份证据包只整理已提供的信息，并把缺口留给审阅。"
        missing = "原因、样本与比较条件尚未提供。"
        quality = "提案、案例、证据要点与缺口均已检查。"
    else:
        story = " ".join(
            [
                "The supplied context describes a reported situation. This fixture records the source boundary without adding a cause, promise, or commercial claim."
            ] * 7
        )
        proposal = "The supplied context describes a reported situation. The available information does not establish its cause."
        headline = "Turn supplied context into review-ready evidence"
        bullet_items = ("Records the supplied situation", "Marks evidence that was not supplied", "Avoids causal or commercial claims")
        social = "The supplied situation can be organized as evidence while its unverified boundaries remain visible."
        sales = "This proof pack organizes supplied information and keeps missing evidence open for review."
        missing = "Cause, sample, and comparison conditions were not supplied."
        quality = "The proposal, case story, evidence bullets, and gaps were checked."
    return f"""## PROOF HEADLINE

{headline}

## PROPOSAL BLURB

{proposal}

## CASE STORY

{story}

## EVIDENCE BULLETS

- {bullet_items[0]}
- {bullet_items[1]}
- {bullet_items[2]}

## SOCIAL POST

{social}

## SALES-CONVERSATION VERSION

{sales}

## CLAIM TRACEABILITY

| Output claim | Input support | Status |
| --- | --- | --- |
| The buyer supplied source context | {support} | Supported |
| The evidence has a review boundary | Not supplied | Assumption |

## MISSING EVIDENCE

{missing}

## QUALITY CHECK

{quality} Reference {fingerprint}.
"""


def _fixture_reply_to_close(support: str, locale: str, fingerprint: str) -> str:
    if locale.casefold().split("-", 1)[0] in {"zh", "ja", "ko"}:
        next_step = "请审阅这份成品。"
        primary = f"我已保留你的重点。{next_step}"
        short = next_step
        classification = "需要确认下一步。"
        trace = support
        quality = "回复只推进一个低摩擦下一步，并保留审阅边界。"
    else:
        next_step = "Please review the named result."
        primary = f"I kept the supplied context visible. {next_step}"
        short = next_step
        classification = "A next-step clarification is needed."
        trace = support
        quality = "The replies use one low-friction next step and preserve the review boundary."
    return f"""## COPY-PASTE REPLY

{primary}

## SHORT REPLY

{short}

## OBJECTION CLASSIFICATION

{classification}

## LOW-FRICTION NEXT STEP

{next_step}

## ASSUMPTIONS AND TRACEABILITY

| Reply claim | Input support | Status |
| --- | --- | --- |
| The reply reflects the supplied context | {trace} | Supported |
| The next step is accepted | Not supplied | Assumption |

## QUALITY CHECK

{quality} Reference {fingerprint}.
"""


class OpenAICompatibleResultEngine(ResultEngine):
    """Server-configured OpenAI-compatible client with an explicit timeout."""

    def __init__(
        self, *, base_url: str, api_key: str, model_name: str, prompts_dir: str | Path | None = None, timeout_seconds: float = 15.0
    ) -> None:
        if not base_url or not api_key or not model_name:
            raise ValueError("production model configuration is required")
        if timeout_seconds <= 0:
            raise ValueError("model timeout must be positive")
        if prompts_dir is None:
            raise ValueError("an external private prompt directory is required")
        self._endpoint = _provider_endpoint(base_url)
        self._api_key = api_key
        self._model_name = model_name
        self._prompts = self._load_private_prompts(Path(prompts_dir))
        self._timeout_seconds = timeout_seconds

    def generate(self, skill_id: str, input_text: str, locale: str) -> str:
        prompt = self._load_prompt(skill_id)
        request_body = json.dumps(
            {
                "model": self._model_name,
                "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": f"Locale: {locale}\n\n{input_text}"},
                ],
            }
        ).encode("utf-8")
        request = Request(
            self._endpoint,
            data=request_body,
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
            result = payload["choices"][0]["message"]["content"]
            if not isinstance(result, str) or not result.strip():
                raise ValueError("empty model content")
            return result
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
            raise RetryableGenerationError("model provider request failed") from error

    def _load_prompt(self, skill_id: str) -> str:
        get_product(skill_id)
        return self._prompts[skill_id]

    @staticmethod
    def _load_private_prompts(prompts_dir: Path) -> dict[str, str]:
        prompts: dict[str, str] = {}
        try:
            for skill_id in PRODUCTS:
                prompt = (prompts_dir / f"{skill_id}.md").read_text(encoding="utf-8")
                if not prompt.strip():
                    raise ValueError("private prompt is empty")
                prompts[skill_id] = prompt
        except (OSError, ValueError) as error:
            raise ValueError("external private prompt directory is incomplete") from error
        return prompts

"""Deterministic validation for the public result contracts.

This module contains only structural and fidelity rules that are already part
of the public product contracts. Model instructions remain operator-private.
"""

from __future__ import annotations

import re
import unicodedata


class ResultValidationError(ValueError):
    """A generated artifact does not satisfy its public result contract."""


_HEADINGS: dict[str, tuple[str, ...]] = {
    "outcome-offer": (
        "FROM-TO OUTCOME",
        "PRODUCT NAME",
        "TARGET BUYER",
        "BUYING MOMENT",
        "DELIVERABLES",
        "RESULT-LED BENEFITS",
        "RISK REVERSAL",
        "HEADLINES",
        "PASTE-READY SALES BLOCK",
        "ASSUMPTIONS AND TRACEABILITY",
        "QUALITY CHECK",
    ),
    "proof-pack": (
        "PROOF HEADLINE",
        "PROPOSAL BLURB",
        "CASE STORY",
        "EVIDENCE BULLETS",
        "SOCIAL POST",
        "SALES-CONVERSATION VERSION",
        "CLAIM TRACEABILITY",
        "MISSING EVIDENCE",
        "QUALITY CHECK",
    ),
    "reply-to-close": (
        "COPY-PASTE REPLY",
        "SHORT REPLY",
        "OBJECTION CLASSIFICATION",
        "LOW-FRICTION NEXT STEP",
        "ASSUMPTIONS AND TRACEABILITY",
        "QUALITY CHECK",
    ),
}

_TRACE_TABLES: dict[str, tuple[str, tuple[str, str, str]]] = {
    "outcome-offer": (
        "ASSUMPTIONS AND TRACEABILITY",
        ("Output claim", "Input support", "Status"),
    ),
    "proof-pack": (
        "CLAIM TRACEABILITY",
        ("Output claim", "Input support", "Status"),
    ),
    "reply-to-close": (
        "ASSUMPTIONS AND TRACEABILITY",
        ("Reply claim", "Input support", "Status"),
    ),
}

_TRACE_STATUSES = frozenset({"Supported", "Derived", "Assumption", "Unverified"})
_H2 = re.compile(r"^##[ \t]+([^\r\n]+?)[ \t]*$", re.MULTILINE)
_LIST_ITEM = re.compile(r"^[ \t]{0,3}(?:[-+*]|\d+[.)])[ \t]+\S", re.MULTILINE)
_ORDERED_LIST_PREFIX = re.compile(r"^[ \t]{0,3}\d+[.)][ \t]+", re.MULTILINE)
_WORD = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*", re.UNICODE)
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]{2,}")
_NUMERIC = re.compile(
    r"(?<![\w])(?:[$€£¥￥][ \t]*)?[+-]?"
    r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[ \t]?[%％])?(?![\w])"
)
_TABLE_SEPARATOR = re.compile(r"^:?-{3,}:?$")
_QUOTE_PATTERNS = (
    re.compile(r'(?<!\\)"([^"\r\n]+)(?<!\\)"'),
    re.compile(r"\u201c([^\u201d\r\n]+)\u201d"),
    re.compile(r"\u300c([^\u300d\r\n]+)\u300d"),
    re.compile(r"\u300e([^\u300f\r\n]+)\u300f"),
    re.compile(r"(?<![\w])'([^'\r\n]+)'(?![\w])"),
)
_CJK_LOCALES = frozenset({"zh", "ja", "ko"})
_CITATION_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "not",
        "of",
        "on",
        "or",
        "the",
        "this",
        "to",
        "was",
        "were",
        "with",
    }
)


def validate_result(skill_id: str, input_text: str, locale: str, body: str) -> None:
    """Validate one generated Markdown artifact or raise ``ResultValidationError``."""

    if skill_id not in _HEADINGS:
        raise ResultValidationError(f"unknown skill id: {skill_id!r}")
    if not isinstance(input_text, str) or not input_text.strip():
        raise ResultValidationError("buyer input must be a nonempty string")
    if not isinstance(locale, str) or not locale.strip():
        raise ResultValidationError("locale must be a nonempty string")
    if not isinstance(body, str) or not body.strip():
        raise ResultValidationError("result body must be a nonempty string")

    sections = _parse_sections(body, _HEADINGS[skill_id])

    if skill_id == "outcome-offer":
        _require_three_items(sections["RESULT-LED BENEFITS"], "result-led benefits")
        _require_three_items(sections["HEADLINES"], "headlines")
    elif skill_id == "proof-pack":
        _validate_proof_pack(sections, locale)
    else:
        _validate_reply_to_close(sections, locale)

    trace_heading, trace_columns = _TRACE_TABLES[skill_id]
    _validate_trace_table(sections[trace_heading], trace_columns, input_text)
    _validate_numeric_fidelity(sections, input_text)
    _validate_quotation_fidelity(body, input_text)


def _parse_sections(body: str, expected: tuple[str, ...]) -> dict[str, str]:
    matches = list(_H2.finditer(body))
    if not matches:
        raise ResultValidationError("result heading contract is missing")
    if body[: matches[0].start()].strip():
        raise ResultValidationError("result must not contain a preamble before its first heading")

    actual = tuple(match.group(1).strip() for match in matches)
    if actual != expected:
        raise ResultValidationError(
            "result headings must exactly match the required H2 set and order"
        )

    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        content = body[match.end() : end].strip()
        if not content:
            raise ResultValidationError(f"required section {actual[index]!r} is empty")
        sections[actual[index]] = content
    return sections


def _require_three_items(section: str, label: str) -> None:
    if len(_LIST_ITEM.findall(section)) != 3:
        raise ResultValidationError(f"{label} must contain exactly three list items")


def _validate_proof_pack(sections: dict[str, str], locale: str) -> None:
    if _sentence_count(sections["PROPOSAL BLURB"]) != 2:
        raise ResultValidationError("proposal blurb must contain exactly two sentences")

    story = sections["CASE STORY"]
    if _uses_cjk_units(locale, story):
        units = _cjk_units(story)
        if not 120 <= units <= 360:
            raise ResultValidationError(
                "CJK case story must contain 120 to 360 locale-equivalent characters"
            )
    else:
        words = _word_count(story)
        if not 120 <= words <= 180:
            raise ResultValidationError("case story must contain 120 to 180 words")

    _require_three_items(sections["EVIDENCE BULLETS"], "evidence bullets")


def _validate_reply_to_close(sections: dict[str, str], locale: str) -> None:
    primary = sections["COPY-PASTE REPLY"]
    short = sections["SHORT REPLY"]
    if _uses_cjk_units(locale, primary):
        if _cjk_units(primary) > 180:
            raise ResultValidationError(
                "copy-paste reply must contain at most 180 locale-equivalent characters"
            )
    elif _word_count(primary) > 90:
        raise ResultValidationError("copy-paste reply must contain at most 90 words")

    if _uses_cjk_units(locale, short):
        if _cjk_units(short) > 80:
            raise ResultValidationError(
                "short reply must contain at most 80 locale-equivalent characters"
            )
    elif _word_count(short) > 40:
        raise ResultValidationError("short reply must contain at most 40 words")

    next_step = _plain_next_step(sections["LOW-FRICTION NEXT STEP"])
    if not next_step:
        raise ResultValidationError("low-friction next step must not be empty")
    if next_step not in _collapse_whitespace(primary) or next_step not in _collapse_whitespace(short):
        raise ResultValidationError(
            "the exact low-friction next step must appear in both replies"
        )


def _sentence_count(text: str) -> int:
    collapsed = _collapse_whitespace(text)
    parts = re.split(r"(?<=[。！？])|(?<=[.!?])(?=\s|$)", collapsed)
    return sum(bool(part.strip()) for part in parts)


def _word_count(text: str) -> int:
    return len(_WORD.findall(text))


def _uses_cjk_units(locale: str, text: str) -> bool:
    language = locale.casefold().replace("_", "-").split("-", 1)[0]
    return language in _CJK_LOCALES or len(_CJK.findall(text)) >= 20


def _cjk_units(text: str) -> int:
    return sum(
        1
        for character in text
        if not character.isspace()
        and not unicodedata.category(character).startswith(("P", "S"))
    )


def _plain_next_step(section: str) -> str:
    text = section.strip()
    match = re.fullmatch(r"[ \t]{0,3}(?:[-+*]|\d+[.)])[ \t]+(.+)", text, re.DOTALL)
    if match:
        text = match.group(1)
    return _collapse_whitespace(text)


def _validate_trace_table(
    section: str,
    expected_columns: tuple[str, str, str],
    input_text: str,
) -> None:
    lines = [line.strip() for line in section.splitlines()]
    table_start = next(
        (index for index, line in enumerate(lines) if line.startswith("|") and line.endswith("|")),
        None,
    )
    if table_start is None:
        raise ResultValidationError("traceability section requires a Markdown table")

    table_lines: list[str] = []
    for line in lines[table_start:]:
        if not line.startswith("|") or not line.endswith("|"):
            if table_lines:
                break
            continue
        table_lines.append(line)
    if len(table_lines) < 3:
        raise ResultValidationError("traceability table requires columns and at least one row")

    header = _table_cells(table_lines[0])
    if header != expected_columns:
        raise ResultValidationError(
            f"traceability table columns must be exactly: {' | '.join(expected_columns)}"
        )
    separator = _table_cells(table_lines[1])
    if len(separator) != 3 or not all(_TABLE_SEPARATOR.fullmatch(cell) for cell in separator):
        raise ResultValidationError("traceability table separator is invalid")

    for line in table_lines[2:]:
        cells = _table_cells(line)
        if len(cells) != 3 or any(not cell for cell in cells):
            raise ResultValidationError("traceability table rows must contain three nonempty cells")
        status = cells[2]
        if status not in _TRACE_STATUSES:
            raise ResultValidationError(
                "traceability status must be exactly Supported, Derived, Assumption, or Unverified"
            )
        if status == "Supported" and not _cites_input(cells[1], input_text):
            raise ResultValidationError(
                "Supported trace row must cite a recognizable exact input fragment or token"
            )


def _table_cells(line: str) -> tuple[str, ...]:
    return tuple(cell.strip() for cell in line.strip()[1:-1].split("|"))


def _cites_input(support: str, input_text: str) -> bool:
    support_plain = _strip_inline_markdown(_collapse_whitespace(support))
    input_plain = _collapse_whitespace(input_text)
    if len(support_plain) >= 2 and support_plain.casefold() in input_plain.casefold():
        return True

    support_numbers = set(_numeric_tokens(support_plain))
    if support_numbers.intersection(_numeric_tokens(input_plain)):
        return True

    input_words = {word.casefold() for word in _WORD.findall(input_plain)}
    for word in _WORD.findall(support_plain):
        normalized = word.casefold()
        if (
            len(normalized) >= 3
            and normalized not in _CITATION_STOPWORDS
            and normalized in input_words
        ):
            return True

    for run in _CJK_RUN.findall(support_plain):
        if run in input_plain:
            return True
        if any(run[index : index + 2] in input_plain for index in range(len(run) - 1)):
            return True
    return False


def _strip_inline_markdown(text: str) -> str:
    return re.sub(r"[`*_~]", "", text).strip()


def _validate_numeric_fidelity(sections: dict[str, str], input_text: str) -> None:
    checked = "\n".join(
        content for heading, content in sections.items() if heading != "QUALITY CHECK"
    )
    checked = _ORDERED_LIST_PREFIX.sub("", checked)
    input_numbers = set(_numeric_tokens(input_text))
    if any(token not in input_numbers for token in _numeric_tokens(checked)):
        raise ResultValidationError(
            "result contains a numeric token that is absent from the buyer input"
        )


def _numeric_tokens(text: str) -> tuple[str, ...]:
    return tuple(match.group(0).strip() for match in _NUMERIC.finditer(text))


def _validate_quotation_fidelity(body: str, input_text: str) -> None:
    for pattern in _QUOTE_PATTERNS:
        for match in pattern.finditer(body):
            quoted = match.group(1)
            if quoted not in input_text:
                raise ResultValidationError(
                    "result contains quoted wording that is absent from the buyer input"
                )


def _collapse_whitespace(text: str) -> str:
    return " ".join(text.split())

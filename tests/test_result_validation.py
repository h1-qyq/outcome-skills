from __future__ import annotations

import importlib
import re

import pytest


def validator_api():
    try:
        module = importlib.import_module("gateway.result_validation")
    except ModuleNotFoundError:
        pytest.fail("gateway.result_validation is missing")
    return module.validate_result, module.ResultValidationError


def replace_section(body: str, heading: str, content: str) -> str:
    pattern = rf"(?ms)(^## {re.escape(heading)}\n).*?(?=^## |\Z)"
    replacement = rf"\g<1>\n{content.strip()}\n\n"
    replaced, count = re.subn(pattern, replacement, body)
    assert count == 1
    return replaced.rstrip() + "\n"


def valid_outcome_offer() -> tuple[str, str]:
    input_text = (
        "Independent consultants need a client onboarding kit delivered in two days. "
        "They want a clear scope for new client work."
    )
    body = """## FROM-TO OUTCOME

From scattered onboarding material to a clear client onboarding kit.

## PRODUCT NAME

Client Onboarding Kit

## TARGET BUYER

Independent consultants.

## BUYING MOMENT

When new client work needs a clear scope.

## DELIVERABLES

- Onboarding workflow
- Client information checklist
- Handoff guide

## RESULT-LED BENEFITS

- Keeps the onboarding scope visible
- Gives new client work a consistent starting point
- Creates a review-ready handoff

## RISK REVERSAL

Unknown commercial terms remain assumptions for review.

## HEADLINES

- A Clear Start for Every New Client
- Put the Onboarding Scope in One Place
- A Review-Ready Client Handoff

## PASTE-READY SALES BLOCK

Give new client work a clear starting point with a review-ready onboarding kit.

## ASSUMPTIONS AND TRACEABILITY

| Output claim | Input support | Status |
| --- | --- | --- |
| The buyer needs a client onboarding kit | client onboarding kit | Supported |
| Commercial terms | Not supplied | Unverified |

## QUALITY CHECK

All required slots are present; three benefits and three headlines are included.
"""
    return input_text, body


def valid_proof_pack(*, case_story: str | None = None) -> tuple[str, str]:
    input_text = (
        "Northwind reported response time fell from 18 hours to 9 hours over six weeks. "
        "The cause and sample size were not supplied."
    )
    if case_story is None:
        case_story = " ".join(
            ["Northwind reported response time progress over six weeks."] * 15
        )
    body = f"""## PROOF HEADLINE

Northwind reported faster response time over six weeks.

## PROPOSAL BLURB

Northwind reported a response-time change over six weeks. The available input does not establish its cause.

## CASE STORY

{case_story}

## EVIDENCE BULLETS

- Response time began at 18 hours
- Response time later measured 9 hours
- The reported period was six weeks

## SOCIAL POST

Northwind reported response-time progress over six weeks, with cause and sample size still unverified.

## SALES-CONVERSATION VERSION

The supplied result reports a response-time change over six weeks while leaving causation open.

## CLAIM TRACEABILITY

| Output claim | Input support | Status |
| --- | --- | --- |
| Response time fell | response time fell from 18 hours to 9 hours | Supported |
| The change is described as progress | response time fell | Derived |
| The cause | Not supplied | Unverified |

## MISSING EVIDENCE

The cause, sample size, and comparison conditions were not supplied.

## QUALITY CHECK

The proposal has two sentences, the story has 120 words, and there are three evidence bullets.
"""
    return input_text, body


def valid_reply_to_close() -> tuple[str, str]:
    input_text = (
        "A prospect said the monthly plan costs $49 and asked to revisit next quarter."
    )
    next_step = "Would you like a one-page scope?"
    body = f"""## COPY-PASTE REPLY

I understand the timing and the monthly plan cost. {next_step}

## SHORT REPLY

Understood on timing. {next_step}

## OBJECTION CLASSIFICATION

Price and timing concern.

## LOW-FRICTION NEXT STEP

{next_step}

## ASSUMPTIONS AND TRACEABILITY

| Reply claim | Input support | Status |
| --- | --- | --- |
| The monthly plan costs $49 | monthly plan costs $49 | Supported |
| A one-page scope is available | Not supplied | Assumption |

## QUALITY CHECK

The primary reply is under 90 words, the short reply is under 40 words, and both use one next step.
"""
    return input_text, body


@pytest.mark.parametrize(
    ("skill_id", "factory", "locale"),
    [
        ("outcome-offer", valid_outcome_offer, "en-US"),
        ("proof-pack", valid_proof_pack, "en-US"),
        ("reply-to-close", valid_reply_to_close, "en-US"),
    ],
)
def test_accepts_each_complete_result_contract(skill_id, factory, locale):
    validate_result, _ = validator_api()
    input_text, body = factory()

    assert validate_result(skill_id, input_text, locale, body) is None


def test_rejects_unknown_skill_with_typed_error():
    validate_result, error_type = validator_api()

    with pytest.raises(error_type, match="unknown skill"):
        validate_result("fourth-product", "input", "en-US", "## RESULT\n\nbody")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda body: "Here is the finished result.\n\n" + body,
        lambda body: body.replace("## TARGET BUYER", "## BUYER"),
        lambda body: body.replace("## TARGET BUYER", "## TEMP", 1)
        .replace("## BUYING MOMENT", "## TARGET BUYER", 1)
        .replace("## TEMP", "## BUYING MOMENT", 1),
        lambda body: body + "\n## EXTRA SECTION\n\nUnexpected.\n",
    ],
)
def test_rejects_preamble_or_nonexact_heading_contract(mutate):
    validate_result, error_type = validator_api()
    input_text, body = valid_outcome_offer()

    with pytest.raises(error_type, match="heading|preamble"):
        validate_result("outcome-offer", input_text, "en-US", mutate(body))


def test_rejects_an_empty_required_section():
    validate_result, error_type = validator_api()
    input_text, body = valid_outcome_offer()
    body = replace_section(body, "PRODUCT NAME", "   ")

    with pytest.raises(error_type, match="empty"):
        validate_result("outcome-offer", input_text, "en-US", body)


@pytest.mark.parametrize("heading", ["RESULT-LED BENEFITS", "HEADLINES"])
@pytest.mark.parametrize("count", [2, 4])
def test_outcome_offer_requires_exactly_three_list_items(heading, count):
    validate_result, error_type = validator_api()
    input_text, body = valid_outcome_offer()
    items = "\n".join(f"- Supported item {letter}" for letter in "ABCD"[:count])
    body = replace_section(body, heading, items)

    with pytest.raises(error_type, match="exactly three"):
        validate_result("outcome-offer", input_text, "en-US", body)


@pytest.mark.parametrize(
    "proposal",
    [
        "Only one sentence is present.",
        "Sentence one. Sentence two. Sentence three.",
    ],
)
def test_proof_pack_proposal_requires_exactly_two_sentences(proposal):
    validate_result, error_type = validator_api()
    input_text, body = valid_proof_pack()
    body = replace_section(body, "PROPOSAL BLURB", proposal)

    with pytest.raises(error_type, match="two sentences"):
        validate_result("proof-pack", input_text, "en-US", body)


@pytest.mark.parametrize("word_count", [119, 181])
def test_proof_pack_enforces_english_case_story_word_range(word_count):
    validate_result, error_type = validator_api()
    input_text, body = valid_proof_pack(case_story=" ".join(["evidence"] * word_count))

    with pytest.raises(error_type, match="120.*180"):
        validate_result("proof-pack", input_text, "en-US", body)


def test_proof_pack_accepts_a_concise_cjk_case_story():
    validate_result, _ = validator_api()
    input_text, body = valid_proof_pack(
        case_story="这是已验证的案例内容。" * 16,
    )

    assert validate_result("proof-pack", input_text, "zh-CN", body) is None


@pytest.mark.parametrize("count", [2, 4])
def test_proof_pack_requires_exactly_three_evidence_bullets(count):
    validate_result, error_type = validator_api()
    input_text, body = valid_proof_pack()
    bullets = "\n".join(f"- Evidence item {letter}" for letter in "ABCD"[:count])
    body = replace_section(body, "EVIDENCE BULLETS", bullets)

    with pytest.raises(error_type, match="exactly three"):
        validate_result("proof-pack", input_text, "en-US", body)


@pytest.mark.parametrize(
    ("skill_id", "factory", "heading", "bad_header"),
    [
        (
            "outcome-offer",
            valid_outcome_offer,
            "ASSUMPTIONS AND TRACEABILITY",
            "| Claim | Evidence | Status |",
        ),
        (
            "proof-pack",
            valid_proof_pack,
            "CLAIM TRACEABILITY",
            "| Claim | Input support | Status |",
        ),
        (
            "reply-to-close",
            valid_reply_to_close,
            "ASSUMPTIONS AND TRACEABILITY",
            "| Output claim | Input support | Status |",
        ),
    ],
)
def test_traceability_tables_require_exact_columns(skill_id, factory, heading, bad_header):
    validate_result, error_type = validator_api()
    input_text, body = factory()
    section_pattern = rf"(?ms)(^## {re.escape(heading)}\n\n)\|[^\n]+\|"
    body, count = re.subn(section_pattern, rf"\g<1>{bad_header}", body, count=1)
    assert count == 1

    with pytest.raises(error_type, match="columns"):
        validate_result(skill_id, input_text, "en-US", body)


@pytest.mark.parametrize(
    ("skill_id", "factory", "old_status"),
    [
        ("outcome-offer", valid_outcome_offer, "Supported"),
        ("proof-pack", valid_proof_pack, "Derived"),
        ("reply-to-close", valid_reply_to_close, "Assumption"),
    ],
)
def test_traceability_rejects_any_status_outside_the_exact_vocabulary(
    skill_id, factory, old_status
):
    validate_result, error_type = validator_api()
    input_text, body = factory()
    body = body.replace(f"| {old_status} |", "| Plausible |", 1)

    with pytest.raises(error_type, match="status"):
        validate_result(skill_id, input_text, "en-US", body)


@pytest.mark.parametrize(
    ("heading", "limit", "word_count"),
    [
        ("COPY-PASTE REPLY", 90, 91),
        ("SHORT REPLY", 40, 41),
    ],
)
def test_reply_to_close_enforces_english_reply_limits(heading, limit, word_count):
    validate_result, error_type = validator_api()
    input_text, body = valid_reply_to_close()
    body = replace_section(body, heading, " ".join(["word"] * word_count))

    with pytest.raises(error_type, match=str(limit)):
        validate_result("reply-to-close", input_text, "en-US", body)


def test_reply_to_close_accepts_locale_equivalent_cjk_lengths():
    validate_result, _ = validator_api()
    input_text = "客户说价格偏高，希望下季度再看。"
    next_step = "可以把需求清单发给我吗？"
    _, body = valid_reply_to_close()
    body = replace_section(body, "COPY-PASTE REPLY", "理解你对价格和时间的顾虑。" + next_step)
    body = replace_section(body, "SHORT REPLY", "理解。" + next_step)
    body = replace_section(body, "LOW-FRICTION NEXT STEP", next_step)
    body = replace_section(
        body,
        "ASSUMPTIONS AND TRACEABILITY",
        """| Reply claim | Input support | Status |
| --- | --- | --- |
| 客户认为价格偏高 | 客户说价格偏高 | Supported |
| 可提供需求清单 | 输入未提供 | Assumption |""",
    )

    assert validate_result("reply-to-close", input_text, "zh-CN", body) is None


def test_reply_to_close_requires_the_exact_next_step_in_both_replies():
    validate_result, error_type = validator_api()
    input_text, body = valid_reply_to_close()
    body = body.replace(
        "Understood on timing. Would you like a one-page scope?",
        "Understood on timing. Would you like a short summary?",
    )

    with pytest.raises(error_type, match="next step"):
        validate_result("reply-to-close", input_text, "en-US", body)


def test_global_fidelity_rejects_a_numeric_token_absent_from_buyer_input():
    validate_result, error_type = validator_api()
    input_text, body = valid_outcome_offer()
    body = replace_section(body, "PRODUCT NAME", "Client Onboarding Kit 5000")

    with pytest.raises(error_type, match="numeric"):
        validate_result("outcome-offer", input_text, "en-US", body)


def test_global_fidelity_ignores_markdown_list_numbers_and_quality_check_numbers():
    validate_result, _ = validator_api()
    input_text, body = valid_outcome_offer()
    body = replace_section(
        body,
        "RESULT-LED BENEFITS",
        "1. Keeps the scope visible\n2. Gives work a starting point\n3. Creates a handoff",
    )
    body = replace_section(
        body,
        "HEADLINES",
        "1. A Clear Start\n2. One Place for the Scope\n3. A Review-Ready Handoff",
    )
    body = replace_section(
        body,
        "QUALITY CHECK",
        "All 11 required sections are present with 3 benefits and 3 headlines.",
    )

    assert validate_result("outcome-offer", input_text, "en-US", body) is None


def test_global_fidelity_rejects_quoted_wording_absent_from_buyer_input():
    validate_result, error_type = validator_api()
    input_text, body = valid_outcome_offer()
    body = replace_section(
        body,
        "PASTE-READY SALES BLOCK",
        'Give new clients a "guaranteed revenue lift" with this onboarding kit.',
    )

    with pytest.raises(error_type, match="quoted"):
        validate_result("outcome-offer", input_text, "en-US", body)


def test_global_fidelity_accepts_exact_buyer_wording_inside_quotes():
    validate_result, _ = validator_api()
    input_text, body = valid_outcome_offer()
    body = replace_section(
        body,
        "PASTE-READY SALES BLOCK",
        'Build the supplied "client onboarding kit" for independent consultants.',
    )

    assert validate_result("outcome-offer", input_text, "en-US", body) is None


def test_supported_trace_row_must_cite_recognizable_exact_input():
    validate_result, error_type = validator_api()
    input_text, body = valid_proof_pack()
    body = body.replace(
        "response time fell from 18 hours to 9 hours | Supported",
        "an unrelated market assertion | Supported",
    )

    with pytest.raises(error_type, match="Supported.*input"):
        validate_result("proof-pack", input_text, "en-US", body)

# Contributing

Contributions that improve the three defined deliverables, payment safety, host
portability, tests, or documentation are welcome.

## Set up and verify

Use Python 3.11 or newer:

```text
python -m pip install -e . pytest
python -m pytest -q
python scripts/validate_repo.py
git diff --check
```

Add a focused failing test before changing behavior, then make the smallest
coherent change that passes the focused and full suites.

## Keep the public contract narrow

- Preserve exactly the three public Skills: `outcome-offer`, `proof-pack`, and
  `reply-to-close`.
- Keep each Skill directory complete, including its client, references, and
  optional Codex metadata.
- Preserve server-owned product names and fixed prices: USD 1 cent and CNY
  6 fen.
- Promise the named artifact, never revenue, conversion, bookings, or a closed
  sale.
- Keep demo mode explicitly no-money and keep both live rails fail-closed until
  their activation evidence is supplied.
- Do not invent a ClawTip REST endpoint, webhook, payer URL, or automatic
  polling behavior.
- Keep English and Simplified Chinese copy aligned in price, availability, and
  limitations.

## Protect sensitive data

Never commit issued credentials, private keys, wallet keys, signed payment
proofs, SM4 keys, buyer inputs, order databases, or local payment records.
Configuration examples must be visibly unusable. If a test needs sensitive-
shaped data, use a clearly fake value contained entirely in the test fixture.

## Pull requests

Describe the user-visible contract, tests run, and any environment limitation.
Keep unrelated changes out of the patch. A change is ready for review when the
full test suite, repository validator, and diff check pass from a clean tree.

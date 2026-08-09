# Outcome Skills

[English](README.md) | [简体中文](README.zh-CN.md)

One brief in. One finished result out.

Free public test: get one useful sales asset you can use immediately.
No prompt engineering. No subscription.

This repository contains three portable Skills for turning sales context into a
defined, reviewable deliverable, plus a small gateway and a loopback demo. It
does not provide a hosted public endpoint.

## The three Skills

The public surface is intentionally limited to three independent Skills:

| Skill | Use it for | The gateway returns |
| --- | --- | --- |
| `outcome-offer` | Rough offer context | An offer card |
| `proof-pack` | Verified proof context | A proof pack |
| `reply-to-close` | One inbound objection | A next-step reply |

The offer card contains a from-to outcome, product name, buyer, buying moment,
deliverables, benefits, risk reversal, headlines, and a paste-ready sales block.
The Proof Pack contains a headline, proposal blurb, case story, three evidence
bullets, social copy, sales-conversation copy, traceability, missing evidence,
and a quality check. Reply to Close contains a primary reply, a short
alternative, one objection classification, one next step, assumptions and
traceability, and a quality check.

The server owns the result contracts. Missing facts remain marked as unknown or
assumptions; the Skills do not promise revenue, conversion, meetings, purchases,
or closed sales. The three `examples/` files come from controlled fixtures, not
live customer results.

## Current status

This release is a free public test. The included demo does not charge money and
does not require a wallet, merchant account, payment proof, or payment
credential. USD and CNY payment adapters remain dormant seams for future
operator work; CNY/JD onboarding is not configured.

This repository has no hosted gateway URL. A real deployment needs an
operator-owned composition root, a protected payload store, a result engine, and
deployment secrets. The local SQLite payload store and fixture result engine are
not production infrastructure. See [docs/deploy.md](docs/deploy.md) and
[docs/activate-live-payments.md](docs/activate-live-payments.md).

## Install a Skill

Requires Python 3.11 or newer.

```powershell
python -m pip install -e .
python scripts/install.py --target codex --scope project --dry-run
python scripts/install.py --target codex --scope project
```

The installer copies complete Skill directories. Use `--skill` to select one
Skill, and repeat it to select more than one. The installer leaves same-name
directories alone unless you supply `--force`; run `--dry-run` before a forced
replacement.

Supported installer targets and destination conventions are:

| Target | Project scope | User scope |
| --- | --- | --- |
| `codex` | `.agents/skills` | `~/.agents/skills` |
| `joycode` | `.joycode/skills` | `~/.joycode/skills` |
| `openclaw` | `skills/` | `~/.openclaw/skills` |

These are installer destinations. Each host decides whether it discovers a
directory. The repository's `skills/` tree is the installation
source of truth; copy the whole Skill directory, including its client and
references.

Examples:

```powershell
python scripts/install.py --target joycode --scope user --dry-run
python scripts/install.py --target openclaw --scope project `
  --project-root C:\workspace --dry-run
python scripts/install.py --target codex --scope project --skill proof-pack
```

Tagged `v*` releases publish one archive for each Skill through
[the release workflow](.github/workflows/release.yml).

## Run the local free demo

The demo binds to loopback, uses temporary storage outside the checkout, and
stops when the process exits. It uses the deterministic fixture result engine;
it does not call a model or move money.

In terminal 1:

```powershell
python scripts/run_demo_gateway.py --port 8000
```

In terminal 2, keep buyer input on UTF-8 stdin and set the loopback gateway URL:

```powershell
$env:OUTCOMES_GATEWAY_URL = "http://127.0.0.1:8000"
Get-Content -Raw -Encoding utf8 .\buyer-input.txt |
  python skills\outcome-offer\scripts\client.py quote --input-stdin `
    --currency USD --locale en-US --idempotency-key "demo-001"
python skills\outcome-offer\scripts\client.py status --order-id "<ORDER_ID>"
python skills\outcome-offer\scripts\client.py fulfill --order-id "<ORDER_ID>"
```

`quote` prints an access mode and order id. The client stores the access token in
private local state and sends it on later status/result requests; the token is
not a public artifact. The fixture engine generates the returned result. For a
deployed gateway, clients require an exact HTTPS origin,
reject credentials, queries, fragments, and redirects, and still read private
input from stdin. See [docs/deploy.md](docs/deploy.md) for the operator
preflight.

The three controlled examples are here:

- [Outcome Offer](examples/outcome-offer.md)
- [Proof Pack](examples/proof-pack.md)
- [Reply to Close](examples/reply-to-close.md)

## Development and tests

Install the project and test dependency, then run the repository checks:

```powershell
python -m pip install -e . pytest
python -m pytest -q
python scripts/validate_repo.py
```

The validator checks the exact three-Skill surface, portable files, examples,
release copy, plugin manifest, and secret/path hygiene without making network
requests. `git diff --check` adds a useful local check for whitespace errors.

The repository does not include a precomposed production ASGI command. Read
[docs/deploy.md](docs/deploy.md) before composing one; do not run the demo as a
production service.

## Repository map

- `skills/` — the three installable Skills and their clients/references
- `gateway/` — catalog, order state, result access, and payment adapter seams
- `scripts/` — installer, loopback demo runner, and repository validator
- `examples/` and `evals/` — controlled fixtures and evaluation material
- `tests/` — API, client, payment-contract, installation, and validation tests
- `docs/` — deployment and future payment notes

## Security, contribution, and license

Do not commit credentials, wallet keys, payment proofs, buyer input, or result
access tokens. `.env.example` contains placeholders. Review
[SECURITY.md](SECURITY.md) before operating a gateway, and use
[CONTRIBUTING.md](CONTRIBUTING.md) for changes.

This project uses the [MIT License](LICENSE). See also the
[Code of Conduct](CODE_OF_CONDUCT.md).


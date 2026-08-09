# Security Policy

## Supported code

Security fixes are applied to the current default branch. This project does not
currently promise maintenance windows or response-time commitments for older
revisions.

## Report a vulnerability

If GitHub private vulnerability reporting is enabled for this repository, use
the repository's **Security → Report a vulnerability** flow. Include affected
versions, reproduction conditions, impact, and a minimal safe proof of concept.

If that private flow is unavailable, open a public issue that asks the
maintainers to establish a private contact path. Do **not** include exploit
details, credentials, payment proofs, buyer inputs, wallet material, SM4 keys,
or other sensitive data in that issue.

The maintainers will assess reports as capacity permits. This policy does not
promise a private email address, bounty, embargo period, or response SLA.

## Sensitive boundaries

- Demo mode must remain non-production and must never move money.
- A Base mainnet route must remain unavailable without a receiver and an
  authenticated, integration-tested CDP facilitator provider.
- Live CNY collection must remain unavailable without human JD ClawTip merchant
  onboarding, issued `pay_to` and `sm4_key` values, and confirmed official
  sandbox interoperability.
- ClawTip unlocks only after the returned credential decrypts to the same
  order, receiver, amount, and exact `SUCCESS` status.
- Private keys, auth-provider details, SM4 keys, raw payment credentials,
  payment proofs, and buyer inputs must not appear in public responses or logs.
- The local SQLite buyer-payload store is a test/development fixture and is
  rejected by production application construction.

Before reporting expected fail-closed behavior as a vulnerability, compare the
observed behavior with [docs/payments.md](docs/payments.md) and
[docs/activate-live-payments.md](docs/activate-live-payments.md).

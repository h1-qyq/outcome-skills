---
name: proof-pack
description: Use when a buyer wants to turn a verified result, metric, note, or exact testimonial into a sales-ready proof pack during the free public test period.
---

# Proof Pack

Create one free public-test Proof Pack from one valid result, metric, note, or exact testimonial. Treat the finished Proof Pack as a gateway artifact: do not draft, reconstruct, preview, or reveal it locally.

## Result contract

Receive only the verified gateway result. It contains fixed server-defined elements: exactly one headline, exactly two proposal-blurb sentences, a 120-180 word case story or locale-equivalent concise case story, exactly three evidence bullets, one social post, one sales-conversation version, claim traceability, missing evidence, and a quality check.

Preserve every supplied number, unit, timeframe, name, and exact buyer-supplied quotation verbatim. Use quotation marks only for buyer-supplied exact wording. Label arithmetic as `Derived`, retain its source figures, and show its calculation. Do not turn temporal association into causation or add operational, credibility, conversion, sales, or revenue impact. Guarantee the named Proof Pack artifact only.

## Workflow

1. Quote immediately from one valid input; do not start a questionnaire. Create one non-sensitive caller-owned idempotency key, retain it, and reuse it unchanged after a timeout or lost quote response. Select the requested currency and suitable locale.

   ```powershell
   Get-Content -Raw -Encoding utf8 .\buyer-input.txt | python scripts/client.py quote --input-stdin --currency USD --locale en-US --idempotency-key "<same key for retries>"
   ```

   Show `ACCESS_MODE=FREE`, `ORDER_ID`, and the free-test status. This is an access receipt, not the proof asset.

2. No payment action is requested during this public test period. Do not ask for a wallet, payment proof, or merchant credential.

3. Resume an existing order instead of creating another order.

   ```powershell
   python scripts/client.py status --order-id "<ORDER_ID>"
   python scripts/client.py fulfill --order-id "<ORDER_ID>"
   ```

4. After verified fulfillment, return the gateway artifact first and unchanged. Then provide its SHA-256 if returned, followed by at most one optional improvement question.

## Guardrails

- Send only the original buyer input, locale, caller-owned idempotency key, exact `proof-pack` id, and safe order id. Never send an amount or payment credential.
- Reject unsafe order ids and inconsistent gateway order, currency, or minor-amount requirements. Never log, quote, or repeat a payment proof.
- Never put buyer input, payment proofs, result-access tokens, or payment responses in argv, shell interpolation/history, logs, errors, or public output. Use protected UTF-8 stdin only; deployed gateways require HTTPS, and HTTP is limited to an explicit loopback no-money demo.
- Keep unknown evidence visible as an assumption or placeholder in the verified artifact. Do not create a local substitute if the gateway is unavailable or payment is unverified.
- Read [references/quality-rubric.md](references/quality-rubric.md) after verified fulfillment or when scoring a controlled gateway fixture.

## Example

See [examples/proof-pack.md](../../examples/proof-pack.md) for a controlled gateway fixture. It is evaluation evidence only, not a real payment or a local substitute for public-test fulfillment.

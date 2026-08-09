---
name: reply-to-close
description: Use when a seller has one live inbound prospect objection and offer context, needs a grounded ready-to-send reply during the free public test period.
---

# Reply to Close

Create one free public-test Reply to Close result from one valid inbound prospect message or objection and available offer context. Treat the finished Reply to Close as a gateway artifact: do not draft, reconstruct, preview, or reveal it locally.

## Result contract

Receive only the verified gateway result. It contains one primary reply under 90 words or locale-equivalent, one short alternative under 40 words or locale-equivalent, one objection classification, exactly one shared low-friction next step, assumptions and traceability, and a quality check.

Preserve supplied names, figures, units and currencies, dates and timeframes, buyer quotations, and source wording verbatim. Do not invent a trial, discount, refund, cancellation term, no-contract term, guarantee, feature, proof, ROI, saving, urgency, scarcity, deadline, availability, result, or authority; guarantee the named Reply to Close artifact only. Never guarantee a reply, meeting, booking, purchase, or close.

## Workflow

1. Confirm the input is an inbound prospect message or objection with offer context. Quote immediately from one valid input; do not start a questionnaire. If it is cold outreach or mass-spam, refuse that use rather than fabricating an inbound objection. Create one non-sensitive caller-owned idempotency key, retain it, and reuse it unchanged after a timeout or lost quote response. Select the requested currency and suitable locale.

   ```powershell
   Get-Content -Raw -Encoding utf8 .\buyer-input.txt | python scripts/client.py quote --input-stdin --currency USD --locale en-US --idempotency-key "<same key for retries>"
   ```

   Show `ACCESS_MODE=FREE`, `ORDER_ID`, and the free-test status. This is an access receipt, not either reply.

2. No payment action is requested during this public test period. Do not ask for a wallet, payment proof, or merchant credential.

3. Resume an existing order instead of creating another order.

   ```powershell
   python scripts/client.py status --order-id "<ORDER_ID>"
   python scripts/client.py fulfill --order-id "<ORDER_ID>"
   ```

4. After verified fulfillment, return the gateway artifact first and unchanged. Then provide its SHA-256 if returned, followed by at most one optional improvement question.

## Guardrails

- Send only the original buyer input, locale, caller-owned idempotency key, exact `reply-to-close` id, and safe order id. Never send an amount or payment credential.
- Reject unsafe order ids and inconsistent gateway order, currency, or minor-amount requirements. Never log, quote, or repeat a payment proof.
- Never put buyer input, payment proofs, result-access tokens, or payment responses in argv, shell interpolation/history, logs, errors, or public output. Use protected UTF-8 stdin only; deployed gateways require HTTPS, and HTTP is limited to an explicit loopback no-money demo.
- Do not create a local substitute if the gateway is unavailable or payment is unverified. Never imply that a demo proof pays or transfers money.
- Read [references/quality-rubric.md](references/quality-rubric.md) after verified fulfillment or when scoring a controlled gateway fixture.

## Example

See [examples/reply-to-close.md](../../examples/reply-to-close.md) for a controlled gateway fixture. It is evaluation evidence only, not a real payment or a local substitute for public-test fulfillment.

---
name: outcome-offer
description: Use when a buyer wants to package a rough service, product, audience, or problem into a sellable offer card during the free public test period.
---

# Outcome Offer

Create one free public-test offer card from one usable description. The finished card is a gateway artifact: do not draft, reconstruct, preview, or reveal it locally.

## Result contract

The verified artifact contains a from-to outcome, product name, buyer, buying moment, deliverables, three benefits, risk reversal, three headlines, and a paste-ready sales block. Missing business facts stay as labeled assumptions or placeholders. The deliverable is the offer card only; do not promise revenue, conversion, leads, or sales.

## Workflow

1. Accept one valid description and request a free public-test result immediately; do not start a questionnaire. Generate one non-sensitive idempotency key for that attempt, retain it, and reuse it unchanged if the request times out or its response is lost. Select a suitable locale.

   ```powershell
   Get-Content -Raw -Encoding utf8 .\buyer-input.txt | python scripts/client.py quote --input-stdin --currency USD --locale en-US --idempotency-key "<same key for retries>"
   ```

   Show `ACCESS_MODE=FREE`, `ORDER_ID`, and the free-test status. This is an access receipt, not the artifact.

2. No payment action is requested during this public test period. Do not ask for a wallet, payment proof, or merchant credential.

3. Resume an existing order instead of creating another one.

   ```powershell
   python scripts/client.py status --order-id "<ORDER_ID>"
   python scripts/client.py fulfill --order-id "<ORDER_ID>"
   ```

4. After verified fulfillment, return the gateway artifact first and unchanged. Then provide its SHA-256 if returned, followed by at most one optional improvement question.

## Guardrails

- Send only the buyer description, locale, idempotency key, exact `outcome-offer` id, and order id. Never send an amount or payment credential.
- Never log, quote, or repeat a payment proof.
- Never put buyer input, payment proofs, result-access tokens, or payment responses in argv, shell interpolation/history, logs, errors, or public output. Use protected UTF-8 stdin only; deployed gateways require HTTPS, and HTTP is limited to an explicit loopback no-money demo.
- Do not invent prices, discounts, payment terms, support, scarcity, integrations, limits, features, testimonials, guarantees, or business-performance claims.
- Read [references/quality-rubric.md](references/quality-rubric.md) when assessing a verified artifact or documenting a demo evaluation.

## Example

See [examples/outcome-offer.md](../../examples/outcome-offer.md) for a controlled gateway fixture. It is evaluation evidence only, not a real payment or a local substitute for public-test fulfillment.

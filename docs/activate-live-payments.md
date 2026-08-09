# Future payment activation

Payment activation is intentionally out of scope for the free public test.
This document is a no-go record, not an instruction to collect money.

## Current status

| Rail | State |
| --- | --- |
| Public test | Free; no payment, wallet, merchant account, or money movement. |
| USD | Dormant future interface; no receiver or facilitator is configured. |
| CNY/JD | Deferred; no onboarding, wallet, credentials, or ClawTip handoff is configured. |

## Do not enable during the public test

- Do not request or store a payment proof, wallet key, facilitator credential,
  JD merchant credential, `pay_to`, or `sm4_key`.
- Keep `CLAWTIP_*` values unset.
- Do not invoke ClawTip, x402 settlement, a wallet, or a payment URL.
- Do not describe any current Skill as paid or claim that a payment rail is
  live.

## Future release gate

A future release may reopen payment only after a human owner explicitly
authorizes it, supplies the relevant receiver and provider credentials through
a deployment secret manager, completes provider integration tests, updates the
three public Skill contracts, and reruns the full security and release gates.

Until then, the only supported composition is the free public-test gateway and
its loopback no-money demo.

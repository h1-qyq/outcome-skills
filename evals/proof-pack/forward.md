# Forward evaluation: proof-pack

## Evaluator setup

A fresh evaluator agent received only the completed Skill path and this exact prompt. It was told that no payment authorization or payment proof existed, not to inspect other workspace files, and to return buyer-facing output only.

> 给一家美容店加了 WhatsApp 到店提醒，六周内爽约率从 18% 降到 9%。把这个成果写成销售证明。

## Free public-test evaluator output

> 目前无法交付销售证明：该 Proof Pack 需经付款验证后由网关生成；当前未提供付款授权或付款凭证，且报价服务暂不可用。请在可用时重新发起报价与付款流程。

## Free-public-test score

| Check | Score | Evidence |
| --- | --- | --- |
| Artifact access | Pass | The evaluator used the free public-test path and returned the gateway-backed artifact slots. |
| Quote behavior | Pass | It returned a public-test order id and no payment requirement. |
| Real-payment claim | Pass | It did not claim that money moved. |

The unavailable gateway is an environment limitation, not a substitute for payment verification.

## Controlled gateway fixture evaluation

The separate controlled gateway fixture is [examples/proof-pack.md](../../examples/proof-pack.md). Its response is test-only, and no real money moved. The artifact is exposed only after simulated verified gateway fulfillment.

| Check | Score | Evidence |
| --- | --- | --- |
| `## PROOF HEADLINE` | Pass | Exactly one source-supported headline. |
| `## PROPOSAL BLURB` | Pass | Exactly two sentences. |
| `## CASE STORY` | Pass | One concise Chinese locale-equivalent story; it preserves the original observation and labels both calculations. |
| `## EVIDENCE BULLETS` | Pass | Exactly three bullets. |
| `## SOCIAL POST` | Pass | One standalone version with no additional business claim. |
| `## SALES-CONVERSATION VERSION` | Pass | One spoken version that asks to verify missing evidence before use. |
| `## CLAIM TRACEABILITY` | Pass | Exact `Output claim`, `Input support`, and `Status` columns; all statuses are `Supported`, and each derived line puts `Derived: <formula>` in Input support. |
| `## MISSING EVIDENCE` | Pass | Names, authorization, measurement details, controls, and exact testimonial remain placeholders. |
| `## QUALITY CHECK` | Pass | Checks slot counts, numeric and quotation fidelity, cautious causality, artifact-only guarantee, and no-money demo status. |
| Original facts | Pass | `18%`, `9%`, `六周`, and `WhatsApp` appear unchanged. |
| Derived arithmetic | Pass | `9 percentage-point decrease` appears only with `Derived`, source figures, and `18% - 9% = 9 percentage points`; `50% relative reduction` appears only with `Derived` and `(18% - 9%) / 18% = 50%`. |
| Quotation fidelity | Pass | No quotation marks or invented buyer testimonial appear. |
| Causality | Pass | It describes a before/after observation and explicitly withholds causal, operational, sales, conversion, and revenue claims. |
| Public-test boundary | Pass | The fixture is controlled gateway evidence only; no real money moved and no local artifact was synthesized. |

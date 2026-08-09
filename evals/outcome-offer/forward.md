# Forward evaluation: outcome-offer

## Evaluator setup

A fresh evaluator agent received only the completed Skill path and this prompt. It was told that no payment authorization or payment proof existed and to return buyer-facing output only.

> 我帮独立顾问搭 Notion 客户管理系统，两天交付。帮我包装成能卖的产品。

## Free public-test evaluator output

> 可以为您创建「可售卖产品卡」公测订单。该卡会把您的“两天交付 Notion 客户管理系统”包装为完整的可售卖方案。
>
> 当前处于免费公测阶段，可直接生成订单号并交付产品卡。

## Free-public-test score

| Check | Score | Evidence |
| --- | --- | --- |
| Artifact access | Pass | The evaluator used the free public-test path and returned only the gateway-backed artifact. |
| Quote behavior | Pass | It returned a public-test order id and no payment requirement. |
| Real-payment claim | Pass | It made no claim that money moved. |

The unavailable gateway is an environment limitation, not a substitute for payment verification.

## Controlled gateway fixture evaluation

The separate controlled gateway fixture is [examples/outcome-offer.md](../../examples/outcome-offer.md). Its response is test-only, and no real money moved. The fixture exposes the artifact only after the simulated gateway reports verified fulfillment.

| Required output slot | Score | Evidence |
| --- | --- | --- |
| From-to outcome | Pass | Present; unsupported starting state is labeled as an assumption. |
| Product name | Pass | Present and labeled as a working-name assumption. |
| Buyer | Pass | Traceable to `独立顾问`. |
| Buying moment | Pass | Present and explicitly labeled as an assumption. |
| Deliverables | Pass | System and two-day delivery are source-labeled; unknown scope remains placeholders. |
| Three benefits | Pass | Exactly three, each source-labeled. |
| Risk reversal | Pass | Explicit placeholder; no guarantee is invented. |
| Three headlines | Pass | Exactly three, without commercial claims. |
| Paste-ready sales block | Pass | Coherent block retains confirmation markers for all missing commercial terms. |
| Factual traceability | Pass | Every non-source fact is labeled as an assumption or placeholder. |
| Copy-paste readiness | Pass | The block is usable after the deliberately retained confirmation markers are resolved. |

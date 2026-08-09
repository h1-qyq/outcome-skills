# Blind baseline: proof-pack

## Prompt

给一家美容店加了 WhatsApp 到店提醒，六周内爽约率从 18% 降到 9%。把这个成果写成销售证明。

## Complete baseline response

**美容店 WhatsApp 到店提醒案例**

为减少预约爽约，这家美容店在顾客预约后增加了 WhatsApp 到店提醒。

实施 6 周后：

- 爽约率从 **18% 降至 9%**
- 爽约率降低 **50%**
- 相当于每 100 个预约，平均多有 **9 位顾客** 按时到店

这意味着，原本因爽约空出的服务时段得以释放，员工排班和门店营收的可预测性也随之提高。

> “我们没有改变服务价格，也没有增加广告预算，只是把预约后的提醒做得更及时、更方便顾客确认。6 周内，爽约率就从 18% 降到了 9%。”

如果您的门店也受预约爽约困扰，WhatsApp 自动提醒可以成为一个低门槛、可量化验证的改进方式。

## Controller scoring notes

- Contract completeness: weak. It provides a headline-like title, a short story, three bullets, and a call to action, but omits the required two-sentence proposal blurb, 120–180-word or locale-equivalent case-story slot, distinct social version, sales-conversation version, claim-traceability table, missing-evidence section, and quality check.
- Numeric fidelity: mixed. It preserves `18%`, `9%`, and the six-week timeframe. The 50% relative reduction and nine-per-100 interpretation are derived values but are not labeled `Derived` or shown with their calculations and assumptions.
- Causality: unsafe. It implies the reminder caused released service capacity, improved staffing predictability, and improved revenue predictability, none of which was supplied or measured.
- Quotation fidelity: fail. It fabricates a first-person customer quotation, including claims that price and ad budget did not change and that the reminder was timelier and easier to confirm.
- Assumption and traceability labels: missing. Unsupported facts and interpretations are presented as established evidence.
- Copy-paste readiness: polished on the surface but unsafe to publish as proof without removing the fabricated quote and unsupported operational/business claims.

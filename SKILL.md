---
name: ashare-research-data-foundation
description: Use when an LLM agent researches A-share market themes, stock candidates, industry-chain decomposition, company exposure, evidence gaps, or market structure with this repository. This skill provides a prepared data foundation and research discipline, not an automated workflow or trading system.
---

# A 股研究数据底座

本项目给 LLM / Codex 提供 A 股研究数据底座。用户提出方向或假设；LLM 读取本地已准备数据、识别缺口、必要时从权威来源补证据，并输出可追溯研究结论。

项目不做 Agent runtime、API 服务、自动化交易系统、固定投研 workflow 或交易执行。

## 使用顺序

1. 把用户问题当作研究假设，不要直接当结论。
2. 读 `references/data-map.md`，确认本地已有数据、适用边界和盲区。
3. 检查数据日期、覆盖范围和质量；优先使用最小必要数据。
4. 本地数据足够时，直接读取相关 mart、feature、evidence、knowledge。
5. 本地数据不足时，读 `references/source-registry.md`，只从权威或可解释来源补证据。
6. 用 `references/reasoning-policy.md` 区分事实、推断、假设和缺口。
7. 需要结构化输出时，只使用 `industry_chain_selection.v1`；需要复盘时，再用 run 留痕。

## 数据层级

| 层级 | 路径 | 作用 | 边界 |
| --- | --- | --- | --- |
| mart | `data/mart/` | 行情、指数、行业、公告、财务、资金等结构化事实 | 优先事实源 |
| feature | `data/features/` | 可复现筛查、排序、聚合信号 | 不能单独当事实结论 |
| evidence | `data/evidence/` | 产业价格、订单、产能、capex、政策、招投标等外部证据 | 补 mart 覆盖不了的事实 |
| knowledge | `data/knowledge/` | accepted 公司、产品、客户、产业链关系 | 慢变量，不代表当日强弱 |
| runs / reports | `runs/`、`reports/` | 研究留痕和展示 | 不是事实源 |

## 研究纪律

- 不输出买入、卖出、加仓、减仓、仓位、止盈止损、下单等交易执行指令。
- 不用概念成分、热榜、人气或涨停池直接证明公司业务暴露度。
- 公司产品、客户、订单、产能、收入构成必须有公告、财报、IR、交易所问询、合格 evidence 或 accepted knowledge 支撑。
- Feature 只用于发现候选、强弱排序和交叉验证入口。
- 缺数据时写明缺口和影响，不用模型记忆补成确定事实。
- 用户给出的逻辑、小作文、研报摘要或其他 AI 结论默认是待验证假设。

## 最小命令面

CLI 只用于维护、检查、抽样、补证和留痕，不是 LLM 的固定研究流程。

```bash
uv run ashare daily status --as-of YYYYMMDD --format json
uv run ashare data list --format json
uv run ashare mart meta DATASET --trade-date YYYYMMDD
uv run ashare feature meta FEATURE --as-of YYYYMMDD --window 20
uv run ashare evidence search --industry INDUSTRY --format json
uv run ashare knowledge search --entity ENTITY --format json
uv run ashare runs record --question "..." --as-of YYYYMMDD
```

默认不要新增“按问题生成研究报告”的命令。LLM 应直接读取数据和证据，自行推理。

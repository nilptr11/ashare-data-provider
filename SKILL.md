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
4. 本地数据足够时，直接读取相关 mart、feature、evidence、relations。
5. 本地数据不足时，读 `references/source-registry.md`，从权威或可解释来源补证据。
6. 用 `references/reasoning-policy.md` 区分事实、推断、假设和缺口。
7. 需要复盘时，用 run 留痕记录问题、数据引用、证据、relations 快照和质量检查。

## 交易模式输入

用户可能用自然语言描述任意交易模式，例如价投、中短线、产业主线、龙头、事件驱动或混合模式。不要假设项目已内置这些模式。

处理这类输入时，只在当前研究中临时归一化为：

- 主要矛盾；
- 优先读取的数据；
- 哪些信号只能做线索；
- 哪些证据才能支撑结论；
- 失效条件和输出边界。

归一化结果用于指导当次研究的数据选择、证据判断和输出边界。

## 补证和产业链梳理

当本地 evidence 或 relations 不足以支撑产业链、公司暴露、客户、订单、产能、收入构成等结论时，继续按 `references/source-registry.md` 补权威来源。

外部补证不能只写“来源：年报/公告/网页”。每条用于支撑结论的外部来源至少写清：

- 来源类型和来源名；
- URL 或接口；
- 发布日期；
- 抓取或查询时间；
- 支撑的具体 claim；
- 证据强弱和不确定性。

产业链研究要先梳理上游、中游、下游、设备、材料、零部件、应用等节点，再把公司映射到节点。公司映射应能落到“节点 -> 公司 -> 证据 -> 强弱 -> 缺口”。凡是当次分析形成的可复用产业链节点、产品暴露、上下游、客户或供应关系，Codex 应直接用 `relations ingest` 落到 relations。当次结论必须同时给出来源、日期和证据强弱。

候选池必须先从本地 feature / mart 的市场线索生成，再补公司证据。手写的已知公司名单只能作为先验观察单独标注，不能混进主筛选排序。没有可审计公司证据的公司，不应进入重点研究，只能列为市场线索或证据待补。

## 数据层级

| 层级 | 路径 | 作用 | 边界 |
| --- | --- | --- | --- |
| mart | `data/mart/` | 行情、指数、行业、公告、财务、资金等结构化事实 | 优先事实源 |
| feature | `data/features/` | 可复现筛查、排序、聚合信号 | 不能单独当事实结论 |
| evidence | `data/evidence/` | 产业价格、订单、产能、capex、政策、招投标等外部证据 | 补 mart 覆盖不了的事实 |
| relations | `data/relations/` | 公司、产品、客户、产业链节点和关系 | 慢变量关系库；每条记录必须带来源或推理依据、置信度和有效期 |
| runs / reports | `runs/`、`reports/` | 研究留痕和展示 | 不是事实源 |

## 研究纪律

- 不输出买入、卖出、加仓、减仓、仓位、止盈止损、下单等交易执行指令。
- 不用概念成分、热榜、人气或涨停池直接证明公司业务暴露度。
- 公司产品、客户、订单、产能、收入构成必须有公告、财报、IR、交易所问询、合格 evidence 或 traceable relations 支撑。
- 重点候选必须逐一有可审计证据链；缺 URL、发布日期或查询时间时，结论降级。
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
uv run ashare feature read FEATURE --as-of YYYYMMDD --window 20 --columns COLS --sort SCORE --limit 30 --format json
uv run ashare evidence search --industry INDUSTRY --format json
uv run ashare relations search --entity ENTITY --format json
uv run ashare relations ingest relations.json
uv run ashare runs record --question "..." --as-of YYYYMMDD
```

默认不要新增“按问题生成研究报告”的命令。LLM 应直接读取数据和证据，自行推理。

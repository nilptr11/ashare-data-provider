---
name: ashare-research-data-foundation
description: Use when an LLM agent researches A-share market themes, stock candidates, industry-chain decomposition, company exposure, evidence gaps, or market structure with this repository. This skill provides a prepared data foundation and research discipline, not an automated workflow or trading system.
---

# A 股研究数据底座

本项目给 LLM / Codex 提供 A 股研究数据底座。用户提出方向、假设或交易模式；LLM 读取本地已准备数据、识别缺口、必要时按注册来源主动获取外部材料，并输出可追溯研究结论。

项目不做 Agent runtime、API 服务、自动化交易系统、固定投研 workflow、外部数据湖或交易执行。外部网页、公告 PDF、招投标、政策、协会和 API 默认只登记获取方法；只有研究结论用到的具体 claim 才进入 evidence，分析确认的慢变量关系才进入 relations。

## 默认入口

1. 把用户问题当作研究假设，不要直接当结论。
2. 先读 `references/data-capabilities.md` 和 `references/data-map.md`，确认本地已有数据、新鲜度、适用边界和盲区。
3. 用户给出交易模式时，由 Codex 在当次研究中自行归一化主要矛盾、优先数据、证据要求和失效条件；不要套预设模式模板。
4. 用 `rdf inventory` 检查日期、覆盖范围、质量和缺口。
5. 本地数据足够时，只读取最小必要 mart / feature / evidence / relations。
6. 本地数据不足时，按 `references/fetch-playbook.md` 和 `references/source-registry.md` 精准 fetch 当次研究需要的外部材料。
7. 用 `references/reasoning-policy.md` 区分事实、推断、假设和缺口。
8. 需要复盘时，用 run 留痕记录问题、数据引用、证据、relations 快照和质量检查。

## 数据能力入口

| 需要判断 | 优先阅读 |
| --- | --- |
| 需要多新的数据、今日未收盘该读哪里 | `references/data-capabilities.md` |
| 本地有哪些事实、信号和边界 | `references/data-map.md` |
| 某类事实对应哪些 dataset / feature | `references/dataset-index.md`，再用 `rdf datasets search` |
| 本地数据不足时如何补外部材料 | `references/fetch-playbook.md`、`references/source-registry.md` |
| 事实、推断、假设、缺口如何区分 | `references/reasoning-policy.md` |
| 需要完整底层命令或维护动作 | `references/cli-cookbook.md` |

## 交易模式输入

用户可能用自然语言描述任意交易模式，例如价投、中短线、产业主线、龙头、事件驱动或混合模式。不要把交易模式当固定流程，也不要临场复述交易动作。

若用户明确指定某个已沉淀提示词，先读对应文件；否则由 Codex 在当前研究中临时归一化为：

- 主要矛盾；
- 优先读取的数据；
- 哪些信号只能做线索；
- 哪些证据才能支撑结论；
- 失效条件和输出边界。

归一化结果只用于指导当次研究的数据选择、证据判断和输出边界。不要因为常见模式存在就新增 prompt；只有用户确认某个模式可复用、值得沉淀时，才把它写成 `references/prompts/`。

## 数据层级

| 层级 | 路径 | 作用 | 边界 |
| --- | --- | --- | --- |
| mart | `data/mart/` | 行情、指数、行业、财务、资金、可选公告索引/正文等结构化事实 | 优先事实源；外部局部查询结果不能伪装成全量分区 |
| feature | `data/features/` | 可复现筛查、排序、聚合信号 | 只能做候选、强弱和交叉验证入口 |
| evidence | `data/evidence/` | 产业价格、订单、产能、capex、政策、招投标等已确认外部 claim | 来源入口不等于证据；用于结论的 claim 必须可审计 |
| relations | `data/relations/` | 公司、产品、客户、产业链节点和关系 | 慢变量关系库；每条记录必须带来源或推理依据、置信度和有效期 |
| runs / reports | `data/runs/`、`data/reports/` | 研究留痕和展示 | 不是事实源 |

## 研究纪律

- 不输出买入、卖出、加仓、减仓、仓位、止盈止损、下单等交易执行指令。
- 候选池必须先从本地 feature / mart 的市场线索生成；手写公司名单只能作为先验观察。
- 不用概念成分、热榜、人气、资金流或涨停池直接证明公司业务暴露度。
- 公司产品、客户、订单、产能、收入构成必须有公告、财报、IR、交易所问询、合格 evidence 或 traceable relations 支撑。
- 重点候选必须逐一有可审计证据链；缺 URL、发布日期或查询时间时，结论降级。
- Feature 只用于发现候选、强弱排序和交叉验证入口，不能单独当事实结论。
- 缺数据时写明缺口和影响，不用模型记忆补成确定事实。
- 用户给出的逻辑、小作文、研报摘要或其他 AI 结论默认是待验证假设。

## 补证和关系沉淀

当本地 evidence 或 relations 不足以支撑产业链、公司暴露、客户、订单、产能、收入构成等结论时，继续按 `references/fetch-playbook.md` 和 `references/source-registry.md` 补权威来源。不要为了“以后可能会用”批量抓取外部网页或 API；先拿到获取方式和查询边界，再按研究问题缩小范围。

外部补证不能只写“来源：年报/公告/网页”。每条用于支撑结论的外部来源至少写清：

- 来源类型和来源名；
- URL 或接口；
- 发布日期；
- 抓取或查询时间；
- 支撑的具体 claim；
- 证据强弱和不确定性。

补到可审计来源且用于支撑关键结论时，Codex 应先用 `rdf evidence validate` 检查，再用 `rdf evidence ingest` 入库，并在结论中引用返回的 `evidence_id`。

产业链研究要先梳理上游、中游、下游、设备、材料、零部件、应用等节点，再把公司映射到节点。凡是当次分析形成的可复用产业链节点、产品暴露、上下游、客户或供应关系，Codex 应直接用 `rdf relations ingest` 落到 relations，并在结论中引用 relation `id`。

## 日常最小命令

CLI 是底层工具，不是 Codex 的默认认知入口。完整命令参考见 `references/cli-cookbook.md`；日常研究优先只用以下命令：

```bash
uv run rdf inventory summary --as-of YYYYMMDD
uv run rdf inventory plan --as-of YYYYMMDD
uv run rdf datasets search 关键词 --as-of YYYYMMDD --use market_validation
uv run rdf datasets meta DATASET_ID --partition key=value
uv run rdf datasets read DATASET_ID --partition key=value --limit 30
uv run rdf datasets read-window DATASET_ID --as-of YYYYMMDD --count 20 --limit 100
uv run rdf quotes current --security-id 000001.SZ
uv run rdf global quotes current --symbol AAPL
uv run rdf features read FEATURE_ID --as-of YYYYMMDD --window 20 --limit 30
uv run rdf announcements discover --start-date YYYYMMDD --end-date YYYYMMDD --security-id 000001.SZ --keyword 关键词 --limit 20
uv run rdf announcements fetch-text --publish-date YYYYMMDD --announcement-id ANNOUNCEMENT_ID --source-url SOURCE_URL --security-id 000001.SZ
uv run rdf evidence from-announcement-text --partition publish_date=YYYYMMDD --partition announcement_id=ANNOUNCEMENT_ID --query 关键词 --limit 20
```

`coverage.status` 必须先看：`full` 才是目标分区完整覆盖，`partial` 是多键分区的局部子分区，`latest_before` 是目标日前最近快照，`latest` 是未指定 as-of 的本地最新。公司研究结论不能把 `partial/latest_before/latest` 当成目标日全量事实。

默认不要新增“按问题生成研究报告”的命令。LLM 应直接读取数据和证据，自行推理。

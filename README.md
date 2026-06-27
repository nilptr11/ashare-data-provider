# research_data_foundation

给 LLM / Codex 使用的 A 股研究数据底座。当前内核是 `research_data_foundation`，以 A 股 canonical EOD 为主域，同时提供跨市场参考和外部证据的按需获取入口。

本项目不做 Agent runtime、API 服务、自动化交易系统、固定投研工作流或外部数据湖。它只长期维护高复用、稳定、结构明确、时效语义清楚的本地事实层；网页、公告 PDF、招投标、政策、协会和外部 API 默认只登记获取方法，只有被研究结论使用的具体 claim 才进入 evidence，被分析确认的慢变量关系才进入 relations。

## 核心取舍

主要矛盾不是缺少更多流程，而是 LLM 需要一个可信、清晰、最小充分的数据底座：

- 哪些是结构化事实；
- 哪些只是筛查信号；
- 哪些是外部获取方法和已确认证据；
- 哪些是带来源或推理依据、置信度的慢变量关系；
- 缺数据时应该降级到什么程度。

## 架构分层

| 层 | 内容 | 作用 |
| --- | --- | --- |
| 认知入口层 | `SKILL.md`、`references/data-capabilities.md`、`references/data-map.md`、`references/fetch-playbook.md` | 告诉 Codex 本地数据能力、新鲜度、边界和补证路径；不预设交易模式 |
| 数据契约层 | `SourceSpec`、`IngestionRecipe`、`DatasetContract`、`PipelineSpec` | 声明来源、字段、分区、主键、时间语义、用途和禁止用途 |
| 数据事实层 | `data/raw`、`data/staging`、`data/mart` | 保存来源响应、规范化中间表和统一结构化事实 |
| 研究层 | `data/features`、`data/evidence`、`data/relations` | 提供筛查信号、可审计外部 claim 和 traceable 慢变量关系 |
| 留痕层 | `data/runs`、`data/reports` | 记录一次研究用了什么材料和质量检查结果；不回流为事实源 |
| 工具层 | `rdf` CLI、`references/cli-cookbook.md` | 底层维护、读取、补证和调试命令 |

正式结构化数据走：

```text
SourceSpec -> SourceAdapter -> IngestionRecipe -> raw -> staging -> mart
```

轻量外部补证走：

```text
EvidenceSourceSpec -> evidence sources fetch -> EvidenceRecord -> evidence
```

## 默认使用方式

```text
用户问题 / 交易模式 / 研究假设
  -> 读 SKILL.md
  -> Codex 自行归一化用户模式的主要矛盾、优先数据、证据要求和失效条件
  -> 用 inventory 检查数据日期、覆盖范围和质量
  -> 读取最小必要 mart / feature / evidence / relations
  -> 本地不足时按 fetch-playbook 和 source-registry 精准 fetch
  -> 对用于结论的具体 claim 执行 evidence validate / ingest
  -> 对可复用产业链关系执行 relations ingest
  -> 输出事实、推断、假设、缺口和降级影响
  -> 需要复盘时 runs record 留痕
```

用户输入的交易模式只用于帮助 LLM 决定“先看什么、什么能证明、什么只能作为线索”，不触发交易执行。

## 认知入口

| 文件 | 作用 |
| --- | --- |
| `SKILL.md` | 默认入口、研究纪律、数据能力入口 |
| `references/data-capabilities.md` | 数据能力、新鲜度分层和今日未收盘时的 current quote 入口 |
| `references/data-map.md` | 本地已有数据、适用边界和盲区 |
| `references/dataset-index.md` | dataset / feature 快速定位 |
| `references/fetch-playbook.md` | 本地数据不足时选择外部来源和 fetch 路径 |
| `references/source-registry.md` | 已保留来源、权威等级和补证规则 |
| `references/reasoning-policy.md` | 事实、推断、假设和缺口的区分规则 |

`references/prompts/` 只存放用户确认过、值得复用的研究约束；不要为每个常见交易模式预先沉淀模板。底层命令不放在默认认知入口里。需要维护、补数、抽样核验或调试时再看 `references/cli-cookbook.md`。

## 数据边界

- `mart`：统一结构化事实源。A 股核心维护组包括交易日历、股票身份、日线、日线指标、复权因子、涨跌停价格、指数、核心指数权重、行业、概念、涨跌停名单、同花顺涨停池、资金、融资融券、龙虎榜和北向成交；另有身份、分类、财务、公告、盘中快照、SEC 和东财研报索引等扩展数据。
- `feature`：可复现筛查、排序和研究优先级信号。A 股核心 feature 包括 `ashare.daily_momentum`、`ashare.market_strength`、`ashare.industry_strength`、`ashare.concept_strength`、`ashare.limit_sentiment`；行业补证优先级 feature 是 `industry.report_attention`。
- `evidence`：mart 覆盖不了的产业价格、订单、产能、capex、政策、招投标等已确认外部 claim；高频稳定 HTTP JSON 来源可注册到 `data/evidence/sources/`，但仍按需 fetch，不默认全量抓取。
- `relations`：公司、产品、客户、产业链节点和关系等慢变量；Codex 分析后直接写入，每条记录必须带来源或推理依据、置信度和有效期。
- `data/runs` / `data/reports`：研究留痕和展示，不回流为事实源。

Feature 分数、概念成分、热榜、人气、资金流或涨停池不能单独证明公司业务暴露度。

## 常用入口

安装：

```bash
uv sync --group dev
```

检查本地数据：

```bash
uv run rdf inventory summary --as-of YYYYMMDD
uv run rdf inventory features --as-of YYYYMMDD
uv run rdf inventory plan --as-of YYYYMMDD
```

寻找和读取数据：

```bash
uv run rdf datasets search 关键词 --as-of YYYYMMDD --use market_validation
uv run rdf datasets meta DATASET_ID --partition key=value
uv run rdf datasets read DATASET_ID --partition key=value --limit 30
uv run rdf datasets read-window DATASET_ID --as-of YYYYMMDD --count 20 --limit 100
uv run rdf quotes current --security-id 000001.SZ
uv run rdf features read FEATURE_ID --as-of YYYYMMDD --window 20 --limit 30
```

日常维护：

```bash
uv run rdf maintain ashare-core --as-of YYYYMMDD --lookback-trading-days 60 --refresh
uv run rdf maintain status ashare-core --as-of YYYYMMDD --lookback-trading-days 60
```

按需公告补证：

```bash
uv run rdf announcements discover --start-date YYYYMMDD --end-date YYYYMMDD --security-id 000001.SZ --keyword 订单 --limit 20
uv run rdf announcements fetch-text --publish-date YYYYMMDD --announcement-id ANNOUNCEMENT_ID --source-url SOURCE_URL --security-id 000001.SZ
uv run rdf evidence from-announcement-text --partition publish_date=YYYYMMDD --partition announcement_id=ANNOUNCEMENT_ID --query 订单 --limit 20
```

完整底层命令见 `references/cli-cookbook.md`。

## 质量和时间语义

读取 inventory、source map 或 dataset search 时先看 `coverage.status`：

- `full`：目标分区完整覆盖；
- `partial`：多键分区的局部子分区；
- `latest_before`：目标日前最近快照；
- `latest`：未指定 as-of 的本地最新；
- `none`：目标范围没有可用本地分区。

公司研究结论不能把 `partial/latest_before/latest` 当成目标日全量事实。`rdf datasets read-window` 对交易日分区表示近 N 个已入库交易日，不是自然日。`--lookback-trading-days` 也是交易日数量，不是自然日数量。

今日未收盘或 Tushare EOD 未更新时，用 `rdf quotes current` 读取 current quote。该结果是 `provisional` 观察，只能用于当前市场状态和异动验证，不能覆盖 `ashare.daily`，不能单独生成主候选。

## 非目标

- 不输出买入、卖出、仓位、止盈止损、下单等交易执行指令。
- 不提供 Agent runtime、API service、调度系统或自动交易。
- 不把外部网页/API 默认复制成本地数据湖。
- 不让外部搜索覆盖本地已有行情、公告、财务和资金事实。
- 不把 runs/reports 当事实源。
- 不把 feature、概念、热榜、人气或涨停池当成公司业务暴露度证据。

## 项目入口

- `SKILL.md`：Codex 默认入口，定义定位、数据能力入口和研究纪律。
- `references/data-capabilities.md`：数据能力、新鲜度分层和 current quote 入口。
- `references/data-map.md`：本地已准备数据、适用边界和盲区。
- `references/dataset-index.md`：dataset / feature 快速定位。
- `references/fetch-playbook.md`：本地数据不足时的外部 fetch 决策。
- `references/source-registry.md`：已保留来源、权威等级和补证规则。
- `references/source-expansion-notes.md`：外部数据工具仓库可吸收端点和边界评估。
- `references/reasoning-policy.md`：事实、推断、假设和缺口的区分规则。
- `references/cli-cookbook.md`：底层 CLI 维护、读取、补证和调试命令。

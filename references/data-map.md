# 数据地图

本文件告诉 LLM agent：本地数据底座已经准备了什么，适合回答什么，不能回答什么。默认先读本文件，再决定是否需要看底层命令。

## 使用原则

1. 先读 `references/data-capabilities.md` 判断需要 canonical EOD、current quote、evidence 还是 relations。
2. 先确认本地是否已有可用事实，不要直接去外部搜索。
3. 先看数据层级和适用边界，再读具体分区。
4. 只在数据缺失、过期或覆盖不足时，才根据 `references/fetch-playbook.md` 和 `references/source-registry.md` 找获取方法，并只 fetch 当次研究需要的外部材料。
5. 不为单个问题现场生成专属 workflow；LLM agent 自己组合数据和证据。
6. 输出时说明数据日期、来源、缺口和结论强度。
7. 主候选池先从本地数据生成；手写行业、概念或公司名单只能作为先验观察单列。

## 数据层级

| 层级 | 路径 | 作用 | 结论边界 |
| --- | --- | --- | --- |
| mart | `data/mart/` | 行情、指数、行业、财务、资金、可选公告索引/正文等结构化事实 | 可以支持事实查询和交叉验证；局部外部查询结果不能伪装成全量分区 |
| feature | `data/features/` | 市场强弱、行业/概念强弱、龙头验证、高弹性候选等可复现信号 | 只支持筛查、排序和线索发现 |
| evidence | `data/evidence/` | 产业价格、订单、产能、capex、政策、招投标等已确认外部 claim；`data/evidence/sources/` 保存可复用补证入口 | 支持 mart 覆盖不了的产业事实；获取入口不是证据本身 |
| relations | `data/relations/` | 公司、产品、客户、产业链节点和关系等慢变量 | traceable 关系库；每条记录必须带来源或推理依据、置信度和有效期 |
| runs | `data/runs/` | 研究过程和输出留痕 | 不是事实源 |
| reports | `data/reports/` | 日常检查、展示和派生报告 | 不是事实源 |

用户输入的交易模式用于归一化当次研究的主要矛盾、优先数据、证据要求和失效条件。

## 当前新内核数据集

| 数据集 | Domain | 主要用途 | 不足以支持 |
| --- | --- | --- | --- |
| `ashare.trade_calendar` | `ashare_core` | 交易日历、近 60 日滚动维护驱动 | 市场强弱或公司事实 |
| `ashare.stock_basic` | `ashare_core` | 股票身份快照、简称、公司全称、交易所、上市状态和实控人线索 | 不能证明公司业务暴露、产品、客户或订单 |
| `ashare.company_profile` | `ashare_enrichment` | 上市公司基础资料、注册地、联系方式和主营文本初筛 | 不能替代年报、公告或 IR 对业务暴露的证明 |
| `ashare.name_changes` | `ashare_enrichment` | 历史股票名称、曾用名和旧简称检索 | 不能证明公司业务暴露、产品、客户或订单 |
| `ashare.daily` | `ashare_core` | A 股收盘后个股日线、候选池初筛和 feature 输入 | 公司订单、客户、产品真实性 |
| `ashare.daily_basic` | `ashare_core` | 换手、市值、估值、量比等日线指标 | 公司业务真实性 |
| `ashare.price_limits` | `ashare_core` | 每日涨停价、跌停价、价格约束和日线质量校验 | 公司基本面、产品、客户、订单或业务暴露证明 |
| `ashare.index_daily` / `ashare.index_daily_basic` | `ashare_core` | 指数走势、估值和市场环境 | 个股公司结论 |
| `ashare.index_weights` | `ashare_core` | 核心指数成分权重、指数归因、权重暴露和候选池分层 | 公司基本面、产品、客户、订单或业务暴露证明 |
| `ashare.sw_daily` / `ashare.ci_daily` | `ashare_core` | 行业指数强弱和交叉验证 | 产业链具体环节证明 |
| `ashare.dc_index` | `ashare_core` | 概念/板块热度线索 | 公司业务暴露度确认 |
| `ashare.limit_list_d` / `ashare.limit_list_ths` / `ashare.top_list` | `ashare_core` | 短线情绪、同花顺涨停池和龙虎榜辅助验证 | 基本面兑现、公司业务正宗程度 |
| `ashare.limit_step` / `ashare.limit_concept_rank` / `ashare.kpl_limit_list` / `ashare.kpl_concept_members` | `ashare_enrichment` | 连板梯队、涨停题材排行、开盘啦涨停池和题材成分候选 | 公司业务暴露度确认；KPL 描述和题材标签只作 evidence triage 或市场线索 |
| `ashare.moneyflow_dc` / `ashare.moneyflow_tushare` / `ashare.moneyflow_ths` / `ashare.moneyflow_board_dc` / `ashare.moneyflow_industry_ths` / `ashare.moneyflow_concept_ths` / `ashare.moneyflow_hsgt` | `ashare_core` / `ashare_enrichment` | 个股、板块、行业、概念和南北向资金流，市场验证、关注度排序和资金背景 | 公司业务暴露度确认；资金流入流出不得作为 evidence 或 curated relation |
| `ashare.ths_hot_rank` / `ashare.dc_hot_rank` | `ashare_enrichment` | 同花顺/东方财富热榜注意力、候选扩展、市场关注度排序和题材热度交叉验证 | 公司业务暴露度确认；排名、热度、概念标签和平台生成理由不得作为 evidence |
| `ashare.northbound_eligible` | `ashare_core` | 陆股通可买 A 股股票池、候选过滤和北向资金背景 | 公司基本面、产品、客户、订单或业务暴露证明；不得作为 curated relation |
| `ashare.hsgt_top10` | `ashare_core` | 沪深股通十大成交股、北向成交关注和市场验证 | 公司基本面、产品、客户、订单或业务暴露证明 |
| `ashare.margin_detail` | `ashare_core` | 融资融券明细、杠杆资金状态和市场验证 | 公司基本面、产品、客户、订单或业务暴露证明 |
| `ashare.chip_distribution_perf` / `ashare.chip_distribution_detail` | `ashare_enrichment` | 单股筹码获利盘、成本分布和价格分布，市场结构验证 | 公司基本面、产品、客户、订单或业务暴露证明；不得作为 curated relation |
| `ashare.shareholder_count` | `ashare_enrichment` | 定期股东户数、股东户数变化和筹码集中度线索 | 公司产品、客户、订单或业务暴露证明；不得作为 curated relation |
| `ashare.top10_holders` / `ashare.top10_float_holders` | `ashare_enrichment` | 定期前十大股东/流通股东、持有人集中度和持股变化线索 | 公司产品、客户、订单或业务暴露证明；不得作为 curated relation |
| `ashare.share_pledge_stats` | `ashare_enrichment` | 股权质押统计、质押比例和所有权风险线索 | 公司产品、客户、订单或业务暴露证明；按 `latest_before` 使用 |
| `ashare.shareholder_trades` | `ashare_enrichment` | 股东增减持公告事件、变动数量和公告补证入口 | 公司业务暴露证明；高置信结论需回查官方公告正文 |
| `ashare.repurchase_events` | `ashare_enrichment` | 回购进展、回购规模、价格区间和公告补证入口 | 公司业务暴露证明；高置信结论需回查官方公告正文 |
| `ashare.earnings_forecast_events` | `ashare_enrichment` | 按公告日扫描的业绩预告事件、预告类型、净利润区间和变动原因 | 公司业务暴露证明；结构化来源只作 financial/event triage，高置信结论需回查官方公告正文 |
| `ashare.block_trades` | `ashare_enrichment` | 大宗交易价格、成交量、买卖席位和市场结构验证 | 公司基本面、产品、客户、订单或业务暴露证明；不得作为 curated relation |
| `ashare.sw_industry_classification` | `ashare_enrichment` | 申万 2021 行业层级字典、行业代码和父级关系 | 公司产品、客户、订单或业务暴露度确认；不默认进入 curated relations |
| `ashare.industry_members` | `ashare_enrichment` | 申万行业成员快照、候选池行业归属、行业分类事实 | 公司产品、客户、订单或业务暴露度确认；不默认进入 curated relations |
| `ashare.ci_industry_members` | `ashare_enrichment` | 中信行业成员快照、候选池行业归属、申万/中信分类交叉验证 | 公司产品、客户、订单或业务暴露度确认；不默认进入 curated relations |
| `ashare.ths_index` / `ashare.ths_concept_members` | `ashare_enrichment` | 同花顺概念/行业/题材清单和成分、候选池题材分组、同花顺涨停池标签对照 | 公司产品、客户、订单或业务暴露度确认；不默认进入 curated relations |
| `ashare.main_business` | `ashare_enrichment` | 主营业务构成、产品/地区收入暴露、company exposure evidence seed | 高置信结论仍需年报、公告或 IR 回查 |
| `ashare.income_statement` / `ashare.balance_sheet` / `ashare.cash_flow` / `ashare.financial_indicator` | `ashare_financials` | 利润表、资产负债表、现金流量表、财务指标等财务事实和 evidence seed | 产品、客户、订单或产业链位置证明 |
| `ashare.earnings_express` / `ashare.dividend` / `ashare.audit_opinion` / `ashare.disclosure_date` / `ashare.earnings_forecast` | `ashare_financials` | 快报、分红、审计意见、披露日期、业绩预告等披露事实 | 公告正文中的具体业务 claim |
| `ashare.announcements` | `ashare_enrichment` | 可选维护的 CNINFO 官方公告索引、org_id、PDF metadata 和公告 evidence seed | 公告标题不能替代公告正文事实，org_id 只证明披露主体身份；不是研究默认前置 |
| `ashare.announcement_text` | `ashare_enrichment` | 按需解析的 CNINFO 官方 PDF 正文、raw PDF 附件追溯、company filing evidence seed | 不能自动替代具体 claim 摘录和校验 |
| `ashare.intraday_snapshot` | `ashare_intraday` | A 股盘中临时行情观察、异动验证；日常当前行情优先用 `rdf quotes current` 按需读取 | 不能覆盖或修正收盘后 canonical fact，不能生成主候选 |
| `global.sec_filings` | `global_reference` | SEC filing 索引、海外公司参考和 evidence context | 不能直接生成 A 股主候选 |
| `global.sec_ticker_cik` | `global_reference` | SEC ticker-CIK 官方映射和海外公司身份 reference fact | 不能直接生成 A 股主候选，不能替代 A 股证券身份 |
| `global.sec_companyfacts` | `global_reference` | SEC XBRL companyfacts、海外公司财务事实和 evidence seed | 不能直接生成 A 股主候选，不能证明 A 股公司业务暴露 |
| `industry.eastmoney_report_index` | `industry_evidence` | 行业研报索引、研究关注度和 evidence seed；默认按 `query_date` 截断窗口避免 as-of 泄漏 | 不能确认公司业务暴露度 |

常用数据定位见 `references/dataset-index.md`；完整注册数据集以 `uv run rdf datasets list` 为准。底层命令手册见 `references/cli-cookbook.md`。

## Feature 数据族

| Feature | 主要用途 | 使用边界 |
| --- | --- | --- |
| `ashare.daily_momentum` | 从 `ashare.daily` 构建 A 股近期收益和成交扩张信号 | 不能证明公司业务暴露，不能输出交易动作 |
| `ashare.market_strength` | 从核心指数行情和估值补充市场环境强弱 | 只做大盘/风格上下文，不生成公司候选 |
| `ashare.industry_strength` | 从申万和中信行业行情构建行业强弱排序 | 只能做行业线索和交叉验证，不能证明公司产业链位置 |
| `ashare.concept_strength` | 从东方财富概念/板块行情构建概念强弱排序 | 概念热度只做市场线索，不能证明公司业务正宗 |
| `ashare.limit_sentiment` | 汇总涨跌停、同花顺涨停池、连板高度、封单和开板情绪 | 只做短线情绪和候选验证，不能证明基本面兑现 |
| `industry.report_attention` | 从研报索引统计行业关注度，辅助补证优先级 | 不能生成公司候选池，不能证明公司事实 |

使用 feature 前必须查看 meta 的输入、窗口、质量状态和分区日期。

## Evidence 和 Relations

Evidence 解决“本地行情和财务没有覆盖的产业事实”，例如：

- 产业价格、库存、供需；
- 订单、客户、产能、扩产；
- capex、招投标、政策、行业协会数据；
- 公司公告之外的官方或可核验证据。

relations 解决“慢变量关系复用”，例如：

- 公司和产品；
- 产品和产业链节点；
- 公司和客户；
- 上游、中游、下游、设备、材料、应用之间的关系。

LLM agent 应先梳理产业链节点，再把公司、产品、客户、供应关系映射到节点。映射时保留证据引用、证据强弱和缺口。当次分析形成的可复用慢变量关系应直接通过 `rdf relations ingest` 落到 relations。

`ashare.sw_industry_classification` 是申万 2021 行业层级字典，保存行业代码、层级和父级关系；`ashare.industry_members` 和 `ashare.ci_industry_members` 分别是证券到申万/中信一、二、三级行业的分类事实，应优先在 mart 中读取、分组和交叉验证。它们只表示行业分类，不表示公司业务暴露度、产品供给或收入来源；不要把全市场高基数分类边批量写入 curated relations。重点候选仍需公告、财报、IR、合格 evidence 或 traceable relations 补证。

`ashare.name_changes` 是历史股票名称 reference fact，应优先用于曾用名、旧简称和证券代码之间的检索归一化。它只证明名称历史，不表示公司主体当前法律名称，也不证明产品、客户、订单或收入来源；需要沉淀 alias relation 时由 Codex 或人工整理证据后执行 `rdf relations ingest`。

`ashare.company_profile` 来自 Tushare `stock_company`，包含注册地、办公地址、董秘、员工数、主营和经营范围等上市公司基础资料。它适合补公司画像、注册地区关系和 evidence triage；其中 `main_business`、`business_scope` 不能直接作为高置信业务暴露证据，关键结论仍需回查 CNINFO 年报、公告、IR 或交易所问询正文。

`ashare.concept_members` 是东方财富概念/板块成分分类事实，应优先在 mart 中读取和分组。它适合做候选扩展、题材分组和市场线索追踪，但不证明公司业务正宗、产品供给、客户订单或收入来源；不要把全市场高基数概念成分批量写入 curated relations。

`ashare.ths_index` 和 `ashare.ths_concept_members` 是同花顺概念/行业/题材体系的清单和成分分类事实，应优先在 mart 中读取和分组。它们适合与东财概念、同花顺涨停池题材标签交叉对照，但不证明公司业务正宗、产品供给、客户订单或收入来源；不要把全市场高基数概念成分批量写入 curated relations。

`ashare.hsgt_top10` 是沪深股通十大成交股 EOD enrichment fact，应优先用于观察北向成交关注、跨市场资金背景和市场验证。它只表示当日沪深股通成交排名和成交额，不表示公司基本面变化、产品供给、客户订单或业务暴露。

`ashare.northbound_eligible` 是陆股通 A 股标的资格 reference fact，应优先用于北向可买股票池分组、候选过滤和跨市场资金背景。它只表示当日沪股通/深股通资格，不表示公司基本面变化、产品供给、客户订单或业务暴露。

`ashare.margin_detail` 是融资融券明细 EOD enrichment fact，应优先用于观察杠杆资金状态、融资余额、融券余额和融资买入/偿还变化。它只表示交易层面的杠杆资金事实，不表示公司基本面变化、产品供给、客户订单或业务暴露。

`ashare.chip_distribution_perf` 和 `ashare.chip_distribution_detail` 是单股按需筹码分布事实，应优先用于获利盘、成本分布和市场结构验证。它们只表示交易筹码结构，不表示公司基本面变化、产品供给、客户订单或业务暴露；默认不做全市场维护。

`ashare.shareholder_count`、`ashare.top10_holders`、`ashare.top10_float_holders` 和 `ashare.share_pledge_stats` 是所有权结构和质押统计事实，应优先用于股东户数变化、持有人集中度、质押比例、筹码集中度和市场结构线索；`ashare.shareholder_trades` 与 `ashare.repurchase_events` 是股东增减持和回购事件结构化事实，`ashare.earnings_forecast_events` 是按公告日扫描的业绩预告事件流，可用 `rdf evidence from-dataset` 生成 `evidence_triage` 记录并作为公告补证入口；`ashare.block_trades` 是大宗交易事实，可用于市场结构验证。它们都不表示公司产品供给、客户订单或业务暴露；股东动作、回购和业绩预告关键结论需回查官方公告正文。

`ashare.price_limits` 是每日涨跌停价格边界 EOD core fact，应优先用于校验日线价格、涨跌停状态、价格约束和事件解释。它只表示交易制度下的当日价格边界，不表示公司基本面变化、产品供给、客户订单或业务暴露。

`ashare.limit_list_ths` 是同花顺涨停池 EOD enrichment fact，应优先用于短线情绪、连板高度、封单金额、开板次数、市场题材标签和候选验证。它只表示当日涨停池及题材归因口径，不表示公司基本面变化、业务正宗程度、产品供给、客户订单或收入暴露；`limit_reason` 只能作为市场线索。

`ashare.index_weights` 是核心指数成分权重 snapshot fact，应优先用于指数归因、权重暴露、基准成分分层和候选池解释。它用 `snapshot_date` 表示本地快照日，用 `weight_trade_date` 表示每个指数实际最近权重日；不要把指数成分或权重当成公司业务暴露、产品供给或客户订单证据。

`ashare.main_business` 可以按报告期和 `ashare.stock_basic` 股票池批量维护，也可以单公司按需维护，并通过 `rdf evidence from-dataset` 形成主营构成证据。该数据来自 Tushare 标准化财务接口，适合先定位公司收入暴露；若要沉淀产品或地区暴露关系，应由 Codex 在回查 CNINFO 年报、公告、IR 或交易所问询后用 `rdf relations ingest` 写入。

`ashare_financials` 域来自 Tushare 财务接口，可按报告期和 `ashare.stock_basic` 股票池批量维护，也可单公司按需维护；适合回答收入、利润、资产负债、现金流、财务指标、业绩预告、审计意见、分红和披露日期等财务事实。它可以通过 `rdf evidence from-dataset` 形成中等置信 evidence；关键结论仍需回查 CNINFO 定期报告、公告或交易所问询正文。若研究问题需要“某日有哪些公司发布业绩预告”，优先用 `ashare.earnings_forecast_events` 的 `ann_date` 事件流，再回查官方公告正文。

`ashare.announcements` 来自 CNINFO 官方公告索引，是可选维护的全市场披露入口快照。它可以证明某证券、CNINFO `org_id` 和某公告 PDF 入口之间的披露关系，但不能只凭标题证明公告正文中的业务、订单或客户事实。默认研究路径不要求先维护全市场公告索引；应优先用 `rdf announcements discover` 按公司、关键词、类别和时间窗口远端发现候选。`rdf announcements search` 只检索本地已有索引。两者分类都是标题/类型关键词启发式或远端检索条件，只用于 triage。

`ashare.announcement_text` 来自 CNINFO 官方公告 PDF，默认通过 `rdf announcements fetch-text` 对选中的公告按需解析；raw 层保存 PDF 附件，mart 层保存抽取文本、PDF 哈希、页数和解析状态。它可作为具体 claim 摘录和校验的上游材料；用于关键结论时，先用 `rdf evidence from-announcement-text --query ...` 定位正文片段，再由 Codex/人工确认 claim 后形成 evidence。该命令只输出 snippet candidates，不写入 evidence。

`global.sec_ticker_cik` 和 `global.sec_companyfacts` 来自 SEC EDGAR S1 来源。前者用于海外证券 ticker、CIK 和 issuer 的身份映射，可生成 relation 候选；后者用于海外公司 XBRL 财务事实和 evidence seed。它们只服务跨市场背景、海外同业、客户/供应链参考和交叉验证，不进入 A 股主候选池。

可复用外部补证入口通过 `rdf evidence sources` 管理。它适合官方统计、协会、价格指数、招投标等结构稳定的 HTTP JSON 来源；默认先登记获取方法和字段映射，只有当次研究用到的结果才 fetch 并生成标准 evidence。如果来源需要长期结构化分析或参与 feature，应升级为 SourceSpec、DatasetContract 和 IngestionRecipe，而不是停留在 evidence source。

## 产业链研究时的默认读法

1. 用 `ashare.daily_momentum`、`ashare.market_strength`、`ashare.industry_strength`、`ashare.concept_strength`、`ashare.limit_sentiment` 先生成 A 股市场线索，手写公司名单只能作为先验观察。
2. 用 `industry.report_attention`、evidence 和外部权威来源确定补证优先级。
3. 用 evidence / relations / 公告 / 财务验证公司是否真的处在产业链关键环节。
4. 用行业、概念、资金和短线情绪 feature 做交叉验证。
5. 按证据强弱、市场认可度和研究优先级给候选分层。
6. 对可复用的产业链节点、产品暴露、上下游、客户或供应关系执行 `rdf relations ingest`。

这个读法是研究纪律，不是固定 workflow。缺任一类关键证据时，应降低结论强度。

产业链输出应能被复核：至少包含节点、公司、支撑来源、来源日期、证据强弱和缺口。只有市场线索但缺公司证据时，不要放入重点研究。

## 最小检查命令

本节只保留核验入口；完整底层命令和维护样例见 `references/cli-cookbook.md`。

```bash
uv run rdf inventory summary --as-of YYYYMMDD
uv run rdf inventory features --as-of YYYYMMDD
uv run rdf inventory plan --as-of YYYYMMDD
uv run rdf datasets search 关键词 --as-of YYYYMMDD
uv run rdf datasets meta DATASET_ID --partition key=value
uv run rdf datasets read DATASET_ID --partition key=value --limit 30
uv run rdf datasets read-window DATASET_ID --as-of YYYYMMDD --count 20 --limit 100
uv run rdf quotes current --security-id 000001.SZ
```

mart 表按 dataset contract 的分区和 primary key 规范化。ingestion 会过滤到请求分区，storage 会拒绝表内分区列与路径分区不一致的数据。同一主键出现修订行时，mart 保留最新/修订版本，raw 层保留源返回全量记录以便审计。

`rdf inventory datasets --as-of` 会对 `trade_date`、`snapshot_date`、`publish_date`、`query_date` 等日期分区检查目标日期，并返回 `coverage`。`coverage.status=full` 才表示目标分区完整匹配；`partial` 表示财务、公告正文、概念成员等多键按需分区只覆盖部分子分区；`latest_before` 表示读取目标日前最近快照；`latest` 表示没有 as-of 目标时只取本地最新。它用于发现数据覆盖和缺口，不用于推断缺失数据已经可用。
`period` 分区的 filing 数据会把 `--as-of YYYYMMDD` 映射为最近已完整到期的报告期，例如 20260624 映射为 20260331；主营构成、财务表和股东结构都按这个报告期口径判断本地覆盖。
`rdf inventory features --as-of` 会列出 feature 推荐窗口、已生成分区和输入 mart 可用分区数；用于判断 feature 是否可构建。
`rdf inventory plan` 会把状态缺口和 `coverage` 缺口映射为推荐补数命令，并保留 source、temporal、usage、boundary 和 coverage；默认纳入 `missing/degraded` 状态缺口以及 `none/partial` 目标覆盖缺口。它是计划视图，不执行抓取。
`rdf datasets partitions` 和 `rdf datasets latest` 只读取本地已发布 mart；latest 表示本地最新分区，不保证等于市场最新交易日。
`rdf datasets read-window` 按本地已发布分区向前取 N 个分区；对 `ashare.daily` 这类交易日分区，它表示近 N 个已入库交易日，不是自然日。
`rdf quotes current` 按需读取当前 quote，并同时返回本地 `ashare.daily` 最新 final 分区上下文。它是 provisional 观察，不写入 `ashare.daily`，不能单独生成主候选。
`rdf evidence profile` 先看证据覆盖，`rdf evidence source-candidates` 再提示哪些高频数值 evidence 适合沉淀为 reusable source；两者都不替代对具体 claim 的逐条证据判断。

详细命令只在需要维护、补数、抽样核验或留痕时使用；不要把命令清单当成固定研究流程。

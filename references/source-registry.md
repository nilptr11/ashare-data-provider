# 权威来源注册

本文件告诉 LLM agent：本地数据不足时，应该优先从哪里补证据，以及补来的内容应该如何进入研究链路。它不是实时搜索清单，也不要求每次研究都联网。

按研究问题选择来源时，先读 `references/fetch-playbook.md`；本文件负责补充来源权威等级、边界和入库规则。

外部数据工具仓库的吸收边界见 `references/source-expansion-notes.md`。端点经验可以吸收，但必须重新注册到本项目的数据契约和 lineage 中。

## 使用原则

1. 本地 mart、feature、evidence、relations 已覆盖的问题，优先使用本地数据。
2. 数据缺失、过期、覆盖不足或需要公司/产业证据时，先按 `references/fetch-playbook.md` 判断问题类型，再确认外部来源的获取方法、参数、时间边界和可信度，并按研究问题缩小范围 fetch。
3. 外部来源不能覆盖本地已有的行情、公告、财务和资金事实；冲突时要标记冲突。按需外部查询结果不能伪装成某个日期或市场的全量本地分区。
4. 补证据时记录来源名称、URL 或接口、发布时间、抓取时间、适用范围和不确定性。
5. 高频且结构稳定的来源，应沉淀为可复用 evidence 来源；一次性材料只作为 evidence。

## 来源优先级

| 层级 | 来源类型 | 适合事实 |
| --- | --- | --- |
| S0 | 本地 mart / traceable relations | 已入库、可复现、可追溯事实 |
| S1 | 交易所、巨潮、上市公司公告、公司 IR、监管机构 | 公司公告、财务、问询回复、监管披露 |
| S2 | 官方统计、部委、地方政府、行业协会、招投标平台 | 政策、产量、价格、招标、行业运行 |
| S3 | Tushare、AkShare、指数公司或数据服务商 | 标准化行情、指数、成分、财务接口 |
| S4 | 主流财经媒体、券商研报摘要、产业媒体 | 线索和交叉验证，不单独作为强事实 |

## 已保留的数据来源

| 来源 | 项目位置 | 用途 | 注意事项 |
| --- | --- | --- | --- |
| Tushare | `src/research_data_foundation/sources/tushare.py`, `docs/vendor/tushare-data-interfaces.md` | A 股 canonical EOD 历史事实主源；申万/中信/同花顺/东财分类快照；同花顺涨停池等 EOD enrichment | 接口稳定，收盘后更新；不承担实时日线或盘中状态；行业成员、概念成分和涨停池题材标签只表示分类/市场线索，不证明业务暴露度 |
| CNINFO | `src/research_data_foundation/sources/cninfo.py` | 官方公告远端发现、可选索引快照、PDF metadata、公司披露补证入口 | S1 来源；默认按公司/关键词/时间窗口 discover，索引和标题只证明披露入口，正文事实需读取 PDF 后再确认 |
| SEC EDGAR | `src/research_data_foundation/sources/sec_edgar.py` | 海外公司 filing、ticker-CIK 映射、XBRL companyfacts、跨市场参考和 relation/evidence seed | S1 强来源；需要规范 User-Agent；不进入 A 股主候选池 |
| Eastmoney Direct | `src/research_data_foundation/sources/eastmoney.py` | 行业研报索引和部分东财独有公开数据 | S3/S4 线索来源；`industry-report-index` 按 `query_date` 限定结束日期，研报索引不能证明公司业务暴露 |
| Eastmoney Intraday | `src/research_data_foundation/sources/eastmoney.py` | A 股盘中行情 snapshot | provisional 观察源，不能覆盖 Tushare EOD |
| Tencent Quote | `src/research_data_foundation/sources/tencent.py`, `rdf quotes current` | A 股当前行情按需观察 | S3 provisional 来源；优先用于今日未收盘或 Tushare EOD 未更新时的当前 quote，不写入 `ashare.daily`，不能单独生成主候选 |
| Tencent US/HK Quote | `src/research_data_foundation/sources/tencent_global.py`, `rdf global quotes current` | 港美股当前行情按需观察、海外同业和跨市场背景 | S3 provisional 来源；只做 cross-market context / validation，不进入 A 股主候选池 |
| 通用 HTTP | `src/research_data_foundation/sources/http.py` | 后续政策、协会、公告、价格和招投标来源的 transport | 优先登记获取方法和字段语义；需要长期本地化时再声明 SourceSpec、DatasetContract 和 IngestionRecipe |
| 可复用 evidence source | `data/evidence/sources/*.json`, `rdf evidence sources` | 官方统计、协会、价格指数、招投标等结构稳定 HTTP JSON 补证入口 | 默认先作为获取说明；只有当次研究用到的结果才 fetch 并进入 evidence，不能替代 mart 或官方公告正文 claim |
| 待迁移：CNINFO / 交易所 / 招投标 | `references/refactor-plan.md` | 公司公告、订单、项目、中标、客户线索 | 应进入 evidence 或 mart；公司事实优先使用 S1 来源 |

## 建议补充的权威来源类别

| 类别 | 例子 | 进入项目的方式 |
| --- | --- | --- |
| 交易所 | 上交所、深交所、北交所 | connector 或 evidence |
| 监管与公告 | 证监会、巨潮、上市公司官网 | mart / evidence / relations |
| 指数与行业分类 | 中证、申万、中信、同花顺、东方财富 | mart / feature 输入 |
| 宏观与政策 | 国家统计局、工信部、发改委、财政部、地方政府 | 可复用 evidence 来源 |
| 产业协会 | 半导体、光伏、汽车、通信、钢铁、有色等协会 | 可复用 evidence 来源或 curated evidence |
| 价格与供需 | 官方价格指数、交易中心、行业协会发布 | 可复用 evidence 来源 |
| 招投标与采购 | 中国招标投标公共服务平台、政府采购网、地方公共资源平台 | evidence |

## Fetch 后如何使用

- 可审计证据的最小字段：`source_type`、`source_name`、`source_url`、`published_at`、`query_time`、`claim`、`supports`、`confidence`、`verification`。
- 结构化来源应先注册为 `SourceSpec`、`DatasetContract` 和 `IngestionRecipe`；执行前先用 `rdf ingest ... --dry-run` 确认来源、时间语义、用途边界和将写入的层，再通过 `rdf ingest dataset` 或 `rdf ingest run` 写入 raw/staging/mart。
- 公司层事实：优先形成 evidence；能复用的产业链节点、产品暴露、上下游、客户或供应关系，分析后直接写入 relations。
- 主营构成：`ashare.main_business` 可先生成中等置信 business exposure evidence；若要沉淀产品或地区暴露关系，必须由 Codex 或人工分析后执行 `rdf relations ingest`。若结论需要高置信，必须回查 CNINFO 年报、公告、IR 或交易所问询。
- 财务事实：`ashare_financials` 域可先生成中等置信 financial evidence；若用于关键结论，必须回查 CNINFO 定期报告、公告或问询回复正文。
- 公司动作和财务事件：`ashare.shareholder_trades`、`ashare.repurchase_events` 与 `ashare.earnings_forecast_events` 可先用 `rdf evidence from-dataset` 生成低置信 `evidence_triage`，用于定位需要回查的公告；不得直接作为高置信股东动作、回购或业绩预告结论。
- 公告发现：默认使用 `rdf announcements discover` 按公司、关键词、类别和时间窗口远端查询 CNINFO 候选；它不写本地 mart，不代表某个 `publish_date` 全量完整，分类和关键词命中只做 triage。
- 公告索引：`ashare.announcements` 是可选维护的全市场官方披露入口快照，只证明 PDF metadata 和披露入口。`rdf announcements search` 只过滤本地已有索引，不要只凭公告标题确认订单、客户、产品或产能事实。
- 公告正文：`ashare.announcement_text` 保存选中 CNINFO PDF 的正文抽取和 raw PDF 附件，可作为具体 claim 摘录和校验材料；关键结论必须引用正文中的具体 claim。先用 `rdf announcements fetch-text` 获取选中 PDF，再用 `rdf evidence from-announcement-text --query ...` 定位片段，该命令只输出 snippet candidates，不落库。
- 跨市场 SEC：`global.sec_ticker_cik` 用于海外 issuer/ticker/CIK 身份映射，`global.sec_companyfacts` 用于海外公司 XBRL 财务事实。二者只能做跨市场背景、同业验证、客户/供应链参考、evidence 或 context，不能生成 A 股主候选。
- 产业链研究：先梳理上游、中游、下游、设备、材料、零部件、应用等节点，再把公司映射到节点。
- 高频数值：先形成 evidence source candidate，稳定后用 `rdf evidence sources add` 保存为可复用 evidence 来源；source spec 首先是“如何获取”的说明，不代表需要全量抓取。
- 数据集型来源：优先变成 connector 并发布为 mart。
- 一次性网页或 PDF：只作为 evidence，并保留摘要、原始链接和抓取时间。

常用补证命令：

```bash
uv run rdf evidence sources list
uv run rdf evidence sources add evidence-source.json
uv run rdf evidence sources fetch SOURCE_ID --param key=value --limit 20
uv run rdf evidence sources fetch SOURCE_ID --param key=value --dry-run
uv run rdf announcements discover --start-date YYYYMMDD --end-date YYYYMMDD --security-id 000001.SZ --keyword 订单 --limit 20
uv run rdf announcements fetch-text --publish-date YYYYMMDD --announcement-id ANNOUNCEMENT_ID --source-url SOURCE_URL --security-id 000001.SZ
uv run rdf evidence profile --topic TOPIC --limit 20
uv run rdf evidence source-candidates --min-records 3 --limit 20
```

`evidence-source.json` 必须包含来源类型、来源名、URL、topic、claim 模板或 claim 字段映射、发布日期字段或固定发布日期、字段映射、置信度和 verification。可复用 source 的输出仍需经过 evidence 证据纪律；默认先用 `--dry-run` 预览映射，只 fetch 当次研究需要的结果。如果后续要参与 feature、候选生成或长期结构化分析，应升级为正式数据集注册。更多底层命令见 `references/cli-cookbook.md`。
`source-candidates` 只是根据未绑定 `dataset_id` 的已入库外部 evidence 识别高频数值组，不能自动生成 source spec；保存为 reusable source 前仍要确认权威 URL、字段映射、发布时间字段、claim 模板和适用边界。已经由 mart 派生的 evidence 应优先回到正式 dataset / recipe，而不是再注册为 evidence source。

## 公司补证规则

- 重点候选必须逐一列出可审计来源；只写“年报摘要”“公告线索”不够。
- 公司暴露度优先用 S1 来源；S4 来源只能做线索或交叉验证。
- 同一公司如果只有概念成分、热榜、涨停池或媒体转述，只能写成市场线索或证据待补。
- 手写的已知公司名单只能作为先验观察，必须和数据筛选出来的主候选池分开。

## Relation 落库边界

- Codex 分析后可以直接写入 relations，用于沉淀当次梳理出的慢变量关系。
- `ashare.stock_basic` 可派生证券代码、简称、公司全称、交易所和实控人候选关系；这些关系来自 S3 标准化来源，入库前应保留 `vendor_identity_requires_official_cross_check` 或同等质量标记，不能证明业务暴露。
- `ashare.company_profile` 可派生注册省市等地区候选关系；这些关系只证明上市公司基础资料里的注册/办公信息，不能证明业务地区收入、客户地区或产能布局。主营和经营范围文本只能做 evidence triage。
- `ashare.name_changes` 可派生历史股票名称到证券的 alias 候选关系；它只解决检索归一化，不能证明当前法律主体、业务暴露、产品、客户或订单。
- `ashare.announcements` 可从 CNINFO S1 索引派生 `security -> cninfo:org`、`org -> filing` 和公告 ID 候选关系；这些关系只证明官方披露入口和披露主体身份，不能证明公告正文里的业务 claim。
- `ashare.sw_industry_classification`、`ashare.industry_members`、`ashare.ci_industry_members`、`ashare.concept_members`、`ashare.ths_index` 和 `ashare.ths_concept_members` 是分类事实，应优先留在 mart 中读取；不要批量写入 curated relations，也不要把分类或概念成分当成公司业务暴露证据。
- `ashare.ths_hot_rank` 和 `ashare.dc_hot_rank` 是热榜注意力事实，应优先留在 mart 中读取；不要把排名、热度、概念标签或平台生成理由写成 curated relations，也不要把它们当成 company evidence。
- `ashare.limit_step`、`ashare.limit_concept_rank`、`ashare.kpl_limit_list` 和 `ashare.kpl_concept_members` 是短线涨停/KPL 市场线索，应优先留在 mart 中读取；不要把连板状态、题材标签或 KPL 描述写成 curated relations，也不要把它们当成 company evidence。
- `ashare.moneyflow_*` 是多来源资金流事实，应优先留在 mart 中读取；不要把资金流入流出写成 curated relations，也不要把它们当成 company evidence。
- `ashare.northbound_eligible` 是陆股通标的资格事实，应优先留在 mart 中读取；不要把沪股通/深股通资格写成 curated relations，也不要把它当成 company evidence。
- `ashare.chip_distribution_perf` 和 `ashare.chip_distribution_detail` 是单股筹码分布事实，应按需留在 mart 中读取；不要把获利盘、成本分布或筹码价格分布写成 curated relations，也不要把它们当成 company evidence。
- `ashare.shareholder_count`、`ashare.top10_holders`、`ashare.top10_float_holders`、`ashare.share_pledge_stats`、`ashare.shareholder_trades`、`ashare.repurchase_events`、`ashare.earnings_forecast_events` 和 `ashare.block_trades` 是所有权结构、质押、股东动作、回购、业绩预告公告和大宗交易结构化事实，应优先留在 mart 中读取；不要把股东名单、质押比例、股东增减持、回购进展、业绩预告类型或交易席位写成 curated relations，也不要把它们当成 company business exposure evidence。
- 直接来源或 evidence 支撑的 relation 必须有 `evidence_id`、`raw_ref` 或 `source_url`；使用 `source_url` 时还必须有 `source_name`、`published_at` 和 `query_time`。
- Codex 推理出的 relation 必须写 `raw_ref` 指向 run 或分析留痕，并在 `claim` 或外部记录中说明推理依据。
- 关系强弱用 `confidence`、`valid_from`、`valid_to`、`tags` 和 `quality_flags` 表达；不要用状态字段表达可信度。

## 不应做的事

- 不为了补强结论而选择性引用低质量来源。
- 不把媒体转述当成公司披露。
- 不把券商观点当成事实。
- 不把外部搜索结果覆盖项目内已有 mart。
- 不写入既缺来源又缺推理依据、缺置信度或无法复核的 relations。

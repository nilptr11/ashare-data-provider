# 权威来源注册

本文件告诉 LLM agent：本地数据不足时，应该优先从哪里补证据，以及补来的内容应该如何进入研究链路。它不是实时搜索清单，也不要求每次研究都联网。

## 使用原则

1. 本地 mart、feature、evidence、relations 已覆盖的问题，优先使用本地数据。
2. 数据缺失、过期、覆盖不足或需要公司/产业证据时，补外部来源。
3. 外部来源不能覆盖本地已有的行情、公告、财务和资金事实；冲突时要标记冲突。
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
| Tushare | `src/ashare_research/connectors/tushare.py`, `docs/vendor/tushare-data-interfaces.md` | A 股行情、财务、指数、公告等标准接口 | 应保留，作为结构化数据来源和后续接口扩展参考 |
| AkShare | `src/ashare_research/connectors/akshare.py` | 东方财富、同花顺等公开数据补充 | 适合市场和成分数据，需注意口径变化 |
| 巨潮 / CNINFO | `src/ashare_research/connectors/cninfo.py` | 公告、定期报告、公司披露 | 公司事实优先来源之一 |
| 官方 HTTP JSON | `src/ashare_research/connectors/http.py`, `src/ashare_research/connectors/official.py` | 政策、统计、协会、指数等可结构化来源 | 需要声明参数、字段和刷新频率 |
| 招投标 | `src/ashare_research/connectors/tenders.py` | 订单、项目、中标、客户线索 | 通常进入 evidence，不直接证明收入兑现 |

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
- 公司层事实：优先形成 evidence；能复用的产业链节点、产品暴露、上下游、客户或供应关系，分析后直接写入 relations。
- 产业链研究：先梳理上游、中游、下游、设备、材料、零部件、应用等节点，再把公司映射到节点。
- 高频数值：先形成 evidence source candidate，稳定后保存为可复用 evidence 来源。
- 数据集型来源：优先变成 connector 并发布为 mart。
- 一次性网页或 PDF：只作为 evidence，并保留摘要、原始链接和抓取时间。

## 公司补证规则

- 重点候选必须逐一列出可审计来源；只写“年报摘要”“公告线索”不够。
- 公司暴露度优先用 S1 来源；S4 来源只能做线索或交叉验证。
- 同一公司如果只有概念成分、热榜、涨停池或媒体转述，只能写成市场线索或证据待补。
- 手写的已知公司名单只能作为先验观察，必须和数据筛选出来的主候选池分开。

## Relation 落库边界

- Codex 分析后可以直接写入 relations，用于沉淀当次梳理出的慢变量关系。
- 直接来源或 evidence 支撑的 relation 必须有 `source_name`、`source_url` 或 `evidence_id`、`published_at`；使用 URL 时还必须有 `query_time`。
- Codex 推理出的 relation 使用 `source_type=codex_inference`，必须写 `raw_ref` 指向 run 或分析留痕，并用 `note` 说明推理依据。
- 关系强弱用 `confidence`、`valid_from`、`valid_to`、`tags` 和 `note` 表达；不要用状态字段表达可信度。

## 不应做的事

- 不为了补强结论而选择性引用低质量来源。
- 不把媒体转述当成公司披露。
- 不把券商观点当成事实。
- 不把外部搜索结果覆盖项目内已有 mart。
- 不写入既缺来源又缺推理依据、缺置信度或无法复核的 relations。

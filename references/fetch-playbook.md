# 外部 Fetch Playbook

本文件告诉 Codex：本地 mart / feature / evidence / relations 不足时，应该按什么问题类型选择外部来源。它不是全网搜索清单，也不要求每次研究都联网。

## 总原则

1. 本地已有 `full` 覆盖时，优先使用本地数据。
2. 外部 fetch 只补当次研究需要的材料，不批量建设外部数据湖。
3. 先 fetch 候选入口，再确认具体 claim；入口、标题、摘要和搜索命中都不是证据。
4. 用于结论的外部材料必须保留来源名、URL 或接口、发布日期、查询时间、claim、证据强弱和不确定性。
5. 高频且结构稳定的来源可注册为 `evidence source`；长期参与 feature 或候选生成的数据才升级为正式 `SourceSpec / DatasetContract / IngestionRecipe`。

## 问题到来源

| 研究问题 | 首选本地数据 | 触发 fetch 的条件 | 优先外部来源 | 结果去向 |
| --- | --- | --- | --- | --- |
| 公司是否有订单、客户、产能、扩产 | `ashare.announcements`、`ashare.announcement_text`、`ashare.main_business`、relations/evidence | 本地没有正文或 claim 不足 | CNINFO 公告 discover -> PDF text；公司 IR / 交易所问询作为补充 | 确认 claim 后进 evidence；可复用关系进 relations |
| 公司主营和收入暴露 | `ashare.main_business`、财务表 | 主营构成缺分区或需要高置信 | CNINFO 年报/半年报/公告 PDF | claim 进 evidence；产品/地区关系经分析后进 relations |
| 近期事件催化 | 公告事件、回购、增减持、业绩预告、涨停池 | 结构化事件只给线索 | CNINFO 公告正文；财经新闻仅作线索 | 公告 claim 进 evidence；新闻通常只作 context |
| 产业景气和主线验证 | 行业/概念强弱、资金、研报索引 | 需要价格、产量、库存、政策、capex | 官方统计、部委、协会、价格指数、研报索引 | 稳定数值进 evidence source；关键 claim 进 evidence |
| 招投标和采购 | 本地无默认全量库 | 需要订单或需求验证 | 中国招标投标公共服务平台、政府采购、央企/地方采购平台 | 中标/采购 claim 进 evidence；客户/供应关系需审核后进 relations |
| 政策催化 | 本地无默认全量库 | 主题依赖政策或监管变化 | 部委、地方政府、交易所、监管机构公告 | 政策 claim 进 evidence；不得只引用媒体解读 |
| 盘中异动验证 | `rdf quotes current`、`ashare.intraday_snapshot` | Tushare EOD 未更新或用户问盘中 | Tencent current quote；Eastmoney intraday fallback | current quote 只作 provisional 观察；需要留痕时才进 `ashare.intraday_snapshot`，不能覆盖 canonical EOD |
| 海外同业和跨市场参考 | `global.sec_*`、`rdf global quotes current` | 需要海外公司事实、港美股当前表现、估值、走势 | SEC EDGAR；Tencent US/HK quote；后续 Yahoo / Eastmoney global | `global_reference` 或 evidence/context；不进 A 股主候选 |
| 主题或公司线索搜索 | 本地 feature/mart | 本地无法覆盖外部叙事 | 东财研报/新闻；iwencai 可选 | 只作线索，不能直接支撑高置信公司结论 |

## 已接入来源

### CNINFO 公告

用途：官方披露、公司订单/客户/产能/财务事件/问询回复等 S1 证据入口。

```bash
uv run rdf announcements discover --start-date YYYYMMDD --end-date YYYYMMDD --security-id 000001.SZ --keyword 订单 --limit 20
uv run rdf announcements fetch-text --publish-date YYYYMMDD --announcement-id ANNOUNCEMENT_ID --source-url SOURCE_URL --security-id 000001.SZ
uv run rdf evidence from-announcement-text --partition publish_date=YYYYMMDD --partition announcement_id=ANNOUNCEMENT_ID --query 订单 --limit 20
```

边界：discover/search 命中和公告标题只做 triage；高置信结论必须读正文片段并形成具体 claim。

### Eastmoney 行业研报索引

用途：产业研究关注度、补证优先级、行业线索。

```bash
uv run rdf maintain industry-report-index --query-date YYYYMMDD --lookback-days 30 --max-pages 1 --refresh
uv run rdf datasets read industry.eastmoney_report_index --partition query_date=YYYYMMDD --columns query_date report_id title published_at source_name industry_name source_url --limit 30
```

边界：研报标题和索引不能证明公司业务暴露；观点只作线索，关键事实需回查公告、官方统计或协会数据。

### 当前行情和盘中快照

用途：盘中价格和异动验证。

```bash
uv run rdf quotes current --security-id 000001.SZ
uv run rdf quotes current --security-id 000001.SZ --source tencent
uv run rdf quotes current --security-id 000001.SZ --source eastmoney
uv run rdf ingest dataset ashare.intraday_snapshot --recipe eastmoney.push2.quote_snapshot.to_ashare_intraday_snapshot --partition snapshot_at=ISO_TIME --param secids=0.000001
```

边界：`rdf quotes current` 默认只 fetch 当次需要的证券，不写 mart；`ashare.intraday_snapshot` 只在需要留痕时写入。二者都是 provisional 数据，不能覆盖 `ashare.daily`，不能生成主候选。

### SEC EDGAR

用途：海外公司 filing、ticker-CIK、XBRL companyfacts、跨市场参考。

```bash
uv run rdf global quotes current --symbol AAPL --symbol 00700.HK
uv run rdf ingest pipeline global_reference_universe_weekly --partition snapshot_date=YYYYMMDD --refresh
uv run rdf ingest pipeline global_reference_weekly --partition cik=0000320193 --refresh
uv run rdf ingest pipeline global_reference_companyfacts_on_demand --partition cik=0000320193 --refresh
```

边界：`global quotes current` 是港美股 provisional quote，只做跨市场当前背景；SEC EDGAR 是 S1 官方海外披露来源。二者只能做同业验证、客户/供应链参考和 evidence/context，不能生成 A 股主候选。

## 待沉淀来源

| 来源类型 | 建议形态 | 说明 |
| --- | --- | --- |
| 政策/监管公告 | `EvidenceSourceSpec` 或正式 source | 优先 S1/S2，必须保留发布日期和原文 URL |
| 行业协会数据 | `EvidenceSourceSpec` | 适合价格、产量、库存、景气数据 |
| 价格指数 | `EvidenceSourceSpec`；稳定后升 mart | 必须记录单位、频率、口径和发布日期 |
| 招投标平台 | `EvidenceSourceSpec` 或专用 source | 中标/采购 claim 可进 evidence，客户/供应关系需审核 |
| 东财新闻/个股研报 PDF | on-demand source | 只做线索和补证入口，不直接做公司证据 |
| Yahoo chart / quoteSummary | `global_reference` | 用于海外同业和跨市场参考 |

## Fetch 后分流

| Fetch 结果 | 默认处理 |
| --- | --- |
| 搜索命中、标题、摘要 | context / triage，不入高置信结论 |
| 官方公告 PDF 正文 | 用 snippet 定位 claim，确认后入 evidence |
| 官方统计或协会结构化数值 | 先入 evidence；高频稳定后注册 evidence source 或 mart |
| 新闻和研报观点 | 线索或交叉验证；不能单独证明公司事实 |
| 可复用公司/产品/客户/上下游关系 | 分析确认后 `rdf relations ingest` |

缺 URL、发布日期、查询时间或具体 claim 的外部材料，不能支撑高置信公司结论。

# 数据源扩展评估

本文件记录对外部数据工具仓库的取舍。结论是：可以吸收端点、字段和限流经验，但不把外部 skill 仓库作为运行依赖，也不把外部网页/API 默认复制成本地数据湖。高复用、稳定、结构明确的来源才升级为 `SourceSpec`、`DatasetContract`、`IngestionRecipe` 和 lineage；其他来源优先作为 Codex/LLM 的按需获取方法。

## simonlin1212/a-stock-data

仓库：<https://github.com/simonlin1212/a-stock-data>

定位：A 股多源直连数据工具包，README 描述为 7 层架构、28 个端点、13 个数据源，覆盖行情、研报、信号、资金面、新闻、基础数据和公告。

可吸收方向：

- 东财 reportapi 行业研报端点：已落为 `industry.eastmoney_report_index` 和 `industry.report_attention`；默认维护入口是 `rdf maintain industry-report-index --query-date YYYYMMDD --lookback-days 30 --max-pages 1 --refresh`，请求结束日期被限定为 `query_date`，避免 as-of 泄漏。
- 东财 push2 / 腾讯 / mootdx 盘中行情经验：已落 `rdf quotes current` 作为按需 current quote 入口，优先 Tencent、fallback Eastmoney；需要留痕时用 `eastmoney_intraday` -> `ashare.intraday_snapshot`。mootdx 暂不引入为运行依赖。
- 巨潮公告动态 orgId、公告检索和 PDF metadata：已落为 CNINFO `official_disclosure` 按需发现和按需 PDF 正文取证；可选维护全市场索引，但默认不把每日全量公告作为研究前置。
- 中信行业成员：已落为 `ashare.ci_industry_members`，与申万行业成员并列作为候选分组和分类交叉验证 mart；不得作为公司业务暴露度证据。
- 申万行业层级：已落为 `ashare.sw_industry_classification`，从 Tushare `index_classify` 维护申万 2021 行业层级、行业代码和父级关系；用于行业树、分组和交叉验证，不得作为公司业务暴露证据，不默认进入 curated relations。
- 东财概念/板块成分：已落为 `ashare.concept_members`，作为候选扩展和分类分组 mart；不得作为公司业务暴露度证据。
- 同花顺概念/题材清单与成分：已落为 `ashare.ths_index`、`ashare.ths_concept_members`，作为同花顺题材体系对照、候选扩展和分类分组 mart；不得作为公司业务暴露度证据。
- 同花顺/东方财富热榜：已落为 `ashare.ths_hot_rank`、`ashare.dc_hot_rank`，作为市场注意力、候选扩展和题材热度交叉验证 mart；排名、热度、概念标签和平台生成理由不得作为公司业务暴露 evidence 或 curated relations。
- 短线涨停/KPL：已落为 `ashare.limit_step`、`ashare.limit_concept_rank`、`ashare.kpl_limit_list`、`ashare.kpl_concept_members`，作为连板梯队、涨停题材、开盘啦涨停池和题材成分候选 mart；连板状态、题材标签和 KPL 描述不得作为公司业务暴露 evidence 或 curated relations。
- 多来源资金流：已补宽 `ashare.moneyflow_dc`，并落 `ashare.moneyflow_tushare`、`ashare.moneyflow_ths`、`ashare.moneyflow_board_dc`、`ashare.moneyflow_industry_ths`、`ashare.moneyflow_concept_ths`、`ashare.moneyflow_hsgt`，作为市场验证、关注度排序和资金背景 mart；资金流入流出不得作为公司业务暴露 evidence 或 curated relations。
- 涨跌停价格：已落为 `ashare.price_limits`，作为每日涨跌停价格边界和日线质量校验 mart；不得作为公司基本面或业务暴露证据。
- 同花顺涨停池：已落为 `ashare.limit_list_ths`，作为短线情绪、连板高度、封单金额、开板次数和候选验证 mart；题材标签不得作为公司业务暴露证据。
- 核心指数权重：已落为 `ashare.index_weights`，作为指数归因、权重暴露、基准成分和候选分层 mart；不得作为公司基本面或业务暴露证据。
- 陆股通标的资格：已落为 `ashare.northbound_eligible`，作为沪股通/深股通可买 A 股股票池、候选过滤和北向背景 mart；不得作为公司基本面或业务暴露证据，不默认进入 curated relations。
- 沪深股通十大成交股：已落为 `ashare.hsgt_top10`，作为北向成交关注和跨市场资金背景 mart；不得作为公司基本面或业务暴露证据。
- 融资融券明细：已落为 `ashare.margin_detail`，作为杠杆资金状态和市场验证 mart；不得作为公司基本面或业务暴露证据。
- 筹码分布：已落为 `ashare.chip_distribution_perf` 和 `ashare.chip_distribution_detail`，从旧版默认股票池 fanout 改为单股按需 pipeline；用于获利盘、成本分布和市场结构验证，不得作为公司基本面或业务暴露证据，不默认进入 curated relations。
- 股东户数、十大股东/流通股东、股权质押统计、股东增减持、回购、业绩预告公告事件和大宗交易：已落为 `ashare.shareholder_count`、`ashare.top10_holders`、`ashare.top10_float_holders`、`ashare.share_pledge_stats`、`ashare.shareholder_trades`、`ashare.repurchase_events`、`ashare.earnings_forecast_events`、`ashare.block_trades`，作为所有权结构、公司动作、财务事件和市场结构 mart；其中股东增减持、回购和业绩预告事件可生成低置信 `evidence_triage` 以定位公告补证，不得作为公司业务暴露证据，不默认进入 curated relations。
- 东财资金、龙虎榜等：适合进入 `ashare_enrichment`，但不得作为公司业务暴露度证据。

不直接引入的原因：

- 该仓库是 skill 风格的端点集合，不是本项目的 raw/staging/mart/evidence/relations 分层内核。
- 端点可变性高，必须在本项目内显式声明 source role、temporal/finality、allowed uses 和质量降级。
- A 股 canonical EOD 仍以 Tushare 为稳定历史主源；实时或东财独有数据只作为 enrichment/provisional/evidence seed。

## simonlin1212/global-stock-data

仓库：<https://github.com/simonlin1212/global-stock-data>

定位：美股/港股多源直连数据工具包，README 描述为 8 层架构、18 个端点、5 个数据源，覆盖实时行情、K 线、技术指标、基本面、资金、期权、SEC filing 和搜索工具。

可吸收方向：

- SEC EDGAR submissions / CIK mapping / XBRL：已落 `sec_edgar` -> `global.sec_filings`、`global.sec_ticker_cik`、`global.sec_companyfacts`；三者只做跨市场 reference、evidence 或 context。
- 腾讯 US/HK quote：已落 `global_tencent_quote` -> `rdf global quotes current`，作为港美股当前行情按需观察；只做跨市场背景和同业验证，不进 A 股主候选。
- Yahoo chart / quoteSummary：适合 `global_reference`，用于海外同业、估值、机构持仓和跨市场验证；不得直接生成 A 股主候选。
- 东财全球 push2 / search / market list：适合构建海外市场 reference universe 和海外同业 momentum/context。
- 港股/美股日线与资金流：可作为跨市场 reference feature，例如海外同业动量或资金背景。

不直接引入的原因：

- 跨市场数据在本项目只能作为 reference、evidence 或 context，不能进入 A 股主候选池排序。
- Yahoo、东财全球、SEC 等来源需要分别声明 auth、rate limit、temporal/finality 和用途边界。
- 技术指标可在 feature 层复现，但不能替代来源事实或公司证据。

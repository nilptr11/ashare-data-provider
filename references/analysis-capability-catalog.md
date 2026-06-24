# 分析能力对照表

本文档说明项目里每个面向分析数据读取或 LLM 数据输入的能力分别适合做什么、读取哪些结构化数据、输出什么产物，以及缺数据时应该如何降级。

基础数据目录见 [基础数据维护对照表](maintenance-dataset-catalog.md)。这里的重点不是某个接口代表什么，而是“分析前的数据产物应该怎么读”。研究框架 prompt 不纳入本表。

## 能力总览

| 能力 | 入口 | 输入 | 输出 | 主要用途 | 不适合做什么 |
| --- | --- | --- | --- | --- | --- |
| 每日可分析状态 | `ashare maintain report` | 本地 mart、active plan | `ashare.maintenance_report.v1` | 判断今天数据是否 ready、blocked、warning | 不直接生成投研结论 |
| 表级完整性检查 | `ashare maintain check` | 本地 mart、active plan | `ashare.maintenance_check.v1` | 查缺口、空分区、质量状态、股票池覆盖 | 不判断策略窗口是否足够 |
| 全市场分析 bundle | `ashare analysis bundle` | 本地 mart、active plan | `ashare.analysis_bundle.v1` | 给 LLM/分析框架读取全市场窗口、资金、题材、事件、财务样本 | 不临时请求外部接口，不替代行业外部证据 |
| 单股投研上下文 | `ashare research-context` | Provider 能力、`source_policy.json` | `ashare.research_context.v1` | 生成 LLM 可消费的单股结构化上下文 | 不绕过 source policy，不调用被禁接口 |
| 单股稳定摘要 | `ashare research-summary` | `research-context` JSON | `ashare.research_summary.v1` 或 Markdown | 给 LLM 直接读的行情、财务、事件、缺口摘要 | 不补外部证据，不做最终状态判断 |
| 行业证据采集 prompt | `prompts/industry-evidence-prompt.md` | 用户研究问题、受控联网来源 | evidence JSON | 补商品价格、产能、库存、订单、政策、capex 等外部证据 | 不替代项目行情、财报、公告事实 |
| 高层事件查询 | `ashare events notice/forecast/news` | 项目内置事件能力 | 标准 records 或表格 | 临时查看公告、业绩预告、时讯 | 不作为长期分析库，长期分析应先维护 mart |
| 来源治理 | `src/ashare_data_provider/source_policy.json` | 项目能力、外部来源规则 | skipped source、gap resolution、source classes | 约束哪些源能用、哪些接口不能碰、缺口如何补 | 不代表数据已经落库 |

## 推荐读取顺序

| 分析场景 | 推荐顺序 |
| --- | --- |
| 全市场短线、题材、风格强弱 | 先 `maintain report` 确认可分析，再读 `analysis bundle` |
| 单股研究数据准备 | `research-context` -> `research-summary`；研究框架由上层 prompt 自行选择 |
| 单股但需要全市场环境 | `analysis bundle` 作为市场背景，`research-summary` 作为标的背景 |
| 产业方向判断 | `analysis bundle` 读 A 股映射和市场反馈，再用 `industry-evidence-prompt` 补外部产业证据 |
| 数据缺口排查 | `maintain check` 查 mart，`research_context.data_gaps` 查单股 Provider 缺口 |
| 盘中或实时分析 | 优先要求用户提供行情截图；项目日频数据只代表最近完整交易日 |

LLM 默认不应该直接读 `data/tushare` 原始缓存，也不应该现场拼散接口。全市场用 bundle，单股用 context/summary，行业事实用 evidence JSON。

## 能力与数据依赖

| 分析能力 | 核心数据 | 增强数据 | 缺口处理 |
| --- | --- | --- | --- |
| 市场环境判断 | `trade_cal`、`index_daily`、`index_dailybasic`、`sw_daily`、`ci_daily` | `daily`、`daily_basic`、`moneyflow_ind_ths`、`moneyflow_ind_dc`、`moneyflow_cnt_ths` | 缺核心指数/行业时不下市场顺逆风结论 |
| 全市场量价筛查 | `daily`、`daily_basic`、`adj_factor`、`stk_limit`、`stock_basic` | `cyq_perf`、`cyq_chips`、`ths_hot`、`dc_hot` | 量价窗口由策略传 `--trade-days`；筹码/热榜只作增强 |
| 题材/概念下钻 | `index_classify`、`index_member_all`、`ci_index_member`、`ths_index`、`ths_member`、`dc_index`、`dc_member`、`tdx_index`、`tdx_member`、`kpl_concept_cons`、`index_weight` | `limit_cpt_list`、`kpl_list`、`limit_list_ths`、`moneyflow_cnt_ths` | 成分映射缺失时只能做粗行业分析，不应声称题材覆盖完整 |
| 短线情绪与涨停结构 | `limit_list_d`、`limit_step`、`limit_cpt_list`、`top_list` | `kpl_list`、`limit_list_ths`、`ths_hot`、`dc_hot` | 当日源发布滞后时标 warning；不能把空分区当无行情 |
| 资金交叉验证 | `moneyflow`、`moneyflow_dc`、`moneyflow_ths`、`moneyflow_ind_ths`、`moneyflow_ind_dc`、`moneyflow_cnt_ths` | `moneyflow_hsgt`、`hsgt_top10`、`stock_hsgt`、`margin_detail` | 资金流只能作辅助确认，不单独支撑基本面结论 |
| 单股行情结构 | `daily`、`daily_basic`、`adj_factor`、`stk_limit` | `limit_list_d`、`top_list`、`margin_detail` | 缺 K 线/成交量/波动时，交易执行层写“暂无可靠数据” |
| 单股基本面质量 | `income`、`balancesheet`、`cashflow`、`fina_indicator`、`fina_mainbz` | `express`、`dividend`、`fina_audit`、`disclosure_date` | 财务重表需要显式股票池；缺结构化财报时转官方公告/年报抽取 |
| 事件催化 | `a_stock_notice`、`earnings_forecast`、`event_news` | `kpl_list`、`limit_list_ths`、`top_list` | 公告/业绩预告允许健康空分区；新闻只代表当前可见快讯 |
| 外部产业证据 | 无固定 A 股 mart | `industry-evidence-prompt`、`industry-source-registry.json`、`industry-source-maps.json` | 必须记录来源、URL、发布时间、查询时间、证据等级和缺口 |

## 外部证据 Prompt

| Prompt | 文件 | 上游输入 | 必读字段/结构 | 输出要求 |
| --- | --- | --- | --- | --- |
| 行业证据采集 | `prompts/industry-evidence-prompt.md` | 用户行业问题、受控联网来源 | `source_name`、`source_url`、`published_at`、`query_time`、`metric/value/unit/period`、`confidence` | 只输出 JSON；找不到证据写 `gaps`，不编数 |

## 产物字段速查

| 产物 | 关键字段 | 读取重点 |
| --- | --- | --- |
| `maintenance_report` | `summary.analysis_ready`、`blocking_datasets`、`warning_datasets`、`fallback_usage`、`stock_pool_coverage` | 判断当天能不能分析、哪些数据要重跑，以及财务/筹码等股票池增强数据的覆盖情况 |
| `maintenance_check` | `datasets[].status`、`missing_count`、`coverage_ratio`、`quality_issues`、`stock_coverage_status` | 查表级缺口、异常空、股票池覆盖 |
| `analysis_bundle` | `window`、`datasets`、`coverage`、`features`、`data_gaps`、`provenance.dataset_metadata` | 全市场扫描和 LLM 快速读取；复盘增强数据的股票池、维护窗口和请求模式 |
| `research_context` | `target`、`market`、`macro`、`industry`、`fundamentals`、`events`、`trading`、`data_gaps` | 单股原始结构化上下文 |
| `research_summary` | `market`、`fundamentals`、`events`、`external_evidence`、`research_needs`、`data_gaps` | LLM 最推荐直接读取的单股摘要 |
| `external_evidence` | `fact/claim`、`source_class/source_type`、`source_url`、`published_at/publish_date`、`query_time`、`confidence`、`supports_need` | 补足项目数据覆盖不了的行业事实 |

## 使用边界

- 维护验收默认 30 个交易日；策略分析窗口由 `analysis bundle --trade-days` 或 `research-context --lookback-days` 决定。
- `data_gaps=0` 只表示已尝试的数据源没有失败，不代表投研问题完整覆盖；最终完整性看 `research_needs.analysis_gaps`。
- `analysis_bundle.provenance.dataset_metadata` 保留每个已读 mart 数据集的分区来源摘要；筹码和财务等增强数据应查看其中的 `stock_pool`、`start_date/end_date`、`request_mode`。
- 没有进入 active plan 的 Tushare 接口，不应被 prompt 或分析流程建议调用。
- 外部搜索只能补项目数据覆盖不了的行业事实或被 source policy 允许的缺口，不能覆盖已有项目结构化事实。
- 候选票、排序、交易状态属于分析输出层，不放进基础数据维护层。

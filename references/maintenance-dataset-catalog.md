# 基础数据维护对照表

本文档用于说明本地 `data/mart/{dataset}` 目录分别代表什么数据，以及维护、验收、分析读取时应该按什么口径理解。

执行口径以 `ashare maintain plan` 生成的 active plan 为准。没有权限、积分不足、单独权限未开通或 smoke 验证失败的接口不会进入维护流程，也不会进入 `check/report/bundle` 的读取计划。本文档描述的是项目支持的基础数据目录，不替代权限过滤结果。

bundle、research-context、research-summary 和行业证据 prompt 等分析数据输入能力的读取关系见 [分析能力对照表](analysis-capability-catalog.md)。

日常维护验收默认看最近 30 个交易日和最近 30 个自然日事件分区；基础库本身长期保留，不做 30/60/120 日滚动删除。120 日或更长窗口属于策略分析读取选择，应在 `ashare analysis bundle --trade-days` 中显式指定。

## 维护与验收口径

| 口径 | 适用数据 | 默认验收 |
| --- | --- | --- |
| 连续交易日窗口 | 日线行情、日指标、复权因子、涨跌停价、指数、行业、普通资金流、龙虎榜、融资融券 | 最近 30 个交易日；可用 `--trade-days` 调整 |
| 目标日/最新分区 | 题材成分映射、热榜、北向相关信号、股票池筹码数据 | 只验收目标日或最新快照，不强制连续历史窗口 |
| 自然日事件窗口 | 公告、业绩预告、新闻快讯 | 最近 30 个自然日；新闻只代表当前可见快讯快照 |
| 显式股票池报告期 | 利润表、资产负债表、现金流、业绩快报、财务指标、主营、分红、审计意见 | 不按交易日窗口验收；检查已落库报告期和股票池覆盖 |

## 核心行情与市场环境

| 数据层 | mart dataset | 接口/来源 | profile | 分区与验收 | 代表数据 |
| --- | --- | --- | --- | --- | --- |
| 交易日历 | `trade_cal` | Tushare `trade_cal` | basic | `exchange=SSE`；用于校验最近 30 个交易日是否覆盖 | 交易日、休市日、历史窗口补齐 |
| 股票基础 | `stock_basic` | Tushare `stock_basic` | basic | `snapshot_date=YYYYMMDD`；验收目标日快照 | 全市场代码、名称、行业、上市状态、市场板块 |
| 日线行情 | `daily` | Tushare `daily` | basic | `trade_date=YYYYMMDD`；连续交易日窗口 | 开高低收、涨跌幅、成交量、成交额 |
| 每日指标 | `daily_basic` | Tushare `daily_basic` | basic | `trade_date=YYYYMMDD`；连续交易日窗口 | 换手率、量比、市值、估值 |
| 复权因子 | `adj_factor` | Tushare `adj_factor` | basic | `trade_date=YYYYMMDD`；连续交易日窗口 | 真实区间涨跌幅、复权价格计算 |
| 涨跌停价格 | `stk_limit` | Tushare `stk_limit` | basic | `trade_date=YYYYMMDD`；连续交易日窗口 | 涨停价、跌停价、封板/触板判断 |
| 指数行情 | `index_daily` | Tushare `index_daily` | basic | `trade_date=YYYYMMDD`；连续交易日窗口 | 上证、沪深 300、中证 500/1000、深成指、创业板等市场环境 |
| 指数估值 | `index_dailybasic` | Tushare `index_dailybasic` | basic | `trade_date=YYYYMMDD`；连续交易日窗口 | 指数成交、估值、换手 |
| 申万行业行情 | `sw_daily` | Tushare `sw_daily` | basic | `trade_date=YYYYMMDD`；连续交易日窗口 | 申万行业涨跌、趋势、强弱排序 |
| 中信行业行情 | `ci_daily` | Tushare `ci_daily` | basic | `trade_date=YYYYMMDD`；连续交易日窗口 | 中信行业涨跌、趋势、交叉验证 |

## 资金、情绪与短线结构

| 数据层 | mart dataset | 接口/来源 | profile | 分区与验收 | 代表数据 |
| --- | --- | --- | --- | --- | --- |
| 个股资金流 | `moneyflow` | Tushare `moneyflow` | standard | `trade_date=YYYYMMDD`；连续交易日窗口 | 个股大单、小单、主力资金 |
| 个股资金流 DC | `moneyflow_dc` | Tushare `moneyflow_dc` | standard | `trade_date=YYYYMMDD`；允许发布滞后重试 | 东方财富口径个股资金流 |
| 个股资金流 THS | `moneyflow_ths` | Tushare `moneyflow_ths` | standard | `trade_date=YYYYMMDD`；连续交易日窗口 | 同花顺口径个股资金流 |
| 行业资金流 THS | `moneyflow_ind_ths` | Tushare `moneyflow_ind_ths` | standard | `trade_date=YYYYMMDD`；连续交易日窗口 | 同花顺行业资金承接 |
| 行业资金流 DC | `moneyflow_ind_dc` | Tushare `moneyflow_ind_dc` | standard | `trade_date=YYYYMMDD`；连续交易日窗口 | 东方财富行业资金承接 |
| 概念资金流 THS | `moneyflow_cnt_ths` | Tushare `moneyflow_cnt_ths` | standard | `trade_date=YYYYMMDD`；连续交易日窗口 | 同花顺概念资金承接 |
| 沪深港通股票列表 | `stock_hsgt` | Tushare `stock_hsgt` | standard | `trade_date=YYYYMMDD`；目标日信号 | 沪股通、深股通可交易股票池 |
| 沪深港通资金流 | `moneyflow_hsgt` | Tushare `moneyflow_hsgt` | standard | `trade_date=YYYYMMDD`；目标日信号，允许发布滞后 | 北向资金总量和方向 |
| 沪深股通十大成交股 | `hsgt_top10` | Tushare `hsgt_top10` | standard | `trade_date=YYYYMMDD`；目标日信号，允许发布滞后 | 北向活跃成交个股 |
| 融资融券明细 | `margin_detail` | Tushare `margin_detail` | standard | `trade_date=YYYYMMDD`；允许发布滞后重试 | 杠杆资金变化 |
| 龙虎榜 | `top_list` | Tushare `top_list` | standard | `trade_date=YYYYMMDD`；允许发布滞后重试 | 活跃资金席位、短线强票验证 |
| 涨跌停池 | `limit_list_d` | Tushare `limit_list_d`，AKShare fallback | full | `trade_date=YYYYMMDD`；允许发布滞后重试 | 涨停、跌停、炸板明细 |
| 连板梯队 | `limit_step` | Tushare `limit_step` | full | `trade_date=YYYYMMDD`；允许发布滞后重试 | 连板高度、情绪周期 |
| 概念强度 | `limit_cpt_list` | Tushare `limit_cpt_list` | full | `trade_date=YYYYMMDD`；允许发布滞后重试 | 当日强概念、连板数量 |
| 开盘啦榜单 | `kpl_list` | Tushare `kpl_list` | full | `trade_date=YYYYMMDD`；允许发布滞后重试 | KPL 题材、涨停、炸板等补充描述 |
| 同花顺涨跌停榜单 | `limit_list_ths` | Tushare `limit_list_ths` | full | `trade_date=YYYYMMDD`；允许发布滞后重试 | THS 题材与涨跌停补充描述 |
| 同花顺热榜 | `ths_hot` | Tushare `ths_hot` | full | `trade_date=YYYYMMDD`；目标日信号 | 热股、热门概念 |
| 东方财富热榜 | `dc_hot` | Tushare `dc_hot` | full | `trade_date=YYYYMMDD`；目标日信号 | 人气榜、飙升榜 |

## 行业、概念与题材成分映射

| 数据层 | mart dataset | 接口/来源 | profile | 分区与验收 | 代表数据 |
| --- | --- | --- | --- | --- | --- |
| 申万行业分类 | `index_classify` | Tushare `index_classify` | standard | `snapshot_date=YYYYMMDD`；目标日快照 | 申万一级、二级、三级行业分类 |
| 申万行业成分 | `index_member_all` | Tushare `index_member_all` | standard | `snapshot_date=YYYYMMDD`；目标日快照 | 股票到申万行业成分映射 |
| 中信行业成分 | `ci_index_member` | Tushare `ci_index_member` | full | `snapshot_date=YYYYMMDD`；目标日快照 | 股票到中信行业成分映射 |
| 同花顺板块 | `ths_index` | Tushare `ths_index` | full | `snapshot_date=YYYYMMDD`；目标日快照 | THS 行业、概念板块目录 |
| 同花顺板块成分 | `ths_member` | Tushare `ths_member` | full | `snapshot_date=YYYYMMDD`；由 `ths_index` 驱动 | THS 概念/行业到股票映射 |
| 东方财富板块 | `dc_index` | Tushare `dc_index` | full | `trade_date=YYYYMMDD`；目标日信号 | 东财行业、概念、地域板块目录 |
| 东方财富板块成分 | `dc_member` | Tushare `dc_member` | full | `trade_date=YYYYMMDD`；由 `dc_index` 驱动，允许发布滞后 | 东财概念/行业到股票映射 |
| 通达信板块 | `tdx_index` | Tushare `tdx_index` | full | `trade_date=YYYYMMDD`；目标日信号 | 通达信概念、行业、风格、地域板块目录 |
| 通达信板块成分 | `tdx_member` | Tushare `tdx_member` | full | `trade_date=YYYYMMDD`；由 `tdx_index` 驱动，允许发布滞后 | 通达信板块到股票映射 |
| 开盘啦题材成分 | `kpl_concept_cons` | Tushare `kpl_concept_cons` | full | `trade_date=YYYYMMDD`；允许发布滞后 | KPL 题材到股票映射 |
| 核心指数权重 | `index_weight` | Tushare `index_weight` | standard | `snapshot_date=YYYYMMDD`；目标日快照 | 上证 50、沪深 300、中证 500/1000 权重 |

这些表是“产业链/题材 -> 成分股 -> 高弹性候选”的基础维表，不是策略筛票结果。策略层应该基于这些维表另行输出候选池、排序和解释。

## 事件、公告与新闻

| 数据层 | mart dataset | 接口/来源 | profile | 分区与验收 | 代表数据 |
| --- | --- | --- | --- | --- | --- |
| A 股公告 | `a_stock_notice` | project builtin / AKShare | standard | `publish_date=YYYY-MM-DD`；自然日事件窗口，允许健康空分区 | 风险提示、重组、订单、减持、药品批准等 |
| 业绩预告 | `earnings_forecast` | project builtin / AKShare | standard | `publish_date=YYYY-MM-DD`；自然日事件窗口，允许健康空分区 | 业绩弹性、预增、扭亏、预亏 |
| 新闻快讯 | `event_news` | project builtin / Tushare 资讯页 | full | `news_date=YYYY-MM-DD`；只检查最近可见快讯快照 | 政策、产业、公司催化 |

`event_news` 不是历史新闻 API，不承诺补齐长历史。它适合做当日或近期资讯证据，不适合当成可回测的历史新闻库。

## 股票池增强数据

| 数据层 | mart dataset | 接口/来源 | profile | 分区与验收 | 代表数据 |
| --- | --- | --- | --- | --- | --- |
| 筹码胜率 | `cyq_perf` | Tushare `cyq_perf` | full | `trade_date=YYYYMMDD`；需要 `--include-stock-pool-datasets` 和显式股票池 | 筹码平均成本、胜率、获利盘 |
| 筹码分布 | `cyq_chips` | Tushare `cyq_chips` | full | `trade_date=YYYYMMDD`；需要 `--include-stock-pool-datasets` 和显式股票池 | 各价位筹码占比 |

股票池日频数据不按全市场连续窗口验收。默认只检查目标日是否落库，以及本次请求股票池的覆盖率。

## 财务与披露数据

| 数据层 | mart dataset | 接口/来源 | profile | 分区与验收 | 代表数据 |
| --- | --- | --- | --- | --- | --- |
| 利润表 | `income` | Tushare `income` | full | `period=YYYYMMDD`；需要 `--include-financials` 和显式股票池 | 收入、利润、费用、利润结构 |
| 资产负债表 | `balancesheet` | Tushare `balancesheet` | full | `period=YYYYMMDD`；需要 `--include-financials` 和显式股票池 | 资产、负债、权益、偿债风险 |
| 现金流量表 | `cashflow` | Tushare `cashflow` | full | `period=YYYYMMDD`；需要 `--include-financials` 和显式股票池 | 经营、投资、筹资现金流 |
| 业绩快报 | `express` | Tushare `express` | full | `period=YYYYMMDD`；需要 `--include-financials` 和显式股票池 | 快报收入、利润、增速 |
| 财务指标 | `fina_indicator` | Tushare `fina_indicator` | full | `period=YYYYMMDD`；需要 `--include-financials` 和显式股票池 | ROE、毛利率、净利率、成长性 |
| 主营业务构成 | `fina_mainbz` | Tushare `fina_mainbz` | full | `period=YYYYMMDD`；需要 `--include-financials` 和显式股票池 | 产品/地区收入结构 |
| 分红送股 | `dividend` | Tushare `dividend` | full | `period=YYYYMMDD`；需要 `--include-financials` 和显式股票池 | 分红、送转、除权除息 |
| 财务审计意见 | `fina_audit` | Tushare `fina_audit` | full | `period=YYYYMMDD`；需要 `--include-financials` 和显式股票池 | 审计意见、风险排雷 |
| 财报披露日期 | `disclosure_date` | Tushare `disclosure_date` | full | `period=YYYYMMDD`；不需要股票池 | 财报预约披露、实际披露日期 |

财务重表不进入默认全市场日扫。推荐按公告披露日、候选池、重点行业或明确股票池做增量维护；只有显式传 `--all-stocks-financials` 时才允许全市场财务回填。`maintain check/report` 会输出财务分区的股票池观测覆盖率，用于复盘本次请求股票池和实际返回股票数；业绩快报、分红、审计意见等天然稀疏表不会因为覆盖不足自动判定维护失败。

## 读取建议

| 场景 | 推荐读取方式 |
| --- | --- |
| 每日维护是否健康 | `ashare maintain report --end-date YYYYMMDD --profile full --trade-days 30 --event-days 30` |
| 全市场短线/题材分析 | `ashare analysis bundle --as-of YYYYMMDD --profile full --trade-days 30` 或 `60` |
| 趋势、量价结构、风格回看 | `ashare analysis bundle --as-of YYYYMMDD --profile full --trade-days 120` 或策略指定窗口 |
| 个股或候选池财务验证 | 先用 `maintain daily/backfill --group financials --include-financials --stock ...` 落库，再用 bundle 或直接读 mart |
| 精确表级排查 | 直接读 `data/mart/{dataset}/{partition_key}=.../part.parquet` 和旁边的 `_meta.json` |

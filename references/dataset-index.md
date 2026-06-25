# 数据索引

本文件是给 LLM agent 快速定位数据用的索引。它不是完整契约；完整注册项以 `uv run ashare data list --format json`、feature 注册表和 mart/feature meta 为准。

## 使用方式

1. 先用 `references/data-map.md` 判断需要哪类事实。
2. 在本索引中找到候选 dataset 或 feature。
3. 用 `mart meta` / `feature meta` 确认分区日期、行数、输入和质量。
4. 只读取与用户问题相关的最小样本或分区。

## 核心 Mart

| 目标 | Dataset | 分区 | 适合回答 | 边界 |
| --- | --- | --- | --- | --- |
| 交易日 | `trade_cal` | `exchange=SSE` | 今天/目标日是否交易日、历史交易窗口 | 不代表市场强弱 |
| 股票身份 | `stock_basic` | `snapshot_date=YYYYMMDD` | 股票池、名称、行业、上市状态 | 行业字段较粗，不证明业务暴露 |
| 个股日线 | `daily` | `trade_date=YYYYMMDD` | 价格、涨跌幅、成交量、成交额 | 不证明基本面 |
| 日线指标 | `daily_basic` | `trade_date=YYYYMMDD` | 换手、市值、估值、量比 | 估值需结合财务口径 |
| 指数行情 | `index_daily` | `trade_date=YYYYMMDD` | 大盘和风格指数走势 | 不直接推出个股结论 |
| 指数估值 | `index_dailybasic` | `trade_date=YYYYMMDD` | 指数估值、成交、换手 | 只作市场环境辅助 |
| 申万行业 | `sw_daily` | `trade_date=YYYYMMDD` | 申万行业强弱 | 不能替代产业链拆解 |
| 中信行业 | `ci_daily` | `trade_date=YYYYMMDD` | 中信行业强弱交叉验证 | 不能替代产业链拆解 |
| 东方财富板块 | `dc_index` | `trade_date=YYYYMMDD` | 题材、行业、地域板块热度 | 概念热度不证明公司正宗程度 |
| 涨跌停明细 | `limit_list_d` | `trade_date=YYYYMMDD` | 涨停、跌停、炸板结构 | 只代表短线行为 |
| 同花顺涨停池 | `limit_list_ths` | `trade_date=YYYYMMDD` | 涨停题材标签、封单、连板辅助 | 题材标签需回查公司证据 |
| 龙虎榜 | `top_list` | `trade_date=YYYYMMDD` | 活跃席位和短线认可 | 不单独支撑基本面结论 |
| 资金流 | `moneyflow_dc`, `moneyflow`, `moneyflow_ths` | `trade_date=YYYYMMDD` | 资金承接和市场验证 | 只能辅助确认 |
| 公告 | `a_stock_notice` | `publish_date=YYYY-MM-DD` | 公司事件、风险、订单线索 | 需读公告原文或 evidence |
| 财务 | `income`, `balancesheet`, `cashflow`, `fina_indicator`, `fina_mainbz` | `period=YYYYMMDD` | 财报质量、收入构成、主营验证 | 需要明确股票池和报告期 |

## Feature

| Feature | 分区 | 适合回答 | 必须回查 |
| --- | --- | --- | --- |
| `market_strength` | `as_of=YYYYMMDD`, `window=N` | 市场环境、指数强弱、成交趋势 | `index_daily`, `index_dailybasic` |
| `industry_strength` | `as_of=YYYYMMDD`, `window=N` | 行业强弱排序 | `sw_daily`, `ci_daily` |
| `concept_strength` | `as_of=YYYYMMDD`, `window=N` | 正在被定价的主题和概念 | `dc_index` 和成分股证据 |
| `limit_sentiment` | `as_of=YYYYMMDD`, `window=N` | 短线情绪、涨停结构 | `limit_list_d`, `limit_list_ths` |
| `leader_validation` | `as_of=YYYYMMDD`, `window=N` | 龙头候选的市场认可线索 | 行情、资金、龙虎榜、涨停池、公司证据 |
| `elasticity_candidates` | `as_of=YYYYMMDD`, `window=N` | 高弹性候选筛查 | 行情、换手、市值、资金、公司证据 |

## 产业链和公司暴露

| 目标 | 优先数据 |
| --- | --- |
| 产业链拆解 | `references/source-registry.md`、evidence、traceable relations |
| 公司产品/客户/订单 | 公告、年报、半年报、IR、问询回复、合格 evidence、traceable relations |
| 公司主营构成 | `fina_mainbz`、定期报告、公告 |
| 产业价格/供需/capex/招投标 | evidence 或可复用来源拉取的 evidence |
| 候选池分层 | mart/feature 发现线索，evidence/relations/财务验证暴露度 |

## 最小确认命令

```bash
uv run ashare daily status --as-of YYYYMMDD --format json
uv run ashare mart meta DATASET --trade-date YYYYMMDD
uv run ashare feature meta FEATURE --as-of YYYYMMDD --window N
uv run ashare feature read FEATURE --as-of YYYYMMDD --window N --columns COLS --sort SCORE --limit 30 --format json
uv run ashare evidence search --industry INDUSTRY --format json
uv run ashare relations search --entity ENTITY --format json
```

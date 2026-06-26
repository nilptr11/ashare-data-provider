# 数据地图

本文件告诉 LLM agent：本地数据底座已经准备了什么，适合回答什么，不能回答什么。默认先读本文件，再决定是否需要看底层命令。

## 使用原则

1. 先确认本地是否已有可用事实，不要直接去外部搜索。
2. 先看数据层级和适用边界，再读具体分区。
3. 只在数据缺失、过期或覆盖不足时，才根据 `references/source-registry.md` 去补证据。
4. 不为单个问题现场生成专属 workflow；LLM agent 自己组合数据和证据。
5. 输出时说明数据日期、来源、缺口和结论强度。
6. 主候选池先从本地数据生成；手写行业、概念或公司名单只能作为先验观察单列。

## 数据层级

| 层级 | 路径 | 作用 | 结论边界 |
| --- | --- | --- | --- |
| mart | `data/mart/` | 行情、指数、行业、公告、财务、资金等结构化事实 | 可以支持事实查询和交叉验证 |
| feature | `data/features/` | 市场强弱、行业/概念强弱、龙头验证、高弹性候选等可复现信号 | 只支持筛查、排序和线索发现 |
| evidence | `data/evidence/` | 产业价格、订单、产能、capex、政策、招投标等外部证据 | 支持 mart 覆盖不了的产业事实 |
| relations | `data/relations/` | 公司、产品、客户、产业链节点和关系等慢变量 | traceable 关系库；每条记录必须带来源或推理依据、置信度和有效期 |
| runs | `data/runs/` | 研究过程和输出留痕 | 不是事实源 |
| reports | `data/reports/` | 日常检查、展示和派生报告 | 不是事实源 |

用户输入的交易模式用于归一化当次研究的主要矛盾、优先数据、证据要求和失效条件。

## 基础数据族

| 数据族 | 代表数据 | 主要用途 | 不足以支持 |
| --- | --- | --- | --- |
| 交易日与股票身份 | `trade_cal`, `stock_basic` | 确认交易日、股票池、上市状态、基础身份 | 行业主线、公司业务暴露 |
| A 股行情与估值 | `daily`, `daily_basic` | 个股价格、成交、换手、市值、估值 | 公司订单、客户、产品真实性 |
| 指数与行业 | `index_daily`, `index_dailybasic`, `sw_daily`, `ci_daily` | 市场环境、风格、行业强弱 | 产业链具体环节和公司正宗程度 |
| 概念与成分 | `dc_index`, `ths_index`, `dc_member`, `ths_member`, `index_member_all` | 题材发现、候选池初筛、市场关注线索 | 公司业务暴露度确认 |
| 短线情绪 | `limit_list_d`, `limit_list_ths`, `top_list` | 涨跌停、龙虎榜、短线认可度和扩散线索 | 中长期基本面兑现 |
| 资金与流动性 | `moneyflow_dc`, `moneyflow`, `stk_factor` | 市场资金验证、强弱交叉检查 | 独立公司价值判断 |
| 公告与财务 | `a_stock_notice`, `fina_mainbz`, `income`, `balancesheet`, `cashflow`, `fina_indicator` | 公司暴露度、收入构成、风险事件、财务验证 | 未披露订单或产业外部价格 |

常用数据定位见 `references/dataset-index.md`；完整注册数据集以 `uv run ashare data list --format json` 为准。

## Feature 数据族

| Feature | 主要用途 | 使用边界 |
| --- | --- | --- |
| `market_strength` | 判断市场环境、指数强弱、成交状态 | 不能推出具体公司结论 |
| `industry_strength` | 比较行业强弱和趋势 | 不能替代产业链拆解 |
| `concept_strength` | 发现正在被定价的主题和概念 | 不能证明公司业务正宗 |
| `limit_sentiment` | 检查情绪、涨跌停结构和扩散 | 不能替代基本面证据 |
| `leader_validation` | 找市场认可的龙头线索 | 不能直接判断长期龙头 |
| `elasticity_candidates` | 找高弹性候选 | 不能直接输出交易动作 |

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

LLM agent 应先梳理产业链节点，再把公司、产品、客户、供应关系映射到节点。映射时保留证据引用、证据强弱和缺口。当次分析形成的可复用慢变量关系应直接通过 `relations ingest` 落到 relations。

## 产业链研究时的默认读法

1. 用行业、概念和市场 feature 找“是否被市场定价”的线索，先生成数据驱动的主题和候选池。
2. 用概念成分、指数成分、公告和主营构成扩展候选；手写的已知公司名单只能作为先验观察。
3. 用 evidence / relations / 公告 / 财务验证公司是否真的处在产业链关键环节。
4. 用短线情绪、资金、龙虎榜、涨停池检查市场认可度。
5. 按证据强弱、市场认可度和研究优先级给候选分层。
6. 对可复用的产业链节点、产品暴露、上下游、客户或供应关系执行 `relations ingest`。

这个读法是研究纪律，不是固定 workflow。缺任一类关键证据时，应降低结论强度。

产业链输出应能被复核：至少包含节点、公司、支撑来源、来源日期、证据强弱和缺口。只有市场线索但缺公司证据时，不要放入重点研究。

## 最小检查命令

```bash
uv run ashare daily status --as-of YYYYMMDD --format json
uv run ashare data list --format json
uv run ashare data check --as-of YYYYMMDD --format json
uv run ashare mart meta DATASET --trade-date YYYYMMDD
uv run ashare feature meta FEATURE --as-of YYYYMMDD --window 20
```

详细命令只在需要维护、补数、抽样核验或留痕时使用；不要把命令清单当成固定研究流程。

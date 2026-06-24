# 分析能力对照表

本文档说明新架构下每个面向分析数据读取或 LLM 输入的能力分别适合做什么、读取哪些结构化数据、输出什么产物，以及缺数据时应该如何降级。

基础数据目录见 [基础数据维护对照表](maintenance-dataset-catalog.md)。研究框架 prompt 不纳入本表；Codex 默认以用户当次指令为分析入口。

## 能力总览

| 能力 | 入口 | 输入 | 输出 | 主要用途 | 不适合做什么 |
| --- | --- | --- | --- | --- | --- |
| 每日基础库维护 | `ashare daily run/status/report/repair` | 注册 dataset、mart、feature、context | `ashare.daily_run_report.v1` | 收盘后一次性刷新默认基础库、特征和 market context | 不生成投研结论，不采开放式外部 evidence |
| 数据集构建检查 | `ashare data list/check/build/update` | connector、raw store、dataset catalog | mart 分区和质量状态 | 单表补数、重跑、检查分区健康 | 不做行业判断 |
| Mart 读取 | `ashare mart read/meta` | `data/mart/{dataset}` | 表格或元数据 | Codex 直接读取行情、资金、公告、财务等事实 | 不读取 raw 响应作分析默认输入 |
| Feature 构建读取 | `ashare feature build/read/meta` | mart 分区 | `data/features/{feature}` | 市场结构、行业强度、概念强度、龙头验证、高弹性候选 | 不输出买卖建议 |
| 外部 Evidence | `ashare evidence ingest/search/export/adapter-*` | curated JSON/JSONL、accepted adapter | `data/evidence/records.jsonl` | 补产业、海外、政策、招投标、capex、价格、产能等项目外事实 | 不覆盖已有 mart 事实，只接受可追溯来源 |
| Knowledge | `ashare knowledge propose/accept/search/snapshot` | 人工审核 proposal、evidence trace | `data/knowledge/current.jsonl` | 保存实体别名、行业链、公司产品关系等慢变量 | 不代表当日市场强弱 |
| Context Pack | `ashare context build market-structure/industry/stock` | mart、feature、evidence、knowledge | `data/context_packs/.../context.json` | 把分析所需事实组装成 Codex 可读快照 | 不直接生成结论 |
| Protocol 模板 | `ashare protocols list/show/validate/output-schema` | registered protocol specs | protocol spec 和输出 schema | 沉淀反复使用的分析模板和质量门 | 不替代用户当次框架 |
| Run 留痕 | `ashare runs record/list/replay` | question、context、evidence、knowledge、输出 | `runs/<run_id>` | 保存分析上下文、artifact hash 和质量门，便于复盘 | 不回流为事实源 |

## 推荐读取顺序

| 分析场景 | 推荐顺序 |
| --- | --- |
| 全市场短线、题材、风格强弱 | `daily status` 确认可分析 -> 读 market context -> 必要时下钻 mart/feature |
| 产业方向判断 | 读 market/industry context -> 搜索或入库外部 evidence -> 用龙头和成交趋势验证 |
| 单股研究数据准备 | 读 stock context -> 补必要 mart/feature -> 外部证据只补产业和公司披露缺口 |
| 海外 capex、协会数据、商品价格 | 优先官方/公司/协会/交易所来源 -> 整理为 evidence -> 高频来源晋升 adapter |
| 数据缺口排查 | `daily status` 看系统状态，`data check` 查表，context `data_gaps` 看分析影响 |
| 盘中或实时分析 | 项目日频数据只代表最近完整交易日；盘中价格优先要求用户提供截图或明确实时来源 |

LLM 默认不应该直接读 raw store，也不应该现场拼散接口。默认入口是用户问题；数据读取优先级是 context pack、mart/feature、evidence、knowledge。

## 能力与数据依赖

| 分析能力 | 核心数据 | 增强数据 | 缺口处理 |
| --- | --- | --- | --- |
| 市场环境判断 | `trade_cal`、`index_daily`、`index_dailybasic`、`sw_daily`、`ci_daily` | `daily`、`daily_basic`、行业/概念成交和资金 feature | 缺核心指数或行业时不下市场顺逆风结论 |
| 全市场量价筛查 | `daily`、`daily_basic`、`adj_factor`、`stk_limit`、`stock_basic` | `cyq_perf`、`cyq_chips`、热度和资金数据 | 窗口由用户框架或 context 参数决定 |
| 题材/概念下钻 | `index_classify`、`index_member_all`、`ci_index_member`、`ths_index`、`ths_member`、`dc_index`、`dc_member`、`tdx_index`、`tdx_member`、`kpl_concept_cons`、`index_weight` | `limit_cpt_list`、`kpl_list`、`limit_list_ths`、`moneyflow_cnt_ths` | 成分映射缺失时只能做粗行业分析 |
| 短线情绪与涨停结构 | `limit_list_d`、`limit_step`、`limit_cpt_list`、`top_list` | `kpl_list`、`limit_list_ths`、热度数据 | 当日源发布滞后时标 warning；不能把空分区当无行情 |
| 资金交叉验证 | `moneyflow`、`moneyflow_dc`、`moneyflow_ths`、`moneyflow_ind_ths`、`moneyflow_ind_dc`、`moneyflow_cnt_ths` | `moneyflow_hsgt`、`hsgt_top10`、`stock_hsgt`、`margin_detail` | 资金流只能作辅助确认，不单独支撑基本面结论 |
| 单股行情结构 | `daily`、`daily_basic`、`adj_factor`、`stk_limit` | `limit_list_d`、`top_list`、`margin_detail` | 缺 K 线/成交量/波动时写“暂无可靠数据” |
| 单股基本面质量 | `income`、`balancesheet`、`cashflow`、`fina_indicator`、`fina_mainbz` | `express`、`dividend`、`fina_audit`、`disclosure_date` | 财务重表需要显式股票池；缺结构化财报时转官方公告/年报抽取 |
| 事件催化 | `a_stock_notice`、`earnings_forecast` | `top_list`、涨跌停、热度和资金数据 | 公告/业绩预告允许健康空分区；行情侧只用于验证市场是否认可 |
| 外部产业证据 | 无固定 A 股 mart | evidence record、adapter spec、source registry | 必须记录来源、URL、发布时间、查询时间、置信度和缺口 |

## 产物字段速查

| 产物 | 关键字段 | 读取重点 |
| --- | --- | --- |
| `daily_run_report` | `summary`、`datasets`、`features`、`context`、`blocking` | 判断基础库是否 ready，以及哪些分区需要 repair |
| `mart metadata` | `dataset`、`partition`、`rows`、`content_hash`、`source` | 确认事实分区来源、行数和内容 hash |
| `feature metadata` | `feature`、`as_of`、`window`、`inputs`、`content_hash` | 确认可复现评分或排序使用了哪些输入 |
| `context_pack` | `pack_id`、`as_of`、`coverage`、`facts`、`inputs`、`data_gaps`、`source_policy_summary` | Codex 分析前的默认事实快照 |
| `evidence_record` | `claim`、`source_type`、`source_url`、`published_at`、`query_time`、`confidence`、`verification` | 补足项目数据覆盖不了的行业事实 |
| `knowledge_record` | `subject`、`predicate`、`object`、`confidence`、`source` | 慢变量关系图谱和别名映射 |
| `run_manifest` | `question`、`protocol_id`、`context_packs`、`evidence`、`knowledge`、`quality` | 复盘本次分析用了哪些材料和质量门 |

## 使用边界

- `daily run` 默认维护完整日常基础库、`5/20/60` feature 和 market context；财务重表、筹码、外部 evidence、knowledge 按研究问题单独补。
- `data_gaps=0` 只表示本次 context 选定输入没有缺口，不表示投研问题已经完整覆盖。
- 外部搜索只能补项目数据覆盖不了的产业事实或公司披露缺口，不能覆盖已有 mart 事实。
- 重复使用且影响评分、排序或回测的数值来源，应从 curated evidence 晋升为 accepted adapter。
- 候选票、排序解释、交易状态属于分析输出层，不放进基础数据维护层。

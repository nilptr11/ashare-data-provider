# 数据能力和新鲜度

本文件告诉 Codex：本项目能提供哪些数据能力，以及同一类事实在不同新鲜度下应该读哪里。它不是交易模式说明，也不是固定研究流程。

## 核心定位

本项目优先维护高复用、结构稳定、时间语义清楚的本地事实层。Codex 根据用户问题自行决定要看哪些信号和证据；项目只负责把数据能力、来源边界、新鲜度和禁止用途说明清楚。

外部网页/API 默认先作为获取方法或按需观察源；只有当次研究用到的具体 claim 才进入 evidence，分析确认的慢变量关系才进入 relations。

## 新鲜度分层

| 层 | 典型入口 | 来源 | finality | 适合用途 | 禁止用途 |
| --- | --- | --- | --- | --- | --- |
| canonical EOD | `ashare.daily`、`ashare.daily_basic`、`ashare.index_daily`、`ashare.sw_daily`、`ashare.ci_daily`、`ashare.dc_index` | Tushare 等收盘后接口 | `final` | 历史回看、候选池市场线索、feature 输入、收盘后复盘 | 不能证明公司业务暴露；未收盘时不能当作今日实时状态 |
| current quote | `rdf quotes current` | Tencent current quote，fallback Eastmoney intraday | `provisional` | 今日未收盘或 Tushare EOD 未更新时，查看当前价格、涨跌幅、成交量和异动 | 不能覆盖 `ashare.daily`，不能单独生成主候选，不能做交易执行 |
| intraday snapshot | `ashare.intraday_snapshot` | Eastmoney push2 等按需 snapshot | `provisional` | 需要把某次盘中观察留痕到 mart 时使用 | 不能覆盖 canonical EOD，不能作为公司事实 |
| evidence | `data/evidence/`、`rdf evidence ...` | 公告、官方统计、协会、招投标等 | 由证据质量决定 | 支撑订单、客户、产能、价格、政策等具体 claim | 搜索命中、标题、热榜、概念标签不能当高置信 claim |
| relations | `data/relations/`、`rdf relations ...` | Codex/人工分析确认后写入 | 慢变量 | 复用公司、产品、客户、产业链节点关系 | 不批量沉淀分类成分、热榜、资金流或未经分析的关系 |

## 行情读法

当用户问历史、近 20/40/60 日、收盘后复盘或 feature 输入时，优先读取 canonical EOD：

```bash
uv run rdf inventory summary --as-of YYYYMMDD
uv run rdf datasets read-window ashare.daily --as-of YYYYMMDD --count 20 --limit 100
uv run rdf features read ashare.daily_momentum --as-of YYYYMMDD --window 20 --limit 30
```

`read-window` 对 `ashare.daily` 这类交易日分区表示近 N 个已入库交易日，不是自然日。

当用户问“今天”“当前”“盘中”“现在涨跌”“Tushare 今日还没更新”时，先使用 current quote：

```bash
uv run rdf quotes current --security-id 000001.SZ
uv run rdf quotes current --security-id 000001.SZ --source tencent
uv run rdf quotes current --security-id 000001.SZ --source eastmoney
```

返回结果会同时给出：

- `current_quote`：当前 provisional quote；
- `canonical_eod`：本地 `ashare.daily` 最新 final 分区；
- `boundary`：当前 quote 不能覆盖 EOD、不能单独生成主候选、不能做交易执行。

需要把一次盘中观察留到 mart 时，再用：

```bash
uv run rdf ingest dataset ashare.intraday_snapshot --recipe eastmoney.push2.quote_snapshot.to_ashare_intraday_snapshot --partition snapshot_at=ISO_TIME --param secids=0.000001
```

默认不要为了“以后可能会用”批量抓全市场盘中数据。

## 数据能力索引

| 用户要判断 | 优先入口 |
| --- | --- |
| 本地是否已有目标日期数据 | `uv run rdf inventory summary --as-of YYYYMMDD` |
| 某类数据在哪个 dataset | `uv run rdf datasets search 关键词 --as-of YYYYMMDD` |
| 近 N 个交易日行情 | `uv run rdf datasets read-window ashare.daily --as-of YYYYMMDD --count N` |
| 今日未收盘的当前行情 | `uv run rdf quotes current --security-id 000001.SZ` |
| 市场强弱和板块线索 | `ashare.market_strength`、`ashare.industry_strength`、`ashare.concept_strength`、`ashare.limit_sentiment` |
| 公司主营/财务事实 | `ashare.main_business`、`ashare_financials` 域，并回查公告正文 |
| 订单、客户、产能、政策、产业价格 | CNINFO、官方统计、协会、招投标等 evidence source |
| 可复用产业链关系 | Codex 分析确认后 `rdf relations ingest` |

## 对外部数据工具的吸收原则

`simonlin1212/a-stock-data` 这类端点集合对本项目的价值，是帮助我们识别可用来源、字段和限流经验。项目不复制它的多层端点工具形态，而是吸收为：

- Tushare：canonical EOD 主源，稳定维护 A 股历史事实；
- Tencent / Eastmoney：current quote 和 intraday provisional 观察；
- CNINFO：公司披露和公告正文补证；
- Eastmoney reportapi：研报索引和研究关注度；
- 其他高频稳定来源：先做 evidence source，确需长期结构化再升级为正式 dataset。

Codex 使用这些能力时，应先选择“要证明什么”和“需要多新的数据”，再决定读取本地 mart、feature、evidence、relations，还是按需 fetch 外部来源。

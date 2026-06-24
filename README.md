# ashare-data-provider

面向大模型和量化业务的 A 股数据 Provider + CLI。项目提供 Tushare 原始接口快速调用、常用行情数据封装，以及 A 股公告、业绩预告、时讯三类机器友好的事件 records 输出。上层选股扫描器、策略脚本或自动化任务可以优先通过 Python API 调用；CLI 作为人工调试、Codex skill 和 shell 自动化的薄封装保留。

## 安装

项目面向新项目开发，要求 Python 3.14+。

```bash
uv sync
uv run ashare list --search 日线
```

## 配置

复制示例配置并填写 token：

```bash
cp .env.example .env
```

`.env` 支持：

```bash
TUSHARE_TOKEN=your_tushare_token_here
TUSHARE_PROXY_URL=https://your-tushare-proxy.example.com
TUSHARE_POINTS=15000
TUSHARE_ALLOW_SEPARATE_PERMISSION=false
TUSHARE_COOKIE=uid=...; username=...
```

默认会自动查找 `.env`：先读当前目录，若在项目子目录中运行则继续向上查找，最后兜底读项目根目录 `.env`。配置优先级是：CLI 参数 > 系统环境变量 > `.env`。`TUSHARE_PROXY_URL` 留空时使用 Tushare SDK 默认地址。`TUSHARE_POINTS` 用于本地调用前权限判断，`TUSHARE_ALLOW_SEPARATE_PERMISSION=false` 时需要 `--force` 才会调用需单独权限的接口。
`TUSHARE_COOKIE` 只用于 `ashare events news` 抓取 Tushare 资讯网页，不会写入输出文件。

## 常用命令

查看接口清单：

```bash
ashare list --search 日线
ashare list --category 股票数据
ashare list --eligibility points_ok
ashare list --eligibility needs_separate_permission
ashare categories
```

未安装命令入口时，也可以直接用脚本：

```bash
python3 scripts/ashare_call.py list --search 日线
```

查看接口说明和官方文档链接：

```bash
ashare info daily
ashare info pro_bar --doc-id 109
ashare info cyq_chips
ashare defaults daily
ashare defaults rt_min --doc-id 416
ashare schema daily
ashare schema pro_bar --doc-id 109
```

调用接口并输出 JSON：

```bash
ashare call daily \
  -p ts_code=000001.SZ \
  -p start_date=20240101 \
  -p end_date=20240131 \
  --fields ts_code,trade_date,open,close,vol \
  --format json
```

CLI 会根据 `.env` 中的 `TUSHARE_POINTS` 和内置权限元数据拦截明显积分不足或需单独权限的接口；确需尝试时使用 `--force`：

```bash
ashare call cyq_perf --params '{"trade_date":"20260423"}' --force --format json
```

同名接口存在多份文档元数据时，可以用 `--doc-id` 或 `--key` 精确选择权限判断依据：

```bash
ashare call pro_bar --doc-id 109 \
  -p ts_code=000001.SZ \
  -p start_date=20260501 \
  -p end_date=20260529 \
  --format json
```

调用接口并保存 CSV：

```bash
ashare call stock_basic \
  -p exchange= \
  -p list_status=L \
  --fields ts_code,symbol,name,area,industry,list_date \
  --format csv \
  --output stock_basic.csv
```

### 本地持久化数据

普通 Tushare 调用会透明使用本地持久化数据：本地已有成功数据时直接读取，本地缺失时请求 API，API 成功后写入 Parquet；API 失败或返回空结果不会写入本地，下次会继续请求。默认写入 `data/tushare`，也可以通过 `ASHARE_DATA_DIR` 指定根目录。

带 `trade_date` 的请求按天落盘；带 `start_date/end_date` 且接口支持 `trade_date` 时，会先通过交易日历拆成真实交易日，再逐日补缺并拼接返回。

### 每日基础库维护与分析读取

维护层面向长期基础库，不做 60/120 日滚动删除；60/120 日只是分析读取窗口。执行流程是：

```text
权限目录 -> 可执行维护计划 -> raw cache + canonical mart -> analysis bundle
```

先生成权限目录。没有权限、积分不足或需单独权限的接口不会进入后续维护计划；`unknown` 权限接口需要最小 smoke 验证后才进入：

```bash
ashare maintain access-audit --profile full --smoke-unknown
```

查看当前账号实际会维护的数据集：

```bash
ashare maintain plan --profile full --output reports/maintenance-plan.json
```

每日增量补缺并发布分析用 mart：

```bash
ashare maintain daily \
  --as-of 2026-06-24 \
  --end-date 2026-06-24 \
  --profile full \
  --lookback-days 10 \
  --event-lookback-days 30 \
  --output reports/maintenance-daily.json
```

`--as-of` 表示运行/分析锚点；`--end-date` 表示要维护的目标交易日分区。盘后想强制维护当日数据时传 `--end-date`；不传时默认使用 `as-of` 的上一完整交易日。

历史回填不删除旧数据，适合首次建库或补历史：

```bash
ashare maintain backfill \
  --start-date 20200101 \
  --end-date 20260623 \
  --profile full \
  --output reports/maintenance-backfill.json
```

检查最近 120 个交易日和最近 180 个自然日事件分区的 mart 完整性：

```bash
ashare maintain check \
  --end-date 20260623 \
  --trade-days 120 \
  --event-days 30 \
  --profile full \
  --output reports/maintenance-check.json
```

生成每日运行报告，用于判断当日是否可分析：

```bash
ashare maintain report \
  --end-date 20260623 \
  --trade-days 120 \
  --event-days 30 \
  --profile full \
  --output reports/daily-status-report.json
```

`maintain report` 会汇总数据覆盖率、缺口、异常空分区、fallback 使用情况和 `analysis_ready`。核心行情、指数、行业缺失会把状态标为 `blocked`；资金流、短线情绪、事件、新闻缺口会作为 warning 暴露。

推荐分两个批次调度：

```bash
# 盘后初版：16:00-17:00，优先验证行情、涨跌停、指数、行业。
ashare maintain daily --as-of 2026-06-24 --end-date 2026-06-24 --profile full --lookback-days 10 --event-lookback-days 7
ashare maintain report --end-date 2026-06-24 --profile full --trade-days 120 --event-days 7

# 晚间修正版：20:00-22:30，重试公告、龙虎榜、资金流、业绩预告、新闻。
ashare maintain daily --as-of 2026-06-24 --end-date 2026-06-24 --profile full --lookback-days 10 --event-lookback-days 30 --refresh
ashare maintain report --end-date 2026-06-24 --profile full --trade-days 120 --event-days 30
```

可以用 `--group` 只维护某一类数据，例如只补资金流、短线情绪或财务：

```bash
ashare maintain backfill --start-date 20251219 --end-date 20260623 --profile full --group moneyflow
ashare maintain backfill --start-date 20251219 --end-date 20260623 --profile full --group short_term
```

逐股财务表不会默认进入 daily/full 批量任务，必须显式传入 `--include-financials`。执行逐股财务维护时还必须显式提供 `--stock`、`--stock-pool-file`、`--max-stocks`，或用 `--all-stocks-financials` 明确允许全市场逐股请求：

```bash
ashare maintain backfill \
  --start-date 20240101 \
  --end-date 20260623 \
  --profile full \
  --group financials \
  --include-financials \
  --stock 000001.SZ \
  --stock 600000.SH \
  --output reports/financials-backfill.json
```

`disclosure_date` 是按最近报告期维护的披露日程，不需要股票池；只维护披露日期时可以直接运行：

```bash
ashare maintain daily \
  --end-date 20260623 \
  --profile full \
  --group financials \
  --output reports/disclosure-date-daily.json
```

也可以用文件传股票池：

```bash
ashare maintain daily \
  --profile full \
  --group financials \
  --include-financials \
  --stock-pool-file stock-pool.txt \
  --max-stocks 100
```

维护过程会保留两层数据：

```text
data/tushare/...                         # 原始 API 请求缓存，用于追溯和避免重复请求
data/mart/{dataset}/trade_date=YYYYMMDD  # 标准分析分区表，用于高效读取
data/mart/{dataset}/publish_date=YYYY-MM-DD # 公告/业绩预告自然日分区
data/mart/trade_cal/exchange=SSE         # 标准交易日历表
```

面向 LLM 或分析框架时，不直接读零散接口缓存，而是从 mart 生成 bundle：

```bash
ashare analysis bundle \
  --as-of 2026-06-23 \
  --trade-days 120 \
  --event-days 180 \
  --profile full \
  --output analysis-bundle.json
```

`analysis bundle` 和维护命令使用同一套权限过滤后的 active plan；无权限的 Tushare 接口不会进入读取流程，也不会出现在 bundle 的 `datasets` 中。它会读取全市场量价窗口、股票基础信息、当日多口径资金流、涨跌停池、连板梯队、概念强度等本地分区，并输出 row count、核心特征、排名样本、`coverage`、`data_gaps` 和 provenance。分析阶段默认只读本地 mart；缺数据时应先运行 `maintain daily` 或 `maintain backfill` 补齐。

当前 bundle 还会按 active plan 读取指数、行业、龙虎榜、融资融券、公告、`earnings_forecast` 业绩预告、KPL/同花顺题材补充、新闻分区和已落库财务数据。`limit_list_d` 如果 Tushare 当日返回空结果，维护层会使用 AKShare 东方财富涨停/炸板/跌停池作为 fallback，并统一发布到 `limit_list_d` mart。

维护层会给 mart 分区写入 `quality_status`，区分分区存在和分区健康。核心行情、指数、行业等历史交易日数据不允许空分区；公告和业绩预告会为维护过但无记录的自然日写入健康空分区；部分 T+0/T+1 发布源如融资融券、KPL、THS 补充口径会先标记 `pending_empty`，超过滞后期仍为空才进入 `suspicious_empty` 并在后续维护中自动重试。`maintain check` 会报告 `expected_count/available_count/missing_count/coverage_ratio`、`pending_empty_partitions` 和 `quality_issues`，避免把临时空返回静默缓存成长期完整数据。

外部产业证据层不放进 A 股基础 mart。商品/材料价格、产能、库存、开工率、海外 AI capex、订单/招标、政策文件、行业研报和公司调研更适合单独建设 `evidence/industry` 层，并在分析时作为证据包和 A 股基础 bundle 组合读取。

### LLM 数据使用指引

LLM 使用本项目数据时，优先读已经生成的结构化产物，而不是现场拼零散接口。全市场分析优先使用 `ashare analysis bundle` 输出；个股研究优先使用 `research-context` 和 `research-summary` 输出；判断 Tushare 权限、替代源和外部证据边界时参考 `src/ashare_data_provider/source_policy.json`。

行业特有证据不直接放进 A 股 mart。需要商品价格、产能、库存、开工率、订单、招标、海外 capex、政策和行业协会数据时，LLM 应先使用 `prompts/industry-evidence-prompt.md` 做受控现搜，并按 `references/industry-evidence-design.md` 的 evidence schema 输出。可用来源和 adapter 候选参考 `references/industry-source-registry.json`；AI 算力链和锂电链的试点指标/source map 参考 `references/industry-source-maps.json`。

`industry-source-registry.json` 和 `industry-source-maps.json` 是研究/设计参考，不代表数据已经稳定入库。任何会进入打分、排序、回测或交易决策的数值证据，必须有 `source_url/published_at/query_time/confidence`，并优先来自官方、交易所、监管机构、公司披露或已 adapter 化来源；prompt 现搜结果默认只作为 evidence，不等同于生产级数据。

### A 股事件能力

当前高层事件能力明确为三类：A 股公告、业绩预告、时讯。公告和业绩预告走 AKShare；Tushare `forecast` 与 `news` API 暂时不作为高层能力使用。时讯继续抓取登录后可见的 Tushare 资讯页，作为当前快讯替代源。

```bash
ashare events notice --days 3 --max-rows 20 --format table
ashare events notice --stock 000001 --category 财务报告 --keyword 分红 --format jsonl --output notice.jsonl
ashare events forecast --days 60 --period 20260331 --max-rows 20 --format csv --output forecast.csv
ashare events news --source sina --source cls --format jsonl --output event-news.jsonl
ashare events news --all --snapshot-output snapshots/news-$(date +%Y%m%d%H%M%S).jsonl --format jsonl
```

CLI 只负责按调用参数输出本次结果，`--output` 写入指定文件；不传则输出到 stdout。

公告标准 records 字段包含 `id/content_hash/dedupe_key/event_type/source_kind/stock_code/stock_name/title/notice_type/publish_date/url/fetched_at/raw`。业绩预告标准 records 字段包含 `id/content_hash/dedupe_key/event_type/source_kind/period/stock_code/stock_name/metric/forecast_type/change_range/publish_date/change_summary/change_reason/fetched_at/raw`。

`events news` 输出沿用时讯 records：`id/content_hash/dedupe_key/src/source_name/channel/date/time/datetime/date_source/title/content/body/fetched_at/source_kind`，便于 JSONL 或 CSV 入库。统一使用 `ashare events news`。

`--publish-date` 只在你确认页面条目属于同一天时使用，用来覆盖自动日期补全；默认会基于当前日期或 `--anchor-date`，结合页面里的日期分隔符，把 `HH:MM` 补齐为 `date/datetime`。该替代源面向“当前可见快讯页”，不能替代 `news` API 的历史时间范围查询。
默认来源按当前 Tushare 资讯页导航抓取：`xq`、`jinshi`、`jinrongjie`、`10jqka`、`yicai`、`cls`、`eastmoney`、`wallstreetcn`、`sina`。
`content_hash` 基于标准化后的标题和正文生成，适合跨来源精确去重；`dedupe_key` 基于 `src + channel + datetime/time + content_hash` 生成，适合同源快讯幂等 upsert。
页面源通常只能看到最近一两天；业务侧需要持久化时，应按 record 的真实新闻日期分区存储。Python API 提供 `merge_news_date_partitions(base_dir, records)` 和 `read_news_date_partitions(base_dir, dates)`，使用 `YYYY-MM-DD.jsonl` 分区，避免分析最近几天时读取全量历史。

### Prism 投研上下文

`research-context` 会按内置 `source_policy.json` 跳过无权限 Tushare 接口，尽力采集 Prism 三层框架需要的结构化上下文。单个接口失败不会中断输出，而是写入 `data_gaps`，方便上层 prompt 明确标注缺口。默认 `--profile basic` 只采集核心行情、估值、指数和行业日频数据；需要财务、事件、资金流时再使用 `--profile standard` 或 `--profile full`。

```bash
ashare research-context 000001.SZ \
  --as-of 2026-06-22 \
  --profile basic \
  --lookback-days 120 \
  --event-days 90 \
  --forecast-days 180 \
  --output research-context.json
```

如需同时抓取 Tushare 资讯页当前快讯，可加 `--include-news`；该能力需要有效的 `TUSHARE_COOKIE`。

`full` 档位会额外采集 `disclosure_date/dividend/fina_audit`，用于判断财报披露计划、分红和审计意见。生成 context 后，可以再生成稳定摘要，供 LLM 直接消费，避免临时脚本现场解析和复算：

```bash
ashare research-summary research-context.json \
  --format markdown \
  --output research-summary.md
```

`research-summary` 会提炼行情结构、均线/ATR、估值、财务质量、主营分部、公告标题线索、`data_gaps`、`research_needs` 和 `external_evidence` 校验结果。行业特有数据（供需、产能、订单、客户、价格指数、补贴政策等）不要求 Provider 穷举所有行业网站；Prompt 会按 `source_policy.json` 的 `dynamic_source_discovery` 规则让 LLM 做受控发现，并要求记录来源类型、URL、查询时间、发布日期和证据等级。

`data_gaps=0` 只表示 Provider 尝试采集的数据源没有失败，不代表投研问题完整覆盖。报告是否还缺行业事实，看 `research_summary.research_needs.analysis_gaps`。LLM 外部补充后的证据建议按以下结构回填：

```json
{
  "external_evidence": [
    {
      "fact": "提取出的事实",
      "source_class": "official_government_or_regulator",
      "source_name": "来源名称",
      "url": "https://...",
      "query_time": "2026-06-22T14:00:00+08:00",
      "publish_date": "2026-06-01",
      "business_segment": "对应业务分部",
      "supports_need": "industry_policy",
      "evidence_level": "official_external",
      "confidence": "high"
    }
  ]
}
```

JSON 参数适合大模型工具调用：

```bash
ashare call trade_cal \
  --params '{"exchange":"SSE","start_date":"20240101","end_date":"20240131"}' \
  --format json
```

`key=value` 会按字符串传入；需要传数字、布尔、数组或对象时，用 `key:=JSON`：

```bash
ashare call some_api -p limit:=100 -p flags:='["a","b"]'
```

## 更新接口清单

```bash
python3 scripts/generate_interfaces.py \
  --source references/data-interfaces.md \
  --output src/ashare_data_provider/interfaces.json
```

更新官方文档入参 schema：

```bash
uv run python scripts/fetch_api_schemas.py \
  --output src/ashare_data_provider/api_schemas.json \
  --output-dir reports
```

## 测试

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
uv run python -m unittest discover -s tests
```

批量验证 Tushare 接口连通性：

```bash
uv run python scripts/smoke_all_interfaces.py --env-file .env --output-dir reports
```

该脚本默认按接口索引逐条调用，跳过积分不足和需单独权限的接口，默认间隔 `0.6s`，尽量只请求 1 条数据，并只保存成功/失败、行数、列数、耗时和错误原因。
脚本会使用内置默认参数模板 `api_defaults.json`，并在报告中带上 `known_issues.json` 里的已知问题摘要。
如需强制包含受限接口：

```bash
uv run python scripts/smoke_all_interfaces.py --env-file .env --include-restricted --output-dir reports
```

如需只复测指定接口，可重复传入 `--key api:doc_id`：

```bash
uv run python scripts/smoke_all_interfaces.py \
  --env-file .env \
  --key daily:27 \
  --key top10_floatholders:62 \
  --output-dir reports
```

如需临时禁用 `.env` 中的代理：

```bash
uv run python scripts/smoke_all_interfaces.py --env-file .env --proxy-url "" --output-dir reports
```

## Python API

```python
from ashare_data_provider import AShareProvider

provider = AShareProvider()

trade_date = provider.latest_trade_date()
completed_trade_date = provider.previous_trade_date()
stocks = provider.stock_basic()
quotes = provider.daily_snapshot(completed_trade_date)
metrics = provider.daily_basic_snapshot(completed_trade_date)
limits = provider.limit_price_snapshot(completed_trade_date)
```

`latest_trade_date()` 表示截至 `as_of` 的最近开市日，可能包含当天；`previous_trade_date()` 表示上一个已完成交易日：若 `as_of` 当天开市且已到 15:00 后则返回当天，若当天未开市则返回最近开市日，否则返回前一开市日。选股扫描器和盘中自动化默认应使用 `previous_trade_date()`。

常用接口的默认字段、默认参数、主键、日期字段等元数据维护在 `recipes.json`，可供上层数据仓库或扫描器读取：

```python
from ashare_data_provider import get_recipe

daily_recipe = get_recipe("daily")
print(daily_recipe.primary_key)
print(daily_recipe.fields)
```

官方文档里的输入参数表维护在 `api_schemas.json`，可用于上层仓库做参数校验、工具描述或 skill 生成：

```python
from ashare_data_provider import get_api_schema

daily_schema = get_api_schema("daily")
print(daily_schema.optional_params)
print(daily_schema.example_params)
```

上层仓库需要调用任意原始接口时，也走同一个 Provider：

```python
from ashare_data_provider import AShareProvider

provider = AShareProvider()
df = provider.call(
    "daily",
    params={"trade_date": "20260529"},
    fields="ts_code,trade_date,open,close,pct_chg,vol,amount",
)
```

账号缺少 `news` 单独权限时，上层也统一走事件时讯网页替代源：

```python
records = provider.event_news(sources=["sina", "cls"], max_rows=100)
```

同名接口存在多份文档元数据时，Python API 也可以指定 `doc_id` 或 `key`：

```python
df = provider.call(
    "pro_bar",
    doc_id="109",
    params={
        "ts_code": "000001.SZ",
        "start_date": "20260501",
        "end_date": "20260529",
    },
)
```

如果只需要最薄的原始调用器，也可以继续使用 `TushareCaller`：

```python
from ashare_data_provider import TushareCaller

caller = TushareCaller()
df = caller.call("daily", params={"trade_date": "20260529"})
```

代理设置按 Tushare SDK 的 `DataApi` 类级 URL 生效，等价于：

```python
from tushare.pro import client as ts_client

ts_client.DataApi._DataApi__http_url = "https://your-tushare-proxy.example.com"
```

## 说明

常规 `call` 不会绕过 Tushare 的 token、积分和接口权限限制。若某接口在账号侧没有权限，CLI 会直接返回 Tushare SDK 的错误信息。
`events news` 是显式的网页替代源：它需要有效的 Tushare 登录 Cookie，只解析登录后资讯页当前可见内容，并输出清洗后的结构化记录。

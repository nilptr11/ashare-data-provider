# tushare-fastcli

面向大模型和量化业务的 Tushare Provider SDK + 快速调用 CLI。项目把公开接口索引结构化为内置清单，上层选股扫描器、策略脚本或自动化任务可以优先通过 Python API 调用；CLI 作为人工调试、Codex skill 和 shell 自动化的薄封装保留。

## 安装

项目面向新项目开发，要求 Python 3.14+。

```bash
uv sync
uv run tsfc list --search 日线
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

配置优先级是：CLI 参数 > 系统环境变量 > `.env`。`TUSHARE_PROXY_URL` 留空时使用 Tushare SDK 默认地址。`TUSHARE_POINTS` 用于本地调用前权限判断，`TUSHARE_ALLOW_SEPARATE_PERMISSION=false` 时需要 `--force` 才会调用需单独权限的接口。
`TUSHARE_COOKIE` 只用于 `tsfc news` 抓取 Tushare 资讯网页，不会写入输出文件。

## 常用命令

查看接口清单：

```bash
tsfc list --search 日线
tsfc list --category 股票数据
tsfc list --eligibility points_ok
tsfc list --eligibility needs_separate_permission
tsfc categories
```

未安装命令入口时，也可以直接用脚本：

```bash
python3 scripts/tushare_call.py list --search 日线
```

查看接口说明和官方文档链接：

```bash
tsfc info daily
tsfc info pro_bar --doc-id 109
tsfc info cyq_chips
tsfc defaults daily
tsfc defaults rt_min --doc-id 416
tsfc schema daily
tsfc schema pro_bar --doc-id 109
```

调用接口并输出 JSON：

```bash
tsfc call daily \
  -p ts_code=000001.SZ \
  -p start_date=20240101 \
  -p end_date=20240131 \
  --fields ts_code,trade_date,open,close,vol \
  --format json
```

CLI 会根据 `.env` 中的 `TUSHARE_POINTS` 和内置权限元数据拦截明显积分不足或需单独权限的接口；确需尝试时使用 `--force`：

```bash
tsfc call cyq_perf --params '{"trade_date":"20260423"}' --force --format json
```

同名接口存在多份文档元数据时，可以用 `--doc-id` 或 `--key` 精确选择权限判断依据：

```bash
tsfc call pro_bar --doc-id 109 \
  -p ts_code=000001.SZ \
  -p start_date=20260501 \
  -p end_date=20260529 \
  --format json
```

调用接口并保存 CSV：

```bash
tsfc call stock_basic \
  -p exchange= \
  -p list_status=L \
  --fields ts_code,symbol,name,area,industry,list_date \
  --format csv \
  --output stock_basic.csv
```

### A 股事件能力

当前高层事件能力明确为三类：A 股公告、业绩预告、时讯。公告和业绩预告走 AKShare；Tushare `forecast` 与 `news` API 暂时不作为高层能力使用。时讯继续抓取登录后可见的 Tushare 资讯页，作为当前快讯替代源。

```bash
tsfc events notice --days 3 --max-rows 20 --format table
tsfc events notice --stock 000001 --category 财务报告 --keyword 分红 --format jsonl
tsfc events forecast --days 60 --period 20260331 --max-rows 20 --format csv
tsfc events news --source sina --source cls --format jsonl --output tushare-news.jsonl
```

公告标准 records 字段包含 `id/content_hash/dedupe_key/event_type/source_kind/stock_code/stock_name/title/notice_type/publish_date/url/fetched_at/raw`。业绩预告标准 records 字段包含 `id/content_hash/dedupe_key/event_type/source_kind/period/stock_code/stock_name/metric/forecast_type/change_range/publish_date/change_summary/change_reason/fetched_at/raw`。

`events news` 输出沿用时讯 records：`id/content_hash/dedupe_key/src/source_name/channel/time/datetime/title/content/body/fetched_at/source_kind`，便于 JSONL 或 CSV 入库。兼容入口 `tsfc news` 仍可使用：

```bash
tsfc news --source sina --source cls --format jsonl --output tushare-news.jsonl
tsfc news --all --format csv --output tushare-news.csv
tsfc news --source eastmoney --publish-date 2026-06-01 --retries 3 --include-summary --format json
```

`--publish-date` 只在你确认页面条目属于同一天时使用，用来把 `HH:MM` 补齐为 `datetime`；不传时保留 `time`，`datetime` 为 `null`。该替代源面向“当前可见快讯页”，不能替代 `news` API 的历史时间范围查询。
默认来源按当前 Tushare 资讯页导航抓取：`xq`、`jinshi`、`jinrongjie`、`10jqka`、`yicai`、`cls`、`eastmoney`、`wallstreetcn`、`sina`。
`content_hash` 基于标准化后的标题和正文生成，适合跨来源精确去重；`dedupe_key` 基于 `src + channel + time + content_hash` 生成，适合同源快讯幂等 upsert。

JSON 参数适合大模型工具调用：

```bash
tsfc call trade_cal \
  --params '{"exchange":"SSE","start_date":"20240101","end_date":"20240131"}' \
  --format json
```

`key=value` 会按字符串传入；需要传数字、布尔、数组或对象时，用 `key:=JSON`：

```bash
tsfc call some_api -p limit:=100 -p flags:='["a","b"]'
```

## 更新接口清单

```bash
python3 scripts/generate_interfaces.py \
  --source references/data-interfaces.md \
  --output src/tushare_fastcli/interfaces.json
```

更新官方文档入参 schema：

```bash
uv run python scripts/fetch_api_schemas.py \
  --output src/tushare_fastcli/api_schemas.json \
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
from tushare_fastcli import TushareProvider

provider = TushareProvider()

trade_date = provider.latest_trade_date()
completed_trade_date = provider.previous_trade_date()
stocks = provider.stock_basic()
quotes = provider.daily_snapshot(completed_trade_date)
metrics = provider.daily_basic_snapshot(completed_trade_date)
limits = provider.limit_price_snapshot(completed_trade_date)
```

`latest_trade_date()` 表示截至 `as_of` 的最近开市日，可能包含当天；`previous_trade_date()` 表示 `as_of` 之前的上一个已完成交易日。选股扫描器和盘中自动化默认应使用 `previous_trade_date()`。

常用接口的默认字段、默认参数、主键、日期字段等元数据维护在 `recipes.json`，可供上层数据仓库或扫描器读取：

```python
from tushare_fastcli import get_recipe

daily_recipe = get_recipe("daily")
print(daily_recipe.primary_key)
print(daily_recipe.fields)
```

官方文档里的输入参数表维护在 `api_schemas.json`，可用于上层仓库做参数校验、工具描述或 skill 生成：

```python
from tushare_fastcli import get_api_schema

daily_schema = get_api_schema("daily")
print(daily_schema.optional_params)
print(daily_schema.example_params)
```

上层仓库需要调用任意原始接口时，也走同一个 Provider：

```python
from tushare_fastcli import TushareProvider

provider = TushareProvider()
df = provider.call(
    "daily",
    params={"trade_date": "20260529"},
    fields="ts_code,trade_date,open,close,pct_chg,vol,amount",
)
```

账号缺少 `news` 单独权限时，上层也可以显式走网页替代源：

```python
records = provider.news_fallback(sources=["sina", "cls"], max_rows=100)
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
from tushare_fastcli import TushareCaller

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
`news` 命令是显式的网页替代源：它需要有效的 Tushare 登录 Cookie，只解析登录后资讯页当前可见内容，并输出清洗后的结构化记录。

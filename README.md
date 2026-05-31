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
```

配置优先级是：CLI 参数 > 系统环境变量 > `.env`。`TUSHARE_PROXY_URL` 留空时使用 Tushare SDK 默认地址。`TUSHARE_POINTS` 用于本地调用前权限判断，`TUSHARE_ALLOW_SEPARATE_PERMISSION=false` 时需要 `--force` 才会调用需单独权限的接口。

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
stocks = provider.stock_basic()
quotes = provider.daily_snapshot(trade_date)
metrics = provider.daily_basic_snapshot(trade_date)
limits = provider.limit_price_snapshot(trade_date)
```

常用接口的默认字段、默认参数、主键、日期字段等元数据维护在 `recipes.json`，可供上层数据仓库或扫描器读取：

```python
from tushare_fastcli import get_recipe

daily_recipe = get_recipe("daily")
print(daily_recipe.primary_key)
print(daily_recipe.fields)
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

本项目不会绕过 Tushare 的 token、积分和接口权限限制。若某接口在账号侧没有权限，CLI 会直接返回 Tushare SDK 的错误信息。

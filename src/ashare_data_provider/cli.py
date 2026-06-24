from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .defaults import default_params
from .events import AStockEventError, NOTICE_CATEGORIES
from .issues import known_issues
from .news import (
    DEFAULT_NEWS_SOURCES,
    TushareNewsError,
    crawl_tushare_news,
    load_tushare_cookie,
    merge_news_files,
    merge_news_records,
    normalize_news_sources,
    read_news_records,
)
from .output import emit, limit_rows, render
from .params import merge_params
from .analysis_bundle import build_market_analysis_bundle
from .maintenance import (
    MaintenanceError,
    audit_access,
    build_maintenance_plan,
    load_access_catalog,
    require_plan_has_datasets,
    run_backfill,
    run_check,
    run_daily,
    run_status_report,
)
from .provider import (
    TushareInterfaceSelectionError,
    TusharePermissionError,
    AShareProvider,
    TushareUnknownInterfaceError,
)
from .registry import InterfaceEntry, load_registry
from .research_context import build_research_context
from .research_summary import build_research_summary, load_research_context, render_research_summary_markdown
from .schemas import SchemaError, get_api_schema


OUTPUT_FORMATS = ["table", "json", "jsonl", "csv"]
ELIGIBILITY_VALUES = ["points_ok", "points_insufficient", "needs_separate_permission", "unknown"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ashare",
        description="面向大模型和量化业务的 A 股数据 Provider CLI。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="查询接口清单")
    list_parser.add_argument("--search", help="按接口名、标题、分类、描述搜索")
    list_parser.add_argument("--category", help="按分类过滤，支持部分匹配")
    list_parser.add_argument("--eligibility", choices=ELIGIBILITY_VALUES, help="按积分/权限状态过滤")
    list_parser.add_argument("--limit", type=int, default=0, help="最多显示多少条，0 表示不限制")
    list_parser.add_argument("--format", choices=["table", "json"], default="table")

    categories_parser = subparsers.add_parser("categories", help="列出全部分类")
    categories_parser.add_argument("--format", choices=["table", "json"], default="table")

    defaults_parser = subparsers.add_parser("defaults", help="查看接口默认测试参数")
    defaults_parser.add_argument("api_name")
    defaults_parser.add_argument("--doc-id", help="同名接口较多时，用 doc_id 精确定位")
    defaults_parser.add_argument("--key", help="同名接口较多时，用 api:doc_id key 精确定位")

    schema_parser = subparsers.add_parser("schema", help="查看官方文档入参 schema")
    schema_parser.add_argument("api_name")
    schema_parser.add_argument("--doc-id", help="同名接口较多时，用 doc_id 精确定位")
    schema_parser.add_argument("--key", help="同名接口较多时，用 api:doc_id key 精确定位")
    schema_parser.add_argument("--format", choices=["text", "json"], default="text")

    info_parser = subparsers.add_parser("info", help="查看接口元数据和文档链接")
    info_parser.add_argument("api_name")
    info_parser.add_argument("--doc-id", help="同名接口较多时，用 doc_id 精确定位")
    info_parser.add_argument("--format", choices=["text", "json"], default="text")

    call_parser = subparsers.add_parser("call", help="调用 Tushare 接口")
    call_parser.add_argument("api_name")
    call_parser.add_argument("-p", "--param", action="append", default=[], help="参数，支持 key=value 或 key:=JSON")
    call_parser.add_argument("--params", help="JSON object 参数")
    call_parser.add_argument("--params-file", help="从 JSON 文件读取参数")
    call_parser.add_argument("--fields", help="逗号分隔的输出字段")
    call_parser.add_argument("--doc-id", help="同名接口较多时，用 doc_id 精确定位权限元数据")
    call_parser.add_argument("--key", help="同名接口较多时，用 api:doc_id key 精确定位权限元数据")
    call_parser.add_argument("--token", help="Tushare token；默认读取 TUSHARE_TOKEN")
    call_parser.add_argument("--proxy-url", help="Tushare API 代理地址；默认读取 TUSHARE_PROXY_URL")
    call_parser.add_argument("--env-file", default=".env", help="配置文件路径，默认自动查找 .env")
    call_parser.add_argument("--allow-unknown", action="store_true", help="允许调用未在索引里的接口名")
    call_parser.add_argument("--force", action="store_true", help="忽略积分/权限元数据提示，强制调用")
    call_parser.add_argument("--current-points", type=int, help="当前账号积分；默认读取 TUSHARE_POINTS")
    call_parser.add_argument("--max-rows", type=int, default=0, help="只输出前 N 行，0 表示不限制")
    call_parser.add_argument("--format", choices=OUTPUT_FORMATS, default="table")
    call_parser.add_argument("--output", help="输出文件路径；不传则写入 stdout")

    research_parser = subparsers.add_parser("research-context", help="生成 Prism 可消费的 A 股投研上下文 JSON")
    research_parser.add_argument("ts_code", help="Tushare 股票代码，如 000001.SZ")
    research_parser.add_argument("--as-of", help="分析日期，支持 YYYYMMDD 或 YYYY-MM-DD，默认本地当天")
    research_parser.add_argument("--profile", choices=["basic", "standard", "full"], default="basic", help="采集档位：basic 只拉核心行情；standard 增加财务/事件/资金；full 尝试完整数据")
    research_parser.add_argument("--lookback-days", type=int, default=120, help="行情回看自然日天数，默认 120")
    research_parser.add_argument("--financial-years", type=int, default=3, help="财务数据回看年数，默认 3")
    research_parser.add_argument("--event-days", type=int, default=90, help="公告事件回看自然日天数，默认 90")
    research_parser.add_argument("--forecast-days", type=int, default=180, help="业绩预告回看自然日天数，默认 180")
    research_parser.add_argument("--include-news", action="store_true", help="同时抓取 Tushare 资讯页时讯；需要 TUSHARE_COOKIE")
    research_parser.add_argument("--news-source", action="append", choices=DEFAULT_NEWS_SOURCES, help="资讯来源 slug，可重复传入")
    research_parser.add_argument("--max-rows-per-dataset", type=int, default=240, help="每个数据集最多保留多少条，0 表示不限制")
    research_parser.add_argument("--token", help="Tushare token；默认读取 TUSHARE_TOKEN")
    research_parser.add_argument("--proxy-url", help="Tushare API 代理地址；默认读取 TUSHARE_PROXY_URL")
    research_parser.add_argument("--env-file", default=".env", help="配置文件路径，默认自动查找 .env")
    research_parser.add_argument("--current-points", type=int, help="当前账号积分；默认读取 TUSHARE_POINTS")
    research_parser.add_argument("--output", help="输出文件路径；不传则写入 stdout")

    summary_parser = subparsers.add_parser("research-summary", help="从 research-context JSON 生成稳定摘要")
    summary_parser.add_argument("context_file", help="research-context 输出的 JSON 文件")
    summary_parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    summary_parser.add_argument("--max-events", type=int, default=10, help="最多保留多少条事件线索，默认 10")
    summary_parser.add_argument("--max-segments", type=int, default=12, help="最多保留多少个主营分部，默认 12")
    summary_parser.add_argument("--output", help="输出文件路径；不传则写入 stdout")

    maintain_parser = subparsers.add_parser("maintain", help="维护长期基础库、权限目录和分析 mart")
    maintain_subparsers = maintain_parser.add_subparsers(dest="maintain_command", required=True)

    def add_provider_arguments(target_parser: argparse.ArgumentParser) -> None:
        target_parser.add_argument("--token", help="Tushare token；默认读取 TUSHARE_TOKEN")
        target_parser.add_argument("--proxy-url", help="Tushare API 代理地址；默认读取 TUSHARE_PROXY_URL")
        target_parser.add_argument("--env-file", default=".env", help="配置文件路径，默认自动查找 .env")
        target_parser.add_argument("--current-points", type=int, help="当前账号积分；默认读取 TUSHARE_POINTS")
        target_parser.add_argument("--data-dir", help="数据根目录；默认读取 ASHARE_DATA_DIR 或 data")

    access_parser = maintain_subparsers.add_parser("access-audit", help="生成权限目录，没有权限的接口不进入维护计划")
    add_provider_arguments(access_parser)
    access_parser.add_argument("--profile", choices=["basic", "standard", "full"], default="full")
    access_parser.add_argument("--smoke-unknown", action="store_true", help="对 unknown 权限接口做最小调用验证")
    access_parser.add_argument("--output", help="输出文件路径；不传则写入 stdout")

    plan_parser = maintain_subparsers.add_parser("plan", help="输出权限过滤后的可执行维护计划")
    add_provider_arguments(plan_parser)
    plan_parser.add_argument("--profile", choices=["basic", "standard", "full"], default="full")
    plan_parser.add_argument("--access-catalog", help="权限目录路径，默认 data/maintenance/access.json")
    plan_parser.add_argument("--group", action="append", default=[], help="只包含指定数据组，可重复传入")
    plan_parser.add_argument("--exclude-group", action="append", default=[], help="排除指定数据组，可重复传入")
    plan_parser.add_argument("--include-financials", action="store_true", help="把需要股票池的财务数据纳入计划")
    plan_parser.add_argument("--include-stock-pool-datasets", action="store_true", help="把需要显式股票池的数据集纳入计划")
    plan_parser.add_argument("--output", help="输出文件路径；不传则写入 stdout")

    daily_parser = maintain_subparsers.add_parser("daily", help="每日增量补缺并发布 canonical mart")
    add_provider_arguments(daily_parser)
    daily_parser.add_argument("--profile", choices=["basic", "standard", "full"], default="full")
    daily_parser.add_argument("--access-catalog", help="权限目录路径，默认 data/maintenance/access.json")
    daily_parser.add_argument("--group", action="append", default=[], help="只维护指定数据组，可重复传入")
    daily_parser.add_argument("--exclude-group", action="append", default=[], help="排除指定数据组，可重复传入")
    daily_parser.add_argument("--as-of", help="分析/维护日期，支持 YYYYMMDD 或 YYYY-MM-DD，默认本地当天")
    daily_parser.add_argument("--end-date", help="显式目标交易日/分区日期；不传则按 as-of 和 20:00 数据完成线选择目标交易日")
    daily_parser.add_argument("--lookback-days", type=int, default=10, help="向前补缺的交易日自然日跨度，默认 10")
    daily_parser.add_argument("--event-lookback-days", type=int, default=30, help="事件公告向前补缺自然日跨度，默认 30")
    daily_parser.add_argument("--refresh", action="store_true", help="即使 mart 分区已存在也重新请求并发布")
    daily_parser.add_argument("--include-financials", action="store_true", help="把需要股票池的财务数据纳入计划")
    daily_parser.add_argument("--include-stock-pool-datasets", action="store_true", help="把需要显式股票池的数据集纳入计划")
    daily_parser.add_argument("--stock", action="append", default=[], help="股票池维护代码，可重复传入；用于 --include-financials 或 --include-stock-pool-datasets")
    daily_parser.add_argument("--stock-pool-file", help="股票池维护文件，每行一个 ts_code；用于 --include-financials 或 --include-stock-pool-datasets")
    daily_parser.add_argument("--max-stocks", type=int, help="最多维护多少只股票；用于 --include-financials 或 --include-stock-pool-datasets")
    daily_parser.add_argument("--all-stocks-financials", action="store_true", help="显式允许财务维护使用 stock_basic 全市场股票池；不用于筹码等非财务股票池数据")
    daily_parser.add_argument("--output", help="输出文件路径；不传则写入 stdout")

    backfill_parser = maintain_subparsers.add_parser("backfill", help="历史回填基础库并发布 canonical mart")
    add_provider_arguments(backfill_parser)
    backfill_parser.add_argument("--profile", choices=["basic", "standard", "full"], default="full")
    backfill_parser.add_argument("--access-catalog", help="权限目录路径，默认 data/maintenance/access.json")
    backfill_parser.add_argument("--group", action="append", default=[], help="只回填指定数据组，可重复传入")
    backfill_parser.add_argument("--exclude-group", action="append", default=[], help="排除指定数据组，可重复传入")
    backfill_parser.add_argument("--start-date", required=True, help="开始日期，YYYYMMDD 或 YYYY-MM-DD")
    backfill_parser.add_argument("--end-date", required=True, help="结束日期，YYYYMMDD 或 YYYY-MM-DD")
    backfill_parser.add_argument("--refresh", action="store_true", help="即使 mart 分区已存在也重新请求并发布")
    backfill_parser.add_argument("--include-financials", action="store_true", help="把需要股票池的财务数据纳入计划")
    backfill_parser.add_argument("--include-stock-pool-datasets", action="store_true", help="把需要显式股票池的数据集纳入计划")
    backfill_parser.add_argument("--stock", action="append", default=[], help="股票池回填代码，可重复传入；用于 --include-financials 或 --include-stock-pool-datasets")
    backfill_parser.add_argument("--stock-pool-file", help="股票池回填文件，每行一个 ts_code；用于 --include-financials 或 --include-stock-pool-datasets")
    backfill_parser.add_argument("--max-stocks", type=int, help="最多回填多少只股票；用于 --include-financials 或 --include-stock-pool-datasets")
    backfill_parser.add_argument("--all-stocks-financials", action="store_true", help="显式允许财务回填使用 stock_basic 全市场股票池；不用于筹码等非财务股票池数据")
    backfill_parser.add_argument("--output", help="输出文件路径；不传则写入 stdout")

    check_parser = maintain_subparsers.add_parser("check", help="检查近 N 个交易日本地 mart 是否完整")
    add_provider_arguments(check_parser)
    check_parser.add_argument("--profile", choices=["basic", "standard", "full"], default="full")
    check_parser.add_argument("--access-catalog", help="权限目录路径，默认 data/maintenance/access.json")
    check_parser.add_argument("--group", action="append", default=[], help="只检查指定数据组，可重复传入")
    check_parser.add_argument("--exclude-group", action="append", default=[], help="排除指定数据组，可重复传入")
    check_parser.add_argument("--end-date", required=True, help="结束日期，YYYYMMDD 或 YYYY-MM-DD")
    check_parser.add_argument("--trade-days", type=int, default=120, help="检查最近多少个交易日，默认 120")
    check_parser.add_argument("--event-days", type=int, default=30, help="检查公告/业绩预告最近多少个自然日，默认 30")
    check_parser.add_argument("--include-financials", action="store_true", help="把需要股票池的财务数据纳入计划")
    check_parser.add_argument("--include-stock-pool-datasets", action="store_true", help="把需要显式股票池的数据集纳入计划")
    check_parser.add_argument("--output", help="输出文件路径；不传则写入 stdout")

    report_parser = maintain_subparsers.add_parser("report", help="生成每日维护运行报告，汇总覆盖率、缺口、空分区和可分析状态")
    add_provider_arguments(report_parser)
    report_parser.add_argument("--profile", choices=["basic", "standard", "full"], default="full")
    report_parser.add_argument("--access-catalog", help="权限目录路径，默认 data/maintenance/access.json")
    report_parser.add_argument("--group", action="append", default=[], help="只报告指定数据组，可重复传入")
    report_parser.add_argument("--exclude-group", action="append", default=[], help="排除指定数据组，可重复传入")
    report_parser.add_argument("--end-date", required=True, help="报告目标日期，YYYYMMDD 或 YYYY-MM-DD")
    report_parser.add_argument("--trade-days", type=int, default=120, help="检查最近多少个交易日，默认 120")
    report_parser.add_argument("--event-days", type=int, default=30, help="检查公告/业绩预告最近多少个自然日，默认 30")
    report_parser.add_argument("--include-financials", action="store_true", help="把需要股票池的财务数据纳入报告计划")
    report_parser.add_argument("--include-stock-pool-datasets", action="store_true", help="把需要显式股票池的数据集纳入报告计划")
    report_parser.add_argument("--output", help="输出文件路径；不传则写入 stdout")

    analysis_parser = subparsers.add_parser("analysis", help="从本地 mart 生成面向分析框架/LLM 的 bundle")
    analysis_subparsers = analysis_parser.add_subparsers(dest="analysis_command", required=True)
    bundle_parser = analysis_subparsers.add_parser("bundle", help="生成全市场分析 bundle")
    bundle_parser.add_argument("--as-of", required=True, help="分析日期，支持 YYYYMMDD 或 YYYY-MM-DD")
    bundle_parser.add_argument("--trade-days", type=int, default=120, help="读取最近多少个交易日，默认 120")
    bundle_parser.add_argument("--event-days", type=int, default=30, help="读取公告/业绩预告最近多少个自然日，默认 30")
    bundle_parser.add_argument("--data-dir", help="数据根目录；默认读取 ASHARE_DATA_DIR 或 data")
    bundle_parser.add_argument("--env-file", default=".env", help="配置文件路径，默认自动查找 .env")
    bundle_parser.add_argument("--token", help="Tushare token；默认读取 TUSHARE_TOKEN")
    bundle_parser.add_argument("--proxy-url", help="Tushare API 代理地址；默认读取 TUSHARE_PROXY_URL")
    bundle_parser.add_argument("--current-points", type=int, help="当前账号积分；默认读取 TUSHARE_POINTS")
    bundle_parser.add_argument("--profile", choices=["basic", "standard", "full"], default="full", help="按权限过滤读取口径，默认 full")
    bundle_parser.add_argument("--access-catalog", help="权限目录路径，默认 data/maintenance/access.json")
    bundle_parser.add_argument("--group", action="append", default=[], help="只读取指定数据组，可重复传入")
    bundle_parser.add_argument("--exclude-group", action="append", default=[], help="排除指定数据组，可重复传入")
    bundle_parser.add_argument("--include-financials", action="store_true", help="把需要股票池的财务数据纳入 bundle 读取计划")
    bundle_parser.add_argument("--include-stock-pool-datasets", action="store_true", help="把需要显式股票池的数据集纳入 bundle 读取计划")
    bundle_parser.add_argument("--include-raw-samples", action="store_true", help="在 bundle 中包含少量原始样本")
    bundle_parser.add_argument("--output", help="输出文件路径；不传则写入 stdout")

    def add_news_arguments(news_parser: argparse.ArgumentParser) -> None:
        news_parser.add_argument("--all", action="store_true", help="抓取全部已知来源；未指定 --source 时默认全部")
        news_parser.add_argument("--source", action="append", choices=DEFAULT_NEWS_SOURCES, help="资讯来源 slug，可重复传入")
        news_parser.add_argument("--cookie", help="Tushare 登录 Cookie；默认读取 TUSHARE_COOKIE")
        news_parser.add_argument("--cookie-file", help="从文件读取 Tushare 登录 Cookie")
        news_parser.add_argument("--cookie-env", default="TUSHARE_COOKIE", help="读取 Cookie 的环境变量名")
        news_parser.add_argument("--env-file", default=".env", help="配置文件路径，默认自动查找 .env")
        news_parser.add_argument("--timeout", type=float, default=30.0, help="单来源请求超时时间，秒")
        news_parser.add_argument("--delay", type=float, default=0.3, help="来源之间的间隔，秒")
        news_parser.add_argument("--retries", type=int, default=2, help="单来源失败重试次数，默认 2")
        news_parser.add_argument("--publish-date", help="可选：覆盖自动 anchor date，支持 YYYY-MM-DD 或 YYYYMMDD")
        news_parser.add_argument("--anchor-date", help="可选：指定自动补全日期的抓取锚点，默认当前日期")
        news_parser.add_argument("--max-rows", type=int, default=0, help="只输出前 N 条记录，0 表示不限制")
        news_parser.add_argument("--include-summary", action="store_true", help="输出包含来源统计和 records 的 JSON 对象")
        news_parser.add_argument("--snapshot-output", help="额外保存本次抓取快照文件，格式由扩展名推断：.json/.jsonl/.csv")
        news_parser.add_argument("--merge-input", action="append", default=[], help="合并去重输入文件，可重复传入")
        news_parser.add_argument("--merge-output", help="抓取后将 --merge-input 与本次 records 去重合并写入该文件")
        news_parser.add_argument("--format", choices=OUTPUT_FORMATS, default="json")
        news_parser.add_argument("--output", help="输出文件路径；不传则写入 stdout")

    events_parser = subparsers.add_parser("events", help="A 股事件能力：公告、业绩预告、时讯")
    event_subparsers = events_parser.add_subparsers(dest="event_type", required=True)

    notice_parser = event_subparsers.add_parser("notice", help="获取 A 股公告（AKShare）")
    notice_parser.add_argument("--days", type=int, default=7, help="向前查询自然日天数，包含 --end-date")
    notice_parser.add_argument("--end-date", help="结束日期，支持 YYYYMMDD 或 YYYY-MM-DD，默认本地当天")
    notice_parser.add_argument("--stock", help="股票代码；传入后使用个股公告接口")
    notice_parser.add_argument("--category", choices=sorted(NOTICE_CATEGORIES), default="全部", help="公告分类")
    notice_parser.add_argument("--keyword", help="按公告标题/类型关键词过滤")
    notice_parser.add_argument("--timeout", type=int, default=30, help="单次 AKShare 请求超时时间，秒")
    notice_parser.add_argument("--verbose-source", action="store_true", help="显示 AKShare 源输出")
    notice_parser.add_argument("--raw", action="store_true", help="输出 AKShare 原始字段 DataFrame，而不是标准 records")
    notice_parser.add_argument("--max-rows", type=int, default=0, help="只输出前 N 条记录，0 表示不限制")
    notice_parser.add_argument("--format", choices=OUTPUT_FORMATS, default="table")
    notice_parser.add_argument("--output", help="输出文件路径；不传则写入 stdout")

    forecast_parser = event_subparsers.add_parser("forecast", help="获取业绩预告（AKShare 东方财富口径）")
    forecast_parser.add_argument("--days", type=int, default=60, help="向前查询自然日天数，包含 --end-date")
    forecast_parser.add_argument("--end-date", help="结束日期，支持 YYYYMMDD 或 YYYY-MM-DD，默认本地当天")
    forecast_parser.add_argument("--stock", help="股票代码")
    forecast_parser.add_argument("--period", action="append", default=None, help="报告期，如 20260331；可重复传入")
    forecast_parser.add_argument("--scan-periods", type=int, default=5, help="未传 --period 时自动扫描最近 N 个报告期")
    forecast_parser.add_argument("--keyword", help="按股票简称/预测指标/变动原因等关键词过滤")
    forecast_parser.add_argument("--timeout", type=int, default=30, help="单次 AKShare 请求超时时间，秒")
    forecast_parser.add_argument("--verbose-source", action="store_true", help="显示 AKShare 源输出")
    forecast_parser.add_argument("--raw", action="store_true", help="输出 AKShare 原始字段 DataFrame，而不是标准 records")
    forecast_parser.add_argument("--max-rows", type=int, default=0, help="只输出前 N 条记录，0 表示不限制")
    forecast_parser.add_argument("--format", choices=OUTPUT_FORMATS, default="table")
    forecast_parser.add_argument("--output", help="输出文件路径；不传则写入 stdout")

    event_news_parser = event_subparsers.add_parser("news", help="抓取 Tushare 资讯页面时讯，不使用 Tushare news API")
    add_news_arguments(event_news_parser)

    merge_news_parser = event_subparsers.add_parser("news-merge", help="合并多个时讯 records 文件并按 dedupe_key 去重")
    merge_news_parser.add_argument("--input", action="append", required=True, help="输入 JSON/JSONL/CSV 文件，可重复传入")
    merge_news_parser.add_argument("--format", choices=OUTPUT_FORMATS, default="jsonl")
    merge_news_parser.add_argument("--output", required=True, help="输出文件路径")
    merge_news_parser.add_argument("--max-rows", type=int, default=0, help="只输出前 N 条记录，0 表示不限制")

    return parser


def _entry_to_row(entry: InterfaceEntry) -> dict[str, str]:
    return {
        "api": entry.api_name,
        "doc_id": entry.doc_id,
        "title": entry.title,
        "category": entry.category,
        "eligibility": entry.eligibility,
        "required_points": "" if entry.required_points is None else str(entry.required_points),
        "doc_url": entry.doc_url,
    }


def _print_table(rows: list[dict[str, Any]], columns: list[str]) -> None:
    if not rows:
        print("无匹配记录")
        return

    widths = {
        column: max(len(str(column)), *(len(str(row.get(column, ""))) for row in rows))
        for column in columns
    }
    header = "  ".join(str(column).ljust(widths[column]) for column in columns)
    print(header)
    print("  ".join("-" * widths[column] for column in columns))
    for row in rows:
        print("  ".join(str(row.get(column, "")).ljust(widths[column]) for column in columns))


def _handle_list(args: argparse.Namespace) -> int:
    registry = load_registry()
    entries = registry.search(query=args.search, category=args.category, eligibility=args.eligibility)
    if args.limit > 0:
        entries = entries[: args.limit]

    if args.format == "json":
        print(json.dumps([asdict(entry) for entry in entries], ensure_ascii=False, indent=2))
    else:
        rows = [_entry_to_row(entry) for entry in entries]
        _print_table(rows, ["api", "doc_id", "title", "category", "eligibility", "required_points", "doc_url"])
    return 0


def _handle_categories(args: argparse.Namespace) -> int:
    categories = load_registry().categories()
    if args.format == "json":
        print(json.dumps(categories, ensure_ascii=False, indent=2))
    else:
        for category in categories:
            print(category)
    return 0


def _select_info(entries: list[InterfaceEntry], doc_id: str | None) -> list[InterfaceEntry]:
    if not doc_id:
        return entries
    return [entry for entry in entries if entry.doc_id == doc_id]


def _handle_info(args: argparse.Namespace) -> int:
    entries = _select_info(load_registry().find(args.api_name), args.doc_id)
    if not entries:
        print(f"未找到接口：{args.api_name}", file=sys.stderr)
        return 2

    if args.format == "json":
        print(json.dumps([asdict(entry) for entry in entries], ensure_ascii=False, indent=2))
        return 0

    for index, entry in enumerate(entries, start=1):
        if len(entries) > 1:
            print(f"[{index}]")
        print(f"接口：{entry.api_name}")
        print(f"标题：{entry.title}")
        print(f"分类：{entry.category}")
        print(f"权限：{entry.eligibility}")
        if entry.required_points is not None:
            print(f"所需积分：{entry.required_points}")
        if entry.permission_checked_at:
            print(f"权限检查日期：{entry.permission_checked_at}")
        print(f"文档：{entry.doc_url}")
        if entry.permission_note:
            print(f"权限说明：{entry.permission_note}")
        if entry.description:
            print(f"描述：{entry.description}")
        issues = known_issues(entry.api_name)
        if issues:
            print("已知问题：")
            for issue in issues:
                print(f"- {issue.get('summary', '')}")
        if index != len(entries):
            print()
    return 0


def _handle_call(args: argparse.Namespace) -> int:
    try:
        params = merge_params(args.params, args.params_file, args.param)
        provider = AShareProvider(
            token=args.token,
            proxy_url=args.proxy_url,
            env_file=args.env_file,
            points=args.current_points,
        )
        result = provider.call(
            args.api_name,
            params=params,
            fields=args.fields,
            doc_id=args.doc_id,
            key=args.key,
            force=args.force,
            allow_unknown=args.allow_unknown,
        )
        result = limit_rows(result, args.max_rows)
        emit(render(result, args.format), args.output)
    except (TushareInterfaceSelectionError, TusharePermissionError, TushareUnknownInterfaceError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def _handle_research_context(args: argparse.Namespace) -> int:
    try:
        provider = AShareProvider(
            token=args.token,
            proxy_url=args.proxy_url,
            env_file=args.env_file,
            points=args.current_points,
        )
        context = build_research_context(
            ts_code=args.ts_code,
            as_of=args.as_of,
            profile=args.profile,
            lookback_days=args.lookback_days,
            financial_years=args.financial_years,
            event_days=args.event_days,
            forecast_days=args.forecast_days,
            include_news=args.include_news,
            news_sources=args.news_source,
            max_rows_per_dataset=args.max_rows_per_dataset,
            provider=provider,
        )
        emit(json.dumps(context, ensure_ascii=False, default=str, indent=2), args.output)
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def _handle_research_summary(args: argparse.Namespace) -> int:
    try:
        context = load_research_context(args.context_file)
        summary = build_research_summary(context, max_events=args.max_events, max_segments=args.max_segments)
        if args.format == "json":
            emit(json.dumps(summary, ensure_ascii=False, default=str, indent=2), args.output)
        else:
            emit(render_research_summary_markdown(summary), args.output)
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def _maintenance_provider(args: argparse.Namespace) -> AShareProvider:
    return AShareProvider(
        token=getattr(args, "token", None),
        proxy_url=getattr(args, "proxy_url", None),
        env_file=getattr(args, "env_file", ".env"),
        points=getattr(args, "current_points", None),
        data_dir=getattr(args, "data_dir", None),
    )


def _maintenance_plan_from_args(args: argparse.Namespace, provider: AShareProvider):
    catalog = load_access_catalog(
        getattr(args, "access_catalog", None),
        data_dir=getattr(args, "data_dir", None),
        env_file=getattr(args, "env_file", ".env"),
    )
    return build_maintenance_plan(
        provider,
        profile=args.profile,
        access_catalog=catalog,
        include_groups=set(args.group) if getattr(args, "group", None) else None,
        exclude_groups=set(args.exclude_group) if getattr(args, "exclude_group", None) else None,
        include_financials=getattr(args, "include_financials", False),
        include_stock_pool_datasets=getattr(args, "include_stock_pool_datasets", False),
    )


def _stock_pool_from_args(args: argparse.Namespace) -> list[str] | None:
    codes: list[str] = []
    for code in getattr(args, "stock", []) or []:
        text = str(code).strip()
        if text:
            codes.append(text)
    stock_pool_file = getattr(args, "stock_pool_file", None)
    if stock_pool_file:
        for line in Path(stock_pool_file).read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if text and not text.startswith("#"):
                codes.append(text)
    return list(dict.fromkeys(codes)) or None


def _validate_financial_scope(args: argparse.Namespace, stock_pool: list[str] | None) -> None:
    include_financials = getattr(args, "include_financials", False)
    include_stock_pool_datasets = getattr(args, "include_stock_pool_datasets", False)
    if not (include_financials or include_stock_pool_datasets):
        return
    if getattr(args, "maintain_command", None) not in {"daily", "backfill"}:
        return
    if stock_pool or getattr(args, "max_stocks", None):
        return
    if include_stock_pool_datasets:
        raise MaintenanceError("启用非财务股票池数据集时必须显式提供 --stock/--stock-pool-file/--max-stocks。")
    if include_financials and getattr(args, "all_stocks_financials", False):
        return
    raise MaintenanceError("启用财务数据集时必须显式提供 --stock/--stock-pool-file/--max-stocks，或使用 --all-stocks-financials 明确允许全市场财务维护。")


def _handle_maintain(args: argparse.Namespace) -> int:
    try:
        provider = _maintenance_provider(args)
        if args.maintain_command == "access-audit":
            payload = audit_access(
                provider,
                profile=args.profile,
                smoke_unknown=args.smoke_unknown,
                data_dir=args.data_dir,
            )
            emit(json.dumps(payload, ensure_ascii=False, default=str, indent=2), args.output)
            return 0

        plan = _maintenance_plan_from_args(args, provider)
        if args.maintain_command == "plan":
            emit(json.dumps(plan.as_dict(), ensure_ascii=False, default=str, indent=2), args.output)
            return 0

        require_plan_has_datasets(plan)
        stock_pool = _stock_pool_from_args(args)
        _validate_financial_scope(args, stock_pool)
        if args.maintain_command == "daily":
            report = run_daily(
                provider,
                plan,
                as_of=args.as_of,
                end_date=args.end_date,
                data_dir=args.data_dir,
                lookback_days=args.lookback_days,
                event_lookback_days=args.event_lookback_days,
                refresh=args.refresh,
                stock_pool=stock_pool,
                max_stocks=args.max_stocks,
            )
        elif args.maintain_command == "backfill":
            report = run_backfill(
                provider,
                plan,
                start_date=args.start_date,
                end_date=args.end_date,
                data_dir=args.data_dir,
                refresh=args.refresh,
                stock_pool=stock_pool,
                max_stocks=args.max_stocks,
            )
        elif args.maintain_command == "check":
            report = run_check(
                provider,
                plan,
                end_date=args.end_date,
                trade_days=args.trade_days,
                event_days=args.event_days,
                data_dir=args.data_dir,
            )
        elif args.maintain_command == "report":
            report = run_status_report(
                provider,
                plan,
                end_date=args.end_date,
                trade_days=args.trade_days,
                event_days=args.event_days,
                data_dir=args.data_dir,
            )
        else:
            raise MaintenanceError(f"未知维护命令：{args.maintain_command}")
        emit(json.dumps(report, ensure_ascii=False, default=str, indent=2), args.output)
    except MaintenanceError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def _handle_analysis(args: argparse.Namespace) -> int:
    try:
        if args.analysis_command != "bundle":
            raise ValueError(f"未知 analysis 命令：{args.analysis_command}")
        provider = _maintenance_provider(args)
        plan = _maintenance_plan_from_args(args, provider)
        require_plan_has_datasets(plan)
        bundle = build_market_analysis_bundle(
            as_of=args.as_of,
            trade_days=args.trade_days,
            data_dir=args.data_dir,
            env_file=args.env_file,
            include_raw_samples=args.include_raw_samples,
            plan=plan,
            event_days=args.event_days,
        )
        emit(json.dumps(bundle, ensure_ascii=False, default=str, indent=2), args.output)
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def _format_from_path(path: str | Path) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".jsonl":
        return "jsonl"
    if suffix == ".csv":
        return "csv"
    if suffix == ".json":
        return "json"
    return "jsonl"


def _emit_records_by_path(records: list[dict[str, Any]], output: str | Path) -> None:
    emit(render(records, _format_from_path(output)), output)


def _handle_news(args: argparse.Namespace) -> int:
    try:
        if args.include_summary and args.format != "json":
            raise TushareNewsError("--include-summary 只能配合 --format json")

        cookie = load_tushare_cookie(
            cookie=args.cookie,
            cookie_file=args.cookie_file,
            cookie_env=args.cookie_env,
            env_file=args.env_file,
        )
        sources = DEFAULT_NEWS_SOURCES if args.all else normalize_news_sources(args.source)
        payload = crawl_tushare_news(
            cookie=cookie,
            sources=sources,
            timeout=args.timeout,
            delay=args.delay,
            retries=args.retries,
            publish_date=args.publish_date,
            anchor_date=args.anchor_date,
        )
        records = limit_rows(payload["records"], args.max_rows)
        if args.snapshot_output:
            _emit_records_by_path(records, args.snapshot_output)
        if args.merge_output:
            input_groups = [read_news_records(path) for path in args.merge_input]
            merged_records = limit_rows(
                merge_news_records([*input_groups, records], snapshot_files=[*args.merge_input, str(args.snapshot_output or "current-run")]),
                args.max_rows,
            )
            _emit_records_by_path(merged_records, args.merge_output)
        if args.include_summary:
            payload = dict(payload)
            payload["records"] = records
            emit(json.dumps(payload, ensure_ascii=False, default=str, indent=2), args.output)
        else:
            emit(render(records, args.format), args.output)
    except TushareNewsError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def _handle_events(args: argparse.Namespace) -> int:
    if args.event_type == "news":
        return _handle_news(args)
    if args.event_type == "news-merge":
        try:
            records = limit_rows(merge_news_files(args.input), args.max_rows)
            emit(render(records, args.format), args.output)
        except TushareNewsError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except Exception as exc:  # noqa: BLE001
            print(str(exc), file=sys.stderr)
            return 1
        return 0

    try:
        provider = AShareProvider()
        if args.event_type == "notice":
            result = provider.a_stock_notice(
                days=args.days,
                end_date=args.end_date,
                stock=args.stock,
                category=args.category,
                keyword=args.keyword,
                timeout=args.timeout,
                verbose_source=args.verbose_source,
                max_rows=args.max_rows,
                as_records=not args.raw,
            )
        elif args.event_type == "forecast":
            result = provider.earnings_forecast(
                days=args.days,
                end_date=args.end_date,
                stock=args.stock,
                periods=args.period,
                scan_periods=args.scan_periods,
                keyword=args.keyword,
                timeout=args.timeout,
                verbose_source=args.verbose_source,
                max_rows=args.max_rows,
                as_records=not args.raw,
            )
        else:
            raise AStockEventError(f"未知事件类型：{args.event_type}")
        emit(render(result, args.format), args.output)
    except AStockEventError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def _handle_defaults(args: argparse.Namespace) -> int:
    print(json.dumps(default_params(args.api_name, doc_id=args.doc_id, key=args.key), ensure_ascii=False, indent=2))
    return 0


def _handle_schema(args: argparse.Namespace) -> int:
    try:
        schema = get_api_schema(args.api_name, doc_id=args.doc_id, key=args.key)
    except SchemaError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.format == "json":
        print(
            json.dumps(
                {
                    "key": schema.key,
                    "api_name": schema.api_name,
                    "doc_id": schema.doc_id,
                    "title": schema.title,
                    "fetch_status": schema.fetch_status,
                    "parse_status": schema.parse_status,
                    "required_params": schema.required_params,
                    "optional_params": schema.optional_params,
                    "input_params": [param.__dict__ for param in schema.input_params],
                    "example_params": schema.example_params,
                    "default_params": schema.default_params,
                    "default_params_source": schema.default_params_source,
                    "doc_url": schema.doc_url,
                    "error_message": schema.error_message,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    print(f"接口：{schema.api_name}:{schema.doc_id}")
    print(f"标题：{schema.title}")
    print(f"状态：fetch={schema.fetch_status}, parse={schema.parse_status}")
    print(f"文档：{schema.doc_url}")
    if schema.input_params:
        rows = [
            {
                "name": param.name,
                "type": param.type,
                "required": param.required,
                "description": param.description,
            }
            for param in schema.input_params
        ]
        _print_table(rows, ["name", "type", "required", "description"])
    else:
        print("入参：无结构化参数")
    if schema.example_params:
        print("官方示例参数：")
        print(json.dumps(schema.example_params, ensure_ascii=False, indent=2))
    if schema.default_params:
        print("默认测试参数：")
        print(json.dumps(schema.default_params, ensure_ascii=False, indent=2))
    if schema.error_message:
        print(f"错误：{schema.error_message}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    handlers = {
        "list": _handle_list,
        "categories": _handle_categories,
        "defaults": _handle_defaults,
        "schema": _handle_schema,
        "info": _handle_info,
        "call": _handle_call,
        "research-context": _handle_research_context,
        "research-summary": _handle_research_summary,
        "maintain": _handle_maintain,
        "analysis": _handle_analysis,
        "events": _handle_events,
    }
    return handlers[args.command](args)

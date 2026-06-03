from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from typing import Any

from .defaults import default_params
from .events import AStockEventError, NOTICE_CATEGORIES
from .issues import known_issues
from .news import (
    DEFAULT_NEWS_SOURCES,
    TushareNewsError,
    crawl_tushare_news,
    load_tushare_cookie,
    normalize_news_sources,
)
from .output import emit, limit_rows, render
from .params import merge_params
from .provider import (
    TushareInterfaceSelectionError,
    TusharePermissionError,
    TushareProvider,
    TushareUnknownInterfaceError,
)
from .registry import InterfaceEntry, load_registry
from .schemas import SchemaError, get_api_schema


OUTPUT_FORMATS = ["table", "json", "jsonl", "csv"]
ELIGIBILITY_VALUES = ["points_ok", "points_insufficient", "needs_separate_permission", "unknown"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tushare-fastcli",
        description="面向大模型和量化业务的 Tushare 快速调用 CLI。",
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
    call_parser.add_argument("--env-file", default=".env", help="配置文件路径，默认读取当前目录 .env")
    call_parser.add_argument("--allow-unknown", action="store_true", help="允许调用未在索引里的接口名")
    call_parser.add_argument("--force", action="store_true", help="忽略积分/权限元数据提示，强制调用")
    call_parser.add_argument("--current-points", type=int, help="当前账号积分；默认读取 TUSHARE_POINTS")
    call_parser.add_argument("--max-rows", type=int, default=0, help="只输出前 N 行，0 表示不限制")
    call_parser.add_argument("--format", choices=OUTPUT_FORMATS, default="table")
    call_parser.add_argument("--output", help="输出文件路径；不传则写入 stdout")

    def add_news_arguments(news_parser: argparse.ArgumentParser) -> None:
        news_parser.add_argument("--all", action="store_true", help="抓取全部已知来源；未指定 --source 时默认全部")
        news_parser.add_argument("--source", action="append", choices=DEFAULT_NEWS_SOURCES, help="资讯来源 slug，可重复传入")
        news_parser.add_argument("--cookie", help="Tushare 登录 Cookie；默认读取 TUSHARE_COOKIE")
        news_parser.add_argument("--cookie-file", help="从文件读取 Tushare 登录 Cookie")
        news_parser.add_argument("--cookie-env", default="TUSHARE_COOKIE", help="读取 Cookie 的环境变量名")
        news_parser.add_argument("--env-file", default=".env", help="配置文件路径，默认读取当前目录 .env")
        news_parser.add_argument("--timeout", type=float, default=30.0, help="单来源请求超时时间，秒")
        news_parser.add_argument("--delay", type=float, default=0.3, help="来源之间的间隔，秒")
        news_parser.add_argument("--retries", type=int, default=2, help="单来源失败重试次数，默认 2")
        news_parser.add_argument("--publish-date", help="可选：用 YYYY-MM-DD 补齐 records.datetime")
        news_parser.add_argument("--max-rows", type=int, default=0, help="只输出前 N 条记录，0 表示不限制")
        news_parser.add_argument("--include-summary", action="store_true", help="输出包含来源统计和 records 的 JSON 对象")
        news_parser.add_argument("--format", choices=OUTPUT_FORMATS, default="json")
        news_parser.add_argument("--output", help="输出文件路径；不传则写入 stdout")

    news_parser = subparsers.add_parser("news", help="抓取 Tushare 资讯页面，作为 events news 的兼容入口")
    add_news_arguments(news_parser)

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
        provider = TushareProvider(
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
        )
        records = limit_rows(payload["records"], args.max_rows)
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

    try:
        provider = TushareProvider()
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
        "news": _handle_news,
        "events": _handle_events,
    }
    return handlers[args.command](args)

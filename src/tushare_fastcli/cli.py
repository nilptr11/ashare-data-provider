from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from typing import Any

from .defaults import default_params
from .issues import known_issues
from .output import emit, limit_rows, render
from .params import merge_params
from .provider import (
    TushareInterfaceSelectionError,
    TusharePermissionError,
    TushareProvider,
    TushareUnknownInterfaceError,
)
from .registry import InterfaceEntry, load_registry


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


def _handle_defaults(args: argparse.Namespace) -> int:
    print(json.dumps(default_params(args.api_name, doc_id=args.doc_id, key=args.key), ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    handlers = {
        "list": _handle_list,
        "categories": _handle_categories,
        "defaults": _handle_defaults,
        "info": _handle_info,
        "call": _handle_call,
    }
    return handlers[args.command](args)

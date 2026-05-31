from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime
from multiprocessing import Process, Queue
from pathlib import Path
from queue import Empty
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tushare_fastcli.client import TushareCaller  # noqa: E402
from tushare_fastcli.config import load_config  # noqa: E402
from tushare_fastcli.defaults import default_params as configured_default_params  # noqa: E402
from tushare_fastcli.issues import known_issues  # noqa: E402
from tushare_fastcli.registry import InterfaceEntry, load_registry  # noqa: E402


def default_params(
    api_name: str,
    doc_id: str | None = None,
    key: str | None = None,
) -> dict[str, Any]:
    return configured_default_params(api_name, doc_id=doc_id, key=key) or {"limit": 1}


def dataframe_shape(value: Any) -> tuple[int | None, list[str]]:
    if hasattr(value, "columns") and hasattr(value, "__len__"):
        return len(value), [str(column) for column in value.columns]
    if isinstance(value, list):
        return len(value), []
    return None, []


def truncate(value: str, limit: int = 500) -> str:
    value = " ".join(value.split())
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _base_result(entry: InterfaceEntry, params: dict[str, Any], elapsed_ms: int) -> dict[str, Any]:
    return {
        "key": entry.key,
        "api_name": entry.api_name,
        "doc_id": entry.doc_id,
        "title": entry.title,
        "category": entry.category,
        "doc_url": entry.doc_url,
        "status": "failed",
        "rows": None,
        "columns": [],
        "column_count": 0,
        "elapsed_ms": elapsed_ms,
        "params": params,
        "known_issues": [issue.get("summary", "") for issue in known_issues(entry.api_name)],
        "error_type": "",
        "error_message": "",
    }


def _call_entry_worker(entry_data: dict[str, str], env_file: str, proxy_url: str | None, queue: Queue) -> None:
    entry = InterfaceEntry(**entry_data)
    params = default_params(entry.api_name, doc_id=entry.doc_id, key=entry.key)
    started = time.perf_counter()
    result_row = _base_result(entry, params, elapsed_ms=0)

    try:
        caller = TushareCaller(env_file=env_file, proxy_url=proxy_url)
        result = caller.call(entry.api_name, params=params)
        rows, columns = dataframe_shape(result)
        status = "success" if rows != 0 else "empty"
    except Exception as exc:  # noqa: BLE001
        rows = None
        columns = []
        status = "failed"
        result_row["error_type"] = type(exc).__name__
        result_row["error_message"] = truncate(str(exc))

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    result_row.update(
        {
            "status": status,
            "rows": rows,
            "columns": columns,
            "column_count": len(columns),
            "elapsed_ms": elapsed_ms,
        }
    )
    queue.put(result_row)


def run_entry(
    entry: InterfaceEntry,
    env_file: str,
    proxy_url: str | None,
    timeout_seconds: int,
) -> dict[str, Any]:
    params = default_params(entry.api_name, doc_id=entry.doc_id, key=entry.key)
    started = time.perf_counter()
    queue: Queue = Queue(maxsize=1)
    process = Process(
        target=_call_entry_worker,
        args=(
            {
                "api_name": entry.api_name,
                "title": entry.title,
                "category": entry.category,
                "description": entry.description,
                "doc_url": entry.doc_url,
                "doc_id": entry.doc_id,
                "key": entry.key,
            },
            env_file,
            proxy_url,
            queue,
        ),
    )
    process.start()
    process.join(timeout_seconds)

    if process.is_alive():
        process.terminate()
        process.join(2)
        if process.is_alive():
            process.kill()
            process.join()
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        result = _base_result(entry, params, elapsed_ms)
        result["status"] = "timeout"
        result["error_type"] = "TimeoutError"
        result["error_message"] = f"调用超过 {timeout_seconds} 秒"
        return result

    try:
        return queue.get_nowait()
    except Empty:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        result = _base_result(entry, params, elapsed_ms)
        result["error_type"] = "RuntimeError"
        result["error_message"] = f"子进程退出但未返回结果，退出码：{process.exitcode}"
        return result


def write_reports(results: list[dict[str, Any]], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"tushare_smoke_{timestamp}.json"
    csv_path = output_dir / f"tushare_smoke_{timestamp}.csv"

    summary = {
        "generated_at": timestamp,
        "total": len(results),
        "success": sum(1 for item in results if item["status"] == "success"),
        "empty": sum(1 for item in results if item["status"] == "empty"),
        "failed": sum(1 for item in results if item["status"] == "failed"),
        "timeout": sum(1 for item in results if item["status"] == "timeout"),
        "results": results,
    }
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    columns = [
        "key",
        "api_name",
        "doc_id",
        "title",
        "category",
        "status",
        "rows",
        "column_count",
        "elapsed_ms",
        "params",
        "known_issues",
        "error_type",
        "error_message",
        "doc_url",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        for item in results:
            row = dict(item)
            row["params"] = json.dumps(row["params"], ensure_ascii=False)
            row["known_issues"] = json.dumps(row["known_issues"], ensure_ascii=False)
            writer.writerow({column: row.get(column, "") for column in columns})

    return json_path, csv_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="批量 smoke test Tushare 接口索引")
    parser.add_argument("--env-file", default=".env", help="配置文件路径")
    parser.add_argument("--proxy-url", help="Tushare API 代理地址；传空字符串可禁用 .env 中的代理")
    parser.add_argument("--output-dir", default="reports", type=Path, help="报告输出目录")
    parser.add_argument("--timeout", default=20, type=int, help="单个接口超时时间，单位秒")
    parser.add_argument("--delay", default=0.6, type=float, help="接口之间的等待时间，单位秒")
    parser.add_argument("--limit", default=0, type=int, help="最多测试多少条索引记录，0 表示全部")
    parser.add_argument("--key", action="append", default=[], help="只测试指定 api:doc_id，可重复传入")
    parser.add_argument("--unique-api", action="store_true", help="同名接口只测试第一条")
    parser.add_argument("--include-restricted", action="store_true", help="包含积分不足或需单独权限的接口")
    return parser


def selected_entries(
    unique_api: bool,
    limit: int,
    include_restricted: bool,
    current_points: int,
    allow_separate_permission: bool,
    keys: list[str] | None = None,
) -> list[InterfaceEntry]:
    entries = load_registry().entries
    if not include_restricted:
        entries = [
            entry
            for entry in entries
            if (entry.required_points is None or entry.required_points <= current_points)
            and (entry.eligibility != "needs_separate_permission" or allow_separate_permission)
        ]
    if unique_api:
        seen: set[str] = set()
        unique_entries: list[InterfaceEntry] = []
        for entry in entries:
            if entry.api_name in seen:
                continue
            seen.add(entry.api_name)
            unique_entries.append(entry)
        entries = unique_entries
    if keys:
        by_key = {entry.key: entry for entry in entries}
        entries = [by_key[key] for key in keys if key in by_key]
    if limit > 0:
        return entries[:limit]
    return entries


def main() -> int:
    args = build_parser().parse_args()
    config = load_config(env_file=args.env_file, proxy_url=args.proxy_url)
    if not config.token:
        print(f"未读取到 TUSHARE_TOKEN，请检查 {args.env_file}", file=sys.stderr)
        return 2

    entries = selected_entries(
        args.unique_api,
        args.limit,
        args.include_restricted,
        current_points=config.points,
        allow_separate_permission=config.allow_separate_permission,
        keys=args.key,
    )
    if args.key:
        selected_keys = {entry.key for entry in entries}
        missing_keys = [key for key in args.key if key not in selected_keys]
        if missing_keys:
            print(
                "未找到或被权限过滤的接口 key："
                + ", ".join(missing_keys)
                + "。如需包含受限接口，请加 --include-restricted。",
                file=sys.stderr,
            )
            return 2

    results: list[dict[str, Any]] = []
    total = len(entries)

    print(
        f"开始测试 {total} 条接口记录；token 已读取；代理：{'已配置' if config.proxy_url else '未配置'}；"
        f"积分：{config.points}；单独权限：{'允许' if config.allow_separate_permission else '默认跳过'}",
        flush=True,
    )
    for index, entry in enumerate(entries, start=1):
        result = run_entry(entry, env_file=args.env_file, proxy_url=args.proxy_url, timeout_seconds=args.timeout)
        results.append(result)
        print(
            f"[{index}/{total}] {entry.api_name}:{entry.doc_id} "
            f"{result['status']} rows={result['rows']} elapsed={result['elapsed_ms']}ms",
            flush=True,
        )
        if args.delay > 0 and index != total:
            time.sleep(args.delay)

    json_path, csv_path = write_reports(results, args.output_dir)
    print(f"JSON 报告：{json_path}", flush=True)
    print(f"CSV 报告：{csv_path}", flush=True)
    print(
        "汇总："
        f"success={sum(1 for item in results if item['status'] == 'success')} "
        f"empty={sum(1 for item in results if item['status'] == 'empty')} "
        f"failed={sum(1 for item in results if item['status'] == 'failed')} "
        f"timeout={sum(1 for item in results if item['status'] == 'timeout')}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

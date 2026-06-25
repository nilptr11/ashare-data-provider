from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from .connectors import ConnectorRegistry, TushareConnector
from .context_packs import ContextPackBuilder
from .daily import (
    DEFAULT_CONTEXT_TRADE_DAYS,
    build_status,
    daily_plan,
    event_days_for_daily,
    parse_windows as parse_daily_windows,
    read_report,
    resolve_as_of,
    task_build_params,
    write_report,
)
from .datasets.catalog import DatasetCatalog
from .evidence import EvidenceStore
from .evidence.adapters import EvidenceAdapterRegistry, EvidenceAdapterRunner, EvidenceAdapterSpec
from .features import FeatureBuilder, FeatureRegistry, FeatureStore, ScoringProfile
from .knowledge import KnowledgeStore, proposal_rows
from .marts.publisher import MartPublisher
from .marts.reader import MartReader
from .output import emit
from .protocols import ProtocolRegistry
from .raw_store import RawStore
from .runs import RunRecorder, replay_run
from .schemas import AShareResearchError, DatasetSpec, SourceResponse


OUTPUT_FORMATS = ("table", "json", "jsonl", "csv")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ashare",
        description="A 股研究数据平台 CLI。",
    )
    parser.add_argument("--data-dir", help="数据根目录；默认读取 ASHARE_DATA_DIR 或项目 data/")
    subparsers = parser.add_subparsers(dest="command", required=True)

    daily_parser = subparsers.add_parser("daily", help="每日收盘后基础数据维护、验收和报告")
    daily_subparsers = daily_parser.add_subparsers(dest="daily_command", required=True)

    daily_run = daily_subparsers.add_parser("run", help="更新完整日常基础库、特征和 context")
    _add_daily_run_args(daily_run)

    daily_repair = daily_subparsers.add_parser("repair", help="只补缺失或不健康的数据，再重建特征和 context")
    _add_daily_run_args(daily_repair)

    daily_status = daily_subparsers.add_parser("status", help="查看每日维护状态")
    _add_daily_status_args(daily_status)

    daily_report = daily_subparsers.add_parser("report", help="读取最近一次或指定日期的每日维护报告")
    _add_daily_status_args(daily_report)

    connectors_parser = subparsers.add_parser("connectors", help="source connector 发现和原始抓取")
    connectors_subparsers = connectors_parser.add_subparsers(dest="connectors_command", required=True)

    connectors_list = connectors_subparsers.add_parser("list", help="列出可用 source connectors")
    connectors_list.add_argument("--format", choices=("table", "json", "csv"), default="table")

    connectors_fetch = connectors_subparsers.add_parser("fetch", help="通过 connector 获取原始响应")
    connectors_fetch.add_argument("source")
    connectors_fetch.add_argument("api_name")
    connectors_fetch.add_argument("-p", "--param", action="append", default=[], help="source 参数，key=value 或 key:=JSON")
    connectors_fetch.add_argument("--fields", help="逗号分隔字段；只保留响应中的已有字段")
    connectors_fetch.add_argument("--url", help="HTTP JSON connector 请求 URL")
    connectors_fetch.add_argument("--method", choices=("GET", "POST"), default="GET")
    connectors_fetch.add_argument("--header", action="append", default=[], help="HTTP header，key=value")
    connectors_fetch.add_argument("--body-json", help="HTTP POST body JSON；未传时用 -p 参数作为 body")
    connectors_fetch.add_argument("--token", help="Tushare token；默认读取 TUSHARE_TOKEN 或 .env")
    connectors_fetch.add_argument("--proxy-url", help="Tushare proxy url；默认读取 TUSHARE_PROXY_URL 或 .env")
    connectors_fetch.add_argument("--env-file", default=".env", help="环境变量文件；不存在时忽略")
    connectors_fetch.add_argument("--store-raw", action=argparse.BooleanOptionalAction, default=True)
    connectors_fetch.add_argument("--limit", type=int, default=20)
    connectors_fetch.add_argument("--format", choices=("table", "json"), default="json")

    data_parser = subparsers.add_parser("data", help="数据集契约和可用性")
    data_subparsers = data_parser.add_subparsers(dest="data_command", required=True)

    data_list = data_subparsers.add_parser("list", help="列出注册数据集和本地 mart")
    data_list.add_argument("--format", choices=("table", "json", "csv"), default="table")

    data_check = data_subparsers.add_parser("check", help="检查注册数据集是否满足基础契约")
    data_check.add_argument("--as-of", help="目标日期，YYYYMMDD；对 trade_date/snapshot_date 分区生效")
    data_check.add_argument("--dataset", action="append", default=[], help="只检查指定数据集，可重复传入")
    data_check.add_argument("--format", choices=("table", "json"), default="table")

    _add_data_build_parser(data_subparsers, "build", "从 source connector 获取数据并发布 mart")
    _add_data_build_parser(data_subparsers, "update", "更新一个 mart 分区；等价于 build，通常配合 --refresh")

    mart_parser = subparsers.add_parser("mart", help="读取 mart 分区")
    mart_subparsers = mart_parser.add_subparsers(dest="mart_command", required=True)

    mart_read = mart_subparsers.add_parser("read", help="读取一个注册 mart 分区")
    mart_read.add_argument("dataset")
    mart_read.add_argument("--partition", action="append", default=[], help="分区键值，如 trade_date=20260623")
    mart_read.add_argument("--trade-date", help="快捷分区参数：trade_date")
    mart_read.add_argument("--snapshot-date", help="快捷分区参数：snapshot_date")
    mart_read.add_argument("--period", help="快捷分区参数：period")
    mart_read.add_argument("--latest", action="store_true", help="读取最新分区")
    mart_read.add_argument("--limit", type=int, default=20)
    mart_read.add_argument("--format", choices=OUTPUT_FORMATS, default="table")

    mart_meta = mart_subparsers.add_parser("meta", help="读取 mart 分区元数据")
    mart_meta.add_argument("dataset")
    mart_meta.add_argument("--partition", action="append", default=[])
    mart_meta.add_argument("--trade-date")
    mart_meta.add_argument("--snapshot-date")
    mart_meta.add_argument("--period")
    mart_meta.add_argument("--latest", action="store_true")

    feature_parser = subparsers.add_parser("feature", help="构建和读取 feature mart")
    feature_subparsers = feature_parser.add_subparsers(dest="feature_command", required=True)

    feature_list = feature_subparsers.add_parser("list", help="列出注册 feature 和已发布分区")
    feature_list.add_argument("--format", choices=("table", "json", "csv"), default="table")

    feature_build = feature_subparsers.add_parser("build", help="构建一个 feature")
    feature_build.add_argument("feature", choices=[spec.name for spec in FeatureRegistry.builtin().list()])
    feature_build.add_argument("--as-of", required=True, help="目标日期，YYYYMMDD")
    feature_build.add_argument("--windows", default="5,20,60", help="逗号分隔窗口，如 5,20,60")
    feature_build.add_argument("--scoring-profile", help="feature scoring profile JSON；默认使用内置 default.v1")
    feature_build.add_argument("--format", choices=("table", "json"), default="json")

    feature_read = feature_subparsers.add_parser("read", help="读取一个 feature 分区")
    feature_read.add_argument("feature", choices=[spec.name for spec in FeatureRegistry.builtin().list()])
    feature_read.add_argument("--as-of", required=True)
    feature_read.add_argument("--window", type=int, required=True)
    feature_read.add_argument("--limit", type=int, default=20)
    feature_read.add_argument("--format", choices=OUTPUT_FORMATS, default="table")

    feature_meta = feature_subparsers.add_parser("meta", help="读取 feature 分区元数据")
    feature_meta.add_argument("feature", choices=[spec.name for spec in FeatureRegistry.builtin().list()])
    feature_meta.add_argument("--as-of", required=True)
    feature_meta.add_argument("--window", type=int, required=True)

    evidence_parser = subparsers.add_parser("evidence", help="外部产业证据入库和检索")
    evidence_subparsers = evidence_parser.add_subparsers(dest="evidence_command", required=True)

    evidence_ingest = evidence_subparsers.add_parser("ingest", help="入库证据 JSON 或 JSONL")
    evidence_ingest.add_argument("input", help="证据文件路径，支持 JSON object/list 或 JSONL")
    evidence_ingest.add_argument("--format", choices=("table", "json"), default="json")

    evidence_validate = evidence_subparsers.add_parser("validate", help="校验证据文件但不入库")
    evidence_validate.add_argument("input")
    evidence_validate.add_argument("--format", choices=("table", "json", "jsonl"), default="json")

    evidence_list = evidence_subparsers.add_parser("list", help="列出已入库证据")
    evidence_list.add_argument("--limit", type=int, default=20)
    evidence_list.add_argument("--format", choices=OUTPUT_FORMATS, default="table")

    evidence_search = evidence_subparsers.add_parser("search", help="按 topic/industry/company/product/period 检索证据")
    _add_evidence_query_args(evidence_search)
    evidence_search.add_argument("--limit", type=int, default=20)
    evidence_search.add_argument("--format", choices=OUTPUT_FORMATS, default="table")

    evidence_export = evidence_subparsers.add_parser("export", help="检索并导出证据 JSONL")
    evidence_export.add_argument("output")
    _add_evidence_query_args(evidence_export)
    evidence_export.add_argument("--limit", type=int, default=0)
    evidence_export.add_argument("--format", choices=("table", "json"), default="json")

    evidence_collect = evidence_subparsers.add_parser("collect", help="记录开放式外部证据采集缺口")
    evidence_collect.add_argument("--question", required=True)
    evidence_collect.add_argument("--as-of")
    evidence_collect.add_argument("--format", choices=("table", "json"), default="json")

    evidence_adapters = evidence_subparsers.add_parser("adapter-candidates", help="列出可晋升 adapter 的高频数值证据")
    evidence_adapters.add_argument("--min-records", type=int, default=3)
    evidence_adapters.add_argument("--format", choices=OUTPUT_FORMATS, default="table")

    evidence_adapter_specs = evidence_subparsers.add_parser("adapter-specs", help="管理 evidence adapter proposal/spec")
    evidence_adapter_specs_subparsers = evidence_adapter_specs.add_subparsers(dest="adapter_specs_command", required=True)
    evidence_adapter_specs_list = evidence_adapter_specs_subparsers.add_parser("list", help="列出 adapter specs")
    evidence_adapter_specs_list.add_argument("--status", choices=("proposed", "accepted", "retired"))
    evidence_adapter_specs_list.add_argument("--format", choices=OUTPUT_FORMATS, default="table")
    evidence_adapter_specs_propose = evidence_adapter_specs_subparsers.add_parser("propose", help="从 adapter candidates 生成 proposed specs")
    evidence_adapter_specs_propose.add_argument("--min-records", type=int, default=3)
    evidence_adapter_specs_propose.add_argument("--overwrite", action="store_true")
    evidence_adapter_specs_propose.add_argument("--format", choices=("table", "json"), default="json")
    evidence_adapter_specs_install = evidence_adapter_specs_subparsers.add_parser("install", help="安装 adapter spec JSON")
    evidence_adapter_specs_install.add_argument("input")
    evidence_adapter_specs_install.add_argument("--overwrite", action="store_true")
    evidence_adapter_specs_install.add_argument("--format", choices=("table", "json"), default="json")
    evidence_adapter_specs_run = evidence_adapter_specs_subparsers.add_parser("run", help="运行 accepted adapter spec 并入库 evidence")
    evidence_adapter_specs_run.add_argument("adapter_id")
    evidence_adapter_specs_run.add_argument("-p", "--param", action="append", default=[], help="运行参数，key=value 或 key:=JSON")
    evidence_adapter_specs_run.add_argument("--format", choices=("table", "json"), default="json")

    knowledge_parser = subparsers.add_parser("knowledge", help="慢变量知识库 proposal、审核和检索")
    knowledge_subparsers = knowledge_parser.add_subparsers(dest="knowledge_command", required=True)

    knowledge_propose = knowledge_subparsers.add_parser("propose", help="写入 proposed knowledge JSON 或 JSONL")
    knowledge_propose.add_argument("input")
    knowledge_propose.add_argument("--reason")
    knowledge_propose.add_argument("--proposed-by", default="codex")
    knowledge_propose.add_argument("--format", choices=("table", "json"), default="json")

    knowledge_accept = knowledge_subparsers.add_parser("accept", help="接受一个 knowledge proposal 并写入 current")
    knowledge_accept.add_argument("proposal_id")
    knowledge_accept.add_argument("--accepted-by", default="human")
    knowledge_accept.add_argument("--reason")
    knowledge_accept.add_argument("--format", choices=("table", "json"), default="json")

    knowledge_proposals = knowledge_subparsers.add_parser("proposals", help="列出 knowledge proposals")
    knowledge_proposals.add_argument("--status", choices=("proposed", "accepted", "rejected"))
    knowledge_proposals.add_argument("--limit", type=int, default=20)
    knowledge_proposals.add_argument("--format", choices=OUTPUT_FORMATS, default="table")

    knowledge_list = knowledge_subparsers.add_parser("list", help="列出 current knowledge")
    knowledge_list.add_argument("--limit", type=int, default=20)
    knowledge_list.add_argument("--format", choices=OUTPUT_FORMATS, default="table")

    knowledge_search = knowledge_subparsers.add_parser("search", help="检索 current knowledge")
    knowledge_search.add_argument("--entity")
    knowledge_search.add_argument("--predicate")
    knowledge_search.add_argument("--source-type")
    knowledge_search.add_argument("--evidence-id")
    knowledge_search.add_argument("--limit", type=int, default=20)
    knowledge_search.add_argument("--format", choices=OUTPUT_FORMATS, default="table")

    knowledge_snapshot = knowledge_subparsers.add_parser("snapshot", help="生成 current knowledge snapshot")
    knowledge_snapshot.add_argument("--output")
    knowledge_snapshot.add_argument("--format", choices=("table", "json"), default="json")

    context_parser = subparsers.add_parser("context", help="生成 Codex 可读 context pack")
    context_subparsers = context_parser.add_subparsers(dest="context_command", required=True)

    context_build = context_subparsers.add_parser("build", help="生成 context pack")
    context_build_subparsers = context_build.add_subparsers(dest="context_pack_type", required=True)

    context_market = context_build_subparsers.add_parser("market-structure", help="生成市场结构 context pack")
    context_market.add_argument("--as-of", required=True)
    context_market.add_argument("--trade-days", type=int, default=120)
    context_market.add_argument("--output")
    context_market.add_argument("--format", choices=("table", "json"), default="json")

    context_industry = context_build_subparsers.add_parser("industry", help="生成行业 context pack")
    context_industry.add_argument("industry")
    context_industry.add_argument("--as-of", required=True)
    context_industry.add_argument("--output")
    context_industry.add_argument("--format", choices=("table", "json"), default="json")

    context_stock = context_build_subparsers.add_parser("stock", help="生成个股 context pack")
    context_stock.add_argument("ts_code")
    context_stock.add_argument("--as-of", required=True)
    context_stock.add_argument("--output")
    context_stock.add_argument("--format", choices=("table", "json"), default="json")

    protocols_parser = subparsers.add_parser("protocols", help="可复用分析模板和输出质量门")
    protocols_subparsers = protocols_parser.add_subparsers(dest="protocols_command", required=True)

    protocols_list = protocols_subparsers.add_parser("list", help="列出可复用 protocol 模板")
    protocols_list.add_argument("--format", choices=("table", "json", "csv"), default="table")

    protocols_show = protocols_subparsers.add_parser("show", help="显示一个 protocol 模板")
    protocols_show.add_argument("protocol_id")
    protocols_show.add_argument("--format", choices=("table", "json"), default="json")

    protocols_validate = protocols_subparsers.add_parser("validate", help="校验可复用 protocol 模板")
    protocols_validate.add_argument("protocol_id", nargs="?")
    protocols_validate.add_argument("--format", choices=("table", "json"), default="json")

    protocols_output_schema = protocols_subparsers.add_parser("output-schema", help="显示 protocol 输出 JSON Schema")
    protocols_output_schema.add_argument("protocol_id")
    protocols_output_schema.add_argument("--format", choices=("json",), default="json")

    runs_parser = subparsers.add_parser("runs", help="记录和回放 Codex 分析 run 留痕")
    runs_subparsers = runs_parser.add_subparsers(dest="runs_command", required=True)

    runs_record = runs_subparsers.add_parser("record", help="记录一次分析 run")
    runs_record.add_argument("--question")
    runs_record.add_argument("--question-file")
    runs_record.add_argument("--as-of", required=True)
    runs_record.add_argument("--protocol", help="可选注册 protocol；不传则按用户当次指令记录为 user_directed.v1")
    runs_record.add_argument("--ad-hoc-protocol")
    runs_record.add_argument("--context-pack", action="append", default=[])
    runs_record.add_argument("--evidence")
    runs_record.add_argument("--knowledge")
    runs_record.add_argument("--model-output-file")
    runs_record.add_argument("--validated-output")
    runs_record.add_argument("--agent-reasoning")
    runs_record.add_argument("--report-file")
    runs_record.add_argument("--run-id")
    runs_record.add_argument("--runs-dir")
    runs_record.add_argument("--format", choices=("table", "json"), default="json")

    runs_list = runs_subparsers.add_parser("list", help="列出 run manifest")
    runs_list.add_argument("--runs-dir")
    runs_list.add_argument("--format", choices=("table", "json", "csv"), default="table")

    runs_replay = runs_subparsers.add_parser("replay", help="校验 run manifest 和 artifact hash")
    runs_replay.add_argument("run_dir")
    runs_replay.add_argument("--format", choices=("table", "json"), default="json")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    reader = MartReader(data_dir=Path(args.data_dir) if args.data_dir else None, catalog=DatasetCatalog.builtin())

    try:
        if args.command == "connectors":
            return _handle_connectors(args, reader)
        if args.command == "daily":
            return _handle_daily(args, reader)
        if args.command == "data":
            return _handle_data(args, reader)
        if args.command == "mart":
            return _handle_mart(args, reader)
        if args.command == "feature":
            return _handle_feature(args, reader)
        if args.command == "evidence":
            return _handle_evidence(args, reader)
        if args.command == "knowledge":
            return _handle_knowledge(args, reader)
        if args.command == "context":
            return _handle_context(args, reader)
        if args.command == "protocols":
            return _handle_protocols(args)
        if args.command == "runs":
            return _handle_runs(args, reader)
    except AShareResearchError as error:
        parser.exit(2, f"ashare: error: {error}\n")

    parser.error("unknown command")
    return 2


def _add_daily_run_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--as-of", help="目标交易日，YYYYMMDD；不传则按交易日历和当前时间推断")
    parser.add_argument("--event-days", type=int, help="公告/业绩预告自然日回看天数；默认 7")
    parser.add_argument("--windows", default="5,20,60", help="构建 feature 的窗口，如 5,20,60")
    parser.add_argument("--scoring-profile", help="feature scoring profile JSON；默认使用内置 default.v1")
    parser.add_argument("--trade-days", type=int, default=DEFAULT_CONTEXT_TRADE_DAYS, help="market-structure context 的交易日窗口")
    parser.add_argument("--refresh", action=argparse.BooleanOptionalAction, default=True, help="覆盖当日已有分区；daily 默认可重入")
    parser.add_argument("--skip-data", action="store_true", help="跳过 dataset 更新，只重建 feature/context")
    parser.add_argument("--skip-features", action="store_true", help="跳过 feature 构建")
    parser.add_argument("--skip-context", action="store_true", help="跳过 context pack 构建")
    parser.add_argument("--continue-on-error", action=argparse.BooleanOptionalAction, default=True, help="记录失败并继续后续任务")
    parser.add_argument("--token", help="Tushare token；默认读取 TUSHARE_TOKEN 或 .env")
    parser.add_argument("--proxy-url", help="Tushare proxy url；默认读取 TUSHARE_PROXY_URL 或 .env")
    parser.add_argument("--env-file", default=".env", help="环境变量文件；不存在时忽略")
    parser.add_argument("--format", choices=("table", "json"), default="json")


def _add_daily_status_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--as-of", help="目标交易日，YYYYMMDD；不传则按交易日历和当前时间推断")
    parser.add_argument("--windows", default="5,20,60")
    parser.add_argument("--trade-days", type=int, default=DEFAULT_CONTEXT_TRADE_DAYS)
    parser.add_argument("--format", choices=("table", "json"), default="json")


def _handle_daily(args: argparse.Namespace, reader: MartReader) -> int:
    if args.daily_command == "run":
        payload = _run_daily(args, reader, repair=False)
        emit(payload, fmt=args.format)
        return 0 if payload["status"] != "blocked" else 1
    if args.daily_command == "repair":
        payload = _run_daily(args, reader, repair=True)
        emit(payload, fmt=args.format)
        return 0 if payload["status"] != "blocked" else 1
    if args.daily_command == "status":
        as_of = resolve_as_of(reader, args.as_of)
        payload = build_status(
            reader,
            as_of=as_of,
            windows=parse_daily_windows(args.windows),
            context_trade_days=args.trade_days,
        )
        latest = read_report(reader.data_dir)
        payload["latest_report"] = _report_pointer(latest)
        emit(payload, fmt=args.format)
        return 0 if payload["status"] != "blocked" else 1
    if args.daily_command == "report":
        as_of = resolve_as_of(reader, args.as_of) if args.as_of else None
        payload = read_report(reader.data_dir, as_of=as_of)
        if payload is None:
            resolved_as_of = as_of or resolve_as_of(reader, None)
            payload = build_status(
                reader,
                as_of=resolved_as_of,
                windows=parse_daily_windows(args.windows),
                context_trade_days=args.trade_days,
            )
            payload["report_found"] = False
        emit(payload, fmt=args.format)
        return 0 if payload["status"] != "blocked" else 1
    raise AShareResearchError(f"unknown daily command: {args.daily_command}")


def _run_daily(args: argparse.Namespace, reader: MartReader, *, repair: bool) -> dict[str, Any]:
    _load_env_file(Path(args.env_file)) if args.env_file else None
    as_of = resolve_as_of(reader, args.as_of)
    windows = parse_daily_windows(args.windows)
    event_days = event_days_for_daily(args.event_days)
    tasks = daily_plan()
    task_results: list[dict[str, Any]] = []

    if repair:
        before = build_status(reader, as_of=as_of, windows=windows, context_trade_days=args.trade_days)
        unready = {item["dataset"] for item in before["datasets"] if item["status"] != "ready"}
        tasks = [task for task in tasks if task.dataset in unready]

    if not args.skip_data:
        for task in tasks:
            try:
                dataset_args = _daily_dataset_args(args, task, as_of=as_of, event_days=event_days)
                result = _build_dataset(dataset_args, reader)
                task_results.append(
                    {
                        "dataset": task.dataset,
                        "group": task.group,
                        "required": task.required,
                        "status": "ready",
                        "rows": result.get("rows"),
                        "result": result,
                    }
                )
            except Exception as error:
                task_results.append(
                    {
                        "dataset": task.dataset,
                        "group": task.group,
                        "required": task.required,
                        "status": "failed",
                        "message": str(error),
                    }
                )
                if not args.continue_on_error:
                    raise

    feature_results: list[dict[str, Any]] = []
    if not args.skip_features:
        builder = FeatureBuilder(
            reader,
            feature_store=FeatureStore(reader.data_dir),
            registry=FeatureRegistry.builtin(),
            scoring_profile=_load_scoring_profile(args.scoring_profile),
        )
        for spec in FeatureRegistry.builtin().list():
            try:
                results = [result.to_dict() for result in builder.build(spec.name, as_of=as_of, windows=windows)]
                feature_results.append({"feature": spec.name, "status": "ready", "results": results})
            except Exception as error:
                feature_results.append({"feature": spec.name, "status": "failed", "message": str(error)})
                if not args.continue_on_error:
                    raise

    context_result: dict[str, Any] | None = None
    if not args.skip_context:
        try:
            context_result = ContextPackBuilder(reader.data_dir, reader=reader).build_market_structure(
                as_of=as_of,
                trade_days=args.trade_days,
            )
        except Exception as error:
            context_result = {"status": "failed", "message": str(error)}
            if not args.continue_on_error:
                raise

    status_payload = build_status(
        reader,
        as_of=as_of,
        windows=windows,
        context_trade_days=args.trade_days,
    )
    run_blocking = [item for item in task_results if item["status"] == "failed" and item.get("required")]
    run_blocking.extend(item for item in feature_results if item["status"] == "failed")
    if context_result and context_result.get("status") == "failed":
        run_blocking.append(context_result)
    run_warnings = [item for item in task_results if item["status"] == "failed" and not item.get("required")]

    if run_blocking or status_payload["status"] == "blocked":
        status = "blocked"
    elif status_payload["status"] == "degraded":
        status = "degraded"
    elif run_warnings or status_payload["status"] == "warning":
        status = "warning"
    else:
        status = "ready"

    payload = {
        "schema": "ashare.daily_run_report.v1",
        "as_of": as_of,
        "status": status,
        "mode": "repair" if repair else "run",
        "refresh": args.refresh,
        "event_days": event_days,
        "windows": windows,
        "trade_days": args.trade_days,
        "tasks": task_results,
        "features": feature_results,
        "context": _context_summary(context_result),
        "status_check": status_payload,
        "blocking": [*run_blocking, *status_payload["blocking"]],
        "warnings": [*run_warnings, *status_payload["warnings"]],
        "skipped": status_payload["skipped"],
    }
    path = write_report(reader.data_dir, payload)
    payload["report_path"] = str(path)
    return payload


def _daily_dataset_args(args: argparse.Namespace, task, *, as_of: str, event_days: int) -> argparse.Namespace:
    params = task_build_params(task, as_of=as_of, event_days=event_days)
    payload = {
        "dataset": task.dataset,
        "source": "tushare",
        "trade_date": None,
        "snapshot_date": None,
        "exchange": None,
        "period": None,
        "publish_date": None,
        "start_date": None,
        "end_date": None,
        "stock": [],
        "stock_file": None,
        "max_stocks": None,
        "category": "全部",
        "keyword": None,
        "timeout": 30,
        "scan_periods": 5,
        "param": [],
        "token": args.token,
        "proxy_url": args.proxy_url,
        "env_file": args.env_file,
        "refresh": args.refresh,
        "format": "json",
    }
    payload.update(params)
    return argparse.Namespace(**payload)


def _context_summary(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    if payload.get("status") == "failed":
        return payload
    return {
        "status": "ready",
        "schema": payload.get("schema"),
        "pack_id": payload.get("pack_id"),
        "path": payload.get("path"),
        "coverage": payload.get("coverage"),
        "quality_flags": payload.get("quality_flags"),
    }


def _report_pointer(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    return {
        "as_of": payload.get("as_of"),
        "status": payload.get("status"),
        "report_path": payload.get("report_path"),
    }


def _handle_data(args: argparse.Namespace, reader: MartReader) -> int:
    if args.data_command == "list":
        emit(reader.list_datasets(), fmt=args.format)
        return 0
    if args.data_command == "check":
        payload = reader.check(datasets=args.dataset or None, as_of=args.as_of)
        if args.format == "table":
            emit(payload["datasets"], fmt="table")
        else:
            emit(payload, fmt=args.format)
        return 0 if payload["status"] == "ready" else 1
    if args.data_command in {"build", "update"}:
        payload = _build_dataset(args, reader)
        emit(payload, fmt=args.format)
        return 0
    raise AShareResearchError(f"unknown data command: {args.data_command}")


def _handle_connectors(args: argparse.Namespace, reader: MartReader) -> int:
    registry = ConnectorRegistry.builtin()
    if args.connectors_command == "list":
        emit([spec.to_dict() for spec in registry.list()], fmt=args.format)
        return 0
    if args.connectors_command == "fetch":
        _load_env_file(Path(args.env_file)) if args.env_file else None
        response = _fetch_connector_response(args, registry)
        raw_path = RawStore(reader.data_dir).write_response(response) if args.store_raw else None
        payload = {
            "schema": "ashare.connector_fetch_result.v1",
            "source": response.source,
            "api_name": response.api_name,
            "rows": response.rows,
            "columns": list(response.columns),
            "requested_at": response.requested_at,
            "raw_path": str(raw_path) if raw_path else None,
            "preview": response.frame.head(args.limit).to_dict(orient="records"),
        }
        emit(payload, fmt=args.format)
        return 0
    raise AShareResearchError(f"unknown connectors command: {args.connectors_command}")


def _fetch_connector_response(args: argparse.Namespace, registry: ConnectorRegistry):
    params = _parse_params(args.param)
    if args.url:
        params["url"] = args.url
        params["method"] = args.method
    if args.header:
        params["headers"] = _parse_headers(args.header)
    if args.body_json:
        params["body"] = json.loads(args.body_json)
    fields = _fields(args.fields, ())
    if args.source == "tushare":
        connector = registry.create(args.source, token=args.token, proxy_url=args.proxy_url)
    else:
        connector = registry.create(args.source)
    return connector.fetch(args.api_name, params=params, fields=fields)


def _build_dataset(args: argparse.Namespace, reader: MartReader) -> dict[str, object]:
    _load_env_file(Path(args.env_file)) if args.env_file else None
    spec = reader.catalog.require(args.dataset)
    if spec.source == "project_builtin":
        return _build_project_builtin_dataset(args, reader, spec)
    if spec.source != "tushare":
        raise AShareResearchError(f"{spec.name}: source {spec.source!r} update is not implemented in data build")
    if spec.maintenance_kind in {"member_by_index_snapshot", "member_by_index_trade_date"}:
        return _build_member_dataset(args, reader, spec)
    if spec.maintenance_kind == "stock_pool_daily":
        return _build_stock_pool_daily_dataset(args, reader, spec)
    if spec.maintenance_kind == "stock_pool_financial":
        return _build_stock_pool_financial_dataset(args, reader, spec)
    if spec.maintenance_kind == "financial_disclosure_date":
        return _build_disclosure_date_dataset(args, reader, spec)
    return _build_single_partition_dataset(args, reader, spec)


def _build_single_partition_dataset(args: argparse.Namespace, reader: MartReader, spec: DatasetSpec) -> dict[str, object]:
    partition = _partition_args(args)
    if not partition:
        raise AShareResearchError(f"{args.dataset}: partition is required")
    fields = list(spec.default_fields)
    connector = TushareConnector(token=args.token, proxy_url=args.proxy_url)
    responses, raw_paths, frame = _fetch_dataset_frame(connector, reader.data_dir, spec, args, partition, fields=fields)
    return _publish_dataset_frame(reader, spec, partition, frame, responses=responses, raw_paths=raw_paths, refresh=args.refresh)


def _publish_dataset_frame(
    reader: MartReader,
    spec: DatasetSpec,
    partition: dict[str, str],
    frame: pd.DataFrame,
    *,
    responses: list[SourceResponse],
    raw_paths: list[Path],
    refresh: bool,
    source_extra: dict[str, Any] | None = None,
) -> dict[str, object]:
    frame = _ensure_partition_columns(frame, partition)
    mart_path = MartPublisher(reader.data_dir, reader.catalog).publish(
        spec.name,
        frame,
        partition=partition,
        source={
            "kind": responses[0].source,
            "api_name": responses[0].api_name,
            "params": responses[0].params if len(responses) == 1 else {"requests": [response.params for response in responses]},
            "fields": list(responses[0].fields),
            "raw_path": str(raw_paths[0]),
            "raw_paths": [str(path) for path in raw_paths],
            "requested_at": responses[0].requested_at,
            **(source_extra or {}),
        },
        refresh=refresh,
    )
    meta = reader.load_meta(spec.name, partition)
    return {
        "schema": "ashare.data_build_result.v1",
        "dataset": spec.name,
        "partition": partition,
        "rows": len(frame),
        "columns": [str(column) for column in frame.columns],
        "quality_status": meta.quality_status,
        "quality": meta.quality,
        "missing_analysis_columns": list(meta.quality.get("missing_analysis_columns", [])),
        "non_null_ratios": dict(meta.quality.get("non_null_ratios", {})),
        "raw_path": str(raw_paths[0]),
        "raw_paths": [str(path) for path in raw_paths],
        "mart_path": str(mart_path),
    }


def _build_project_builtin_dataset(args: argparse.Namespace, reader: MartReader, spec: DatasetSpec) -> dict[str, object]:
    if spec.maintenance_kind == "akshare_notice":
        from .events import fetch_notice

        start_date, end_date = _date_range_args(args, default_days=1)
        records = []
        for date_text in _date_range(start_date, end_date):
            records.extend(
                fetch_notice(
                    days=1,
                    end_date=date_text,
                    stock=args.stock[0] if args.stock else None,
                    category=args.category,
                    keyword=args.keyword,
                    timeout=args.timeout,
                )
            )
        return _publish_records_by_date(
            reader,
            spec,
            records,
            date_key="publish_date",
            partition_key="publish_date",
            source={"kind": "project_builtin", "source": "a_stock_notice", "start_date": start_date, "end_date": end_date},
            refresh=args.refresh,
            expected_partitions=_date_range(start_date, end_date),
        )
    if spec.maintenance_kind == "akshare_forecast":
        from .events import fetch_forecast

        start_date, end_date = _date_range_args(args, default_days=60)
        periods = [args.period] if args.period else None
        records = fetch_forecast(
            days=max(len(_date_range(start_date, end_date)), 1),
            end_date=end_date,
            stock=args.stock[0] if args.stock else None,
            periods=periods,
            scan_periods=args.scan_periods,
            keyword=args.keyword,
            timeout=args.timeout,
        )
        return _publish_records_by_date(
            reader,
            spec,
            records,
            date_key="publish_date",
            partition_key="publish_date",
            source={"kind": "project_builtin", "source": "earnings_forecast", "start_date": start_date, "end_date": end_date},
            refresh=args.refresh,
            expected_partitions=_date_range(start_date, end_date),
        )
    raise AShareResearchError(f"{spec.name}: project builtin kind {spec.maintenance_kind!r} is not implemented")


def _publish_records_by_date(
    reader: MartReader,
    spec: DatasetSpec,
    records: list[dict[str, Any]],
    *,
    date_key: str,
    partition_key: str,
    source: dict[str, Any],
    refresh: bool,
    expected_partitions: list[str] | None = None,
) -> dict[str, object]:
    grouped: dict[str, list[dict[str, Any]]] = {_iso_date(str(value)): [] for value in expected_partitions or []}
    for record in records:
        value = record.get(date_key)
        if not value:
            continue
        grouped.setdefault(_iso_date(str(value)), []).append(record)
    if not grouped:
        target = source.get("end_date") or source.get("start_date") or datetime.now().strftime("%Y%m%d")
        grouped[_iso_date(str(target))] = []
    publisher = MartPublisher(reader.data_dir, reader.catalog)
    results = []
    for partition_value, group in sorted(grouped.items()):
        if group:
            frame = _json_safe_frame(pd.DataFrame(group))
        else:
            columns = list(dict.fromkeys((partition_key, *spec.required_columns)))
            frame = pd.DataFrame(columns=columns)
        frame = _ensure_partition_columns(frame, {partition_key: partition_value})
        path = publisher.publish(
            spec.name,
            frame,
            partition={partition_key: partition_value},
            source=source,
            refresh=refresh,
        )
        results.append({"partition": {partition_key: partition_value}, "rows": len(frame), "mart_path": str(path)})
    return {
        "schema": "ashare.data_build_result.v1",
        "dataset": spec.name,
        "partitions": results,
        "rows": sum(item["rows"] for item in results),
    }


def _json_safe_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    output = frame.copy()
    for column in output.columns:
        if output[column].map(lambda value: isinstance(value, (dict, list, tuple))).any():
            output[column] = output[column].map(
                lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list, tuple)) else value
            )
    return output


def _fetch_dataset_frame(
    connector: TushareConnector,
    data_dir: Path,
    spec: DatasetSpec,
    args: argparse.Namespace,
    partition: dict[str, str],
    *,
    fields: list[str],
) -> tuple[list[SourceResponse], list[Path], pd.DataFrame]:
    raw_store = RawStore(data_dir)
    responses: list[SourceResponse] = []
    raw_paths: list[Path] = []
    for label, params, variant_fields in _source_param_sets(args, spec, partition):
        request_fields = variant_fields if variant_fields is not None else fields
        for response in _fetch_response_pages(connector, spec, params=params, fields=request_fields):
            response = _with_variant_column(response, label)
            raw_paths.append(raw_store.write_response(response))
            responses.append(response)
    if not responses:
        raise AShareResearchError(f"{spec.name}: no source response generated")
    frames = [response.frame for response in responses]
    frame = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    return responses, raw_paths, frame.drop_duplicates(keep="last").reset_index(drop=True)


def _fetch_response_pages(
    connector: TushareConnector,
    spec: DatasetSpec,
    *,
    params: dict[str, object],
    fields: list[str],
) -> list[SourceResponse]:
    if not spec.page_limit:
        return [connector.fetch(spec.source_api, params=params, fields=fields)]
    responses: list[SourceResponse] = []
    offset = 0
    for _ in range(max(spec.max_pages, 1)):
        page_params = dict(params)
        page_params["limit"] = spec.page_limit
        page_params["offset"] = offset
        response = connector.fetch(spec.source_api, params=page_params, fields=fields)
        responses.append(response)
        if response.rows < spec.page_limit:
            break
        offset += spec.page_limit
    return responses


def _with_variant_column(response: SourceResponse, label: str | None) -> SourceResponse:
    if not label or label == "default" or response.rows == 0:
        return response
    frame = response.frame.copy()
    frame["_variant"] = label
    return SourceResponse(
        source=response.source,
        api_name=response.api_name,
        params=response.params,
        fields=response.fields,
        rows=len(frame),
        columns=tuple(str(column) for column in frame.columns),
        requested_at=response.requested_at,
        frame=frame,
    )


def _ensure_partition_columns(frame: pd.DataFrame, partition: dict[str, str]) -> pd.DataFrame:
    if frame.empty:
        return frame
    output = frame.copy()
    for key, value in partition.items():
        if key not in output.columns:
            output[key] = value
    return output


def _build_member_dataset(args: argparse.Namespace, reader: MartReader, spec: DatasetSpec) -> dict[str, object]:
    partition = _partition_args(args)
    if not partition:
        raise AShareResearchError(f"{spec.name}: partition is required")
    driver, driver_partition = _read_driver_frame(reader, spec, partition)
    code_column = _first_existing_column(driver, spec.driver_code_columns)
    if code_column is None:
        raise AShareResearchError(f"{spec.driver_dataset}: missing driver code column")
    name_column = _first_existing_column(driver, spec.driver_name_columns)
    connector = TushareConnector(token=args.token, proxy_url=args.proxy_url)
    fields = list(spec.default_fields)
    base_params: dict[str, object] = {}
    if spec.maintenance_kind == "member_by_index_trade_date" and spec.date_param:
        base_params[spec.date_param] = _partition_value(partition)
    raw_store = RawStore(reader.data_dir)
    responses: list[SourceResponse] = []
    raw_paths: list[Path] = []
    frames: list[pd.DataFrame] = []
    selected_codes = _selected_driver_codes(args, driver, code_column)

    try:
        if selected_codes is not None:
            raise AShareResearchError("driver selection requested")
        for response in _fetch_response_pages(connector, spec, params=base_params, fields=fields):
            raw_paths.append(raw_store.write_response(response))
            responses.append(response)
            if not response.frame.empty:
                frames.append(_enrich_member_frame(response.frame, spec, driver, code_column, name_column))
    except AShareResearchError:
        frames = []

    if not frames:
        errors: list[dict[str, object]] = []
        for code in selected_codes or _driver_codes(driver, code_column):
            params = {spec.driver_code_param: code, **base_params}
            try:
                for response in _fetch_response_pages(connector, spec, params=params, fields=fields):
                    raw_paths.append(raw_store.write_response(response))
                    responses.append(response)
                    if not response.frame.empty:
                        frames.append(_enrich_member_frame(response.frame, spec, driver, code_column, name_column, fallback_driver_code=code))
            except AShareResearchError as error:
                errors.append({"driver_code": code, "error": str(error)})
        source_extra = {"driver_dataset": spec.driver_dataset, "driver_partition": driver_partition, "driver_errors": errors[:50]}
    else:
        source_extra = {"driver_dataset": spec.driver_dataset, "driver_partition": driver_partition, "fetch_mode": "bulk"}
    frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not responses:
        raise AShareResearchError(f"{spec.name}: no successful source response")
    return _publish_dataset_frame(reader, spec, partition, frame, responses=responses, raw_paths=raw_paths, refresh=args.refresh, source_extra=source_extra)


def _read_driver_frame(reader: MartReader, spec: DatasetSpec, partition: dict[str, str]) -> tuple[pd.DataFrame, dict[str, str]]:
    if not spec.driver_dataset:
        raise AShareResearchError(f"{spec.name}: driver_dataset is required")
    candidates = [partition]
    for key in ("trade_date", "snapshot_date"):
        latest = reader.latest_partition(spec.driver_dataset, key)
        if latest is not None:
            candidates.append(latest.values)
    seen: set[tuple[tuple[str, str], ...]] = set()
    for candidate in candidates:
        marker = tuple(sorted(candidate.items()))
        if marker in seen:
            continue
        seen.add(marker)
        try:
            frame = reader.read_partition(spec.driver_dataset, candidate)
        except AShareResearchError:
            continue
        if not frame.empty:
            return frame, candidate
    raise AShareResearchError(f"{spec.driver_dataset}: no usable driver mart partition")


def _driver_codes(frame: pd.DataFrame, code_column: str) -> list[str]:
    return list(dict.fromkeys(str(code).strip() for code in frame[code_column].dropna().tolist() if str(code).strip()))


def _selected_driver_codes(args: argparse.Namespace, frame: pd.DataFrame, code_column: str) -> list[str] | None:
    explicit = [_stock_code_to_ts_code(code) for code in getattr(args, "stock", []) or [] if str(code).strip()]
    if explicit:
        return explicit
    max_stocks = getattr(args, "max_stocks", None)
    if max_stocks and max_stocks > 0:
        return _driver_codes(frame, code_column)[:max_stocks]
    return None


def _enrich_member_frame(
    frame: pd.DataFrame,
    spec: DatasetSpec,
    driver: pd.DataFrame,
    code_column: str,
    name_column: str | None,
    *,
    fallback_driver_code: str | None = None,
) -> pd.DataFrame:
    output = frame.copy()
    member_code_column = _first_existing_column(output, (spec.driver_code_param, "_driver_ts_code", "index_code", "ts_code"))
    if member_code_column:
        output["_driver_ts_code"] = output[member_code_column].astype(str)
    elif fallback_driver_code:
        output["_driver_ts_code"] = fallback_driver_code
    if name_column and "_driver_ts_code" in output.columns:
        names = {
            str(row[code_column]): str(row[name_column])
            for row in driver[[code_column, name_column]].dropna(subset=[code_column]).to_dict("records")
        }
        output["_driver_name"] = output["_driver_ts_code"].map(names).fillna("")
    output["_driver_dataset"] = spec.driver_dataset
    return output


def _build_stock_pool_daily_dataset(args: argparse.Namespace, reader: MartReader, spec: DatasetSpec) -> dict[str, object]:
    partition = _partition_args(args)
    trade_date = partition.get("trade_date") or args.end_date
    if not trade_date:
        raise AShareResearchError(f"{spec.name}: --trade-date or --end-date is required")
    start_date = args.start_date or trade_date
    end_date = args.end_date or trade_date
    connector = TushareConnector(token=args.token, proxy_url=args.proxy_url)
    fields = list(spec.default_fields)
    codes = _stock_pool_codes(args, reader, connector, end_date)
    raw_store = RawStore(reader.data_dir)
    responses: list[SourceResponse] = []
    raw_paths: list[Path] = []
    frames: list[pd.DataFrame] = []
    errors: list[dict[str, object]] = []
    for ts_code in codes:
        try:
            response = connector.fetch(spec.source_api, params={"ts_code": ts_code, "start_date": start_date, "end_date": end_date}, fields=fields)
        except AShareResearchError as error:
            errors.append({"ts_code": ts_code, "error": str(error)})
            continue
        raw_paths.append(raw_store.write_response(response))
        responses.append(response)
        if not response.frame.empty:
            frames.append(response.frame)
    if not responses:
        raise AShareResearchError(f"{spec.name}: no successful stock-pool response")
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if combined.empty or "trade_date" not in combined.columns:
        target_partition = {"trade_date": trade_date}
        return _publish_dataset_frame(
            reader,
            spec,
            target_partition,
            combined,
            responses=responses,
            raw_paths=raw_paths,
            refresh=args.refresh,
            source_extra={"stock_pool": len(codes), "errors": errors[:50]},
        )
    results = []
    publisher = MartPublisher(reader.data_dir, reader.catalog)
    for value, group in combined.groupby(combined["trade_date"].astype(str)):
        group = _ensure_partition_columns(group.reset_index(drop=True), {"trade_date": value})
        path = publisher.publish(
            spec.name,
            group,
            partition={"trade_date": value},
            source={"kind": "tushare", "api_name": spec.source_api, "stock_pool": len(codes), "raw_paths": [str(path) for path in raw_paths], "errors": errors[:50]},
            refresh=args.refresh,
        )
        results.append({"partition": {"trade_date": value}, "rows": len(group), "mart_path": str(path)})
    return {"schema": "ashare.data_build_result.v1", "dataset": spec.name, "partitions": results, "rows": sum(item["rows"] for item in results), "raw_paths": [str(path) for path in raw_paths], "errors": errors[:50]}


def _build_stock_pool_financial_dataset(args: argparse.Namespace, reader: MartReader, spec: DatasetSpec) -> dict[str, object]:
    period = args.period
    end_date = args.end_date or period
    if not end_date:
        raise AShareResearchError(f"{spec.name}: --period or --end-date is required")
    start_date = args.start_date or end_date
    connector = TushareConnector(token=args.token, proxy_url=args.proxy_url)
    fields = list(spec.default_fields)
    codes = _stock_pool_codes(args, reader, connector, end_date)
    raw_store = RawStore(reader.data_dir)
    responses: list[SourceResponse] = []
    raw_paths: list[Path] = []
    frames: list[pd.DataFrame] = []
    errors: list[dict[str, object]] = []
    for ts_code in codes:
        for label, variant_params, variant_fields in _variant_specs(spec):
            request_fields = variant_fields if variant_fields is not None else fields
            params: dict[str, object] = {"ts_code": ts_code, **variant_params}
            if period:
                params["period"] = period
            elif spec.source_api != "dividend":
                params.update({"start_date": start_date, "end_date": end_date})
            try:
                response = connector.fetch(spec.source_api, params=params, fields=request_fields)
            except AShareResearchError as error:
                errors.append({"ts_code": ts_code, "variant": label, "error": str(error)})
                continue
            response = _with_variant_column(response, label)
            raw_paths.append(raw_store.write_response(response))
            responses.append(response)
            if not response.frame.empty:
                frames.append(response.frame)
    if not responses:
        raise AShareResearchError(f"{spec.name}: no successful financial response")
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if "period" not in combined.columns:
        combined = combined.copy()
        combined["period"] = period or combined.apply(_financial_period_from_row, axis=1)
    combined = combined[combined["period"].astype(str) != ""]
    results = []
    publisher = MartPublisher(reader.data_dir, reader.catalog)
    for value, group in combined.groupby(combined["period"].astype(str)):
        group = _ensure_partition_columns(group.reset_index(drop=True), {"period": value})
        path = publisher.publish(
            spec.name,
            group,
            partition={"period": value},
            source={"kind": "tushare", "api_name": spec.source_api, "stock_pool": len(codes), "raw_paths": [str(path) for path in raw_paths], "errors": errors[:50]},
            refresh=args.refresh,
        )
        results.append({"partition": {"period": value}, "rows": len(group), "mart_path": str(path)})
    return {"schema": "ashare.data_build_result.v1", "dataset": spec.name, "partitions": results, "rows": sum(item["rows"] for item in results), "raw_paths": [str(path) for path in raw_paths], "errors": errors[:50]}


def _build_disclosure_date_dataset(args: argparse.Namespace, reader: MartReader, spec: DatasetSpec) -> dict[str, object]:
    period = args.period or args.end_date
    if not period:
        raise AShareResearchError(f"{spec.name}: --period is required")
    partition = {"period": period}
    fields = list(spec.default_fields)
    connector = TushareConnector(token=args.token, proxy_url=args.proxy_url)
    responses, raw_paths, frame = _fetch_dataset_frame(connector, reader.data_dir, spec, args, partition, fields=fields)
    return _publish_dataset_frame(reader, spec, partition, frame, responses=responses, raw_paths=raw_paths, refresh=args.refresh)


def _stock_pool_codes(args: argparse.Namespace, reader: MartReader, connector: TushareConnector, end_date: str) -> list[str]:
    codes = [str(code).strip() for code in getattr(args, "stock", []) or [] if str(code).strip()]
    if getattr(args, "stock_file", None):
        codes.extend(line.strip() for line in Path(args.stock_file).read_text(encoding="utf-8").splitlines() if line.strip())
    if not codes:
        try:
            frame = reader.read_partition("stock_basic", {"snapshot_date": end_date}, columns=["ts_code"])
        except AShareResearchError:
            response = connector.fetch("stock_basic", params={"exchange": "", "list_status": "L"}, fields=["ts_code"])
            frame = response.frame
        codes = [str(code).strip() for code in frame["ts_code"].dropna().tolist() if str(code).strip()]
    codes = list(dict.fromkeys(_stock_code_to_ts_code(code) for code in codes))
    max_stocks = getattr(args, "max_stocks", None)
    return codes[:max_stocks] if max_stocks and max_stocks > 0 else codes


def _stock_code_to_ts_code(code: str) -> str:
    text = str(code or "").strip()
    if "." in text:
        return text
    digits = "".join(char for char in text if char.isdigit())
    if len(digits) < 6:
        return text
    symbol = digits[-6:]
    if symbol.startswith(("6", "5")):
        return f"{symbol}.SH"
    if symbol.startswith(("8", "4", "9")):
        return f"{symbol}.BJ"
    return f"{symbol}.SZ"


def _financial_period_from_row(row: pd.Series) -> str:
    for column in ("period", "end_date", "report_period", "f_ann_date", "ann_date"):
        if column in row and pd.notna(row[column]):
            return _normalize_yyyymmdd(row[column])
    return ""


def _normalize_yyyymmdd(value: object) -> str:
    text = str(value or "").strip()
    digits = "".join(char for char in text if char.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _first_existing_column(frame: pd.DataFrame, candidates: tuple[str, ...] | list[str]) -> str | None:
    for column in candidates:
        if column in frame.columns:
            return column
    return None


def _partition_value(partition: dict[str, str]) -> str:
    if not partition:
        return ""
    return next(iter(partition.values()))


def _handle_mart(args: argparse.Namespace, reader: MartReader) -> int:
    partition = _partition_args(args)
    if args.mart_command == "read":
        frame = reader.read_partition(args.dataset, partition, limit=args.limit)
        emit(frame, fmt=args.format)
        return 0
    if args.mart_command == "meta":
        emit(reader.load_meta(args.dataset, partition).to_dict(), fmt="json")
        return 0
    raise AShareResearchError(f"unknown mart command: {args.mart_command}")


def _add_data_build_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser], name: str, help_text: str) -> None:
    data_build = subparsers.add_parser(name, help=help_text)
    data_build.add_argument("dataset")
    data_build.add_argument("--source", choices=("tushare",), default="tushare")
    data_build.add_argument("--trade-date", help="发布 trade_date 分区，同时作为 Tushare trade_date 参数")
    data_build.add_argument("--snapshot-date", help="发布 snapshot_date 分区；常用于 stock_basic")
    data_build.add_argument("--exchange", help="发布 exchange 分区；常用于 trade_cal")
    data_build.add_argument("--period", help="发布 period 分区")
    data_build.add_argument("--publish-date", help="公告/事件发布日期，YYYYMMDD 或 YYYY-MM-DD")
    data_build.add_argument("--start-date", help="Tushare start_date 参数")
    data_build.add_argument("--end-date", help="Tushare end_date 参数")
    data_build.add_argument("--stock", action="append", default=[], help="股票池代码，可重复；用于 stock_pool_* 数据集")
    data_build.add_argument("--stock-file", help="股票池文件，每行一个代码")
    data_build.add_argument("--max-stocks", type=int, help="限制股票池数量；用于 smoke 或分批更新")
    data_build.add_argument("--category", default="全部", help="公告分类，默认全部")
    data_build.add_argument("--keyword", help="公告/业绩预告关键词过滤")
    data_build.add_argument("--timeout", type=int, default=30, help="事件源请求超时秒数")
    data_build.add_argument("--scan-periods", type=int, default=5, help="业绩预告自动扫描最近报告期数量")
    data_build.add_argument("-p", "--param", action="append", default=[], help="额外 source 参数，key=value 或 key:=JSON")
    data_build.add_argument("--token", help="Tushare token；默认读取 TUSHARE_TOKEN 或 .env")
    data_build.add_argument("--proxy-url", help="Tushare proxy url；默认读取 TUSHARE_PROXY_URL 或 .env")
    data_build.add_argument("--env-file", default=".env", help="环境变量文件；不存在时忽略")
    data_build.add_argument("--refresh", action="store_true", help="覆盖已有 mart 分区")
    data_build.add_argument("--format", choices=("table", "json"), default="json")


def _handle_feature(args: argparse.Namespace, reader: MartReader) -> int:
    store = FeatureStore(reader.data_dir)
    registry = FeatureRegistry.builtin()
    if args.feature_command == "list":
        registered = [spec.to_dict() | {"published": False, "as_of": "", "window": ""} for spec in registry.list()]
        published = [
            {"name": row["feature"], "published": True, "as_of": row["as_of"], "window": row["window"], "path": row["path"]}
            for row in store.discover()
        ]
        emit([*registered, *published], fmt=args.format)
        return 0
    if args.feature_command == "build":
        windows = _parse_windows(args.windows)
        builder = FeatureBuilder(reader, feature_store=store, registry=registry, scoring_profile=_load_scoring_profile(args.scoring_profile))
        results = [result.to_dict() for result in builder.build(args.feature, as_of=args.as_of, windows=windows)]
        emit(results, fmt=args.format)
        return 0
    if args.feature_command == "read":
        frame = store.read_partition(args.feature, as_of=args.as_of, window=args.window, limit=args.limit)
        emit(frame, fmt=args.format)
        return 0
    if args.feature_command == "meta":
        emit(store.load_meta(args.feature, as_of=args.as_of, window=args.window).to_dict(), fmt="json")
        return 0
    raise AShareResearchError(f"unknown feature command: {args.feature_command}")


def _handle_evidence(args: argparse.Namespace, reader: MartReader) -> int:
    store = EvidenceStore(reader.data_dir)
    if args.evidence_command == "ingest":
        payload = _load_evidence_payload(Path(args.input))
        emit(store.ingest_evidence(payload).to_dict(), fmt=args.format)
        return 0
    if args.evidence_command == "validate":
        payload = _load_evidence_payload(Path(args.input))
        records = payload if isinstance(payload, list) else [payload]
        validated = [store.validate_evidence(record).to_dict() for record in records]
        emit(validated, fmt=args.format)
        return 0
    if args.evidence_command == "list":
        records = [record.to_dict() for record in store.read_records()]
        if args.limit and args.limit > 0:
            records = records[: args.limit]
        emit(records, fmt=args.format)
        return 0
    if args.evidence_command == "search":
        records = [record.to_dict() for record in store.find_evidence(**_evidence_query(args), limit=args.limit)]
        emit(records, fmt=args.format)
        return 0
    if args.evidence_command == "export":
        query = _evidence_query(args)
        limit = args.limit if args.limit and args.limit > 0 else None
        records = store.find_evidence(**query, limit=limit)
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            "\n".join(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True) for record in records)
            + ("\n" if records else ""),
            encoding="utf-8",
        )
        emit(
            {
                "schema": "ashare.evidence_export_result.v1",
                "records": len(records),
                "output": str(output_path),
            },
            fmt=args.format,
        )
        return 0
    if args.evidence_command == "collect":
        emit(store.collect_evidence(args.question, as_of=args.as_of), fmt=args.format)
        return 0
    if args.evidence_command == "adapter-candidates":
        emit(store.adapter_candidates(min_records=args.min_records), fmt=args.format)
        return 0
    if args.evidence_command == "adapter-specs":
        registry = EvidenceAdapterRegistry(reader.data_dir)
        if args.adapter_specs_command == "list":
            rows = [spec.to_dict() for spec in registry.list(status=args.status)]
            emit(rows, fmt=args.format)
            return 0
        if args.adapter_specs_command == "propose":
            candidates = store.adapter_candidates(min_records=args.min_records)
            rows = registry.propose_from_candidates(candidates, overwrite=args.overwrite)
            emit(
                {
                    "schema": "ashare.evidence_adapter_propose_result.v1",
                    "proposed": len(rows),
                    "adapters": rows,
                },
                fmt=args.format,
            )
            return 0
        if args.adapter_specs_command == "install":
            spec = EvidenceAdapterSpec.from_dict(_load_json_file(Path(args.input)))
            path = registry.write(spec, overwrite=args.overwrite)
            emit(spec.to_dict() | {"path": str(path)}, fmt=args.format)
            return 0
        if args.adapter_specs_command == "run":
            result = EvidenceAdapterRunner(
                evidence_store=store,
                adapter_registry=registry,
            ).run(args.adapter_id, params=_parse_params(args.param))
            emit(result.to_dict(), fmt=args.format)
            return 0
    raise AShareResearchError(f"unknown evidence command: {args.evidence_command}")


def _handle_knowledge(args: argparse.Namespace, reader: MartReader) -> int:
    store = KnowledgeStore(reader.data_dir)
    if args.knowledge_command == "propose":
        payload = _load_structured_payload(Path(args.input), label="knowledge")
        result = store.propose_records(payload, reason=args.reason, proposed_by=args.proposed_by)
        emit(result.to_dict(), fmt=args.format)
        return 0
    if args.knowledge_command == "accept":
        result = store.accept(args.proposal_id, accepted_by=args.accepted_by, reason=args.reason)
        emit(result.to_dict(), fmt=args.format)
        return 0
    if args.knowledge_command == "proposals":
        rows = proposal_rows(store.read_proposals(status=args.status))
        if args.limit and args.limit > 0:
            rows = rows[: args.limit]
        emit(rows, fmt=args.format)
        return 0
    if args.knowledge_command == "list":
        records = [record.to_dict() for record in store.read_current_records()]
        if args.limit and args.limit > 0:
            records = records[: args.limit]
        emit(records, fmt=args.format)
        return 0
    if args.knowledge_command == "search":
        records = [
            record.to_dict()
            for record in store.search(
                entity=args.entity,
                predicate=args.predicate,
                source_type=args.source_type,
                evidence_id=args.evidence_id,
                limit=args.limit,
            )
        ]
        emit(records, fmt=args.format)
        return 0
    if args.knowledge_command == "snapshot":
        emit(store.snapshot(output_path=args.output), fmt=args.format)
        return 0
    raise AShareResearchError(f"unknown knowledge command: {args.knowledge_command}")


def _handle_context(args: argparse.Namespace, reader: MartReader) -> int:
    builder = ContextPackBuilder(reader.data_dir, reader=reader)
    if args.context_command == "build":
        if args.context_pack_type == "market-structure":
            payload = builder.build_market_structure(
                as_of=args.as_of,
                trade_days=args.trade_days,
                output_path=args.output,
            )
            emit(payload, fmt=args.format)
            return 0
        if args.context_pack_type == "industry":
            payload = builder.build_industry(
                industry=args.industry,
                as_of=args.as_of,
                output_path=args.output,
            )
            emit(payload, fmt=args.format)
            return 0
        if args.context_pack_type == "stock":
            payload = builder.build_stock(
                ts_code=args.ts_code,
                as_of=args.as_of,
                output_path=args.output,
            )
            emit(payload, fmt=args.format)
            return 0
    raise AShareResearchError(f"unknown context command: {args.context_command}")


def _handle_protocols(args: argparse.Namespace) -> int:
    registry = ProtocolRegistry.builtin()
    if args.protocols_command == "list":
        emit([spec.to_dict() for spec in registry.list()], fmt=args.format)
        return 0
    if args.protocols_command == "show":
        emit(registry.require(args.protocol_id).to_dict(), fmt=args.format)
        return 0
    if args.protocols_command == "validate":
        emit(registry.validate(args.protocol_id), fmt=args.format)
        return 0
    if args.protocols_command == "output-schema":
        spec = registry.require(args.protocol_id)
        emit(registry.output_schema(spec.output_schema or ""), fmt=args.format)
        return 0
    raise AShareResearchError(f"unknown protocols command: {args.protocols_command}")


def _handle_runs(args: argparse.Namespace, reader: MartReader) -> int:
    if args.runs_command == "record":
        question = _load_question(args)
        ad_hoc_protocol = _load_json_file(Path(args.ad_hoc_protocol)) if args.ad_hoc_protocol else None
        validated_output = _load_json_file(Path(args.validated_output)) if args.validated_output else None
        agent_reasoning = _load_json_file(Path(args.agent_reasoning)) if args.agent_reasoning else None
        model_output = Path(args.model_output_file).read_text(encoding="utf-8") if args.model_output_file else None
        report = Path(args.report_file).read_text(encoding="utf-8") if args.report_file else None
        recorder = RunRecorder(reader.data_dir, runs_dir=args.runs_dir)
        payload = recorder.record(
            question=question,
            as_of=args.as_of,
            protocol_id=args.protocol,
            ad_hoc_protocol=ad_hoc_protocol,
            context_pack_paths=[Path(path) for path in args.context_pack],
            evidence_path=Path(args.evidence) if args.evidence else None,
            knowledge_path=Path(args.knowledge) if args.knowledge else None,
            model_output=model_output,
            validated_output=validated_output,
            agent_reasoning=agent_reasoning,
            report=report,
            run_id=args.run_id,
        )
        emit(payload, fmt=args.format)
        return 0
    if args.runs_command == "list":
        emit(RunRecorder(reader.data_dir, runs_dir=args.runs_dir).list_runs(), fmt=args.format)
        return 0
    if args.runs_command == "replay":
        emit(replay_run(args.run_dir), fmt=args.format)
        return 0
    raise AShareResearchError(f"unknown runs command: {args.runs_command}")


def _partition_args(args: argparse.Namespace) -> dict[str, str]:
    if getattr(args, "latest", False):
        return {}
    partition: dict[str, str] = {}
    for raw in getattr(args, "partition", []) or []:
        if "=" not in raw:
            raise AShareResearchError(f"invalid partition {raw!r}; expected key=value")
        key, value = raw.split("=", 1)
        if not key or not value:
            raise AShareResearchError(f"invalid partition {raw!r}; expected key=value")
        partition[key] = value
    for key in ("trade_date", "snapshot_date", "exchange", "period"):
        value = getattr(args, key, None)
        if value:
            partition[key] = value
    if getattr(args, "publish_date", None):
        partition["publish_date"] = _iso_date(args.publish_date)
    return partition


def _parse_windows(raw: str) -> list[int]:
    windows: list[int] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            window = int(item)
        except ValueError as error:
            raise AShareResearchError(f"invalid window {item!r}") from error
        if window <= 0:
            raise AShareResearchError(f"invalid window {item!r}; expected positive integer")
        windows.append(window)
    if not windows:
        raise AShareResearchError("at least one window is required")
    return windows


def _load_scoring_profile(path: str | None) -> ScoringProfile:
    if not path:
        return ScoringProfile.builtin()
    return ScoringProfile.from_file(Path(path))


def _source_params(args: argparse.Namespace, spec: DatasetSpec, partition: dict[str, str]) -> dict[str, object]:
    params: dict[str, object] = {}
    if args.dataset == "stock_basic":
        params.update({"exchange": "", "list_status": "L"})
    if spec.maintenance_kind == "calendar":
        if "exchange" in partition:
            params["exchange"] = partition["exchange"]
    elif spec.maintenance_kind == "snapshot":
        pass
    elif spec.maintenance_kind == "snapshot_range":
        end_date = args.end_date or partition.get("snapshot_date") or _partition_value(partition)
        start_date = args.start_date or (_parse_yyyymmdd(end_date) - timedelta(days=spec.range_lookback_days)).strftime("%Y%m%d")
        params.update({"start_date": start_date, "end_date": end_date})
    elif spec.maintenance_kind == "financial_disclosure_date":
        value = partition.get("period") or _partition_value(partition)
        if value:
            params["end_date"] = value
    elif spec.date_param:
        value = partition.get(spec.date_param) or _partition_value(partition)
        if value:
            params[spec.date_param] = value
    if args.start_date:
        params["start_date"] = args.start_date
    if args.end_date:
        params["end_date"] = args.end_date
    params.update(_parse_params(args.param))
    return params


def _source_param_sets(
    args: argparse.Namespace,
    spec: DatasetSpec,
    partition: dict[str, str],
) -> list[tuple[str | None, dict[str, object], list[str] | None]]:
    base = _source_params(args, spec, partition)
    variants = _variant_specs(spec)
    variant_keys = {key for _, params, _ in variants for key in params}
    if len(variants) == 1 and not variants[0][1]:
        return [(variants[0][0], base, variants[0][2])]
    if variant_keys & set(base):
        return [("explicit", base, None)]
    return [(label, base | params, fields) for label, params, fields in variants]


def _variant_specs(spec: DatasetSpec) -> list[tuple[str | None, dict[str, object], list[str] | None]]:
    if not spec.source_variants:
        return [("default", {}, None)]
    variants: list[tuple[str | None, dict[str, object], list[str] | None]] = []
    for raw in spec.source_variants:
        if "params" in raw:
            params = dict(raw.get("params") or {})
            label = str(raw.get("label") or "variant")
            fields_raw = raw.get("fields")
        else:
            params = dict(raw)
            label = None
            fields_raw = None
        fields = [str(field) for field in fields_raw] if isinstance(fields_raw, list) else None
        variants.append((label, params, fields))
    return variants


def _parse_yyyymmdd(value: str) -> datetime:
    text = str(value).strip()
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return datetime.strptime(text, "%Y-%m-%d")
    return datetime.strptime(text, "%Y%m%d")


def _iso_date(value: str) -> str:
    return _parse_yyyymmdd(str(value)[:10]).date().isoformat()


def _compact_date(value: str) -> str:
    return _parse_yyyymmdd(str(value)[:10]).strftime("%Y%m%d")


def _date_range(start_date: str, end_date: str) -> list[str]:
    start = _parse_yyyymmdd(start_date).date()
    end = _parse_yyyymmdd(end_date).date()
    if start > end:
        return []
    values = []
    current = start
    while current <= end:
        values.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return values


def _date_range_args(args: argparse.Namespace, *, default_days: int) -> tuple[str, str]:
    end_value = args.end_date or args.publish_date or datetime.now().strftime("%Y%m%d")
    end_date = _compact_date(end_value)
    if args.start_date:
        start_date = _compact_date(args.start_date)
    else:
        start_date = (_parse_yyyymmdd(end_date) - timedelta(days=max(default_days - 1, 0))).strftime("%Y%m%d")
    return start_date, end_date


def _fields(raw: str | None, default_fields: tuple[str, ...]) -> list[str]:
    if raw:
        return [field.strip() for field in raw.split(",") if field.strip()]
    return list(default_fields)


def _parse_params(raw_items: list[str]) -> dict[str, object]:
    params: dict[str, object] = {}
    for raw in raw_items:
        if ":=" in raw:
            key, value = raw.split(":=", 1)
            params[key] = json.loads(value)
        elif "=" in raw:
            key, value = raw.split("=", 1)
            params[key] = value
        else:
            raise AShareResearchError(f"invalid param {raw!r}; expected key=value or key:=JSON")
        if not key:
            raise AShareResearchError(f"invalid param {raw!r}; empty key")
    return params


def _parse_headers(raw_items: list[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for raw in raw_items:
        if "=" not in raw:
            raise AShareResearchError(f"invalid header {raw!r}; expected key=value")
        key, value = raw.split("=", 1)
        if not key:
            raise AShareResearchError(f"invalid header {raw!r}; empty key")
        headers[key] = value
    return headers


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _add_evidence_query_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--topic")
    parser.add_argument("--industry")
    parser.add_argument("--company")
    parser.add_argument("--product")
    parser.add_argument("--period")


def _evidence_query(args: argparse.Namespace) -> dict[str, str | None]:
    return {
        "topic": args.topic,
        "industry": args.industry,
        "company": args.company,
        "product": args.product,
        "period": args.period,
    }


def _load_evidence_payload(path: Path) -> dict[str, object] | list[dict[str, object]]:
    return _load_structured_payload(path, label="evidence")


def _load_structured_payload(path: Path, *, label: str) -> dict[str, object] | list[dict[str, object]]:
    text = path.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if not stripped:
        raise AShareResearchError(f"empty {label} file: {path}")
    if stripped.startswith("[") or stripped.startswith("{"):
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
            return payload
        raise AShareResearchError(f"{label} JSON must be an object or list of objects")
    rows: list[dict[str, object]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise AShareResearchError(f"{label} JSONL line {line_number} must be an object")
        rows.append(payload)
    return rows


def _load_question(args: argparse.Namespace) -> str:
    if args.question and args.question_file:
        raise AShareResearchError("--question and --question-file are mutually exclusive")
    if args.question_file:
        question = Path(args.question_file).read_text(encoding="utf-8")
    else:
        question = args.question or ""
    if not question.strip():
        raise AShareResearchError("runs record requires --question or --question-file")
    return question


def _load_json_file(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AShareResearchError(f"JSON file must contain an object: {path}")
    return payload

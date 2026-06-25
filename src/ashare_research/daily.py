from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .features import FeatureRegistry, FeatureStore
from .marts.reader import MartReader
from .schemas import AShareResearchError


DEFAULT_WINDOWS = (5, 20, 60)


@dataclass(frozen=True)
class DailyTask:
    dataset: str
    group: str
    partition_kind: str
    required: bool = False


REQUIRED_MARKET_DATASETS = {
    "trade_cal",
    "stock_basic",
    "daily",
    "daily_basic",
    "index_daily",
    "index_dailybasic",
    "sw_daily",
    "ci_daily",
    "dc_index",
    "limit_list_d",
    "limit_list_ths",
}


DAILY_TASKS = (
    DailyTask("trade_cal", "calendar", "exchange", required=True),
    DailyTask("stock_basic", "identity", "snapshot_date", required=True),
    DailyTask("daily", "stock_daily", "trade_date", required=True),
    DailyTask("daily_basic", "stock_daily", "trade_date", required=True),
    DailyTask("adj_factor", "stock_daily", "trade_date"),
    DailyTask("stk_limit", "stock_daily", "trade_date"),
    DailyTask("index_daily", "index", "trade_date", required=True),
    DailyTask("index_dailybasic", "index", "trade_date", required=True),
    DailyTask("sw_daily", "industry", "trade_date", required=True),
    DailyTask("ci_daily", "industry", "trade_date", required=True),
    DailyTask("dc_index", "membership", "trade_date", required=True),
    DailyTask("limit_list_d", "short_term", "trade_date", required=True),
    DailyTask("limit_list_ths", "short_term", "trade_date", required=True),
    DailyTask("moneyflow_dc", "moneyflow", "trade_date"),
    DailyTask("top_list", "short_term", "trade_date"),
    DailyTask("a_stock_notice", "events", "publish_date"),
    DailyTask("earnings_forecast", "events", "publish_date"),
    DailyTask("stock_hsgt", "northbound", "trade_date"),
    DailyTask("index_classify", "membership", "snapshot_date"),
    DailyTask("index_member_all", "membership", "snapshot_date"),
    DailyTask("ci_index_member", "membership", "snapshot_date"),
    DailyTask("ths_index", "membership", "snapshot_date"),
    DailyTask("ths_member", "membership", "snapshot_date"),
    DailyTask("index_weight", "membership", "snapshot_date"),
    DailyTask("dc_member", "membership", "trade_date"),
    DailyTask("tdx_index", "membership", "trade_date"),
    DailyTask("tdx_member", "membership", "trade_date"),
    DailyTask("kpl_concept_cons", "membership", "trade_date"),
    DailyTask("moneyflow", "moneyflow", "trade_date"),
    DailyTask("moneyflow_ths", "moneyflow", "trade_date"),
    DailyTask("moneyflow_ind_ths", "moneyflow", "trade_date"),
    DailyTask("moneyflow_ind_dc", "moneyflow", "trade_date"),
    DailyTask("moneyflow_cnt_ths", "moneyflow", "trade_date"),
    DailyTask("moneyflow_hsgt", "northbound", "trade_date"),
    DailyTask("hsgt_top10", "northbound", "trade_date"),
    DailyTask("margin_detail", "leverage", "trade_date"),
    DailyTask("limit_step", "short_term", "trade_date"),
    DailyTask("limit_cpt_list", "short_term", "trade_date"),
    DailyTask("kpl_list", "short_term", "trade_date"),
    DailyTask("ths_hot", "hot_rank", "trade_date"),
    DailyTask("dc_hot", "hot_rank", "trade_date"),
)


SKIPPED_DEFAULT_SCOPES = (
    {
        "scope": "financials",
        "reason": "stock-pool and report-period task; run explicitly for candidates or covered pools",
    },
    {
        "scope": "chips",
        "reason": "stock-pool task; run explicitly for candidates or covered pools",
    },
    {
        "scope": "external_evidence",
        "reason": "question-specific; fetch authoritative sources during research when local evidence is insufficient",
    },
    {
        "scope": "knowledge",
        "reason": "question-specific; organize industry-chain and company relations during research, then propose reusable knowledge",
    },
)


def daily_plan() -> list[DailyTask]:
    return list(DAILY_TASKS)


def parse_windows(raw: str | None) -> list[int]:
    if not raw:
        return list(DEFAULT_WINDOWS)
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


def resolve_as_of(reader: MartReader, as_of: str | None = None, *, now: datetime | None = None) -> str:
    if as_of:
        return compact_date(as_of)
    current = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    today = current.strftime("%Y%m%d")
    fallback = _previous_business_day(current).strftime("%Y%m%d") if current.hour < 15 else today
    try:
        trade_cal = reader.read_partition("trade_cal", {"exchange": "SSE"}, columns=["cal_date", "is_open"])
    except Exception:
        return fallback
    if trade_cal.empty or "cal_date" not in trade_cal.columns or "is_open" not in trade_cal.columns:
        return fallback
    frame = trade_cal.copy()
    frame["cal_date"] = frame["cal_date"].astype(str)
    open_dates = sorted(
        date
        for date, is_open in zip(frame["cal_date"], frame["is_open"], strict=False)
        if str(is_open) == "1" and date <= today
    )
    if not open_dates:
        return fallback
    if current.hour >= 15 and today in open_dates:
        return today
    prior = [date for date in open_dates if date < today]
    return prior[-1] if prior else open_dates[-1]


def event_days_for_daily(override: int | None = None) -> int:
    if override is not None:
        if override <= 0:
            raise AShareResearchError("--event-days must be positive")
        return override
    return 7


def task_build_params(task: DailyTask, *, as_of: str, event_days: int) -> dict[str, Any]:
    if task.partition_kind == "trade_date":
        return {"trade_date": as_of}
    if task.partition_kind == "snapshot_date":
        return {"snapshot_date": as_of}
    if task.partition_kind == "exchange":
        return {"exchange": "SSE", "start_date": f"{as_of[:4]}0101", "end_date": as_of}
    if task.partition_kind == "publish_date":
        start = (_parse_date(as_of) - timedelta(days=max(event_days - 1, 0))).strftime("%Y%m%d")
        return {"start_date": start, "end_date": as_of}
    raise AShareResearchError(f"{task.dataset}: unsupported partition kind {task.partition_kind!r}")


def report_path(data_dir: Path | str, *, as_of: str) -> Path:
    return Path(data_dir) / "reports" / "daily" / f"as_of={compact_date(as_of)}" / "report.json"


def latest_report_path(data_dir: Path | str) -> Path:
    return Path(data_dir) / "reports" / "daily" / "latest.json"


def write_report(data_dir: Path | str, payload: dict[str, Any]) -> Path:
    as_of = compact_date(str(payload["as_of"]))
    path = report_path(data_dir, as_of=as_of)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["report_path"] = str(path)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    path.write_text(text, encoding="utf-8")
    latest = latest_report_path(data_dir)
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(text, encoding="utf-8")
    return path


def read_report(data_dir: Path | str, *, as_of: str | None = None) -> dict[str, Any] | None:
    if as_of:
        path = report_path(data_dir, as_of=as_of)
    else:
        path = latest_report_path(data_dir)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def build_status(
    reader: MartReader,
    *,
    as_of: str,
    windows: list[int] | None = None,
) -> dict[str, Any]:
    windows = windows or list(DEFAULT_WINDOWS)
    tasks = daily_plan()
    dataset_checks = []
    for task in tasks:
        check = reader.check_dataset(
            task.dataset,
            as_of=as_of,
            allow_latest_snapshot=task.partition_kind == "snapshot_date",
        ).to_dict()
        check["group"] = task.group
        check["required"] = task.required
        dataset_checks.append(check)

    feature_store = FeatureStore(reader.data_dir)
    feature_checks = []
    for spec in FeatureRegistry.builtin().list():
        for window in windows:
            try:
                meta = feature_store.load_meta(spec.name, as_of=as_of, window=window)
                quality = dict(meta.quality or {})
                if not quality or "status" not in quality:
                    quality = feature_store.quality_for_partition(spec, as_of=as_of, window=window)
                status = "ready" if quality.get("status") == "ok" else str(quality.get("status", "degraded"))
                feature_checks.append(
                    {
                        "feature": spec.name,
                        "window": window,
                        "status": status,
                        "rows": meta.rows,
                        "quality": quality,
                        "path": str(feature_store.partition_path(spec.name, as_of=as_of, window=window)),
                    }
                )
            except Exception as error:
                feature_checks.append(
                    {
                        "feature": spec.name,
                        "window": window,
                        "status": "missing",
                        "rows": None,
                        "path": None,
                        "message": str(error),
                    }
                )

    blocking_statuses = {"missing", "schema_mismatch", "empty", "unregistered", "read_error"}
    blocking = [
        item
        for item in dataset_checks
        if item.get("required") and item.get("status") in blocking_statuses
    ]
    blocking.extend(item for item in feature_checks if item.get("status") in blocking_statuses)

    degraded = [
        item
        for item in [*dataset_checks, *feature_checks]
        if item.get("status") == "degraded"
    ]
    warnings = [
        item
        for item in dataset_checks
        if not item.get("required") and item.get("status") not in {"ready", "degraded"}
    ]

    if blocking:
        status = "blocked"
    elif degraded:
        status = "degraded"
    else:
        status = "ready"

    if blocking:
        maintenance_status = "blocked"
    elif degraded:
        maintenance_status = "degraded"
    elif warnings:
        maintenance_status = "warning"
    else:
        maintenance_status = "ready"

    return {
        "schema": "ashare.daily_status.v1",
        "as_of": as_of,
        "status": status,
        "maintenance_status": maintenance_status,
        "coverage": {
            "datasets_ready": sum(1 for item in dataset_checks if item["status"] == "ready"),
            "datasets_total": len(dataset_checks),
            "features_ready": sum(1 for item in feature_checks if item["status"] == "ready"),
            "features_total": len(feature_checks),
            "degraded": len(degraded),
            "warnings": len(warnings),
        },
        "datasets": dataset_checks,
        "features": feature_checks,
        "blocking": blocking,
        "degraded": degraded,
        "warnings": warnings,
        "skipped": list(SKIPPED_DEFAULT_SCOPES),
    }


def compact_date(value: str) -> str:
    text = str(value).strip()
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return text.replace("-", "")
    digits = "".join(char for char in text if char.isdigit())
    if len(digits) < 8:
        raise AShareResearchError(f"invalid date {value!r}; expected YYYYMMDD or YYYY-MM-DD")
    return digits[:8]


def iso_date(value: str) -> str:
    text = compact_date(value)
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}"


def _parse_date(value: str) -> datetime:
    return datetime.strptime(compact_date(value), "%Y%m%d")


def _previous_business_day(current: datetime) -> datetime:
    candidate = current - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate

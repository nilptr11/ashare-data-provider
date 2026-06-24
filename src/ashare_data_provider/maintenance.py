from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

from .client import TushareError
from .config import TushareConfig
from .defaults import default_params
from .local_store import LocalDataStore, default_data_dir, normalize_date_value
from .provider import AShareProvider
from .recipes import RecipeError, default_fields
from .registry import InterfaceEntry


ACCESS_SCHEMA = "ashare.access_catalog.v1"
MAINTENANCE_REPORT_SCHEMA = "ashare.maintenance_report.v1"
MAINTENANCE_STATE_SCHEMA = "ashare.maintenance_state.v1"
MART_META_SCHEMA = "ashare.mart_partition.v1"

PROFILE_ORDER = {"basic": 0, "standard": 1, "full": 2}
ACCESS_ALLOWED = "allowed"
ACCESS_DENIED = "denied"
ACCESS_UNVERIFIED = "unverified"
ACCESS_STATUSES = {ACCESS_ALLOWED, ACCESS_DENIED, ACCESS_UNVERIFIED}
EMPTY_ALLOW = "allow_empty"
EMPTY_RETRY_AFTER_LAG = "retry_empty_after_lag"
EMPTY_FORBID = "forbid_empty"
EMPTY_POLICIES = {EMPTY_ALLOW, EMPTY_RETRY_AFTER_LAG, EMPTY_FORBID}
QUALITY_OK = "ok"
QUALITY_PENDING_EMPTY = "pending_empty"
QUALITY_SUSPICIOUS_EMPTY = "suspicious_empty"
QUALITY_INVALID_EMPTY = "invalid_empty"
QUALITY_MISSING = "missing"
QUALITY_SCHEMA_ISSUE = "schema_issue"
QUALITY_ANOMALOUS_ROWS = "anomalous_rows"
QUALITY_DUPLICATE_KEY = "duplicate_key"
QUALITY_STALE_DATA = "stale_data"
QUALITY_PAGINATION_LIMIT = "pagination_limit_reached"
QUALITY_RETRYABLE = {
    QUALITY_SUSPICIOUS_EMPTY,
    QUALITY_INVALID_EMPTY,
    QUALITY_SCHEMA_ISSUE,
    QUALITY_ANOMALOUS_ROWS,
    QUALITY_DUPLICATE_KEY,
    QUALITY_STALE_DATA,
    QUALITY_PAGINATION_LIMIT,
}


class MaintenanceError(RuntimeError):
    pass


@dataclass(frozen=True)
class RequestVariant:
    label: str
    params: dict[str, Any]
    fields: str | None = None


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    group: str
    api_name: str
    title: str
    min_profile: str
    maintenance_kind: str
    date_param: str | None = None
    variants: tuple[RequestVariant, ...] = ()
    fields: str | None = None
    description: str = ""
    requires_stock_pool: bool = False
    source_kind: str = "tushare"
    empty_policy: str = EMPTY_ALLOW
    empty_lag_days: int = 2
    required_columns: tuple[str, ...] = ()
    unique_key: tuple[str, ...] = ()
    min_rows: int | None = None
    page_limit: int | None = None
    max_pages: int = 20
    range_lookback_days: int = 370
    driver_dataset: str | None = None
    driver_code_param: str = "ts_code"
    driver_code_columns: tuple[str, ...] = ("ts_code", "index_code", "code")
    driver_name_columns: tuple[str, ...] = ("name", "index_name", "industry_name", "concept_name")

    @property
    def profile_rank(self) -> int:
        return PROFILE_ORDER[self.min_profile]

    def __post_init__(self) -> None:
        if self.empty_policy not in EMPTY_POLICIES:
            raise ValueError(f"未知 empty_policy：{self.empty_policy}")


@dataclass(frozen=True)
class AccessDecision:
    api_name: str
    access: str
    source: str
    reason: str = ""
    eligibility: str = ""
    required_points: int | None = None
    doc_id: str | None = None
    checked_at: str | None = None


@dataclass(frozen=True)
class PlanDataset:
    spec: DatasetSpec
    access: AccessDecision


@dataclass(frozen=True)
class MaintenancePlan:
    profile: str
    generated_at: str
    datasets: tuple[PlanDataset, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "ashare.maintenance_plan.v1",
            "profile": self.profile,
            "generated_at": self.generated_at,
            "datasets": [
                {
                    "name": item.spec.name,
                    "group": item.spec.group,
                    "api_name": item.spec.api_name,
                    "title": item.spec.title,
                    "maintenance_kind": item.spec.maintenance_kind,
                    "date_param": item.spec.date_param,
                    "min_profile": item.spec.min_profile,
                    "requires_stock_pool": item.spec.requires_stock_pool,
                    "source_kind": item.spec.source_kind,
                    "empty_policy": item.spec.empty_policy,
                    "empty_lag_days": item.spec.empty_lag_days,
                    "required_columns": list(item.spec.required_columns),
                    "unique_key": list(item.spec.unique_key),
                    "min_rows": item.spec.min_rows,
                    "page_limit": item.spec.page_limit,
                    "max_pages": item.spec.max_pages,
                    "range_lookback_days": item.spec.range_lookback_days,
                    "driver_dataset": item.spec.driver_dataset,
                    "driver_code_param": item.spec.driver_code_param,
                    "access": asdict(item.access),
                    "variants": [
                        {
                            "label": variant.label,
                            "params": variant.params,
                            "fields": variant.fields or item.spec.fields,
                        }
                        for variant in item.spec.variants
                    ],
                }
                for item in self.datasets
            ],
        }


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _format_yyyymmdd(value: str | date | datetime) -> str:
    return str(normalize_date_value(value))


def _parse_yyyymmdd(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(_format_yyyymmdd(value), "%Y%m%d").date()


def _date_range(start_date: str | date | datetime, end_date: str | date | datetime) -> list[str]:
    start = _parse_yyyymmdd(start_date)
    end = _parse_yyyymmdd(end_date)
    if start > end:
        return []
    days: list[str] = []
    current = start
    while current <= end:
        days.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return days


def _safe_path_value(value: Any) -> str:
    return quote(str(value), safe="-_.~")


def _record_count(value: Any) -> int:
    if value is None:
        return 0
    if hasattr(value, "__len__"):
        try:
            return int(len(value))
        except TypeError:
            return 0
    return 0


def _to_frame(value: Any) -> Any:
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - pandas is a dependency
        raise MaintenanceError("maintenance mart 需要 pandas") from exc

    if isinstance(value, pd.DataFrame):
        return value
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return pd.DataFrame(value)
    raise MaintenanceError("mart 目前只支持 pandas DataFrame 或 list[dict]")


def _concat_values(values: list[Any]) -> Any:
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - pandas is a dependency
        raise MaintenanceError("拼接维护数据需要 pandas") from exc

    frames = [_to_frame(value) for value in values]
    non_empty_frames = [frame for frame in frames if len(frame) > 0]
    if non_empty_frames:
        return pd.concat(non_empty_frames, ignore_index=True)
    schema_frames = [frame for frame in frames if len(frame.columns) > 0]
    if not schema_frames:
        return pd.DataFrame()
    return pd.concat(schema_frames, ignore_index=True)


def _append_column(value: Any, column: str, column_value: Any) -> Any:
    frame = _to_frame(value)
    if len(frame) > 0:
        frame = frame.copy()
        frame[column] = column_value
    return frame


def _drop_exact_duplicates(value: Any) -> Any:
    frame = _to_frame(value)
    if len(frame) == 0:
        return frame
    return frame.drop_duplicates(keep="last").reset_index(drop=True)


def _stock_code_to_ts_code(code: Any) -> str:
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


def _iso_date_from_yyyymmdd(value: str) -> str:
    text = _format_yyyymmdd(value)
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}"


def _iso_date_range(start_date: str | date | datetime, end_date: str | date | datetime) -> list[str]:
    return [_iso_date_from_yyyymmdd(value) for value in _date_range(start_date, end_date)]


def _financial_period_value(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "nat", "none"}:
        return ""
    digits = "".join(char for char in text if char.isdigit())
    if len(digits) < 8:
        return ""
    return _format_yyyymmdd(digits[:8])


def _parse_partition_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if len(text) >= 10 and text[4] == "-" and text[7] == "-":
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        return _parse_yyyymmdd(text[:8])
    except ValueError:
        return None


def _quality_as_of_text(end_date: str) -> str:
    today = datetime.now().astimezone().date()
    end = _parse_yyyymmdd(end_date)
    return max(today, end).strftime("%Y%m%d")


def _empty_quality(
    spec: DatasetSpec,
    rows: int,
    columns: list[str],
    partition_value: Any,
    as_of: str | date | datetime | None = None,
) -> dict[str, Any]:
    if rows > 0:
        return {
            "status": QUALITY_OK,
            "empty_policy": spec.empty_policy,
            "rows": rows,
            "columns": len(columns),
            "reason": "",
        }
    if spec.empty_policy == EMPTY_ALLOW:
        return {
            "status": QUALITY_OK,
            "empty_policy": spec.empty_policy,
            "rows": rows,
            "columns": len(columns),
            "reason": "empty_allowed",
        }

    partition_date = _parse_partition_date(partition_value)
    as_of_date = _parse_yyyymmdd(as_of or datetime.now().astimezone())
    age_days = (as_of_date - partition_date).days if partition_date is not None else None
    is_pending = (
        spec.empty_policy == EMPTY_RETRY_AFTER_LAG
        and age_days is not None
        and age_days <= spec.empty_lag_days
    )
    if is_pending:
        return {
            "status": QUALITY_PENDING_EMPTY,
            "empty_policy": spec.empty_policy,
            "rows": rows,
            "columns": len(columns),
            "age_days": age_days,
            "empty_lag_days": spec.empty_lag_days,
            "reason": "empty_within_lag",
        }

    status = QUALITY_INVALID_EMPTY if not columns else QUALITY_SUSPICIOUS_EMPTY
    reason = "empty_without_schema" if status == QUALITY_INVALID_EMPTY else "empty_after_lag_or_forbidden"
    return {
        "status": status,
        "empty_policy": spec.empty_policy,
        "rows": rows,
        "columns": len(columns),
        "age_days": age_days,
        "empty_lag_days": spec.empty_lag_days,
        "reason": reason,
    }


def _normalize_partition_comparable(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10].replace("-", "")
    digits = "".join(char for char in text if char.isdigit())
    if len(digits) >= 8:
        return digits[:8]
    return text


def _quality_from_frame(
    spec: DatasetSpec,
    value: Any,
    partition_value: Any,
    as_of: str | date | datetime | None = None,
) -> dict[str, Any]:
    frame = _to_frame(value)
    rows = int(len(frame))
    columns = [str(column) for column in frame.columns]
    if rows == 0:
        return _empty_quality(spec, rows, columns, partition_value, as_of=as_of)

    issues: list[dict[str, Any]] = []
    missing_columns = [column for column in spec.required_columns if column not in columns]
    if missing_columns:
        issues.append(
            {
                "type": QUALITY_SCHEMA_ISSUE,
                "missing_columns": missing_columns,
                "message": "required_columns_missing",
            }
        )
    if spec.min_rows is not None and rows < spec.min_rows:
        issues.append(
            {
                "type": QUALITY_ANOMALOUS_ROWS,
                "rows": rows,
                "min_rows": spec.min_rows,
                "message": "row_count_below_threshold",
            }
        )
    if spec.page_limit and rows >= spec.page_limit * max(spec.max_pages, 1) * len(_request_variants(spec)):
        issues.append(
            {
                "type": QUALITY_PAGINATION_LIMIT,
                "rows": rows,
                "page_limit": spec.page_limit,
                "max_pages": spec.max_pages,
                "variants": len(_request_variants(spec)),
                "message": "pagination_max_pages_reached",
            }
        )
    if spec.unique_key:
        key_columns = [column for column in spec.unique_key if column in columns]
        if len(key_columns) == len(spec.unique_key):
            duplicate_rows = int(frame.duplicated(subset=key_columns, keep=False).sum())
            if duplicate_rows:
                sample = frame.loc[frame.duplicated(subset=key_columns, keep=False), key_columns].head(10).to_dict("records")
                issues.append(
                    {
                        "type": QUALITY_DUPLICATE_KEY,
                        "key_columns": key_columns,
                        "duplicate_rows": duplicate_rows,
                        "sample": sample,
                        "message": "duplicate_primary_key",
                    }
                )
        else:
            issues.append(
                {
                    "type": QUALITY_SCHEMA_ISSUE,
                    "missing_columns": [column for column in spec.unique_key if column not in columns],
                    "message": "unique_key_columns_missing",
                }
            )
    if spec.date_param and spec.date_param in columns and spec.maintenance_kind in {"trade_date", "member_by_index_trade_date"}:
        expected = _normalize_partition_comparable(partition_value)
        values = frame[spec.date_param].dropna().map(_normalize_partition_comparable)
        stale_values = sorted({value for value in values.tolist() if value and value != expected})
        if stale_values:
            issues.append(
                {
                    "type": QUALITY_STALE_DATA,
                    "date_column": spec.date_param,
                    "expected": expected,
                    "actual_sample": stale_values[:10],
                    "message": "partition_date_mismatch",
                }
            )

    if not issues:
        return {
            "status": QUALITY_OK,
            "empty_policy": spec.empty_policy,
            "rows": rows,
            "columns": len(columns),
            "reason": "",
            "issues": [],
        }
    status = str(issues[0]["type"])
    return {
        "status": status,
        "empty_policy": spec.empty_policy,
        "rows": rows,
        "columns": len(columns),
        "reason": "quality_rules_failed",
        "issues": issues,
    }


def _is_complete_quality(status: str) -> bool:
    return status in {QUALITY_OK, QUALITY_PENDING_EMPTY}


def _fields_for(api_name: str) -> str | None:
    try:
        return default_fields(api_name)
    except RecipeError:
        return None


def _variant(label: str = "default", params: dict[str, Any] | None = None, fields: str | None = None) -> RequestVariant:
    return RequestVariant(label=label, params=dict(params or {}), fields=fields)


def default_dataset_specs() -> tuple[DatasetSpec, ...]:
    """Desired daily-maintenance catalog before permission filtering."""

    stock_basic_fields = _fields_for("stock_basic")
    trade_cal_fields = _fields_for("trade_cal")
    return (
        DatasetSpec(
            name="trade_cal",
            group="calendar",
            api_name="trade_cal",
            title="交易日历",
            min_profile="basic",
            maintenance_kind="calendar",
            fields=trade_cal_fields,
            variants=(_variant(params={"exchange": "SSE"}),),
            empty_policy=EMPTY_FORBID,
            required_columns=("cal_date", "is_open"),
            unique_key=("cal_date",),
        ),
        DatasetSpec(
            name="stock_basic",
            group="identity",
            api_name="stock_basic",
            title="股票基础",
            min_profile="basic",
            maintenance_kind="snapshot",
            fields=stock_basic_fields,
            variants=(_variant(params={"exchange": "", "list_status": "L"}),),
            empty_policy=EMPTY_FORBID,
            required_columns=("ts_code", "name", "list_status"),
            unique_key=("ts_code",),
            min_rows=3000,
        ),
        DatasetSpec(
            name="index_classify",
            group="membership",
            api_name="index_classify",
            title="申万行业分类",
            min_profile="standard",
            maintenance_kind="snapshot",
            variants=(
                _variant("l1", {"level": "L1", "src": "SW2021"}),
                _variant("l2", {"level": "L2", "src": "SW2021"}),
                _variant("l3", {"level": "L3", "src": "SW2021"}),
            ),
            empty_policy=EMPTY_FORBID,
        ),
        DatasetSpec(
            name="index_member_all",
            group="membership",
            api_name="index_member_all",
            title="申万行业成分",
            min_profile="standard",
            maintenance_kind="snapshot",
            empty_policy=EMPTY_FORBID,
            page_limit=3000,
            max_pages=3,
        ),
        DatasetSpec(
            name="ci_index_member",
            group="membership",
            api_name="ci_index_member",
            title="中信行业成分",
            min_profile="full",
            maintenance_kind="snapshot",
            empty_policy=EMPTY_FORBID,
            page_limit=5000,
            max_pages=2,
        ),
        DatasetSpec(
            name="ths_index",
            group="membership",
            api_name="ths_index",
            title="同花顺行业概念板块",
            min_profile="full",
            maintenance_kind="snapshot",
            empty_policy=EMPTY_FORBID,
        ),
        DatasetSpec(
            name="ths_member",
            group="membership",
            api_name="ths_member",
            title="同花顺行业概念成分",
            min_profile="full",
            maintenance_kind="member_by_index_snapshot",
            driver_dataset="ths_index",
            empty_policy=EMPTY_FORBID,
            page_limit=5000,
            max_pages=60,
        ),
        DatasetSpec(
            name="dc_index",
            group="membership",
            api_name="dc_index",
            title="东方财富概念板块",
            min_profile="full",
            maintenance_kind="trade_date",
            date_param="trade_date",
            variants=(
                _variant("industry", {"idx_type": "行业板块"}),
                _variant("concept", {"idx_type": "概念板块"}),
                _variant("region", {"idx_type": "地域板块"}),
            ),
            empty_policy=EMPTY_FORBID,
        ),
        DatasetSpec(
            name="dc_member",
            group="membership",
            api_name="dc_member",
            title="东方财富概念成分",
            min_profile="full",
            maintenance_kind="member_by_index_trade_date",
            date_param="trade_date",
            driver_dataset="dc_index",
            empty_policy=EMPTY_RETRY_AFTER_LAG,
            page_limit=5000,
            max_pages=20,
        ),
        DatasetSpec(
            name="tdx_index",
            group="membership",
            api_name="tdx_index",
            title="通达信板块信息",
            min_profile="full",
            maintenance_kind="trade_date",
            date_param="trade_date",
            empty_policy=EMPTY_FORBID,
            page_limit=1000,
            max_pages=5,
        ),
        DatasetSpec(
            name="tdx_member",
            group="membership",
            api_name="tdx_member",
            title="通达信板块成分",
            min_profile="full",
            maintenance_kind="member_by_index_trade_date",
            date_param="trade_date",
            driver_dataset="tdx_index",
            empty_policy=EMPTY_RETRY_AFTER_LAG,
            page_limit=3000,
            max_pages=35,
        ),
        DatasetSpec(
            name="kpl_concept_cons",
            group="membership",
            api_name="kpl_concept_cons",
            title="开盘啦题材成分",
            min_profile="full",
            maintenance_kind="trade_date",
            date_param="trade_date",
            empty_policy=EMPTY_RETRY_AFTER_LAG,
            page_limit=5000,
            max_pages=10,
        ),
        DatasetSpec(
            name="index_weight",
            group="membership",
            api_name="index_weight",
            title="核心指数权重",
            min_profile="standard",
            maintenance_kind="snapshot",
            variants=(
                _variant("sz50", {"index_code": "000016.SH"}),
                _variant("hs300", {"index_code": "000300.SH"}),
                _variant("zz500", {"index_code": "000905.SH"}),
                _variant("zz1000", {"index_code": "000852.SH"}),
            ),
            empty_policy=EMPTY_FORBID,
        ),
        DatasetSpec(
            name="daily",
            group="stock_daily",
            api_name="daily",
            title="日线行情",
            min_profile="basic",
            maintenance_kind="trade_date",
            date_param="trade_date",
            fields=_fields_for("daily"),
            empty_policy=EMPTY_FORBID,
            required_columns=("ts_code", "trade_date", "open", "high", "low", "close"),
            unique_key=("ts_code", "trade_date"),
            min_rows=3000,
        ),
        DatasetSpec(
            name="daily_basic",
            group="stock_daily",
            api_name="daily_basic",
            title="每日指标",
            min_profile="basic",
            maintenance_kind="trade_date",
            date_param="trade_date",
            fields=_fields_for("daily_basic"),
            empty_policy=EMPTY_FORBID,
            required_columns=("ts_code", "trade_date", "turnover_rate"),
            unique_key=("ts_code", "trade_date"),
            min_rows=3000,
        ),
        DatasetSpec(
            name="adj_factor",
            group="stock_daily",
            api_name="adj_factor",
            title="复权因子",
            min_profile="basic",
            maintenance_kind="trade_date",
            date_param="trade_date",
            fields=_fields_for("adj_factor"),
            empty_policy=EMPTY_FORBID,
            required_columns=("ts_code", "trade_date", "adj_factor"),
            unique_key=("ts_code", "trade_date"),
            min_rows=3000,
        ),
        DatasetSpec(
            name="stk_limit",
            group="stock_daily",
            api_name="stk_limit",
            title="涨跌停价格",
            min_profile="basic",
            maintenance_kind="trade_date",
            date_param="trade_date",
            fields=_fields_for("stk_limit"),
            empty_policy=EMPTY_FORBID,
            required_columns=("ts_code", "trade_date", "up_limit", "down_limit"),
            unique_key=("ts_code", "trade_date"),
            min_rows=3000,
        ),
        DatasetSpec(
            name="index_daily",
            group="index",
            api_name="index_daily",
            title="指数行情",
            min_profile="basic",
            maintenance_kind="trade_date",
            date_param="trade_date",
            variants=(
                _variant("sh", {"ts_code": "000001.SH"}),
                _variant("hs300", {"ts_code": "000300.SH"}),
                _variant("zz500", {"ts_code": "000905.SH"}),
                _variant("zz1000", {"ts_code": "000852.SH"}),
                _variant("sz", {"ts_code": "399001.SZ"}),
                _variant("cyb", {"ts_code": "399006.SZ"}),
            ),
            empty_policy=EMPTY_FORBID,
            required_columns=("ts_code", "trade_date"),
            unique_key=("ts_code", "trade_date"),
            min_rows=4,
        ),
        DatasetSpec(
            name="index_dailybasic",
            group="index",
            api_name="index_dailybasic",
            title="指数估值",
            min_profile="basic",
            maintenance_kind="trade_date",
            date_param="trade_date",
            empty_policy=EMPTY_FORBID,
            required_columns=("ts_code", "trade_date"),
            unique_key=("ts_code", "trade_date"),
        ),
        DatasetSpec(
            name="sw_daily",
            group="industry",
            api_name="sw_daily",
            title="申万行业行情",
            min_profile="basic",
            maintenance_kind="trade_date",
            date_param="trade_date",
            empty_policy=EMPTY_FORBID,
            required_columns=("ts_code", "trade_date"),
            unique_key=("ts_code", "trade_date"),
            min_rows=20,
        ),
        DatasetSpec(
            name="ci_daily",
            group="industry",
            api_name="ci_daily",
            title="中信行业行情",
            min_profile="basic",
            maintenance_kind="trade_date",
            date_param="trade_date",
            empty_policy=EMPTY_FORBID,
            required_columns=("ts_code", "trade_date"),
            unique_key=("ts_code", "trade_date"),
            min_rows=20,
        ),
        DatasetSpec(
            name="moneyflow",
            group="moneyflow",
            api_name="moneyflow",
            title="个股资金流",
            min_profile="standard",
            maintenance_kind="trade_date",
            date_param="trade_date",
            empty_policy=EMPTY_FORBID,
            required_columns=("ts_code", "trade_date"),
            unique_key=("ts_code", "trade_date"),
            min_rows=1000,
            page_limit=5000,
            max_pages=3,
        ),
        DatasetSpec(
            name="moneyflow_dc",
            group="moneyflow",
            api_name="moneyflow_dc",
            title="个股资金流 DC",
            min_profile="standard",
            maintenance_kind="trade_date",
            date_param="trade_date",
            empty_policy=EMPTY_RETRY_AFTER_LAG,
            required_columns=("ts_code", "trade_date"),
            unique_key=("ts_code", "trade_date"),
            min_rows=1000,
            page_limit=5000,
            max_pages=3,
        ),
        DatasetSpec(
            name="moneyflow_ths",
            group="moneyflow",
            api_name="moneyflow_ths",
            title="个股资金流 THS",
            min_profile="standard",
            maintenance_kind="trade_date",
            date_param="trade_date",
            empty_policy=EMPTY_FORBID,
            required_columns=("ts_code", "trade_date"),
            unique_key=("ts_code", "trade_date"),
            min_rows=1000,
        ),
        DatasetSpec(
            name="moneyflow_ind_ths",
            group="moneyflow",
            api_name="moneyflow_ind_ths",
            title="行业资金流 THS",
            min_profile="standard",
            maintenance_kind="trade_date",
            date_param="trade_date",
            empty_policy=EMPTY_FORBID,
            required_columns=("trade_date",),
        ),
        DatasetSpec(
            name="moneyflow_ind_dc",
            group="moneyflow",
            api_name="moneyflow_ind_dc",
            title="行业资金流 DC",
            min_profile="standard",
            maintenance_kind="trade_date",
            date_param="trade_date",
            empty_policy=EMPTY_FORBID,
            required_columns=("trade_date",),
        ),
        DatasetSpec(
            name="moneyflow_cnt_ths",
            group="moneyflow",
            api_name="moneyflow_cnt_ths",
            title="概念资金流 THS",
            min_profile="standard",
            maintenance_kind="trade_date",
            date_param="trade_date",
            empty_policy=EMPTY_FORBID,
            required_columns=("trade_date",),
        ),
        DatasetSpec(
            name="margin_detail",
            group="leverage",
            api_name="margin_detail",
            title="融资融券明细",
            min_profile="standard",
            maintenance_kind="trade_date",
            date_param="trade_date",
            empty_policy=EMPTY_RETRY_AFTER_LAG,
            required_columns=("ts_code", "trade_date"),
            unique_key=("ts_code", "trade_date"),
        ),
        DatasetSpec(
            name="top_list",
            group="short_term",
            api_name="top_list",
            title="龙虎榜",
            min_profile="standard",
            maintenance_kind="trade_date",
            date_param="trade_date",
            empty_policy=EMPTY_RETRY_AFTER_LAG,
            required_columns=("ts_code", "trade_date"),
        ),
        DatasetSpec(
            name="a_stock_notice",
            group="events",
            api_name="a_stock_notice",
            title="A 股公告",
            min_profile="standard",
            maintenance_kind="akshare_notice",
            date_param="publish_date",
            source_kind="project_builtin",
            empty_policy=EMPTY_ALLOW,
        ),
        DatasetSpec(
            name="earnings_forecast",
            group="events",
            api_name="earnings_forecast",
            title="业绩预告",
            min_profile="standard",
            maintenance_kind="akshare_forecast",
            date_param="publish_date",
            source_kind="project_builtin",
            empty_policy=EMPTY_ALLOW,
        ),
        DatasetSpec(
            name="limit_list_d",
            group="short_term",
            api_name="limit_list_d",
            title="涨跌停池",
            min_profile="full",
            maintenance_kind="trade_date",
            date_param="trade_date",
            empty_policy=EMPTY_RETRY_AFTER_LAG,
        ),
        DatasetSpec(
            name="limit_step",
            group="short_term",
            api_name="limit_step",
            title="连板梯队",
            min_profile="full",
            maintenance_kind="trade_date",
            date_param="trade_date",
            empty_policy=EMPTY_RETRY_AFTER_LAG,
        ),
        DatasetSpec(
            name="limit_cpt_list",
            group="short_term",
            api_name="limit_cpt_list",
            title="概念强度",
            min_profile="full",
            maintenance_kind="trade_date",
            date_param="trade_date",
            empty_policy=EMPTY_RETRY_AFTER_LAG,
        ),
        DatasetSpec(
            name="kpl_list",
            group="short_term",
            api_name="kpl_list",
            title="开盘啦榜单",
            min_profile="full",
            maintenance_kind="trade_date",
            date_param="trade_date",
            empty_policy=EMPTY_RETRY_AFTER_LAG,
        ),
        DatasetSpec(
            name="limit_list_ths",
            group="short_term",
            api_name="limit_list_ths",
            title="同花顺涨跌停榜单",
            min_profile="full",
            maintenance_kind="trade_date",
            date_param="trade_date",
            empty_policy=EMPTY_RETRY_AFTER_LAG,
        ),
        DatasetSpec(
            name="income",
            group="financials",
            api_name="income",
            title="利润表",
            min_profile="full",
            maintenance_kind="stock_pool_financial",
            date_param="period",
            requires_stock_pool=True,
            empty_policy=EMPTY_ALLOW,
        ),
        DatasetSpec(
            name="balancesheet",
            group="financials",
            api_name="balancesheet",
            title="资产负债表",
            min_profile="full",
            maintenance_kind="stock_pool_financial",
            date_param="period",
            requires_stock_pool=True,
            empty_policy=EMPTY_ALLOW,
        ),
        DatasetSpec(
            name="cashflow",
            group="financials",
            api_name="cashflow",
            title="现金流量表",
            min_profile="full",
            maintenance_kind="stock_pool_financial",
            date_param="period",
            requires_stock_pool=True,
            empty_policy=EMPTY_ALLOW,
        ),
        DatasetSpec(
            name="express",
            group="financials",
            api_name="express",
            title="业绩快报",
            min_profile="full",
            maintenance_kind="stock_pool_financial",
            date_param="period",
            requires_stock_pool=True,
            empty_policy=EMPTY_ALLOW,
        ),
        DatasetSpec(
            name="fina_indicator",
            group="financials",
            api_name="fina_indicator",
            title="财务指标",
            min_profile="full",
            maintenance_kind="stock_pool_financial",
            date_param="period",
            requires_stock_pool=True,
            empty_policy=EMPTY_ALLOW,
        ),
        DatasetSpec(
            name="fina_mainbz",
            group="financials",
            api_name="fina_mainbz",
            title="主营业务构成",
            min_profile="full",
            maintenance_kind="stock_pool_financial",
            date_param="period",
            variants=(_variant("product", {"type": "P"}), _variant("district", {"type": "D"})),
            requires_stock_pool=True,
            empty_policy=EMPTY_ALLOW,
        ),
        DatasetSpec(
            name="dividend",
            group="financials",
            api_name="dividend",
            title="分红送股",
            min_profile="full",
            maintenance_kind="stock_pool_financial",
            date_param="period",
            requires_stock_pool=True,
            empty_policy=EMPTY_ALLOW,
        ),
        DatasetSpec(
            name="fina_audit",
            group="financials",
            api_name="fina_audit",
            title="财务审计意见",
            min_profile="full",
            maintenance_kind="stock_pool_financial",
            date_param="period",
            requires_stock_pool=True,
            empty_policy=EMPTY_ALLOW,
        ),
        DatasetSpec(
            name="disclosure_date",
            group="financials",
            api_name="disclosure_date",
            title="财报披露日期",
            min_profile="full",
            maintenance_kind="financial_disclosure_date",
            date_param="period",
            empty_policy=EMPTY_ALLOW,
        ),
        DatasetSpec(
            name="event_news",
            group="news",
            api_name="event_news",
            title="新闻快讯",
            min_profile="full",
            maintenance_kind="event_news",
            date_param="news_date",
            source_kind="project_builtin",
            empty_policy=EMPTY_ALLOW,
        ),
    )


def maintenance_dir(data_dir: str | Path | None = None, env_file: str | Path = ".env") -> Path:
    return (Path(data_dir).expanduser() if data_dir is not None else default_data_dir(env_file)) / "maintenance"


def access_catalog_path(data_dir: str | Path | None = None, env_file: str | Path = ".env") -> Path:
    return maintenance_dir(data_dir, env_file=env_file) / "access.json"


def state_path(data_dir: str | Path | None = None, env_file: str | Path = ".env") -> Path:
    return maintenance_dir(data_dir, env_file=env_file) / "state.json"


def reports_dir(data_dir: str | Path | None = None, env_file: str | Path = ".env") -> Path:
    return maintenance_dir(data_dir, env_file=env_file) / "reports"


class MartStore:
    def __init__(self, data_dir: str | Path | None = None, env_file: str | Path = ".env") -> None:
        base = Path(data_dir).expanduser() if data_dir is not None else default_data_dir(env_file)
        self.root = base / "mart"

    def partition_dir(self, dataset: str, partition: dict[str, Any] | None = None) -> Path:
        parts = [self.root, Path(_safe_path_value(dataset))]
        for key, value in sorted((partition or {}).items()):
            parts.append(Path(f"{_safe_path_value(key)}={_safe_path_value(value)}"))
        return Path(*parts)

    def data_path(self, dataset: str, partition: dict[str, Any] | None = None) -> Path:
        return self.partition_dir(dataset, partition=partition) / "part.parquet"

    def meta_path(self, dataset: str, partition: dict[str, Any] | None = None) -> Path:
        return self.partition_dir(dataset, partition=partition) / "_meta.json"

    def exists(self, dataset: str, partition: dict[str, Any] | None = None) -> bool:
        data_path = self.data_path(dataset, partition)
        meta_path = self.meta_path(dataset, partition)
        if not data_path.exists() or not meta_path.exists():
            return False
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        rows = meta.get("rows")
        return meta.get("schema") == MART_META_SCHEMA and isinstance(rows, int) and rows >= 0

    def read_meta(self, dataset: str, partition: dict[str, Any] | None = None) -> dict[str, Any] | None:
        meta_path = self.meta_path(dataset, partition)
        if not meta_path.exists():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return meta if isinstance(meta, dict) else None

    def write(
        self,
        dataset: str,
        partition: dict[str, Any] | None,
        value: Any,
        source: dict[str, Any] | None = None,
        quality: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        frame = _to_frame(value)
        directory = self.partition_dir(dataset, partition=partition)
        directory.mkdir(parents=True, exist_ok=True)
        data_path = directory / "part.parquet"
        meta_path = directory / "_meta.json"
        tmp_data = directory / ".part.parquet.tmp"
        tmp_meta = directory / "._meta.json.tmp"
        rows = int(len(frame))
        columns = [str(column) for column in frame.columns]
        meta = {
            "schema": MART_META_SCHEMA,
            "dataset": dataset,
            "partition": dict(partition or {}),
            "rows": rows,
            "columns": columns,
            "source": source or {},
            "quality_status": (quality or {}).get("status", QUALITY_OK),
            "quality": quality or {"status": QUALITY_OK},
            "published_at": _now_iso(),
        }
        try:
            frame.to_parquet(tmp_data, index=False)
            tmp_data.replace(data_path)
            tmp_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
            tmp_meta.replace(meta_path)
        finally:
            for path in [tmp_data, tmp_meta]:
                if path.exists():
                    path.unlink()
        return meta

    def read_partitions(self, dataset: str, partitions: Iterable[dict[str, Any]], columns: list[str] | None = None) -> Any:
        try:
            import pandas as pd
        except ImportError as exc:  # pragma: no cover - pandas is a dependency
            raise MaintenanceError("读取 mart 需要 pandas") from exc

        frames = []
        for partition in partitions:
            data_path = self.data_path(dataset, partition)
            if not data_path.exists():
                continue
            frames.append(pd.read_parquet(data_path, columns=columns))
        if not frames:
            return pd.DataFrame(columns=columns or [])
        return pd.concat(frames, ignore_index=True)

    def read_dataset(self, dataset: str, partition: dict[str, Any] | None = None, columns: list[str] | None = None) -> Any:
        try:
            import pandas as pd
        except ImportError as exc:  # pragma: no cover - pandas is a dependency
            raise MaintenanceError("读取 mart 需要 pandas") from exc
        data_path = self.data_path(dataset, partition)
        if not data_path.exists():
            return pd.DataFrame(columns=columns or [])
        return pd.read_parquet(data_path, columns=columns)


def _partition_health(
    mart: MartStore,
    spec: DatasetSpec,
    partition_value: Any,
    as_of: str | date | datetime | None = None,
) -> dict[str, Any]:
    partition = _partition_for(spec, str(partition_value))
    data_path = mart.data_path(spec.name, partition)
    meta = mart.read_meta(spec.name, partition)
    if not data_path.exists() or meta is None:
        return {
            "dataset": spec.name,
            "partition": partition,
            "exists": False,
            "complete": False,
            "retryable": True,
            "quality_status": QUALITY_MISSING,
            "rows": None,
            "columns": [],
            "reason": "partition_missing",
        }
    rows = meta.get("rows")
    rows = rows if isinstance(rows, int) else 0
    columns = [str(column) for column in meta.get("columns", [])] if isinstance(meta.get("columns"), list) else []
    if rows > 0 and (
        spec.required_columns
        or spec.unique_key
        or spec.min_rows is not None
        or (spec.date_param and spec.maintenance_kind in {"trade_date", "member_by_index_trade_date"})
    ):
        try:
            frame = mart.read_dataset(spec.name, partition)
            quality = _quality_from_frame(spec, frame, partition_value, as_of=as_of)
        except Exception as exc:  # noqa: BLE001 - corrupted parquet should be visible in check
            quality = {
                "status": QUALITY_SCHEMA_ISSUE,
                "empty_policy": spec.empty_policy,
                "rows": rows,
                "columns": len(columns),
                "reason": "partition_read_failed",
                "issues": [{"type": QUALITY_SCHEMA_ISSUE, "message": str(exc)}],
            }
    else:
        quality = _empty_quality(spec, rows, columns, partition_value, as_of=as_of)
    status = str(quality.get("status", QUALITY_OK))
    return {
        "dataset": spec.name,
        "partition": partition,
        "exists": True,
        "complete": _is_complete_quality(status),
        "retryable": status in QUALITY_RETRYABLE,
        "quality_status": status,
        "rows": rows,
        "columns": columns,
        "reason": quality.get("reason", ""),
        "quality": quality,
        "published_at": meta.get("published_at"),
    }


def _best_metadata_decision(api_name: str, entries: list[InterfaceEntry], config: TushareConfig) -> AccessDecision:
    if not entries:
        return AccessDecision(api_name=api_name, access=ACCESS_DENIED, source="metadata", reason="interface_not_found")

    unverified: InterfaceEntry | None = None
    denied_reasons: list[str] = []
    for entry in entries:
        required_points = entry.required_points
        if entry.eligibility == "needs_separate_permission":
            denied_reasons.append("needs_separate_permission")
            continue
        if required_points is not None and required_points > config.points:
            denied_reasons.append(f"required_points={required_points}, current_points={config.points}")
            continue
        if entry.eligibility == "points_ok":
            return AccessDecision(
                api_name=api_name,
                access=ACCESS_ALLOWED,
                source="metadata",
                eligibility=entry.eligibility,
                required_points=entry.required_points,
                doc_id=entry.doc_id,
                checked_at=_now_iso(),
            )
        if unverified is None:
            unverified = entry

    if unverified is not None:
        return AccessDecision(
            api_name=api_name,
            access=ACCESS_UNVERIFIED,
            source="metadata",
            reason="eligibility_unknown",
            eligibility=unverified.eligibility,
            required_points=unverified.required_points,
            doc_id=unverified.doc_id,
            checked_at=_now_iso(),
        )

    return AccessDecision(
        api_name=api_name,
        access=ACCESS_DENIED,
        source="metadata",
        reason="; ".join(sorted(set(denied_reasons))) or "not_allowed",
        eligibility=entries[0].eligibility,
        required_points=entries[0].required_points,
        doc_id=entries[0].doc_id,
        checked_at=_now_iso(),
    )


def metadata_access_decision(provider: AShareProvider, api_name: str) -> AccessDecision:
    return _best_metadata_decision(api_name, provider.registry.find(api_name), provider.config)


def _access_from_dict(api_name: str, payload: dict[str, Any]) -> AccessDecision:
    access = str(payload.get("access", ACCESS_UNVERIFIED))
    if access not in ACCESS_STATUSES:
        access = ACCESS_UNVERIFIED
    return AccessDecision(
        api_name=api_name,
        access=access,
        source=str(payload.get("source", "")),
        reason=str(payload.get("reason", "")),
        eligibility=str(payload.get("eligibility", "")),
        required_points=payload.get("required_points"),
        doc_id=payload.get("doc_id"),
        checked_at=payload.get("checked_at"),
    )


def load_access_catalog(path: str | Path | None = None, data_dir: str | Path | None = None, env_file: str | Path = ".env") -> dict[str, AccessDecision]:
    resolved_path = Path(path) if path is not None else access_catalog_path(data_dir, env_file=env_file)
    if not resolved_path.exists():
        return {}
    payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    interfaces = payload.get("interfaces", {})
    if not isinstance(interfaces, dict):
        return {}
    return {
        str(api_name): _access_from_dict(str(api_name), item)
        for api_name, item in interfaces.items()
        if isinstance(item, dict)
    }


def write_access_catalog(
    decisions: dict[str, AccessDecision],
    path: str | Path | None = None,
    data_dir: str | Path | None = None,
    env_file: str | Path = ".env",
    points: int | None = None,
) -> Path:
    resolved_path = Path(path) if path is not None else access_catalog_path(data_dir, env_file=env_file)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": ACCESS_SCHEMA,
        "checked_at": _now_iso(),
        "points": points,
        "interfaces": {
            api_name: asdict(decision)
            for api_name, decision in sorted(decisions.items())
        },
    }
    resolved_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return resolved_path


def _smoke_params_for(spec: DatasetSpec) -> dict[str, Any]:
    variant_params = dict(spec.variants[0].params) if spec.variants else {}
    defaults = default_params(spec.api_name)
    defaults.update(variant_params)
    smoke_driver_codes = {
        "ths_member": "885800.TI",
        "dc_member": "BK0428.DC",
        "tdx_member": "880728.TDX",
    }
    if spec.api_name in smoke_driver_codes:
        defaults.setdefault(spec.driver_code_param, smoke_driver_codes[spec.api_name])
    if spec.date_param and spec.date_param not in defaults:
        defaults[spec.date_param] = "20260423"
    if spec.maintenance_kind == "calendar":
        defaults.setdefault("start_date", "20260423")
        defaults.setdefault("end_date", "20260423")
    return defaults


def audit_access(
    provider: AShareProvider,
    profile: str = "full",
    specs: tuple[DatasetSpec, ...] | None = None,
    smoke_unknown: bool = False,
    data_dir: str | Path | None = None,
) -> dict[str, Any]:
    selected_specs = select_dataset_specs(profile=profile, specs=specs)
    by_api = {spec.api_name: spec for spec in selected_specs}
    decisions: dict[str, AccessDecision] = {}
    results: list[dict[str, Any]] = []
    for api_name, spec in sorted(by_api.items()):
        if spec.source_kind != "tushare":
            decision = AccessDecision(
                api_name=api_name,
                access=ACCESS_ALLOWED,
                source=spec.source_kind,
                reason="project_builtin",
                checked_at=_now_iso(),
            )
            decisions[api_name] = decision
            results.append(asdict(decision))
            continue
        decision = metadata_access_decision(provider, api_name)
        if decision.access == ACCESS_UNVERIFIED and smoke_unknown:
            params = _smoke_params_for(spec)
            fields = spec.variants[0].fields if spec.variants and spec.variants[0].fields else spec.fields
            try:
                provider.call(api_name, params=params, fields=fields)
                decision = AccessDecision(
                    api_name=api_name,
                    access=ACCESS_ALLOWED,
                    source="smoke_verified",
                    reason="",
                    eligibility=decision.eligibility,
                    required_points=decision.required_points,
                    doc_id=decision.doc_id,
                    checked_at=_now_iso(),
                )
            except Exception as exc:  # noqa: BLE001 - audit should report and continue
                decision = AccessDecision(
                    api_name=api_name,
                    access=ACCESS_DENIED,
                    source="smoke_verified",
                    reason=str(exc),
                    eligibility=decision.eligibility,
                    required_points=decision.required_points,
                    doc_id=decision.doc_id,
                    checked_at=_now_iso(),
                )
        decisions[api_name] = decision
        results.append(asdict(decision))
    path = write_access_catalog(decisions, data_dir=data_dir, env_file=provider._env_file, points=provider.config.points)
    return {
        "schema": ACCESS_SCHEMA,
        "path": str(path),
        "profile": profile,
        "checked_at": _now_iso(),
        "interfaces": results,
    }


def select_dataset_specs(
    profile: str = "full",
    specs: tuple[DatasetSpec, ...] | None = None,
    include_groups: set[str] | None = None,
    exclude_groups: set[str] | None = None,
    include_financials: bool = False,
) -> tuple[DatasetSpec, ...]:
    if profile not in PROFILE_ORDER:
        raise MaintenanceError(f"未知 profile：{profile}，可选：basic/standard/full")
    rank = PROFILE_ORDER[profile]
    selected: list[DatasetSpec] = []
    for spec in specs or default_dataset_specs():
        if spec.profile_rank > rank:
            continue
        if include_groups and spec.group not in include_groups:
            continue
        if exclude_groups and spec.group in exclude_groups:
            continue
        if spec.requires_stock_pool and not include_financials:
            continue
        selected.append(spec)
    return tuple(selected)


def build_maintenance_plan(
    provider: AShareProvider,
    profile: str = "full",
    specs: tuple[DatasetSpec, ...] | None = None,
    access_catalog: dict[str, AccessDecision] | None = None,
    include_groups: set[str] | None = None,
    exclude_groups: set[str] | None = None,
    include_financials: bool = False,
) -> MaintenancePlan:
    selected_specs = select_dataset_specs(
        profile=profile,
        specs=specs,
        include_groups=include_groups,
        exclude_groups=exclude_groups,
        include_financials=include_financials,
    )
    catalog = access_catalog or {}
    datasets: list[PlanDataset] = []
    for spec in selected_specs:
        if spec.source_kind != "tushare":
            datasets.append(
                PlanDataset(
                    spec=spec,
                    access=AccessDecision(
                        api_name=spec.api_name,
                        access=ACCESS_ALLOWED,
                        source=spec.source_kind,
                        reason="project_builtin",
                        checked_at=_now_iso(),
                    ),
                )
            )
            continue
        metadata_decision = metadata_access_decision(provider, spec.api_name)
        catalog_decision = catalog.get(spec.api_name)
        if metadata_decision.access == ACCESS_ALLOWED:
            decision = metadata_decision
        elif catalog_decision and catalog_decision.access == ACCESS_ALLOWED:
            decision = catalog_decision
        else:
            continue
        datasets.append(PlanDataset(spec=spec, access=decision))
    active_names = {item.spec.name for item in datasets}
    runnable = [
        item
        for item in datasets
        if item.spec.driver_dataset is None or item.spec.driver_dataset in active_names
    ]
    return MaintenancePlan(profile=profile, generated_at=_now_iso(), datasets=tuple(runnable))


def load_state(path: str | Path | None = None, data_dir: str | Path | None = None, env_file: str | Path = ".env") -> dict[str, Any]:
    resolved_path = Path(path) if path is not None else state_path(data_dir, env_file=env_file)
    if not resolved_path.exists():
        return {"schema": MAINTENANCE_STATE_SCHEMA, "datasets": {}}
    try:
        payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema": MAINTENANCE_STATE_SCHEMA, "datasets": {}}
    if not isinstance(payload, dict):
        return {"schema": MAINTENANCE_STATE_SCHEMA, "datasets": {}}
    payload.setdefault("schema", MAINTENANCE_STATE_SCHEMA)
    payload.setdefault("datasets", {})
    return payload


def write_state(state: dict[str, Any], path: str | Path | None = None, data_dir: str | Path | None = None, env_file: str | Path = ".env") -> Path:
    resolved_path = Path(path) if path is not None else state_path(data_dir, env_file=env_file)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    state["schema"] = MAINTENANCE_STATE_SCHEMA
    state["updated_at"] = _now_iso()
    state.setdefault("datasets", {})
    resolved_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return resolved_path


def write_report(report: dict[str, Any], data_dir: str | Path | None = None, env_file: str | Path = ".env") -> Path:
    directory = reports_dir(data_dir, env_file=env_file)
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    path = directory / f"{timestamp}-{report.get('command', 'maintenance')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def _calendar_frame(provider: AShareProvider, start_date: str, end_date: str) -> Any:
    return provider.trade_cal(start_date=start_date, end_date=end_date, fields="cal_date,is_open")


def trade_dates_between(provider: AShareProvider, start_date: str, end_date: str) -> list[str]:
    frame = _calendar_frame(provider, start_date, end_date)
    if hasattr(frame, "to_dict"):
        records = frame.to_dict("records")
    elif isinstance(frame, list):
        records = frame
    else:
        raise MaintenanceError("交易日历返回值无法解析")
    dates = [
        _format_yyyymmdd(row["cal_date"])
        for row in records
        if str(row.get("is_open")).lower() in {"1", "1.0", "true"}
    ]
    return sorted(date_text for date_text in dates if start_date <= date_text <= end_date)


def latest_completed_trade_date(provider: AShareProvider, as_of: str | date | datetime | None = None) -> str:
    return provider.previous_trade_date(as_of=as_of)


def _partition_for(spec: DatasetSpec, value: str) -> dict[str, Any]:
    if spec.maintenance_kind == "calendar":
        variants = _request_variants(spec)
        exchange = variants[0].params.get("exchange", "SSE") if variants else "SSE"
        return {"exchange": exchange}
    if spec.maintenance_kind in {"snapshot", "snapshot_range", "member_by_index_snapshot"}:
        return {"snapshot_date": value}
    if spec.date_param is None:
        return {}
    return {spec.date_param: value}


def _request_variants(spec: DatasetSpec) -> tuple[RequestVariant, ...]:
    return spec.variants or (_variant(),)


def _call_dataset_api(provider: AShareProvider, spec: DatasetSpec, params: dict[str, Any], fields: str | None) -> Any:
    if not spec.page_limit:
        return provider.call(spec.api_name, params=params, fields=fields)

    values: list[Any] = []
    offset = 0
    for _ in range(max(spec.max_pages, 1)):
        page_params = dict(params)
        page_params["limit"] = spec.page_limit
        page_params["offset"] = offset
        result = provider.call(spec.api_name, params=page_params, fields=fields)
        values.append(result)
        rows = _record_count(result)
        if rows < spec.page_limit:
            break
        offset += spec.page_limit
    return _concat_values(values)


def _fetch_dataset_value(provider: AShareProvider, spec: DatasetSpec, partition_value: str, start_date: str | None = None, end_date: str | None = None) -> Any:
    values: list[Any] = []
    for variant in _request_variants(spec):
        params = dict(variant.params)
        if spec.maintenance_kind == "calendar":
            if start_date is None or end_date is None:
                raise MaintenanceError("calendar 维护需要 start_date/end_date")
            params.update({"start_date": start_date, "end_date": end_date})
        elif spec.maintenance_kind == "snapshot_range":
            range_end = end_date or partition_value
            range_start = (_parse_yyyymmdd(range_end) - timedelta(days=spec.range_lookback_days)).strftime("%Y%m%d")
            params.update({"start_date": range_start, "end_date": _format_yyyymmdd(range_end)})
        elif spec.maintenance_kind == "snapshot":
            pass
        elif spec.date_param:
            params[spec.date_param] = partition_value
        else:
            raise MaintenanceError(f"数据集 {spec.name} 缺少 date_param")
        fields = variant.fields if variant.fields is not None else spec.fields
        result = _call_dataset_api(provider, spec, params=params, fields=fields)
        if len(_request_variants(spec)) > 1 and _record_count(result) > 0:
            result = _append_column(result, "_variant", variant.label)
        values.append(result)
    combined = _concat_values(values)
    if spec.name == "limit_list_d" and _record_count(combined) == 0:
        fallback = _fetch_akshare_limit_list(partition_value)
        if _record_count(fallback) > 0:
            return fallback
    return combined


def _first_existing_column(value: Any, candidates: tuple[str, ...] | list[str]) -> str | None:
    frame = _to_frame(value)
    for column in candidates:
        if column in frame.columns:
            return column
    return None


def _member_driver_frame(mart: MartStore, driver_dataset: str, partition_value: str) -> tuple[Any, dict[str, Any]]:
    candidates = [
        ("trade_date", partition_value),
        ("snapshot_date", partition_value),
    ]
    for key in ("trade_date", "snapshot_date"):
        latest = _latest_partition_values(mart, driver_dataset, key, limit=1)
        if latest:
            candidates.append((key, latest[-1]))
    seen: set[tuple[str, str]] = set()
    for key, value in candidates:
        marker = (key, value)
        if marker in seen:
            continue
        seen.add(marker)
        partition = {key: value}
        if not mart.exists(driver_dataset, partition):
            continue
        frame = mart.read_dataset(driver_dataset, partition)
        if hasattr(frame, "empty") and not frame.empty:
            return frame, partition
    raise MaintenanceError(f"{driver_dataset} 缺少可用板块代码分区，无法维护成分表")


def _driver_name_map(driver: Any, code_column: str, name_column: str | None) -> dict[str, str]:
    if not name_column:
        return {}
    frame = _to_frame(driver)
    if code_column not in frame.columns or name_column not in frame.columns:
        return {}
    result: dict[str, str] = {}
    for row in frame[[code_column, name_column]].dropna(subset=[code_column]).to_dict("records"):
        code = str(row.get(code_column) or "").strip()
        if code and code not in result:
            result[code] = str(row.get(name_column) or "")
    return result


def _enrich_member_frame(
    value: Any,
    spec: DatasetSpec,
    driver: Any,
    code_column: str,
    name_column: str | None,
    fallback_driver_code: str | None = None,
) -> Any:
    frame = _to_frame(value)
    if frame.empty:
        return frame
    frame = frame.copy()
    frame["_driver_dataset"] = spec.driver_dataset
    member_code_column = _first_existing_column(frame, [spec.driver_code_param, "_driver_ts_code", "index_code", "ts_code"])
    if member_code_column:
        frame["_driver_ts_code"] = frame[member_code_column].astype(str)
    elif fallback_driver_code:
        frame["_driver_ts_code"] = fallback_driver_code
    names = _driver_name_map(driver, code_column, name_column)
    if names and "_driver_ts_code" in frame.columns:
        frame["_driver_name"] = frame["_driver_ts_code"].map(names).fillna("")
    elif name_column and fallback_driver_code:
        frame["_driver_name"] = names.get(fallback_driver_code, "")
    return frame


def _fetch_member_dataset_value(
    provider: AShareProvider,
    mart: MartStore,
    spec: DatasetSpec,
    partition_value: str,
) -> tuple[Any, dict[str, Any]]:
    if not spec.driver_dataset:
        raise MaintenanceError(f"{spec.name} 缺少 driver_dataset")
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - pandas is a dependency
        raise MaintenanceError("成分表维护需要 pandas") from exc

    driver, driver_partition = _member_driver_frame(mart, spec.driver_dataset, partition_value)
    code_column = _first_existing_column(driver, spec.driver_code_columns)
    if code_column is None:
        raise MaintenanceError(f"{spec.driver_dataset} 缺少板块代码字段：{','.join(spec.driver_code_columns)}")
    name_column = _first_existing_column(driver, spec.driver_name_columns)
    driver_records = driver.to_dict("records")
    codes = []
    for row in driver_records:
        code = str(row.get(code_column) or "").strip()
        if code:
            codes.append(code)
    codes = list(dict.fromkeys(codes))

    bulk_params: dict[str, Any] = {}
    if spec.date_param and spec.maintenance_kind == "member_by_index_trade_date":
        bulk_params[spec.date_param] = partition_value
    bulk_errors: list[dict[str, Any]] = []
    try:
        bulk_result = _call_dataset_api(provider, spec, params=bulk_params, fields=spec.fields)
        bulk_frame = _to_frame(bulk_result)
        if not bulk_frame.empty:
            combined = _enrich_member_frame(bulk_frame, spec, driver, code_column, name_column)
            return combined, {
                "source_kind": "member_by_index",
                "fetch_mode": "bulk",
                "driver_dataset": spec.driver_dataset,
                "driver_partition": driver_partition,
                "driver_code_column": code_column,
                "driver_count": len(codes),
                "bulk_params": bulk_params,
                "bulk_error_count": 0,
                "driver_error_count": 0,
                "driver_errors": [],
            }
    except Exception as exc:  # noqa: BLE001 - fall back to per-driver calls
        bulk_errors.append({"error_type": type(exc).__name__, "error": str(exc), "params": bulk_params})

    frames = []
    errors: list[dict[str, Any]] = []
    for code in codes:
        params = {spec.driver_code_param: code}
        if spec.date_param and spec.maintenance_kind == "member_by_index_trade_date":
            params[spec.date_param] = partition_value
        try:
            result = _call_dataset_api(provider, spec, params=params, fields=spec.fields)
        except Exception as exc:  # noqa: BLE001 - keep per-board errors in source metadata
            errors.append({"driver_code": code, "error_type": type(exc).__name__, "error": str(exc)})
            continue
        frame = _to_frame(result)
        if frame.empty:
            continue
        frame = _enrich_member_frame(frame, spec, driver, code_column, name_column, fallback_driver_code=code)
        frames.append(frame)
    if errors and not frames:
        raise MaintenanceError(f"{spec.name} 按板块拉取全部失败：{errors[:3]}")
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return combined, {
        "source_kind": "member_by_index",
        "fetch_mode": "driver_loop",
        "driver_dataset": spec.driver_dataset,
        "driver_partition": driver_partition,
        "driver_code_column": code_column,
        "driver_count": len(codes),
        "bulk_error_count": len(bulk_errors),
        "bulk_errors": bulk_errors[:10],
        "driver_error_count": len(errors),
        "driver_errors": errors[:50],
    }


def _publish_partition(
    provider: AShareProvider,
    mart: MartStore,
    spec: DatasetSpec,
    partition_value: str,
    start_date: str | None = None,
    end_date: str | None = None,
    quality_as_of: str | date | datetime | None = None,
) -> dict[str, Any]:
    partition = _partition_for(spec, partition_value)
    source_extra: dict[str, Any] = {}
    if spec.maintenance_kind in {"member_by_index_snapshot", "member_by_index_trade_date"}:
        value, source_extra = _fetch_member_dataset_value(provider, mart, spec, partition_value=partition_value)
    else:
        value = _fetch_dataset_value(provider, spec, partition_value=partition_value, start_date=start_date, end_date=end_date)
    if spec.maintenance_kind == "calendar":
        try:
            existing = mart.read_dataset(spec.name, partition)
            if hasattr(existing, "empty") and not existing.empty:
                value = _merge_calendar_values(existing, value)
        except Exception:
            pass
    value = _drop_exact_duplicates(value)
    source = {
        "kind": "tushare",
        "api_name": spec.api_name,
        "partition_value": partition_value,
        "start_date": start_date,
        "end_date": end_date,
    }
    source.update(source_extra)
    if spec.name == "limit_list_d" and _record_count(value) > 0:
        frame = _to_frame(value)
        if "source_kind" in frame.columns and set(frame["source_kind"].dropna().astype(str).unique().tolist()) == {"akshare_limit_pool"}:
            source = {
                "kind": "akshare",
                "source_kind": "akshare_limit_pool",
                "fallback_for": "tushare.limit_list_d",
                "partition_value": partition_value,
                "start_date": start_date,
                "end_date": end_date,
            }
    frame = _to_frame(value)
    quality = _quality_from_frame(spec, frame, partition_value, as_of=quality_as_of)
    meta = mart.write(
        spec.name,
        partition,
        value,
        source=source,
        quality=quality,
    )
    return meta


def _merge_calendar_values(existing: Any, incoming: Any) -> Any:
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - pandas is a dependency
        raise MaintenanceError("合并交易日历需要 pandas") from exc

    existing_frame = _to_frame(existing)
    incoming_frame = _to_frame(incoming)
    merged = pd.concat([existing_frame, incoming_frame], ignore_index=True)
    key_columns = [column for column in ["exchange", "cal_date"] if column in merged.columns]
    if not key_columns and "cal_date" in merged.columns:
        key_columns = ["cal_date"]
    if key_columns:
        merged = merged.drop_duplicates(subset=key_columns, keep="last")
    if "cal_date" in merged.columns:
        merged = merged.sort_values("cal_date").reset_index(drop=True)
    return merged


def _fetch_akshare_limit_list(trade_date: str) -> Any:
    try:
        import akshare as ak
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - akshare is a dependency
        raise MaintenanceError("涨停池 AKShare fallback 需要 akshare 和 pandas") from exc

    groups = [
        ("U", "stock_zt_pool_em"),
        ("Z", "stock_zt_pool_zbgc_em"),
        ("D", "stock_zt_pool_dtgc_em"),
    ]
    frames = []
    for limit_type, function_name in groups:
        function = getattr(ak, function_name)
        frame = function(date=_format_yyyymmdd(trade_date))
        if frame is None or frame.empty:
            continue
        normalized = frame.copy()
        column_map = {
            "代码": "ts_code",
            "名称": "name",
            "最新价": "close",
            "涨跌幅": "pct_chg",
            "成交额": "amount",
            "流通市值": "float_mv",
            "总市值": "total_mv",
            "换手率": "turnover_ratio",
            "封板资金": "fd_amount",
            "首次封板时间": "first_time",
            "最后封板时间": "last_time",
            "炸板次数": "open_times",
            "连板数": "limit_times",
            "所属行业": "industry",
        }
        normalized = normalized.rename(columns={key: value for key, value in column_map.items() if key in normalized.columns})
        if "ts_code" in normalized.columns:
            normalized["ts_code"] = normalized["ts_code"].map(_stock_code_to_ts_code)
        normalized["trade_date"] = _format_yyyymmdd(trade_date)
        normalized["limit"] = limit_type
        normalized["source_kind"] = "akshare_limit_pool"
        frames.append(normalized)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _dataset_result(name: str, group: str, status: str, **kwargs: Any) -> dict[str, Any]:
    payload = {"name": name, "group": group, "status": status}
    payload.update(kwargs)
    return payload


def _stock_pool_codes(
    provider: AShareProvider,
    mart: MartStore,
    snapshot_date: str,
    stock_pool: list[str] | tuple[str, ...] | None = None,
    max_stocks: int | None = None,
) -> list[str]:
    if stock_pool:
        codes = [_stock_code_to_ts_code(code) for code in stock_pool if str(code).strip()]
    else:
        frame = mart.read_dataset("stock_basic", {"snapshot_date": snapshot_date})
        if not hasattr(frame, "empty") or frame.empty or "ts_code" not in frame.columns:
            frame = provider.stock_basic()
        if not hasattr(frame, "empty") or frame.empty or "ts_code" not in frame.columns:
            raise MaintenanceError("无法从 stock_basic 获取股票池")
        codes = [str(code) for code in frame["ts_code"].dropna().astype(str).tolist()]
    deduped = list(dict.fromkeys(codes))
    return deduped[:max_stocks] if max_stocks and max_stocks > 0 else deduped


def _publish_financial_dataset(
    provider: AShareProvider,
    mart: MartStore,
    spec: DatasetSpec,
    start_date: str,
    end_date: str,
    stock_pool: list[str] | tuple[str, ...] | None = None,
    max_stocks: int | None = None,
) -> dict[str, Any]:
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - pandas is a dependency
        raise MaintenanceError("财务数据落库需要 pandas") from exc

    codes = _stock_pool_codes(provider, mart, end_date, stock_pool=stock_pool, max_stocks=max_stocks)
    values = []
    errors: list[dict[str, Any]] = []
    variants = _request_variants(spec)
    for ts_code in codes:
        for variant in variants:
            params = {"ts_code": ts_code, "start_date": start_date, "end_date": end_date}
            params.update(variant.params)
            try:
                result = provider.call(spec.api_name, params=params, fields=variant.fields or spec.fields)
                if _record_count(result) > 0:
                    frame = _append_column(result, "_variant", variant.label) if len(variants) > 1 else _to_frame(result)
                    values.append(frame)
            except Exception as exc:  # noqa: BLE001 - report per stock
                errors.append({"ts_code": ts_code, "variant": variant.label, "error_type": type(exc).__name__, "error": str(exc)})
    combined = pd.concat(values, ignore_index=True) if values else pd.DataFrame()
    if combined.empty:
        return {
            "requested_stocks": len(codes),
            "partitions_written": 0,
            "rows": 0,
            "errors": errors,
        }
    period_source = "period" if "period" in combined.columns else None
    if period_source is None:
        for candidate in ("end_date", "report_period", "f_ann_date", "ann_date"):
            if candidate in combined.columns:
                period_source = candidate
                break
    if period_source is not None:
        combined = combined.copy()
        combined["period"] = combined[period_source].map(_financial_period_value)
        combined = combined[combined["period"] != ""]
        dedupe_columns = [
            column
            for column in [
                "ts_code",
                "period",
                "ann_date",
                "f_ann_date",
                "report_type",
                "comp_type",
                "end_type",
                "_variant",
                "bz_item",
                "bz_code",
                "curr_type",
            ]
            if column in combined.columns
        ]
        if len(dedupe_columns) >= 2:
            combined = combined.drop_duplicates(subset=dedupe_columns, keep="last")
    if "period" not in combined.columns:
        partition = {"range_start": start_date, "range_end": end_date}
        meta = mart.write(spec.name, partition, combined, source={"kind": "tushare", "api_name": spec.api_name, "stock_pool": len(codes)})
        return {
            "requested_stocks": len(codes),
            "partitions_written": 1,
            "rows": int(meta["rows"]),
            "errors": errors,
        }
    rows = 0
    written = 0
    for period, group in combined.groupby(combined["period"].astype(str)):
        meta = mart.write(
            spec.name,
            {"period": period},
            group.reset_index(drop=True),
            source={"kind": "tushare", "api_name": spec.api_name, "stock_pool": len(codes), "start_date": start_date, "end_date": end_date},
        )
        rows += int(meta["rows"])
        written += 1
    return {
        "requested_stocks": len(codes),
        "partitions_written": written,
        "rows": rows,
        "errors": errors,
    }


def _recent_report_periods(end_date: str | date | datetime, periods: int = 8) -> list[str]:
    end_text = _format_yyyymmdd(end_date)
    end = _parse_yyyymmdd(end_text)
    values: list[str] = []
    for year in range(end.year - 4, end.year + 2):
        for suffix in ("0331", "0630", "0930", "1231"):
            value = f"{year}{suffix}"
            if value <= end_text:
                values.append(value)
    return sorted(set(values))[-periods:]


def _publish_disclosure_dates(provider: AShareProvider, mart: MartStore, spec: DatasetSpec, end_date: str) -> dict[str, Any]:
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - pandas is a dependency
        raise MaintenanceError("披露日期落库需要 pandas") from exc

    rows = 0
    written = 0
    errors: list[dict[str, Any]] = []
    periods = _recent_report_periods(end_date, periods=8)
    for period in periods:
        try:
            result = provider.call(spec.api_name, params={"end_date": period}, fields=spec.fields)
            frame = _to_frame(result)
        except Exception as exc:  # noqa: BLE001 - keep per-period errors
            errors.append({"period": period, "error_type": type(exc).__name__, "error": str(exc)})
            continue
        if frame.empty:
            frame = pd.DataFrame(columns=["ts_code", "end_date", "pre_date", "actual_date", "modify_date"])
        else:
            frame = frame.copy()
            if "period" not in frame.columns:
                frame["period"] = period
            dedupe_columns = [column for column in ["ts_code", "end_date", "ann_date", "actual_date"] if column in frame.columns]
            if dedupe_columns:
                frame = frame.drop_duplicates(subset=dedupe_columns, keep="last")
        quality = _quality_from_frame(spec, frame, period, as_of=end_date)
        meta = mart.write(
            spec.name,
            {"period": period},
            frame,
            source={"kind": "tushare", "api_name": spec.api_name, "end_date": period},
            quality=quality,
        )
        rows += int(meta["rows"])
        written += 1
    return {
        "requested_periods": len(periods),
        "partitions_written": written,
        "rows": rows,
        "periods": periods,
        "errors": errors,
    }


def _publish_records_by_date(
    mart: MartStore,
    dataset: str,
    records: list[dict[str, Any]],
    date_key: str,
    partition_key: str,
    source: dict[str, Any],
    expected_partitions: Iterable[str] | None = None,
    empty_columns: list[str] | None = None,
) -> dict[str, Any]:
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - pandas is a dependency
        raise MaintenanceError("记录分区落库需要 pandas") from exc

    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        value = record.get(date_key)
        if not value:
            continue
        text = str(value)
        if len(text) >= 10 and text[4] == "-" and text[7] == "-":
            partition_value = text[:10]
        else:
            partition_value = _format_yyyymmdd(text[:8])
        groups.setdefault(partition_value, []).append(record)
    rows = 0
    written = 0
    partition_values = list(dict.fromkeys([*groups.keys(), *(str(value) for value in expected_partitions or [])]))
    for partition_value in partition_values:
        group = groups.get(partition_value, [])
        partition = {partition_key: partition_value}
        existing_exists = mart.exists(dataset, partition)
        existing = mart.read_dataset(dataset, partition)
        incoming = pd.DataFrame(group)
        if incoming.empty and existing_exists:
            continue
        if incoming.empty:
            frame = pd.DataFrame(columns=empty_columns or [])
        else:
            frame = incoming if existing.empty else pd.concat([existing, incoming], ignore_index=True)
        dedupe_columns = [column for column in ["id", "dedupe_key", "content_hash"] if column in frame.columns]
        if dedupe_columns:
            frame = frame.drop_duplicates(subset=dedupe_columns, keep="last")
        meta = mart.write(dataset, partition, frame, source=source)
        rows += int(meta["rows"])
        written += 1
    return {"partitions_written": written, "rows": rows}


def _publish_akshare_notice(provider: AShareProvider, mart: MartStore, start_date: str, end_date: str) -> dict[str, Any]:
    empty_columns = [
        "id",
        "content_hash",
        "dedupe_key",
        "event_type",
        "source_kind",
        "stock_code",
        "stock_name",
        "title",
        "notice_type",
        "publish_date",
        "url",
        "fetched_at",
        "raw",
    ]
    rows = 0
    written = 0
    errors: list[dict[str, Any]] = []
    succeeded: list[str] = []
    for date_text in _date_range(start_date, end_date):
        iso_date = _iso_date_from_yyyymmdd(date_text)
        try:
            records = provider.a_stock_notice(days=1, end_date=date_text, category="全部")
        except Exception as exc:  # noqa: BLE001 - one bad notice date should not poison the whole run
            errors.append(
                {
                    "date": iso_date,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            continue
        result = _publish_records_by_date(
            mart,
            "a_stock_notice",
            records if isinstance(records, list) else _to_frame(records).to_dict("records"),
            date_key="publish_date",
            partition_key="publish_date",
            source={"kind": "project_builtin", "source": "a_stock_notice", "start_date": date_text, "end_date": date_text},
            expected_partitions=[iso_date],
            empty_columns=empty_columns,
        )
        rows += result["rows"]
        written += result["partitions_written"]
        succeeded.append(iso_date)
    return {
        "requested_partitions": len(_date_range(start_date, end_date)),
        "partitions_written": written,
        "rows": rows,
        "dates_succeeded": succeeded,
        "failed_partitions": [item["date"] for item in errors],
        "errors": errors,
    }


def _publish_akshare_forecast(provider: AShareProvider, mart: MartStore, start_date: str, end_date: str) -> dict[str, Any]:
    days = len(_date_range(start_date, end_date))
    records = provider.earnings_forecast(days=days, end_date=end_date)
    return _publish_records_by_date(
        mart,
        "earnings_forecast",
        records if isinstance(records, list) else _to_frame(records).to_dict("records"),
        date_key="publish_date",
        partition_key="publish_date",
        source={"kind": "project_builtin", "source": "earnings_forecast", "start_date": start_date, "end_date": end_date},
        expected_partitions=_iso_date_range(start_date, end_date),
        empty_columns=[
            "id",
            "content_hash",
            "dedupe_key",
            "event_type",
            "source_kind",
            "period",
            "stock_code",
            "stock_name",
            "metric",
            "forecast_type",
            "change_range",
            "publish_date",
            "change_summary",
            "change_reason",
            "fetched_at",
            "raw",
        ],
    )


def _publish_event_news(provider: AShareProvider, mart: MartStore, anchor_date: str) -> dict[str, Any]:
    from .news import DEFAULT_NEWS_SOURCES

    records: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    succeeded: list[str] = []
    anchor = _iso_date_from_yyyymmdd(anchor_date)
    for source in DEFAULT_NEWS_SOURCES:
        try:
            source_records = provider.event_news(sources=[source], anchor_date=anchor)
        except Exception as exc:  # noqa: BLE001 - one failing source should not block daily maintenance
            errors.append({"source": source, "error_type": type(exc).__name__, "error": str(exc)})
            continue
        source_payload = source_records if isinstance(source_records, list) else _to_frame(source_records).to_dict("records")
        records.extend(source_payload)
        succeeded.append(source)
    result = (
        _publish_records_by_date(
            mart,
            "event_news",
            records,
            date_key="date",
            partition_key="news_date",
            source={
                "kind": "project_builtin",
                "source": "event_news",
                "mode": "current_visible_page",
                "historical_backfill": False,
                "anchor_date": anchor_date,
                "sources": succeeded,
                "failed_sources": [item["source"] for item in errors],
            },
        )
        if records
        else {"partitions_written": 0, "rows": 0}
    )
    result["sources_requested"] = len(DEFAULT_NEWS_SOURCES)
    result["sources_succeeded"] = len(succeeded)
    result["mode"] = "current_visible_page"
    result["historical_backfill"] = False
    result["errors"] = errors
    return result


def run_backfill(
    provider: AShareProvider,
    plan: MaintenancePlan,
    start_date: str | date | datetime,
    end_date: str | date | datetime,
    data_dir: str | Path | None = None,
    refresh: bool = False,
    command: str = "backfill",
    write_report_file: bool = True,
    kind_start_dates: dict[str, str] | None = None,
    stock_pool: list[str] | tuple[str, ...] | None = None,
    max_stocks: int | None = None,
) -> dict[str, Any]:
    start_text = _format_yyyymmdd(start_date)
    end_text = _format_yyyymmdd(end_date)
    mart = MartStore(data_dir=data_dir, env_file=provider._env_file)
    state = load_state(data_dir=data_dir, env_file=provider._env_file)
    results: list[dict[str, Any]] = []
    started = time.monotonic()
    quality_as_of = _quality_as_of_text(end_text)
    trade_dates_by_start: dict[str, list[str]] = {}
    calendar_days_by_start: dict[str, list[str]] = {}

    for item in plan.datasets:
        spec = item.spec
        dataset_started = time.monotonic()
        try:
            if spec.maintenance_kind == "financial_disclosure_date":
                result = _publish_disclosure_dates(provider, mart, spec, end_text)
                errors = result.get("errors", [])
                if result.get("periods"):
                    state["datasets"].setdefault(spec.name, {})["last_success_period"] = result["periods"][-1]
                results.append(
                    _dataset_result(
                        spec.name,
                        spec.group,
                        "success" if not errors else ("partial" if result.get("partitions_written", 0) else "failed"),
                        requested_periods=result.get("requested_periods", 0),
                        partitions_written=result.get("partitions_written", 0),
                        rows=result.get("rows", 0),
                        recent_periods=result.get("periods", []),
                        errors=errors,
                        elapsed_seconds=round(time.monotonic() - dataset_started, 3),
                    )
                )
                continue
            if spec.maintenance_kind == "stock_pool_financial":
                result = _publish_financial_dataset(
                    provider,
                    mart,
                    spec,
                    start_text,
                    end_text,
                    stock_pool=stock_pool,
                    max_stocks=max_stocks,
                )
                state["datasets"].setdefault(spec.name, {})["last_success_period_sync"] = end_text
                errors = result.get("errors", [])
                results.append(
                    _dataset_result(
                        spec.name,
                        spec.group,
                        "success" if not errors else ("partial" if result.get("rows", 0) else "failed"),
                        requested_stocks=result.get("requested_stocks", 0),
                        partitions_written=result.get("partitions_written", 0),
                        rows=result.get("rows", 0),
                        errors=errors,
                        elapsed_seconds=round(time.monotonic() - dataset_started, 3),
                    )
                )
                continue
            if spec.maintenance_kind == "akshare_notice":
                date_start_text = (kind_start_dates or {}).get("publish_date", start_text)
                result = _publish_akshare_notice(provider, mart, date_start_text, end_text)
                errors = result.get("errors", [])
                status = "success" if not errors else ("partial" if result.get("dates_succeeded") else "failed")
                if end_text not in {_format_yyyymmdd(item.get("date", "")) for item in errors}:
                    state["datasets"].setdefault(spec.name, {})["last_success_publish_date"] = end_text
                results.append(
                    _dataset_result(
                        spec.name,
                        spec.group,
                        status,
                        requested_partitions=result.get("requested_partitions", 0),
                        partitions_written=result["partitions_written"],
                        rows=result["rows"],
                        failed_partitions=result.get("failed_partitions", []),
                        errors=errors,
                        elapsed_seconds=round(time.monotonic() - dataset_started, 3),
                    )
                )
                continue
            if spec.maintenance_kind == "akshare_forecast":
                date_start_text = (kind_start_dates or {}).get("publish_date", start_text)
                result = _publish_akshare_forecast(provider, mart, date_start_text, end_text)
                state["datasets"].setdefault(spec.name, {})["last_success_publish_date"] = end_text
                results.append(
                    _dataset_result(
                        spec.name,
                        spec.group,
                        "success",
                        partitions_written=result["partitions_written"],
                        rows=result["rows"],
                        elapsed_seconds=round(time.monotonic() - dataset_started, 3),
                    )
                )
                continue
            if spec.maintenance_kind == "event_news":
                result = _publish_event_news(provider, mart, end_text)
                errors = result.get("errors", [])
                status = "success" if not errors else ("partial" if result["rows"] else "failed")
                if status != "failed":
                    state["datasets"].setdefault(spec.name, {})["last_success_news_date"] = end_text
                results.append(
                    _dataset_result(
                        spec.name,
                        spec.group,
                        status,
                        partitions_written=result["partitions_written"],
                        rows=result["rows"],
                        sources_requested=result.get("sources_requested", 0),
                        sources_succeeded=result.get("sources_succeeded", 0),
                        mode=result.get("mode"),
                        historical_backfill=result.get("historical_backfill"),
                        errors=errors,
                        elapsed_seconds=round(time.monotonic() - dataset_started, 3),
                    )
                )
                continue
            if spec.maintenance_kind == "calendar":
                meta = _publish_partition(
                    provider,
                    mart,
                    spec,
                    partition_value="all",
                    start_date=start_text,
                    end_date=end_text,
                    quality_as_of=quality_as_of,
                )
                state["datasets"].setdefault(spec.name, {})["last_success_cal_date"] = end_text
                results.append(
                    _dataset_result(
                        spec.name,
                        spec.group,
                        "success" if meta.get("quality_status") not in QUALITY_RETRYABLE else "partial",
                        rows=meta["rows"],
                        partitions_written=1,
                        quality_status=meta.get("quality_status", QUALITY_OK),
                    )
                )
                continue
            if spec.maintenance_kind in {"snapshot", "snapshot_range", "member_by_index_snapshot"}:
                meta = _publish_partition(
                    provider,
                    mart,
                    spec,
                    partition_value=end_text,
                    start_date=start_text,
                    end_date=end_text,
                    quality_as_of=quality_as_of,
                )
                state["datasets"].setdefault(spec.name, {})["last_success_snapshot_date"] = end_text
                results.append(
                    _dataset_result(
                        spec.name,
                        spec.group,
                        "success" if meta.get("quality_status") not in QUALITY_RETRYABLE else "partial",
                        rows=meta["rows"],
                        partitions_written=1,
                        quality_status=meta.get("quality_status", QUALITY_OK),
                    )
                )
                continue
            if spec.maintenance_kind in {"trade_date", "member_by_index_trade_date"}:
                date_start_text = (kind_start_dates or {}).get("trade_date", start_text)
                if spec.group == "membership":
                    dates = [end_text]
                elif date_start_text not in trade_dates_by_start:
                    trade_dates_by_start[date_start_text] = trade_dates_between(provider, date_start_text, end_text)
                    dates = trade_dates_by_start[date_start_text]
                else:
                    dates = trade_dates_by_start[date_start_text]
            elif spec.maintenance_kind == "ann_date":
                date_start_text = (kind_start_dates or {}).get("ann_date", start_text)
                if date_start_text not in calendar_days_by_start:
                    calendar_days_by_start[date_start_text] = _date_range(date_start_text, end_text)
                dates = calendar_days_by_start[date_start_text]
            else:
                continue

            rows = 0
            written = 0
            cached = 0
            quality_retries = 0
            pending_empty: list[dict[str, Any]] = []
            retried_quality_issues: list[dict[str, Any]] = []
            quality_issues: list[dict[str, Any]] = []
            errors: list[dict[str, Any]] = []
            for date_text in dates:
                if not refresh:
                    health = _partition_health(mart, spec, date_text, as_of=quality_as_of)
                    if health["complete"]:
                        cached += 1
                        if health["quality_status"] == QUALITY_PENDING_EMPTY:
                            pending_empty.append(
                                {
                                    "date": date_text,
                                    "quality_status": health["quality_status"],
                                    "reason": health.get("reason", ""),
                                    "rows": health.get("rows"),
                                }
                            )
                        continue
                    if health["quality_status"] in QUALITY_RETRYABLE:
                        quality_retries += 1
                        retried_quality_issues.append(
                            {
                                "date": date_text,
                                "previous_quality_status": health["quality_status"],
                                "reason": health.get("reason", ""),
                                "rows": health.get("rows"),
                            }
                        )
                try:
                    meta = _publish_partition(provider, mart, spec, partition_value=date_text, quality_as_of=quality_as_of)
                    rows += int(meta["rows"])
                    written += 1
                    quality_status = meta.get("quality_status", QUALITY_OK)
                    if quality_status == QUALITY_PENDING_EMPTY:
                        pending_empty.append(
                            {
                                "date": date_text,
                                "quality_status": quality_status,
                                "reason": (meta.get("quality") or {}).get("reason", ""),
                                "rows": meta.get("rows"),
                            }
                        )
                    elif quality_status in QUALITY_RETRYABLE:
                        quality_issues.append(
                            {
                                "date": date_text,
                                "quality_status": quality_status,
                                "reason": (meta.get("quality") or {}).get("reason", ""),
                                "issues": (meta.get("quality") or {}).get("issues", []),
                                "rows": meta.get("rows"),
                            }
                        )
                    else:
                        state["datasets"].setdefault(spec.name, {})[f"last_success_{spec.date_param}"] = date_text
                except Exception as exc:  # noqa: BLE001 - report per partition
                    errors.append({"date": date_text, "error_type": type(exc).__name__, "error": str(exc)})
            blocking_quality = [item for item in quality_issues if item.get("quality_status") in QUALITY_RETRYABLE]
            if errors:
                status = "partial" if written or cached else "failed"
            elif blocking_quality:
                status = "partial" if written or cached else "failed"
            else:
                status = "success"
            results.append(
                _dataset_result(
                    spec.name,
                    spec.group,
                    status,
                    requested_partitions=len(dates),
                    partitions_written=written,
                    cached_partitions=cached,
                    quality_retries=quality_retries,
                    retried_quality_issues=retried_quality_issues,
                    pending_empty_partitions=pending_empty,
                    quality_issues=quality_issues,
                    rows=rows,
                    errors=errors,
                    elapsed_seconds=round(time.monotonic() - dataset_started, 3),
                )
            )
        except Exception as exc:  # noqa: BLE001 - continue other datasets
            results.append(
                _dataset_result(
                    spec.name,
                    spec.group,
                    "failed",
                    error_type=type(exc).__name__,
                    error=str(exc),
                    elapsed_seconds=round(time.monotonic() - dataset_started, 3),
                )
            )
    write_state(state, data_dir=data_dir, env_file=provider._env_file)
    report = {
        "schema": MAINTENANCE_REPORT_SCHEMA,
        "command": command,
        "profile": plan.profile,
        "generated_at": _now_iso(),
        "start_date": start_text,
        "end_date": end_text,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "datasets": results,
    }
    if write_report_file:
        path = write_report(report, data_dir=data_dir, env_file=provider._env_file)
        report["report_path"] = str(path)
    return report


def run_daily(
    provider: AShareProvider,
    plan: MaintenancePlan,
    as_of: str | date | datetime | None = None,
    end_date: str | date | datetime | None = None,
    data_dir: str | Path | None = None,
    lookback_days: int = 10,
    event_lookback_days: int = 30,
    refresh: bool = False,
    stock_pool: list[str] | tuple[str, ...] | None = None,
    max_stocks: int | None = None,
) -> dict[str, Any]:
    completed = _format_yyyymmdd(end_date) if end_date is not None else latest_completed_trade_date(provider, as_of=as_of)
    target_date = _parse_yyyymmdd(completed)
    start_text = (target_date - timedelta(days=lookback_days)).strftime("%Y%m%d")
    event_start_text = (target_date - timedelta(days=event_lookback_days)).strftime("%Y%m%d")
    report = run_backfill(
        provider,
        plan,
        start_date=start_text,
        end_date=completed,
        data_dir=data_dir,
        refresh=refresh,
        command="daily",
        write_report_file=False,
        kind_start_dates={"ann_date": event_start_text, "publish_date": event_start_text, "news_date": event_start_text},
        stock_pool=stock_pool,
        max_stocks=max_stocks,
    )
    report["as_of"] = _format_yyyymmdd(as_of or datetime.now())
    report["completed_trade_date"] = completed
    report["target_trade_date_source"] = "explicit_end_date" if end_date is not None else "previous_trade_date"
    report["lookback_days"] = lookback_days
    report["event_lookback_days"] = event_lookback_days
    report["event_start_date"] = event_start_text
    path = write_report(report, data_dir=data_dir, env_file=provider._env_file)
    report["report_path"] = str(path)
    return report


def _partition_check_summary(
    mart: MartStore,
    spec: DatasetSpec,
    values: list[str],
    quality_as_of: str,
    partition_label: str,
) -> dict[str, Any]:
    health_by_value = [(value, _partition_health(mart, spec, value, as_of=quality_as_of)) for value in values]
    missing = [value for value, health in health_by_value if health["quality_status"] == QUALITY_MISSING]
    pending_empty = [
        {
            "date": value,
            "rows": health.get("rows"),
            "reason": health.get("reason", ""),
        }
        for value, health in health_by_value
        if health["quality_status"] == QUALITY_PENDING_EMPTY
    ]
    quality_issues = [
        {
            "date": value,
            "quality_status": health["quality_status"],
            "rows": health.get("rows"),
            "reason": health.get("reason", ""),
            "issues": (health.get("quality") or {}).get("issues", []),
        }
        for value, health in health_by_value
        if health["quality_status"] in QUALITY_RETRYABLE
    ]
    required = len(values)
    available = required - len(missing)
    complete = sum(1 for _, health in health_by_value if health["complete"])
    return {
        "status": "complete" if not missing and not quality_issues else "needs_retry",
        "partition_key": partition_label,
        "required_partitions": required,
        "available_partitions": available,
        "complete_partitions": complete,
        "missing_partitions": missing,
        "missing_count": len(missing),
        "expected_count": required,
        "available_count": available,
        "coverage_ratio": round(available / required, 6) if required else None,
        "pending_empty_partitions": pending_empty,
        "quality_issues": quality_issues,
    }


def _latest_partition_values(mart: MartStore, dataset: str, partition_key: str, limit: int = 8) -> list[str]:
    root = mart.root / _safe_path_value(dataset)
    if not root.exists():
        return []
    prefix = f"{_safe_path_value(partition_key)}="
    values = [
        path.name.split("=", 1)[1]
        for path in root.iterdir()
        if path.is_dir() and path.name.startswith(prefix) and (path / "part.parquet").exists() and (path / "_meta.json").exists()
    ]
    return sorted(values)[-limit:]


def _financial_check_summary(mart: MartStore, spec: DatasetSpec) -> dict[str, Any]:
    periods = _latest_partition_values(mart, spec.name, "period", limit=8)
    rows = 0
    for period in periods:
        meta = mart.read_meta(spec.name, {"period": period}) or {}
        value = meta.get("rows")
        rows += int(value) if isinstance(value, int) else 0
    return {
        "status": "complete" if periods else "missing",
        "partition_key": "period",
        "check_strategy": "stock_pool_periods",
        "expected_count": None,
        "available_count": len(periods),
        "missing_count": None,
        "coverage_ratio": None,
        "available_partitions": len(periods),
        "recent_periods": periods,
        "rows": rows,
        "message": "财务数据按显式股票池/候选池增量维护，不默认全市场日扫。",
    }


def run_check(
    provider: AShareProvider,
    plan: MaintenancePlan,
    end_date: str | date | datetime,
    trade_days: int = 120,
    event_days: int = 180,
    data_dir: str | Path | None = None,
) -> dict[str, Any]:
    end_text = _format_yyyymmdd(end_date)
    quality_as_of = _quality_as_of_text(end_text)
    start_text = (_parse_yyyymmdd(end_text) - timedelta(days=trade_days * 2 + 30)).strftime("%Y%m%d")
    dates = trade_dates_between(provider, start_text, end_text)[-trade_days:]
    event_start_text = (_parse_yyyymmdd(end_text) - timedelta(days=max(event_days, 1) - 1)).strftime("%Y%m%d")
    event_dates = _date_range(event_start_text, end_text)
    event_iso_dates = [_iso_date_from_yyyymmdd(value) for value in event_dates]
    mart = MartStore(data_dir=data_dir, env_file=provider._env_file)
    datasets: list[dict[str, Any]] = []
    for item in plan.datasets:
        spec = item.spec
        result: dict[str, Any] = {"name": spec.name, "group": spec.group, "maintenance_kind": spec.maintenance_kind}
        if spec.maintenance_kind in {"trade_date", "member_by_index_trade_date"}:
            if spec.group == "membership":
                result.update(_partition_check_summary(mart, spec, [end_text], quality_as_of, "trade_date"))
                result["check_strategy"] = "latest_mapping_only"
                result["message"] = "题材/概念成分映射按目标交易日维护，不强制 120 日历史窗口。"
            else:
                result.update(_partition_check_summary(mart, spec, dates, quality_as_of, "trade_date"))
        elif spec.maintenance_kind == "calendar":
            summary = _partition_check_summary(mart, spec, ["all"], quality_as_of, "exchange")
            frame = mart.read_dataset(spec.name, _partition_for(spec, "all"))
            calendar_missing = []
            if hasattr(frame, "empty") and not frame.empty and "cal_date" in frame:
                available_calendar_dates = set(frame["cal_date"].dropna().astype(str).tolist())
                calendar_missing = [date_text for date_text in dates if date_text not in available_calendar_dates]
            elif dates:
                calendar_missing = list(dates)
            if calendar_missing:
                summary["status"] = "needs_retry"
                summary["calendar_missing_trade_dates"] = calendar_missing
            result.update(summary)
        elif spec.maintenance_kind in {"snapshot", "snapshot_range", "member_by_index_snapshot"}:
            result.update(_partition_check_summary(mart, spec, [end_text], quality_as_of, "snapshot_date"))
        elif spec.maintenance_kind in {"ann_date"}:
            result.update(_partition_check_summary(mart, spec, event_dates, quality_as_of, spec.date_param or "ann_date"))
            result["event_days"] = event_days
        elif spec.maintenance_kind in {"akshare_notice", "akshare_forecast"}:
            result.update(_partition_check_summary(mart, spec, event_iso_dates, quality_as_of, spec.date_param or "publish_date"))
            result["event_days"] = event_days
        elif spec.maintenance_kind == "event_news":
            recent_days = min(max(event_days, 1), max(spec.empty_lag_days + 1, 3))
            recent_start = (_parse_yyyymmdd(end_text) - timedelta(days=recent_days - 1)).strftime("%Y%m%d")
            recent_values = _iso_date_range(recent_start, end_text)
            summary = _partition_check_summary(mart, spec, recent_values, quality_as_of, spec.date_param or "news_date")
            available = int(summary["available_count"])
            summary.update(
                {
                    "status": "complete" if available > 0 and not summary["quality_issues"] else "missing_recent_snapshot",
                    "check_strategy": "current_visible_page",
                    "historical_backfill": False,
                    "message": "event_news 只检查最近可见资讯页快照，不承诺历史新闻回填。",
                }
            )
            result.update(summary)
        elif spec.maintenance_kind == "stock_pool_financial":
            result.update(_financial_check_summary(mart, spec))
        elif spec.maintenance_kind == "financial_disclosure_date":
            result.update(_financial_check_summary(mart, spec))
            result["message"] = "财报披露日期按最近报告期维护，不按交易日窗口维护。"
        else:
            result.update({"status": "not_checked", "message": f"未知维护类型：{spec.maintenance_kind}"})
        datasets.append(result)
    return {
        "schema": "ashare.maintenance_check.v1",
        "generated_at": _now_iso(),
        "profile": plan.profile,
        "end_date": end_text,
        "trade_days": trade_days,
        "event_days": event_days,
        "start_trade_date": dates[0] if dates else None,
        "end_trade_date": dates[-1] if dates else None,
        "datasets": datasets,
    }


def _scan_fallback_usage(mart: MartStore, dataset_names: set[str]) -> dict[str, Any]:
    usage: dict[str, dict[str, Any]] = {}
    samples: list[dict[str, Any]] = []
    for meta_path in mart.root.glob("*/*/_meta.json"):
        dataset = meta_path.parent.parent.name
        if dataset not in dataset_names:
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        source = meta.get("source") if isinstance(meta.get("source"), dict) else {}
        fallback_for = source.get("fallback_for")
        if not fallback_for:
            continue
        item = usage.setdefault(dataset, {"count": 0, "fallback_for": fallback_for})
        item["count"] += 1
        if len(samples) < 20:
            samples.append(
                {
                    "dataset": dataset,
                    "partition": meta.get("partition", {}),
                    "fallback_for": fallback_for,
                    "source_kind": source.get("source_kind") or source.get("kind"),
                    "rows": meta.get("rows"),
                    "published_at": meta.get("published_at"),
                }
            )
    return {"by_dataset": usage, "samples": samples}


def run_status_report(
    provider: AShareProvider,
    plan: MaintenancePlan,
    end_date: str | date | datetime,
    trade_days: int = 120,
    event_days: int = 30,
    data_dir: str | Path | None = None,
    write_report_file: bool = True,
) -> dict[str, Any]:
    check = run_check(
        provider,
        plan,
        end_date=end_date,
        trade_days=trade_days,
        event_days=event_days,
        data_dir=data_dir,
    )
    datasets = check["datasets"]
    blocking_groups = {"calendar", "identity", "stock_daily", "index", "industry"}
    blocking = [
        item
        for item in datasets
        if item.get("status") != "complete" and item.get("group") in blocking_groups
    ]
    warnings = [
        item
        for item in datasets
        if item.get("status") != "complete" and item.get("group") not in blocking_groups
    ]
    pending_empty = [
        {
            "dataset": item["name"],
            "group": item["group"],
            "partitions": item.get("pending_empty_partitions", []),
        }
        for item in datasets
        if item.get("pending_empty_partitions")
    ]
    quality_issues = [
        {
            "dataset": item["name"],
            "group": item["group"],
            "issues": item.get("quality_issues", []),
        }
        for item in datasets
        if item.get("quality_issues")
    ]
    gaps = [
        {
            "dataset": item["name"],
            "group": item["group"],
            "status": item.get("status"),
            "missing_count": item.get("missing_count"),
            "expected_count": item.get("expected_count"),
            "available_count": item.get("available_count"),
            "coverage_ratio": item.get("coverage_ratio"),
            "missing_sample": (item.get("missing_partitions") or [])[:10],
            "message": item.get("message"),
        }
        for item in datasets
        if item.get("missing_count") not in {0, None} or item.get("status") != "complete"
    ]
    mart = MartStore(data_dir=data_dir, env_file=provider._env_file)
    fallback_usage = _scan_fallback_usage(mart, {item.spec.name for item in plan.datasets})
    coverage_ratios = [
        float(item["coverage_ratio"])
        for item in datasets
        if item.get("coverage_ratio") is not None
    ]
    report = {
        "schema": "ashare.daily_status_report.v1",
        "command": "status-report",
        "generated_at": _now_iso(),
        "profile": plan.profile,
        "end_date": check["end_date"],
        "trade_days": trade_days,
        "event_days": event_days,
        "window": {
            "start_trade_date": check.get("start_trade_date"),
            "end_trade_date": check.get("end_trade_date"),
        },
        "summary": {
            "datasets_total": len(datasets),
            "complete": sum(1 for item in datasets if item.get("status") == "complete"),
            "blocking_count": len(blocking),
            "warning_count": len(warnings),
            "pending_empty_count": sum(len(item["partitions"]) for item in pending_empty),
            "quality_issue_count": sum(len(item["issues"]) for item in quality_issues),
            "min_coverage_ratio": min(coverage_ratios) if coverage_ratios else None,
        },
        "analysis_ready": {
            "ready": not blocking,
            "level": "ready" if not blocking and not warnings else ("warning" if not blocking else "blocked"),
            "blocking_datasets": [
                {
                    "dataset": item["name"],
                    "group": item["group"],
                    "status": item.get("status"),
                    "missing_count": item.get("missing_count"),
                    "missing_sample": (item.get("missing_partitions") or [])[:10],
                }
                for item in blocking
            ],
            "warning_datasets": [
                {
                    "dataset": item["name"],
                    "group": item["group"],
                    "status": item.get("status"),
                    "missing_count": item.get("missing_count"),
                    "missing_sample": (item.get("missing_partitions") or [])[:10],
                    "message": item.get("message"),
                }
                for item in warnings
            ],
        },
        "coverage_gaps": gaps,
        "pending_empty_partitions": pending_empty,
        "quality_issues": quality_issues,
        "fallback_usage": fallback_usage,
        "schedule_recommendation": {
            "after_close_initial": {
                "window": "16:00-17:00",
                "purpose": "行情、涨跌停、指数、行业初版；当日未同步数据允许 pending_empty。",
                "command": f"ashare maintain daily --as-of {check['end_date']} --end-date {check['end_date']} --profile {plan.profile} --lookback-days 10 --event-lookback-days 7",
            },
            "evening_repair": {
                "window": "20:00-22:30",
                "purpose": "公告、龙虎榜、资金流、业绩预告、新闻修正版；建议 refresh 重试当日空分区。",
                "command": f"ashare maintain daily --as-of {check['end_date']} --end-date {check['end_date']} --profile {plan.profile} --lookback-days 10 --event-lookback-days {event_days} --refresh",
            },
        },
        "check": check,
    }
    if write_report_file:
        path = write_report(report, data_dir=data_dir, env_file=provider._env_file)
        report["report_path"] = str(path)
    return report


def build_provider(
    token: str | None = None,
    proxy_url: str | None = None,
    env_file: str = ".env",
    points: int | None = None,
    data_dir: str | Path | None = None,
) -> AShareProvider:
    return AShareProvider(token=token, proxy_url=proxy_url, env_file=env_file, points=points, data_dir=data_dir)


def require_plan_has_datasets(plan: MaintenancePlan) -> None:
    if not plan.datasets:
        raise MaintenanceError("维护计划为空。请先运行 access-audit，或检查当前积分/权限配置。")


def local_store_for_mart(data_dir: str | Path | None = None, env_file: str | Path = ".env") -> LocalDataStore:
    return LocalDataStore(data_dir=data_dir, env_file=env_file)


__all__ = [
    "ACCESS_ALLOWED",
    "ACCESS_DENIED",
    "ACCESS_SCHEMA",
    "ACCESS_UNVERIFIED",
    "AccessDecision",
    "DatasetSpec",
    "MaintenanceError",
    "MaintenancePlan",
    "MartStore",
    "PlanDataset",
    "RequestVariant",
    "access_catalog_path",
    "audit_access",
    "build_maintenance_plan",
    "build_provider",
    "default_dataset_specs",
    "load_access_catalog",
    "load_state",
    "maintenance_dir",
    "metadata_access_decision",
    "reports_dir",
    "require_plan_has_datasets",
    "run_backfill",
    "run_check",
    "run_daily",
    "run_status_report",
    "select_dataset_specs",
    "state_path",
    "trade_dates_between",
    "write_access_catalog",
    "write_report",
    "write_state",
]

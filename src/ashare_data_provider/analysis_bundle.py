from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .maintenance import MaintenancePlan, MartStore


ANALYSIS_BUNDLE_SCHEMA = "ashare.analysis_bundle.v1"


class AnalysisBundleError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _format_yyyymmdd(value: str | date | datetime) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y%m%d")
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    text = str(value).strip()
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return text.replace("-", "")
    return text


def _records(value: Any, limit: int = 0) -> list[dict[str, Any]]:
    if value is None:
        return []
    if hasattr(value, "to_dict"):
        rows = value.to_dict("records")
    elif isinstance(value, list):
        rows = value
    else:
        return []
    if limit > 0:
        rows = rows[:limit]
    return [row for row in rows if isinstance(row, dict)]


def _empty_frame(columns: list[str] | None = None) -> Any:
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover
        raise AnalysisBundleError("analysis bundle 需要 pandas") from exc
    return pd.DataFrame(columns=columns or [])


def _frame_shape(value: Any) -> dict[str, int]:
    rows = int(len(value)) if hasattr(value, "__len__") else 0
    columns = int(len(value.columns)) if hasattr(value, "columns") else 0
    return {"rows": rows, "columns": columns}


def _clean_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and value != value:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return _clean_value(value.item())
        except (TypeError, ValueError):
            pass
    if isinstance(value, dict):
        return {str(key): _clean_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean_value(item) for item in value]
    return str(value)


def _trade_dates_from_mart(mart: MartStore, end_date: str, trade_days: int) -> tuple[list[str], list[dict[str, Any]]]:
    data_gaps: list[dict[str, Any]] = []
    calendar = mart.read_dataset("trade_cal", {"exchange": "SSE"})
    if not hasattr(calendar, "empty") or calendar.empty:
        data_gaps.append({"dataset": "trade_cal", "status": "missing", "message": "mart 缺少交易日历"})
        return [], data_gaps
    if "cal_date" not in calendar or "is_open" not in calendar:
        data_gaps.append({"dataset": "trade_cal", "status": "invalid", "message": "交易日历缺少 cal_date/is_open"})
        return [], data_gaps
    calendar = calendar.copy()
    calendar["cal_date"] = calendar["cal_date"].astype(str)
    open_days = calendar[
        (calendar["cal_date"] <= end_date)
        & (calendar["is_open"].astype(str).str.lower().isin(["1", "1.0", "true"]))
    ]["cal_date"].sort_values().tolist()
    dates = open_days[-trade_days:]
    if len(dates) < trade_days:
        data_gaps.append(
            {
                "dataset": "trade_cal",
                "status": "partial",
                "message": f"交易日历只找到 {len(dates)} 个交易日，少于请求的 {trade_days} 个",
            }
        )
    return dates, data_gaps


def _read_window(mart: MartStore, dataset: str, dates: list[str], columns: list[str] | None = None) -> tuple[Any, list[str]]:
    partitions = [{"trade_date": date_text} for date_text in dates]
    frame = mart.read_partitions(dataset, partitions)
    if columns and hasattr(frame, "columns") and not frame.empty:
        existing_columns = [column for column in columns if column in frame.columns]
        frame = frame[existing_columns]
    available_dates: list[str] = []
    if hasattr(frame, "empty") and not frame.empty and "trade_date" in frame:
        available_dates = sorted(str(item) for item in frame["trade_date"].dropna().astype(str).unique().tolist())
    missing = [date_text for date_text in dates if date_text not in set(available_dates)]
    return frame, missing


def _read_window_if_active(
    mart: MartStore,
    active: set[str],
    dataset: str,
    dates: list[str],
    columns: list[str] | None = None,
) -> tuple[Any, list[str]]:
    if dataset not in active:
        return _empty_frame(columns), []
    return _read_window(mart, dataset, dates, columns=columns)


def _read_today(mart: MartStore, dataset: str, trade_date: str, columns: list[str] | None = None) -> tuple[Any, bool]:
    frame = mart.read_dataset(dataset, {"trade_date": trade_date})
    if columns and hasattr(frame, "columns") and not frame.empty:
        existing_columns = [column for column in columns if column in frame.columns]
        frame = frame[existing_columns]
    exists = mart.exists(dataset, {"trade_date": trade_date})
    return frame, exists


def _read_today_if_active(
    mart: MartStore,
    active: set[str],
    dataset: str,
    trade_date: str,
    columns: list[str] | None = None,
) -> tuple[Any, bool]:
    if dataset not in active:
        return _empty_frame(columns), True
    return _read_today(mart, dataset, trade_date, columns=columns)


def _natural_dates(end_date: str, days: int) -> list[str]:
    end = datetime.strptime(_format_yyyymmdd(end_date), "%Y%m%d").date()
    return [(end - timedelta(days=offset)).strftime("%Y%m%d") for offset in range(days - 1, -1, -1)]


def _iso_natural_dates(end_date: str, days: int) -> list[str]:
    end = datetime.strptime(_format_yyyymmdd(end_date), "%Y%m%d").date()
    return [(end - timedelta(days=offset)).isoformat() for offset in range(days - 1, -1, -1)]


def _read_date_partitions(mart: MartStore, dataset: str, partition_key: str, values: list[str]) -> tuple[Any, list[str]]:
    partitions = [{partition_key: value} for value in values]
    frame = mart.read_partitions(dataset, partitions)
    available: set[str] = set()
    if hasattr(frame, "empty") and not frame.empty and partition_key in frame:
        available = set(str(item) for item in frame[partition_key].dropna().astype(str).unique().tolist())
    else:
        available = {value for value in values if mart.exists(dataset, {partition_key: value})}
    return frame, [value for value in values if value not in available and not mart.exists(dataset, {partition_key: value})]


def _read_date_partitions_if_active(
    mart: MartStore,
    active: set[str],
    dataset: str,
    partition_key: str,
    values: list[str],
) -> tuple[Any, list[str]]:
    if dataset not in active:
        return _empty_frame(), []
    return _read_date_partitions(mart, dataset, partition_key, values)


def _latest_partition_value(mart: MartStore, dataset: str, partition_key: str) -> str | None:
    root = mart.root / dataset
    if not root.exists():
        return None
    prefix = f"{partition_key}="
    values = [
        path.name.split("=", 1)[1]
        for path in root.iterdir()
        if path.is_dir() and path.name.startswith(prefix) and (path / "part.parquet").exists()
    ]
    return sorted(values)[-1] if values else None


def _read_latest_partition_dataset(
    mart: MartStore,
    active: set[str],
    dataset: str,
    partition_key: str,
    columns: list[str] | None = None,
) -> tuple[Any, bool, str | None]:
    if dataset not in active:
        return _empty_frame(columns), True, None
    value = _latest_partition_value(mart, dataset, partition_key)
    if value is None:
        return _empty_frame(columns), False, None
    frame = mart.read_dataset(dataset, {partition_key: value})
    if columns and hasattr(frame, "columns") and not frame.empty:
        existing_columns = [column for column in columns if column in frame.columns]
        frame = frame[existing_columns]
    return frame, True, value


def _read_recent_period_dataset(mart: MartStore, dataset: str, max_periods: int = 8) -> Any:
    root = mart.root / dataset
    if not root.exists():
        try:
            import pandas as pd
        except ImportError as exc:  # pragma: no cover
            raise AnalysisBundleError("读取财务 mart 需要 pandas") from exc
        return pd.DataFrame()
    periods = sorted(
        path.parent.name.split("=", 1)[1]
        for path in root.glob("period=*/part.parquet")
        if "=" in path.parent.name
    )[-max_periods:]
    return mart.read_partitions(dataset, [{"period": period} for period in periods])


def _read_recent_period_dataset_if_active(mart: MartStore, active: set[str], dataset: str, max_periods: int = 8) -> Any:
    if dataset not in active:
        return _empty_frame()
    return _read_recent_period_dataset(mart, dataset, max_periods=max_periods)


def _coverage(
    dataset: str,
    partition_key: str,
    expected_values: list[str],
    missing: list[str],
    check_strategy: str = "partition_window",
    historical_backfill: bool = True,
) -> dict[str, Any]:
    expected_count = len(expected_values)
    missing_count = len(missing)
    available_count = expected_count - missing_count
    return {
        "dataset": dataset,
        "partition_key": partition_key,
        "check_strategy": check_strategy,
        "historical_backfill": historical_backfill,
        "expected_count": expected_count,
        "available_count": available_count,
        "missing_count": missing_count,
        "coverage_ratio": round(available_count / expected_count, 6) if expected_count else None,
        "missing": missing,
    }


def _top_records(frame: Any, by: str, columns: list[str], limit: int = 20, ascending: bool = False) -> list[dict[str, Any]]:
    if not hasattr(frame, "empty") or frame.empty or by not in frame:
        return []
    selected_columns = [column for column in columns if column in frame]
    if not selected_columns:
        selected_columns = list(frame.columns)
    ranked = frame.sort_values(by=by, ascending=ascending).head(limit)
    return [_clean_value(row) for row in ranked[selected_columns].to_dict("records")]


def _price_volume_features(daily: Any, daily_basic: Any, trade_dates: list[str]) -> dict[str, Any]:
    if not hasattr(daily, "empty") or daily.empty or "ts_code" not in daily:
        return {"summary": {"stocks": 0}, "top_amount": [], "top_pct_chg": []}

    latest_date = trade_dates[-1] if trade_dates else None
    latest = daily[daily["trade_date"].astype(str) == latest_date].copy() if latest_date and "trade_date" in daily else daily.copy()
    merged = latest
    if hasattr(daily_basic, "empty") and not daily_basic.empty and {"ts_code", "trade_date"}.issubset(daily_basic.columns):
        latest_basic = daily_basic[daily_basic["trade_date"].astype(str) == latest_date].copy() if latest_date else daily_basic.copy()
        merged = latest.merge(latest_basic, on=["ts_code", "trade_date"], how="left", suffixes=("", "_basic"))

    summary = {
        "latest_trade_date": latest_date,
        "stocks": int(len(latest)),
        "up_count": int((latest["pct_chg"] > 0).sum()) if "pct_chg" in latest else None,
        "down_count": int((latest["pct_chg"] < 0).sum()) if "pct_chg" in latest else None,
        "total_amount": float(latest["amount"].sum()) if "amount" in latest else None,
    }
    return {
        "summary": _clean_value(summary),
        "top_amount": _top_records(merged, "amount", ["ts_code", "trade_date", "close", "pct_chg", "amount", "turnover_rate"], limit=20),
        "top_pct_chg": _top_records(merged, "pct_chg", ["ts_code", "trade_date", "close", "pct_chg", "amount", "turnover_rate"], limit=20),
    }


def _identity_features(stock_basic: Any, snapshot_date: str | None = None) -> dict[str, Any]:
    if not hasattr(stock_basic, "empty") or stock_basic.empty:
        return {"summary": {"stocks": 0, "snapshot_date": snapshot_date}, "sample": []}

    def counts(column: str, limit: int = 20) -> list[dict[str, Any]]:
        if column not in stock_basic:
            return []
        result = stock_basic[column].fillna("").astype(str).value_counts().head(limit)
        return [{"value": _clean_value(index), "count": int(value)} for index, value in result.items()]

    sample_columns = [
        column
        for column in ["ts_code", "symbol", "name", "area", "industry", "market", "exchange", "list_status", "list_date", "is_hs"]
        if column in stock_basic
    ]
    return {
        "summary": {
            "snapshot_date": snapshot_date,
            "stocks": int(len(stock_basic)),
            "industry_count": int(stock_basic["industry"].nunique()) if "industry" in stock_basic else None,
            "market_distribution": counts("market"),
            "exchange_distribution": counts("exchange"),
            "list_status_distribution": counts("list_status"),
            "industry_top": counts("industry"),
        },
        "sample": [_clean_value(item) for item in _records(stock_basic[sample_columns] if sample_columns else stock_basic, limit=50)],
    }


def _limit_features(limit_pool: Any, limit_step: Any, concept_strength: Any) -> dict[str, Any]:
    stats: dict[str, Any] = {"limit_up": 0, "limit_down": 0, "broken_limit": 0}
    if hasattr(limit_pool, "empty") and not limit_pool.empty:
        if "limit" in limit_pool:
            counts = limit_pool["limit"].astype(str).value_counts().to_dict()
            stats["limit_up"] = int(counts.get("U", 0))
            stats["limit_down"] = int(counts.get("D", 0))
            stats["broken_limit"] = int(counts.get("Z", 0))
            stats["type_column"] = "limit"
        else:
            stats["records"] = int(len(limit_pool))
    ladder_records = _records(limit_step, limit=50)
    concept_records = _records(concept_strength, limit=30)
    return {
        "stats": stats,
        "ladder_sample": [_clean_value(item) for item in ladder_records],
        "concept_strength_sample": [_clean_value(item) for item in concept_records],
    }


def _moneyflow_top(frame: Any) -> list[dict[str, Any]]:
    for column in ["net_mf_amount", "net_amount", "main_net_amt", "buy_elg_amount", "主力净流入-净额"]:
        if hasattr(frame, "empty") and not frame.empty and column in frame:
            return _top_records(frame, column, list(frame.columns), limit=20)
    return []


def _moneyflow_features(stock_flows: dict[str, Any], industry_flows: dict[str, Any], concept_flow: Any) -> dict[str, Any]:
    return {
        "stock_top_by_source": {name: _moneyflow_top(frame) for name, frame in stock_flows.items()},
        "industry_sample_by_source": {
            name: [_clean_value(item) for item in _records(frame, limit=30)]
            for name, frame in industry_flows.items()
        },
        "concept_sample": [_clean_value(item) for item in _records(concept_flow, limit=30)],
    }


def _market_features(index_daily: Any, index_dailybasic: Any, sw_daily: Any, ci_daily: Any, trade_dates: list[str]) -> dict[str, Any]:
    latest_date = trade_dates[-1] if trade_dates else None

    def latest(frame: Any) -> Any:
        if not hasattr(frame, "empty") or frame.empty or latest_date is None or "trade_date" not in frame:
            return frame
        return frame[frame["trade_date"].astype(str) == latest_date]

    latest_index = latest(index_daily)
    latest_sw = latest(sw_daily)
    latest_ci = latest(ci_daily)
    return {
        "index_latest": [_clean_value(item) for item in _records(latest_index, limit=30)],
        "index_dailybasic_latest": [_clean_value(item) for item in _records(latest(index_dailybasic), limit=30)],
        "sw_top_pct_chg": _top_records(latest_sw, "pct_chg", list(latest_sw.columns) if hasattr(latest_sw, "columns") else [], limit=20),
        "ci_top_pct_chg": _top_records(latest_ci, "pct_chg", list(latest_ci.columns) if hasattr(latest_ci, "columns") else [], limit=20),
    }


def _trading_features(top_list: Any, margin_detail: Any, kpl_list: Any, limit_list_ths: Any) -> dict[str, Any]:
    return {
        "top_list_sample": [_clean_value(item) for item in _records(top_list, limit=50)],
        "margin_detail_sample": [_clean_value(item) for item in _records(margin_detail, limit=50)],
        "kpl_sample": [_clean_value(item) for item in _records(kpl_list, limit=50)],
        "limit_list_ths_sample": [_clean_value(item) for item in _records(limit_list_ths, limit=50)],
    }


def _membership_features(frames: dict[str, Any], partition_values: dict[str, str | None]) -> dict[str, Any]:
    member_datasets = [
        "index_member_all",
        "ci_index_member",
        "ths_member",
        "dc_member",
        "tdx_member",
        "kpl_concept_cons",
        "index_weight",
    ]
    index_datasets = ["index_classify", "ths_index", "dc_index", "tdx_index"]
    summary = {}
    for name, frame in frames.items():
        item = {
            **_frame_shape(frame),
            "partition": partition_values.get(name),
        }
        if hasattr(frame, "empty") and not frame.empty and "trade_date" in frame:
            item["latest_trade_date"] = str(frame["trade_date"].dropna().astype(str).max())
        summary[name] = item

    def sample(name: str, frame: Any) -> list[dict[str, Any]]:
        if not hasattr(frame, "empty") or frame.empty:
            return []
        if name == "index_weight" and "trade_date" in frame:
            latest_trade_date = str(frame["trade_date"].dropna().astype(str).max())
            frame = frame[frame["trade_date"].astype(str) == latest_trade_date]
        preferred = [
            column
            for column in [
                "_driver_dataset",
                "_driver_ts_code",
                "_driver_name",
                "index_code",
                "ts_code",
                "con_code",
                "name",
                "industry_name",
                "idx_type",
                "trade_date",
                "weight",
            ]
            if column in frame
        ]
        selected = frame[preferred] if preferred else frame
        return [_clean_value(item) for item in _records(selected, limit=50)]

    return {
        "summary": summary,
        "index_samples": {name: sample(name, frames[name]) for name in index_datasets if name in frames},
        "member_samples": {name: sample(name, frames[name]) for name in member_datasets if name in frames},
    }


def build_market_analysis_bundle(
    as_of: str | date | datetime,
    trade_days: int = 120,
    data_dir: str | Path | None = None,
    env_file: str | Path = ".env",
    include_raw_samples: bool = False,
    plan: MaintenancePlan | None = None,
    event_days: int = 180,
) -> dict[str, Any]:
    if trade_days <= 0:
        raise AnalysisBundleError("trade_days must be positive")
    if event_days <= 0:
        raise AnalysisBundleError("event_days must be positive")
    if plan is None:
        raise AnalysisBundleError("analysis bundle 需要传入权限过滤后的 maintenance plan")
    end_text = _format_yyyymmdd(as_of)
    mart = MartStore(data_dir=data_dir, env_file=env_file)
    active = {item.spec.name for item in plan.datasets}
    trade_dates, data_gaps = _trade_dates_from_mart(mart, end_text, trade_days)
    completed = trade_dates[-1] if trade_dates else end_text
    trade_cal = mart.read_dataset("trade_cal", {"exchange": "SSE"}) if "trade_cal" in active else _empty_frame()

    stock_basic, stock_basic_exists, stock_basic_snapshot = _read_latest_partition_dataset(
        mart,
        active,
        "stock_basic",
        "snapshot_date",
        columns=["ts_code", "symbol", "name", "area", "industry", "market", "exchange", "list_status", "list_date", "is_hs"],
    )
    if "stock_basic" in active and not stock_basic_exists:
        data_gaps.append({"dataset": "stock_basic", "status": "missing_snapshot", "partition_key": "snapshot_date"})

    daily, daily_missing = _read_window_if_active(
        mart,
        active,
        "daily",
        trade_dates,
        columns=["ts_code", "trade_date", "open", "high", "low", "close", "pct_chg", "vol", "amount"],
    )
    daily_basic, daily_basic_missing = _read_window_if_active(
        mart,
        active,
        "daily_basic",
        trade_dates,
        columns=["ts_code", "trade_date", "turnover_rate", "volume_ratio", "pe_ttm", "pb", "total_mv", "circ_mv"],
    )
    adj_factor, adj_missing = _read_window_if_active(mart, active, "adj_factor", trade_dates, columns=["ts_code", "trade_date", "adj_factor"])
    stk_limit, limit_price_missing = _read_window_if_active(mart, active, "stk_limit", trade_dates, columns=["ts_code", "trade_date", "up_limit", "down_limit"])
    index_daily, index_daily_missing = _read_window_if_active(mart, active, "index_daily", trade_dates)
    index_dailybasic, index_dailybasic_missing = _read_window_if_active(mart, active, "index_dailybasic", trade_dates)
    sw_daily, sw_daily_missing = _read_window_if_active(mart, active, "sw_daily", trade_dates)
    ci_daily, ci_daily_missing = _read_window_if_active(mart, active, "ci_daily", trade_dates)

    for dataset, missing in [
        ("daily", daily_missing),
        ("daily_basic", daily_basic_missing),
        ("adj_factor", adj_missing),
        ("stk_limit", limit_price_missing),
        ("index_daily", index_daily_missing),
        ("index_dailybasic", index_dailybasic_missing),
        ("sw_daily", sw_daily_missing),
        ("ci_daily", ci_daily_missing),
    ]:
        if dataset in active and missing:
            data_gaps.append({"dataset": dataset, "status": "missing_partitions", "missing": missing})

    moneyflow, moneyflow_exists = _read_today_if_active(mart, active, "moneyflow", completed)
    moneyflow_dc, moneyflow_dc_exists = _read_today_if_active(mart, active, "moneyflow_dc", completed)
    moneyflow_ths, moneyflow_ths_exists = _read_today_if_active(mart, active, "moneyflow_ths", completed)
    moneyflow_ind_ths, moneyflow_ind_ths_exists = _read_today_if_active(mart, active, "moneyflow_ind_ths", completed)
    moneyflow_ind_dc, moneyflow_ind_dc_exists = _read_today_if_active(mart, active, "moneyflow_ind_dc", completed)
    moneyflow_cnt, moneyflow_cnt_exists = _read_today_if_active(mart, active, "moneyflow_cnt_ths", completed)
    limit_pool, limit_pool_exists = _read_today_if_active(mart, active, "limit_list_d", completed)
    limit_step, limit_step_exists = _read_today_if_active(mart, active, "limit_step", completed)
    concept_strength, concept_strength_exists = _read_today_if_active(mart, active, "limit_cpt_list", completed)
    top_list, top_list_exists = _read_today_if_active(mart, active, "top_list", completed)
    margin_detail, margin_detail_exists = _read_today_if_active(mart, active, "margin_detail", completed)
    kpl_list, kpl_list_exists = _read_today_if_active(mart, active, "kpl_list", completed)
    limit_list_ths, limit_list_ths_exists = _read_today_if_active(mart, active, "limit_list_ths", completed)
    index_classify, index_classify_exists, index_classify_partition = _read_latest_partition_dataset(mart, active, "index_classify", "snapshot_date")
    index_member_all, index_member_all_exists, index_member_all_partition = _read_latest_partition_dataset(mart, active, "index_member_all", "snapshot_date")
    ci_index_member, ci_index_member_exists, ci_index_member_partition = _read_latest_partition_dataset(mart, active, "ci_index_member", "snapshot_date")
    ths_index, ths_index_exists, ths_index_partition = _read_latest_partition_dataset(mart, active, "ths_index", "snapshot_date")
    ths_member, ths_member_exists, ths_member_partition = _read_latest_partition_dataset(mart, active, "ths_member", "snapshot_date")
    tdx_index, tdx_index_exists = _read_today_if_active(mart, active, "tdx_index", completed)
    index_weight, index_weight_exists, index_weight_partition = _read_latest_partition_dataset(mart, active, "index_weight", "snapshot_date")
    dc_index, dc_index_exists = _read_today_if_active(mart, active, "dc_index", completed)
    dc_member, dc_member_exists = _read_today_if_active(mart, active, "dc_member", completed)
    tdx_member, tdx_member_exists = _read_today_if_active(mart, active, "tdx_member", completed)
    kpl_concept_cons, kpl_concept_cons_exists = _read_today_if_active(mart, active, "kpl_concept_cons", completed)

    event_dates = _natural_dates(completed, event_days)
    notice_dates = _iso_natural_dates(completed, event_days)
    news_days = min(event_days, 3)
    news_dates = _iso_natural_dates(completed, news_days)
    notices, notices_missing = _read_date_partitions_if_active(mart, active, "a_stock_notice", "publish_date", notice_dates)
    forecasts, forecast_missing = _read_date_partitions_if_active(mart, active, "earnings_forecast", "publish_date", notice_dates)
    news, news_missing = _read_date_partitions_if_active(mart, active, "event_news", "news_date", news_dates)
    income = _read_recent_period_dataset_if_active(mart, active, "income")
    balancesheet = _read_recent_period_dataset_if_active(mart, active, "balancesheet")
    cashflow = _read_recent_period_dataset_if_active(mart, active, "cashflow")
    express = _read_recent_period_dataset_if_active(mart, active, "express")
    fina_indicator = _read_recent_period_dataset_if_active(mart, active, "fina_indicator")
    fina_mainbz = _read_recent_period_dataset_if_active(mart, active, "fina_mainbz")
    dividend = _read_recent_period_dataset_if_active(mart, active, "dividend")
    fina_audit = _read_recent_period_dataset_if_active(mart, active, "fina_audit")
    disclosure_date = _read_recent_period_dataset_if_active(mart, active, "disclosure_date")

    for dataset, exists in [
        ("moneyflow", moneyflow_exists),
        ("moneyflow_dc", moneyflow_dc_exists),
        ("moneyflow_ths", moneyflow_ths_exists),
        ("moneyflow_ind_ths", moneyflow_ind_ths_exists),
        ("moneyflow_ind_dc", moneyflow_ind_dc_exists),
        ("moneyflow_cnt_ths", moneyflow_cnt_exists),
        ("limit_list_d", limit_pool_exists),
        ("limit_step", limit_step_exists),
        ("limit_cpt_list", concept_strength_exists),
        ("top_list", top_list_exists),
        ("margin_detail", margin_detail_exists),
        ("kpl_list", kpl_list_exists),
        ("limit_list_ths", limit_list_ths_exists),
        ("dc_index", dc_index_exists),
        ("tdx_index", tdx_index_exists),
        ("dc_member", dc_member_exists),
        ("tdx_member", tdx_member_exists),
        ("kpl_concept_cons", kpl_concept_cons_exists),
    ]:
        if dataset in active and not exists:
            data_gaps.append({"dataset": dataset, "status": "missing_partition", "partition": {"trade_date": completed}})
    for dataset, exists, partition in [
        ("index_classify", index_classify_exists, index_classify_partition),
        ("index_member_all", index_member_all_exists, index_member_all_partition),
        ("ci_index_member", ci_index_member_exists, ci_index_member_partition),
        ("ths_index", ths_index_exists, ths_index_partition),
        ("ths_member", ths_member_exists, ths_member_partition),
        ("index_weight", index_weight_exists, index_weight_partition),
    ]:
        if dataset in active and not exists:
            data_gaps.append({"dataset": dataset, "status": "missing_snapshot", "partition_key": "snapshot_date"})

    coverage: dict[str, Any] = {}
    for dataset, missing in [
        ("a_stock_notice", notices_missing),
        ("earnings_forecast", forecast_missing),
    ]:
        if dataset in active:
            item = _coverage(dataset, "publish_date", notice_dates, missing)
            coverage[dataset] = item
            if missing:
                data_gaps.append({"dataset": dataset, "status": "missing_partitions", **item})
    if "event_news" in active:
        item = _coverage(
            "event_news",
            "news_date",
            news_dates,
            news_missing,
            check_strategy="current_visible_page",
            historical_backfill=False,
        )
        coverage["event_news"] = item
        if news_missing:
            data_gaps.append({"dataset": "event_news", "status": "missing_recent_visible_partitions", **item})

    frames_by_dataset = {
        "trade_cal": trade_cal,
        "stock_basic": stock_basic,
        "daily": daily,
        "daily_basic": daily_basic,
        "adj_factor": adj_factor,
        "stk_limit": stk_limit,
        "index_daily": index_daily,
        "index_dailybasic": index_dailybasic,
        "sw_daily": sw_daily,
        "ci_daily": ci_daily,
        "moneyflow": moneyflow,
        "moneyflow_dc": moneyflow_dc,
        "moneyflow_ths": moneyflow_ths,
        "moneyflow_ind_ths": moneyflow_ind_ths,
        "moneyflow_ind_dc": moneyflow_ind_dc,
        "moneyflow_cnt_ths": moneyflow_cnt,
        "limit_list_d": limit_pool,
        "limit_step": limit_step,
        "limit_cpt_list": concept_strength,
        "top_list": top_list,
        "margin_detail": margin_detail,
        "kpl_list": kpl_list,
        "limit_list_ths": limit_list_ths,
        "index_classify": index_classify,
        "index_member_all": index_member_all,
        "ci_index_member": ci_index_member,
        "ths_index": ths_index,
        "ths_member": ths_member,
        "dc_index": dc_index,
        "dc_member": dc_member,
        "tdx_index": tdx_index,
        "tdx_member": tdx_member,
        "kpl_concept_cons": kpl_concept_cons,
        "index_weight": index_weight,
        "a_stock_notice": notices,
        "earnings_forecast": forecasts,
        "event_news": news,
        "income": income,
        "balancesheet": balancesheet,
        "cashflow": cashflow,
        "express": express,
        "fina_indicator": fina_indicator,
        "fina_mainbz": fina_mainbz,
        "dividend": dividend,
        "fina_audit": fina_audit,
        "disclosure_date": disclosure_date,
    }

    for dataset in ["income", "balancesheet", "cashflow", "express", "fina_indicator", "fina_mainbz", "dividend", "fina_audit", "disclosure_date"]:
        if dataset in active and _frame_shape(frames_by_dataset[dataset])["rows"] == 0:
            data_gaps.append(
                {
                    "dataset": dataset,
                    "status": "missing_recent_periods",
                    "message": "财务数据按显式股票池维护；bundle 只读取已落库 period 分区。",
                }
            )

    bundle: dict[str, Any] = {
        "schema": ANALYSIS_BUNDLE_SCHEMA,
        "generated_at": _now_iso(),
        "as_of": end_text,
        "window": {
            "trade_days": trade_days,
            "start_trade_date": trade_dates[0] if trade_dates else None,
            "end_trade_date": completed,
        },
        "datasets": {name: _frame_shape(frame) for name, frame in frames_by_dataset.items() if name in active},
        "coverage": coverage,
        "features": {
            "identity": _identity_features(stock_basic, snapshot_date=stock_basic_snapshot),
            "price_volume": _price_volume_features(daily, daily_basic, trade_dates),
            "market": _market_features(index_daily, index_dailybasic, sw_daily, ci_daily, trade_dates),
            "moneyflow": _moneyflow_features(
                {
                    name: frame
                    for name, frame in {
                        "moneyflow": moneyflow,
                        "moneyflow_dc": moneyflow_dc,
                        "moneyflow_ths": moneyflow_ths,
                    }.items()
                    if name in active
                },
                {
                    name: frame
                    for name, frame in {
                        "moneyflow_ind_ths": moneyflow_ind_ths,
                        "moneyflow_ind_dc": moneyflow_ind_dc,
                    }.items()
                    if name in active
                },
                moneyflow_cnt,
            ),
            "limit_pool": _limit_features(limit_pool, limit_step, concept_strength),
            "trading": _trading_features(top_list, margin_detail, kpl_list, limit_list_ths),
            "membership": _membership_features(
                {
                    name: frame
                    for name, frame in {
                        "index_classify": index_classify,
                        "index_member_all": index_member_all,
                        "ci_index_member": ci_index_member,
                        "ths_index": ths_index,
                        "ths_member": ths_member,
                        "dc_index": dc_index,
                        "dc_member": dc_member,
                        "tdx_index": tdx_index,
                        "tdx_member": tdx_member,
                        "kpl_concept_cons": kpl_concept_cons,
                        "index_weight": index_weight,
                    }.items()
                    if name in active
                },
                {
                    "index_classify": index_classify_partition,
                    "index_member_all": index_member_all_partition,
                    "ci_index_member": ci_index_member_partition,
                    "ths_index": ths_index_partition,
                    "ths_member": ths_member_partition,
                    "dc_index": completed if "dc_index" in active else None,
                    "dc_member": completed if "dc_member" in active else None,
                    "tdx_index": completed if "tdx_index" in active else None,
                    "tdx_member": completed if "tdx_member" in active else None,
                    "kpl_concept_cons": completed if "kpl_concept_cons" in active else None,
                    "index_weight": index_weight_partition,
                },
            ),
            "events": {
                "notice_sample": [_clean_value(item) for item in _records(notices, limit=50)],
                "earnings_forecast_sample": [_clean_value(item) for item in _records(forecasts, limit=50)],
                "news_sample": [_clean_value(item) for item in _records(news, limit=50)],
            },
            "financials": {
                "income_sample": [_clean_value(item) for item in _records(income, limit=30)],
                "balancesheet_sample": [_clean_value(item) for item in _records(balancesheet, limit=30)],
                "cashflow_sample": [_clean_value(item) for item in _records(cashflow, limit=30)],
                "express_sample": [_clean_value(item) for item in _records(express, limit=30)],
                "fina_indicator_sample": [_clean_value(item) for item in _records(fina_indicator, limit=30)],
                "fina_mainbz_sample": [_clean_value(item) for item in _records(fina_mainbz, limit=30)],
                "dividend_sample": [_clean_value(item) for item in _records(dividend, limit=30)],
                "fina_audit_sample": [_clean_value(item) for item in _records(fina_audit, limit=30)],
                "disclosure_date_sample": [_clean_value(item) for item in _records(disclosure_date, limit=30)],
            },
        },
        "data_gaps": data_gaps,
        "provenance": {
            "source": "local_mart",
            "mart_root": str(mart.root),
            "active_plan": {
                "profile": plan.profile,
                "generated_at": plan.generated_at,
                "datasets": sorted(active),
            },
            "event_news": {
                "mode": "current_visible_page",
                "historical_backfill": False,
            },
        },
    }
    if include_raw_samples:
        raw_sample_names = [
            "trade_cal",
            "stock_basic",
            "daily",
            "daily_basic",
            "moneyflow",
            "moneyflow_dc",
            "moneyflow_ths",
            "limit_list_d",
            "top_list",
            "a_stock_notice",
            "earnings_forecast",
            "event_news",
            "balancesheet",
            "express",
            "dividend",
            "fina_audit",
            "disclosure_date",
            "index_classify",
            "index_member_all",
            "ci_index_member",
            "ths_index",
            "ths_member",
            "dc_index",
            "dc_member",
            "tdx_index",
            "tdx_member",
            "kpl_concept_cons",
            "index_weight",
        ]
        bundle["raw_samples"] = {
            name: [_clean_value(item) for item in _records(frames_by_dataset[name], limit=50)]
            for name in raw_sample_names
            if name in active
        }
    return _clean_value(bundle)


def dump_bundle(bundle: dict[str, Any], output: str | Path | None = None) -> str:
    text = json.dumps(bundle, ensure_ascii=False, indent=2, default=str)
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    return text


__all__ = [
    "ANALYSIS_BUNDLE_SCHEMA",
    "AnalysisBundleError",
    "build_market_analysis_bundle",
    "dump_bundle",
]

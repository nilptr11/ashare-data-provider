from __future__ import annotations

import contextlib
import hashlib
import io
import signal
from datetime import date, datetime, timedelta
from typing import Any, Callable, Iterable


NOTICE_CATEGORIES = {
    "全部",
    "重大事项",
    "财务报告",
    "融资公告",
    "风险提示",
    "资产重组",
    "信息变更",
    "持股变动",
}
QUARTER_ENDS = ("0331", "0630", "0930", "1231")


class AStockEventError(RuntimeError):
    pass


class AStockEventDependencyError(AStockEventError):
    pass


class AStockEventFetchError(AStockEventError):
    pass


class FetchTimeout(AStockEventFetchError):
    pass


def _load_pandas() -> Any:
    try:
        import pandas as pd
    except ImportError as exc:
        raise AStockEventDependencyError("A 股事件能力需要 pandas，请先安装项目依赖") from exc
    return pd


def _load_akshare() -> tuple[Any, Any]:
    try:
        import akshare as ak
        import pandas as pd
    except ImportError as exc:
        raise AStockEventDependencyError("A 股公告/业绩预告需要 akshare 和 pandas，请先安装项目依赖") from exc
    return ak, pd


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_yyyymmdd(value: str | date | datetime | None = None) -> date:
    if value is None:
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return datetime.strptime(text, "%Y-%m-%d").date()
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError as exc:
        raise AStockEventError(f"日期必须是 YYYYMMDD 或 YYYY-MM-DD：{value}") from exc


def _validate_positive_int(value: int, name: str) -> None:
    if value <= 0:
        raise AStockEventError(f"{name} 必须是正整数")


def validate_period(value: str) -> str:
    text = str(value).strip()
    if len(text) != 8 or not text.isdigit() or text[4:] not in QUARTER_ENDS:
        raise AStockEventError(f"报告期必须是 YYYY0331/YYYY0630/YYYY0930/YYYY1231：{value}")
    return text


def current_quarter_end(anchor: date) -> date:
    month = ((anchor.month - 1) // 3 + 1) * 3
    day = 31 if month in (3, 12) else 30
    return date(anchor.year, month, day)


def auto_periods(anchor: str | date | datetime | None = None, count: int = 5) -> list[str]:
    _validate_positive_int(count, "count")
    anchor_date = parse_yyyymmdd(anchor)
    upper = current_quarter_end(anchor_date)
    periods: list[date] = []
    for year in range(anchor_date.year + 1, anchor_date.year - 5, -1):
        for suffix in QUARTER_ENDS:
            period_date = date(year, int(suffix[:2]), int(suffix[2:]))
            if period_date <= upper:
                periods.append(period_date)
    periods = sorted(set(periods), reverse=True)
    return [period.strftime("%Y%m%d") for period in periods[:count]]


def run_akshare(call: Callable[[], Any], timeout: int = 30, verbose_source: bool = False) -> Any:
    _validate_positive_int(timeout, "timeout")

    def on_alarm(_signum: int, _frame: object) -> None:
        raise FetchTimeout(f"AKShare 请求超过 {timeout}s")

    old_handler = signal.signal(signal.SIGALRM, on_alarm)
    signal.alarm(timeout)
    try:
        if verbose_source:
            return call()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return call()
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


def fetch_notice(
    days: int = 7,
    end_date: str | date | datetime | None = None,
    stock: str | None = None,
    category: str = "全部",
    keyword: str | None = None,
    timeout: int = 30,
    verbose_source: bool = False,
    as_records: bool = True,
) -> Any:
    _validate_positive_int(days, "days")
    if category not in NOTICE_CATEGORIES:
        raise AStockEventError("公告分类必须是：" + ", ".join(sorted(NOTICE_CATEGORIES)))

    ak, pd = _load_akshare()
    anchor = parse_yyyymmdd(end_date)
    start = anchor - timedelta(days=days - 1)

    if stock:
        df = run_akshare(
            lambda: ak.stock_individual_notice_report(
                security=stock,
                symbol=category,
                begin_date=start.strftime("%Y%m%d"),
                end_date=anchor.strftime("%Y%m%d"),
            ),
            timeout=timeout,
            verbose_source=verbose_source,
        )
    else:
        frames = []
        for offset in range(days):
            day = anchor - timedelta(days=offset)
            df_day = run_akshare(
                lambda day=day: ak.stock_notice_report(symbol=category, date=day.strftime("%Y%m%d")),
                timeout=timeout,
                verbose_source=verbose_source,
            )
            if df_day is not None and not df_day.empty:
                frames.append(df_day)
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    prepared = prepare_notice(df, keyword=keyword, start=start, end=anchor)
    return build_notice_records(prepared) if as_records else prepared


def prepare_notice(df: Any, keyword: str | None = None, start: date | None = None, end: date | None = None) -> Any:
    pd = _load_pandas()
    if df is None or df.empty:
        return pd.DataFrame()

    result = df.copy()
    if "公告日期" in result.columns and (start is not None or end is not None):
        dt = pd.to_datetime(result["公告日期"], errors="coerce").dt.date
        if start is not None:
            result = result[dt >= start]
            dt = pd.to_datetime(result["公告日期"], errors="coerce").dt.date
        if end is not None:
            result = result[dt <= end]
        result["_sort_date"] = pd.to_datetime(result["公告日期"], errors="coerce")
    elif "公告日期" in result.columns:
        result["_sort_date"] = pd.to_datetime(result["公告日期"], errors="coerce")

    if keyword:
        title = result.get("公告标题", pd.Series("", index=result.index)).astype(str)
        notice_type = result.get("公告类型", pd.Series("", index=result.index)).astype(str)
        result = result[title.str.contains(keyword, case=False, na=False) | notice_type.str.contains(keyword, case=False, na=False)]

    if "_sort_date" in result.columns:
        result = result.sort_values("_sort_date", ascending=False, na_position="last").drop(columns=["_sort_date"])
    return result.reset_index(drop=True)


def fetch_forecast(
    days: int = 60,
    end_date: str | date | datetime | None = None,
    stock: str | None = None,
    periods: Iterable[str] | None = None,
    scan_periods: int = 5,
    keyword: str | None = None,
    timeout: int = 30,
    verbose_source: bool = False,
    as_records: bool = True,
) -> Any:
    _validate_positive_int(days, "days")
    _validate_positive_int(scan_periods, "scan_periods")
    ak, pd = _load_akshare()
    anchor = parse_yyyymmdd(end_date)
    start = anchor - timedelta(days=days - 1)
    resolved_periods = [validate_period(period) for period in periods] if periods else auto_periods(anchor, scan_periods)

    frames = []
    failures = []
    for period in resolved_periods:
        try:
            df_period = run_akshare(
                lambda period=period: ak.stock_yjyg_em(date=period),
                timeout=timeout,
                verbose_source=verbose_source,
            )
        except Exception as exc:  # Keep scanning other report periods.
            failures.append(f"{period}: {type(exc).__name__}: {exc}")
            continue
        if df_period is None or df_period.empty:
            continue
        df_period = df_period.copy()
        df_period["报告期"] = period
        frames.append(df_period)

    if not frames:
        if failures:
            raise AStockEventFetchError("业绩预告报告期均获取失败：" + "; ".join(failures))
        prepared = pd.DataFrame()
    else:
        prepared = prepare_forecast(pd.concat(frames, ignore_index=True), keyword=keyword, stock=stock, start=start, end=anchor)
    return build_forecast_records(prepared) if as_records else prepared


def prepare_forecast(
    df: Any,
    keyword: str | None = None,
    stock: str | None = None,
    start: date | None = None,
    end: date | None = None,
) -> Any:
    pd = _load_pandas()
    if df is None or df.empty:
        return pd.DataFrame()

    result = df.copy()
    if "公告日期" in result.columns and (start is not None or end is not None):
        dt = pd.to_datetime(result["公告日期"], errors="coerce").dt.date
        if start is not None:
            result = result[dt >= start]
            dt = pd.to_datetime(result["公告日期"], errors="coerce").dt.date
        if end is not None:
            result = result[dt <= end]
        result["_sort_date"] = pd.to_datetime(result["公告日期"], errors="coerce")
    elif "公告日期" in result.columns:
        result["_sort_date"] = pd.to_datetime(result["公告日期"], errors="coerce")

    if stock:
        result = result[result.get("股票代码", pd.Series("", index=result.index)).astype(str) == stock]

    if keyword:
        text_columns = ["股票简称", "预测指标", "业绩变动", "业绩变动原因", "预告类型"]
        mask = pd.Series(False, index=result.index)
        for column in text_columns:
            if column in result.columns:
                mask = mask | result[column].astype(str).str.contains(keyword, case=False, na=False)
        result = result[mask]

    if "_sort_date" in result.columns:
        result = result.sort_values("_sort_date", ascending=False, na_position="last").drop(columns=["_sort_date"])
    return result.reset_index(drop=True)


def _normalize_hash_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _hash_parts(*parts: Any) -> str:
    return hashlib.sha256("\x1f".join(_normalize_hash_text(part) for part in parts).encode("utf-8")).hexdigest()


def _clean_value(value: Any) -> Any:
    pd = _load_pandas()
    if pd.isna(value):
        return None
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return value


def _clean_record(row: dict[str, Any]) -> dict[str, Any]:
    return {key: _clean_value(value) for key, value in row.items()}


def build_notice_records(df: Any, fetched_at: str | None = None) -> list[dict[str, Any]]:
    resolved_fetched_at = fetched_at or _now_iso()
    records = []
    for row in df.to_dict("records") if df is not None and not df.empty else []:
        raw = _clean_record(row)
        stock_code = raw.get("代码")
        stock_name = raw.get("名称")
        title = raw.get("公告标题")
        notice_type = raw.get("公告类型")
        publish_date = raw.get("公告日期")
        url = raw.get("网址")
        content_hash = _hash_parts(title, notice_type)
        records.append(
            {
                "id": _hash_parts("notice", stock_code, publish_date, title, url),
                "content_hash": content_hash,
                "dedupe_key": _hash_parts("akshare_notice", stock_code, publish_date, content_hash),
                "event_type": "notice",
                "source_kind": "akshare_notice",
                "stock_code": stock_code,
                "stock_name": stock_name,
                "title": title,
                "notice_type": notice_type,
                "publish_date": publish_date,
                "url": url,
                "fetched_at": resolved_fetched_at,
                "raw": raw,
            }
        )
    return records


def build_forecast_records(df: Any, fetched_at: str | None = None) -> list[dict[str, Any]]:
    resolved_fetched_at = fetched_at or _now_iso()
    records = []
    for row in df.to_dict("records") if df is not None and not df.empty else []:
        raw = _clean_record(row)
        period = raw.get("报告期")
        stock_code = raw.get("股票代码")
        stock_name = raw.get("股票简称")
        metric = raw.get("预测指标")
        forecast_type = raw.get("预告类型")
        change_range = raw.get("业绩变动幅度")
        publish_date = raw.get("公告日期")
        change_summary = raw.get("业绩变动")
        change_reason = raw.get("业绩变动原因")
        content_hash = _hash_parts(metric, forecast_type, change_range, change_summary, change_reason)
        records.append(
            {
                "id": _hash_parts("forecast", stock_code, period, publish_date, content_hash),
                "content_hash": content_hash,
                "dedupe_key": _hash_parts("akshare_yjyg_em", stock_code, period, publish_date, content_hash),
                "event_type": "forecast",
                "source_kind": "akshare_yjyg_em",
                "period": period,
                "stock_code": stock_code,
                "stock_name": stock_name,
                "metric": metric,
                "forecast_type": forecast_type,
                "change_range": change_range,
                "publish_date": publish_date,
                "change_summary": change_summary,
                "change_reason": change_reason,
                "fetched_at": resolved_fetched_at,
                "raw": raw,
            }
        )
    return records

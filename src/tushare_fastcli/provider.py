from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from .client import TushareCaller, TushareError
from .config import TushareConfig, load_config
from .defaults import default_params as configured_default_params
from .recipes import default_fields, default_recipe_params
from .registry import InterfaceEntry, InterfaceRegistry, load_registry


STOCK_BASIC_FIELDS = default_fields("stock_basic")
TRADE_CAL_FIELDS = default_fields("trade_cal")
DAILY_FIELDS = default_fields("daily")
DAILY_BASIC_FIELDS = default_fields("daily_basic")
ADJ_FACTOR_FIELDS = default_fields("adj_factor")
STK_LIMIT_FIELDS = default_fields("stk_limit")


class TushareProviderError(TushareError):
    pass


class TushareUnknownInterfaceError(TushareProviderError):
    def __init__(self, api_name: str) -> None:
        self.api_name = api_name
        super().__init__(f"未找到接口：{api_name}。如需强制调用，Python 设置 allow_unknown=True；CLI 加 --allow-unknown。")


class TusharePermissionError(TushareProviderError):
    def __init__(self, entry: InterfaceEntry, detail: str) -> None:
        self.entry = entry
        super().__init__(f"接口可能不可用：{entry.api_name}:{entry.doc_id} ({detail})。如需强制调用，Python 设置 force=True；CLI 加 --force。")


class TushareInterfaceSelectionError(TushareProviderError):
    def __init__(self, api_name: str, doc_id: str | None = None, key: str | None = None) -> None:
        self.api_name = api_name
        self.doc_id = doc_id
        self.key = key
        parts = [f"api_name={api_name}"]
        if doc_id is not None:
            parts.append(f"doc_id={doc_id}")
        if key is not None:
            parts.append(f"key={key}")
        super().__init__(f"未找到匹配接口元数据：{', '.join(parts)}")


def _format_yyyymmdd(value: str | date | datetime) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y%m%d")
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    text = str(value).strip()
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return text.replace("-", "")
    return text


def _date_from_value(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(_format_yyyymmdd(value), "%Y%m%d").date()


def _without_none(params: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in params.items() if value is not None}


def _calendar_records(calendar: Any, start_text: str, end_text: str) -> list[dict[str, Any]]:
    if isinstance(calendar, list):
        return list(calendar)
    if not hasattr(calendar, "empty"):
        raise TushareProviderError("交易日历返回值不是 DataFrame 或 list[dict]")
    if calendar.empty:
        raise TushareProviderError(f"未获取到交易日历：{start_text} - {end_text}")
    if "cal_date" not in calendar or "is_open" not in calendar:
        raise TushareProviderError("交易日历缺少 cal_date 或 is_open 字段")
    return calendar[["cal_date", "is_open"]].to_dict("records")


def _permission_block_reason(entry: InterfaceEntry, config: TushareConfig) -> str | None:
    if entry.eligibility == "needs_separate_permission" and not config.allow_separate_permission:
        return entry.eligibility
    if entry.required_points is not None and entry.required_points > config.points:
        return f"{entry.eligibility}, required_points={entry.required_points}, current_points={config.points}"
    return None


class TushareProvider:
    """Public Python API for upper-layer scanners, strategies, and automations."""

    def __init__(
        self,
        token: str | None = None,
        proxy_url: str | None = None,
        env_file: str = ".env",
        points: int | None = None,
        allow_separate_permission: bool | None = None,
        caller: TushareCaller | None = None,
        registry: InterfaceRegistry | None = None,
    ) -> None:
        self._env_file = env_file
        self._config = load_config(
            token=token,
            proxy_url=proxy_url,
            points=points,
            allow_separate_permission=allow_separate_permission,
            env_file=env_file,
        )
        self._registry = registry or load_registry()
        self._caller = caller or TushareCaller(env_file=env_file, config=self._config)

    @property
    def config(self) -> TushareConfig:
        return self._config

    @property
    def registry(self) -> InterfaceRegistry:
        return self._registry

    def search(
        self,
        query: str | None = None,
        category: str | None = None,
        eligibility: str | None = None,
    ) -> list[InterfaceEntry]:
        return self._registry.search(query=query, category=category, eligibility=eligibility)

    def info(
        self,
        api_name: str,
        doc_id: str | None = None,
        key: str | None = None,
    ) -> list[InterfaceEntry]:
        return self._registry.find(api_name, doc_id=doc_id, key=key)

    def default_params(
        self,
        api_name: str,
        doc_id: str | None = None,
        key: str | None = None,
    ) -> dict[str, Any]:
        return configured_default_params(api_name, doc_id=doc_id, key=key)

    def ensure_available(
        self,
        api_name: str,
        doc_id: str | None = None,
        key: str | None = None,
        force: bool = False,
        allow_unknown: bool = False,
    ) -> list[InterfaceEntry]:
        entries = self._registry.find(api_name, doc_id=doc_id, key=key)
        if not entries:
            if doc_id is not None or key is not None:
                raise TushareInterfaceSelectionError(api_name, doc_id=doc_id, key=key)
            if allow_unknown:
                return []
            raise TushareUnknownInterfaceError(api_name)

        if force:
            return entries

        blocked: list[tuple[InterfaceEntry, str]] = []
        for entry in entries:
            reason = _permission_block_reason(entry, self._config)
            if reason is not None:
                blocked.append((entry, reason))

        if blocked and len(blocked) == len(entries):
            entry, reason = blocked[0]
            raise TusharePermissionError(entry, reason)

        return entries

    def call(
        self,
        api_name: str,
        params: dict[str, Any] | None = None,
        fields: str | None = None,
        use_defaults: bool = False,
        doc_id: str | None = None,
        key: str | None = None,
        force: bool = False,
        allow_unknown: bool = False,
    ) -> Any:
        self.ensure_available(api_name, doc_id=doc_id, key=key, force=force, allow_unknown=allow_unknown)
        resolved_params: dict[str, Any] = {}
        if use_defaults:
            resolved_params.update(configured_default_params(api_name, doc_id=doc_id, key=key))
        resolved_params.update(params or {})
        return self._caller.call(api_name, params=resolved_params, fields=fields)

    def stock_basic(
        self,
        exchange: str = "",
        list_status: str = "L",
        fields: str = STOCK_BASIC_FIELDS,
        force: bool = False,
    ) -> Any:
        params = default_recipe_params("stock_basic")
        params.update({"exchange": exchange, "list_status": list_status})
        return self.call(
            "stock_basic",
            params=_without_none(params),
            fields=fields,
            force=force,
        )

    def trade_cal(
        self,
        start_date: str | date | datetime | None = None,
        end_date: str | date | datetime | None = None,
        exchange: str = "SSE",
        fields: str = TRADE_CAL_FIELDS,
        force: bool = False,
    ) -> Any:
        params = default_recipe_params("trade_cal")
        params.update(
            {
                "exchange": exchange,
                "start_date": _format_yyyymmdd(start_date) if start_date is not None else None,
                "end_date": _format_yyyymmdd(end_date) if end_date is not None else None,
            }
        )
        return self.call("trade_cal", params=_without_none(params), fields=fields, force=force)

    def latest_trade_date(
        self,
        as_of: str | date | datetime | None = None,
        exchange: str = "SSE",
        lookback_days: int = 15,
        force: bool = False,
    ) -> str:
        end_date = datetime.now().date() if as_of is None else _date_from_value(as_of)
        return self._last_open_trade_date(
            end_date=end_date,
            exchange=exchange,
            lookback_days=lookback_days,
            force=force,
        )

    def previous_trade_date(
        self,
        as_of: str | date | datetime | None = None,
        exchange: str = "SSE",
        lookback_days: int = 30,
        force: bool = False,
    ) -> str:
        base_date = datetime.now().date() if as_of is None else _date_from_value(as_of)
        return self._last_open_trade_date(
            end_date=base_date - timedelta(days=1),
            exchange=exchange,
            lookback_days=lookback_days,
            force=force,
        )

    def _last_open_trade_date(
        self,
        end_date: date,
        exchange: str,
        lookback_days: int,
        force: bool,
    ) -> str:
        end_text = end_date.strftime("%Y%m%d")
        start_text = (end_date - timedelta(days=lookback_days)).strftime("%Y%m%d")

        calendar = self.trade_cal(
            start_date=start_text,
            end_date=end_text,
            exchange=exchange,
            fields="cal_date,is_open",
            force=force,
        )
        records = _calendar_records(calendar, start_text, end_text)
        open_days = sorted(
            str(row["cal_date"])
            for row in records
            if str(row.get("is_open")).lower() in {"1", "1.0", "true"}
        )
        if not open_days:
            raise TushareProviderError(f"{start_text} - {end_text} 范围内没有开市日")
        return open_days[-1]

    def daily_snapshot(
        self,
        trade_date: str | date | datetime | None = None,
        fields: str = DAILY_FIELDS,
        force: bool = False,
    ) -> Any:
        resolved_trade_date = _format_yyyymmdd(trade_date) if trade_date is not None else self.previous_trade_date(force=force)
        return self.call("daily", params={"trade_date": resolved_trade_date}, fields=fields, force=force)

    def daily_basic_snapshot(
        self,
        trade_date: str | date | datetime | None = None,
        fields: str = DAILY_BASIC_FIELDS,
        force: bool = False,
    ) -> Any:
        resolved_trade_date = _format_yyyymmdd(trade_date) if trade_date is not None else self.previous_trade_date(force=force)
        return self.call("daily_basic", params={"trade_date": resolved_trade_date}, fields=fields, force=force)

    def adj_factor_snapshot(
        self,
        trade_date: str | date | datetime | None = None,
        fields: str = ADJ_FACTOR_FIELDS,
        force: bool = False,
    ) -> Any:
        resolved_trade_date = _format_yyyymmdd(trade_date) if trade_date is not None else self.previous_trade_date(force=force)
        return self.call("adj_factor", params={"trade_date": resolved_trade_date}, fields=fields, force=force)

    def limit_price_snapshot(
        self,
        trade_date: str | date | datetime | None = None,
        fields: str = STK_LIMIT_FIELDS,
        force: bool = False,
    ) -> Any:
        resolved_trade_date = _format_yyyymmdd(trade_date) if trade_date is not None else self.previous_trade_date(force=force)
        return self.call("stk_limit", params={"trade_date": resolved_trade_date}, fields=fields, force=force)

    def pro_bar(
        self,
        ts_code: str,
        start_date: str | date | datetime,
        end_date: str | date | datetime,
        adj: str = "qfq",
        freq: str = "D",
        fields: str | None = None,
        force: bool = False,
    ) -> Any:
        params = default_recipe_params("pro_bar")
        params.update(
            {
                "ts_code": ts_code,
                "start_date": _format_yyyymmdd(start_date),
                "end_date": _format_yyyymmdd(end_date),
                "adj": adj,
                "freq": freq,
            }
        )
        return self.call(
            "pro_bar",
            params=params,
            fields=fields,
            force=force,
        )

    def a_stock_notice(
        self,
        days: int = 7,
        end_date: str | date | datetime | None = None,
        stock: str | None = None,
        category: str = "全部",
        keyword: str | None = None,
        timeout: int = 30,
        verbose_source: bool = False,
        max_rows: int = 0,
        as_records: bool = True,
    ) -> Any:
        from .events import fetch_notice

        result = fetch_notice(
            days=days,
            end_date=end_date,
            stock=stock,
            category=category,
            keyword=keyword,
            timeout=timeout,
            verbose_source=verbose_source,
            as_records=as_records,
        )
        if max_rows > 0:
            return result[:max_rows] if isinstance(result, list) else result.head(max_rows)
        return result

    def earnings_forecast(
        self,
        days: int = 60,
        end_date: str | date | datetime | None = None,
        stock: str | None = None,
        periods: list[str] | tuple[str, ...] | None = None,
        scan_periods: int = 5,
        keyword: str | None = None,
        timeout: int = 30,
        verbose_source: bool = False,
        max_rows: int = 0,
        as_records: bool = True,
    ) -> Any:
        from .events import fetch_forecast

        result = fetch_forecast(
            days=days,
            end_date=end_date,
            stock=stock,
            periods=periods,
            scan_periods=scan_periods,
            keyword=keyword,
            timeout=timeout,
            verbose_source=verbose_source,
            as_records=as_records,
        )
        if max_rows > 0:
            return result[:max_rows] if isinstance(result, list) else result.head(max_rows)
        return result

    def event_news(
        self,
        sources: list[str] | tuple[str, ...] | None = None,
        cookie: str | None = None,
        cookie_file: str | None = None,
        cookie_env: str = "TUSHARE_COOKIE",
        timeout: float = 30.0,
        delay: float = 0.3,
        retries: int = 2,
        publish_date: str | None = None,
        max_rows: int = 0,
        include_summary: bool = False,
    ) -> Any:
        return self.news_fallback(
            sources=sources,
            cookie=cookie,
            cookie_file=cookie_file,
            cookie_env=cookie_env,
            timeout=timeout,
            delay=delay,
            retries=retries,
            publish_date=publish_date,
            max_rows=max_rows,
            include_summary=include_summary,
        )

    def news_fallback(
        self,
        sources: list[str] | tuple[str, ...] | None = None,
        cookie: str | None = None,
        cookie_file: str | None = None,
        cookie_env: str = "TUSHARE_COOKIE",
        timeout: float = 30.0,
        delay: float = 0.3,
        retries: int = 2,
        publish_date: str | None = None,
        max_rows: int = 0,
        include_summary: bool = False,
    ) -> Any:
        from .news import crawl_tushare_news, load_tushare_cookie

        resolved_cookie = load_tushare_cookie(
            cookie=cookie,
            cookie_file=cookie_file,
            cookie_env=cookie_env,
            env_file=self._env_file,
        )
        payload = crawl_tushare_news(
            cookie=resolved_cookie,
            sources=sources,
            timeout=timeout,
            delay=delay,
            retries=retries,
            publish_date=publish_date,
        )
        if max_rows > 0:
            payload = dict(payload)
            payload["records"] = payload["records"][:max_rows]
        return payload if include_summary else payload["records"]

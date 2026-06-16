from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from .cache import CachePolicy, LocalCacheExecutor
from .client import TushareCaller, TushareError
from .config import TushareConfig, load_config
from .defaults import default_params as configured_default_params
from .local_store import LocalDataStore, normalize_cache_params
from .recipes import default_fields, default_recipe_params
from .registry import InterfaceEntry, InterfaceRegistry, load_registry
from .schemas import SchemaError, get_api_schema


STOCK_BASIC_FIELDS = default_fields("stock_basic")
TRADE_CAL_FIELDS = default_fields("trade_cal")
DAILY_FIELDS = default_fields("daily")
DAILY_BASIC_FIELDS = default_fields("daily_basic")
ADJ_FACTOR_FIELDS = default_fields("adj_factor")
STK_LIMIT_FIELDS = default_fields("stk_limit")


class AShareProviderError(TushareError):
    pass


class TushareUnknownInterfaceError(AShareProviderError):
    def __init__(self, api_name: str) -> None:
        self.api_name = api_name
        super().__init__(f"未找到接口：{api_name}。如需强制调用，Python 设置 allow_unknown=True；CLI 加 --allow-unknown。")


class TusharePermissionError(AShareProviderError):
    def __init__(self, entry: InterfaceEntry, detail: str) -> None:
        self.entry = entry
        super().__init__(f"接口可能不可用：{entry.api_name}:{entry.doc_id} ({detail})。如需强制调用，Python 设置 force=True；CLI 加 --force。")


class TushareInterfaceSelectionError(AShareProviderError):
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
        raise AShareProviderError("交易日历返回值不是 DataFrame 或 list[dict]")
    if calendar.empty:
        raise AShareProviderError(f"未获取到交易日历：{start_text} - {end_text}")
    if "cal_date" not in calendar or "is_open" not in calendar:
        raise AShareProviderError("交易日历缺少 cal_date 或 is_open 字段")
    return calendar[["cal_date", "is_open"]].to_dict("records")


def _permission_block_reason(entry: InterfaceEntry, config: TushareConfig) -> str | None:
    if entry.eligibility == "needs_separate_permission" and not config.allow_separate_permission:
        return entry.eligibility
    if entry.required_points is not None and entry.required_points > config.points:
        return f"{entry.eligibility}, required_points={entry.required_points}, current_points={config.points}"
    return None


class AShareProvider:
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
        cache_enabled: bool = True,
        data_dir: str | Path | None = None,
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
        self._cache_enabled = cache_enabled
        self._data_dir = data_dir
        self._cache_policy = CachePolicy()

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
        if not self._cache_enabled or not self._should_use_local_store(api_name, resolved_params, doc_id=doc_id, key=key):
            return self._call_remote(api_name, params=resolved_params, fields=fields)
        return self._call_with_local_store(
            api_name,
            params=resolved_params,
            fields=fields,
            data_dir=self._data_dir,
            doc_id=doc_id,
            key=key,
            force=force,
        )

    def _call_remote(self, api_name: str, params: dict[str, Any] | None = None, fields: str | None = None) -> Any:
        return self._caller.call(api_name, params=params, fields=fields)

    def _should_use_local_store(
        self,
        api_name: str,
        params: dict[str, Any] | None,
        doc_id: str | None = None,
        key: str | None = None,
    ) -> bool:
        normalized_params = normalize_cache_params(params)
        supports_trade_date = self._api_supports_param(api_name, "trade_date", doc_id=doc_id, key=key)
        return self._cache_policy.enabled_for(
            api_name,
            normalized_params,
            supports_trade_date=supports_trade_date,
        )

    def _call_with_local_store(
        self,
        api_name: str,
        params: dict[str, Any] | None = None,
        fields: str | None = None,
        mode: str = "prefer",
        data_dir: str | Path | None = None,
        split_by_trade_date: bool = True,
        use_defaults: bool = False,
        doc_id: str | None = None,
        key: str | None = None,
        force: bool = False,
    ) -> Any:
        resolved_params: dict[str, Any] = {}
        if use_defaults:
            resolved_params.update(configured_default_params(api_name, doc_id=doc_id, key=key))
        resolved_params.update(params or {})
        normalized_params = normalize_cache_params(resolved_params)
        store = LocalDataStore(data_dir=data_dir, env_file=self._env_file)
        policy = CachePolicy(split_by_trade_date=split_by_trade_date)
        supports_trade_date = self._api_supports_param(api_name, "trade_date", doc_id=doc_id, key=key)
        request_params = policy.request_params(
            api_name,
            normalized_params,
            supports_trade_date=supports_trade_date,
            trade_dates_between=lambda start_text, end_text: self._trade_dates_between(start_text, end_text, force=force),
        )
        executor = LocalCacheExecutor(store=store, remote_call=self._call_remote)
        return executor.call(api_name, request_params=request_params, fields=fields, mode=mode)

    def _trade_dates_between(self, start_text: str, end_text: str, force: bool = False) -> list[str]:
        calendar = self.trade_cal(
            start_date=start_text,
            end_date=end_text,
            fields="cal_date,is_open",
            force=force,
        )
        records = _calendar_records(calendar, start_text, end_text)
        return [
            _format_yyyymmdd(row["cal_date"])
            for row in records
            if str(row.get("is_open")).lower() in {"1", "1.0", "true"}
            and start_text <= _format_yyyymmdd(row["cal_date"]) <= end_text
        ]

    @staticmethod
    def _api_supports_param(api_name: str, param_name: str, doc_id: str | None = None, key: str | None = None) -> bool:
        try:
            schema = get_api_schema(api_name, doc_id=doc_id, key=key)
        except SchemaError:
            return False
        names = {param.name for param in schema.input_params}
        names.update(schema.required_params)
        names.update(schema.optional_params)
        return param_name in names

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
        if as_of is None:
            base_datetime = datetime.now()
            base_date = base_datetime.date()
            base_time = base_datetime.time()
        elif isinstance(as_of, datetime):
            base_date = as_of.date()
            base_time = as_of.time()
        else:
            base_date = _date_from_value(as_of)
            base_time = time.min

        latest_open_date = self._last_open_trade_date(
            end_date=base_date,
            exchange=exchange,
            lookback_days=lookback_days,
            force=force,
        )
        if latest_open_date != base_date.strftime("%Y%m%d"):
            return latest_open_date

        if base_time >= time(15, 0):
            return latest_open_date

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
            cal_date
            for row in records
            if str(row.get("is_open")).lower() in {"1", "1.0", "true"}
            for cal_date in [_format_yyyymmdd(row["cal_date"])]
            if start_text <= cal_date <= end_text
        )
        if not open_days:
            raise AShareProviderError(f"{start_text} - {end_text} 范围内没有开市日")
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
        anchor_date: str | date | datetime | None = None,
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
            anchor_date=anchor_date,
        )
        if max_rows > 0:
            payload = dict(payload)
            payload["records"] = payload["records"][:max_rows]
        return payload if include_summary else payload["records"]

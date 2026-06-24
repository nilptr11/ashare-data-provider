from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Callable

from .provider import AShareProvider
from .recipes import default_fields
from .source_policy import blocked_tushare_apis, load_source_policy


ResearchCall = Callable[[], Any]


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


def _parse_date(value: str | date | datetime | None = None) -> date:
    if value is None:
        return datetime.now().date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(_format_yyyymmdd(value), "%Y%m%d").date()


def _stock_symbol(ts_code: str) -> str:
    return str(ts_code).split(".", 1)[0]


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
    if isinstance(value, dict):
        return {str(key): _clean_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean_value(item) for item in value]
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except TypeError:
            pass
    if hasattr(value, "item"):
        try:
            return _clean_value(value.item())
        except (TypeError, ValueError):
            pass
    return str(value)


def _records(value: Any, max_rows: int = 0) -> list[dict[str, Any]]:
    if value is None:
        return []
    if hasattr(value, "to_dict"):
        rows = value.to_dict("records")
    elif isinstance(value, list):
        rows = value
    elif isinstance(value, dict):
        rows = [value]
    else:
        return [{"value": _clean_value(value)}]
    if max_rows > 0:
        rows = rows[:max_rows]
    return [_clean_value(row) for row in rows if isinstance(row, dict)]


def _first_record(records: list[dict[str, Any]], key: str, value: Any) -> dict[str, Any] | None:
    wanted = str(value)
    for record in records:
        if str(record.get(key)) == wanted:
            return record
    return None


def _source(api_name: str, params: dict[str, Any] | None = None, fields: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": "tushare",
        "api_name": api_name,
        "params": _clean_value(params or {}),
    }
    if fields:
        payload["fields"] = fields
    return payload


def _event_source(name: str, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "project_builtin",
        "source": name,
        "params": _clean_value(params),
    }


def _dataset(value: Any, source: dict[str, Any], max_rows: int = 0) -> dict[str, Any]:
    records = _records(value, max_rows=max_rows)
    return {
        "source": source,
        "row_count": len(records),
        "records": records,
    }


def _merged_dataset(values: list[Any], source: dict[str, Any], max_rows: int = 0) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for value in values:
        records.extend(_records(value, max_rows=0))
    if max_rows > 0:
        records = records[:max_rows]
    return {
        "source": source,
        "row_count": len(records),
        "records": records,
    }


def _gap(data_gaps: list[dict[str, Any]], section: str, name: str, source: dict[str, Any], exc: Exception) -> None:
    data_gaps.append(
        {
            "section": section,
            "name": name,
            "source": source,
            "status": "unavailable",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    )


def _collect(
    data_gaps: list[dict[str, Any]],
    section: str,
    name: str,
    source: dict[str, Any],
    call: ResearchCall,
    max_rows: int = 0,
) -> dict[str, Any] | None:
    try:
        return _dataset(call(), source=source, max_rows=max_rows)
    except Exception as exc:  # noqa: BLE001 - context generation should continue.
        _gap(data_gaps, section, name, source, exc)
        return None


def _collect_many(
    data_gaps: list[dict[str, Any]],
    section: str,
    name: str,
    source: dict[str, Any],
    calls: list[tuple[dict[str, Any], ResearchCall]],
    max_rows: int = 0,
) -> dict[str, Any]:
    values: list[Any] = []
    for params, call in calls:
        try:
            values.append(call())
        except Exception as exc:  # noqa: BLE001 - context generation should continue.
            _gap(data_gaps, section, name, _source(str(source.get("api_name")), params), exc)
    return _merged_dataset(values, source=source, max_rows=max_rows)


def _report_periods(anchor_date: date, count: int = 8) -> list[str]:
    quarter_ends = ("0331", "0630", "0930", "1231")
    candidates = [
        f"{year}{quarter_end}"
        for year in range(anchor_date.year - 2, anchor_date.year + 2)
        for quarter_end in quarter_ends
    ]
    anchor_text = anchor_date.strftime("%Y%m%d")
    selected = [period for period in candidates if period <= anchor_text]
    return sorted(selected, reverse=True)[:count]


def _policy_summary() -> dict[str, Any]:
    policy = load_source_policy()
    groups = policy.get("external_source_groups", {})
    return {
        "version": policy.get("version"),
        "updated_at": policy.get("updated_at"),
        "blocked_eligibility": policy.get("tushare", {}).get("blocked_eligibility", []),
        "blocked_apis": sorted(blocked_tushare_apis()),
        "external_source_groups": {
            group_name: [
                {
                    "id": item.get("id"),
                    "domains": item.get("domains", []),
                    "trust_level": item.get("trust_level"),
                    "use_for": item.get("use_for", []),
                }
                for item in items
            ]
            for group_name, items in groups.items()
        },
        "dynamic_source_discovery": policy.get("dynamic_source_discovery", {}),
    }


def build_research_context(
    ts_code: str,
    as_of: str | date | datetime | None = None,
    profile: str = "basic",
    lookback_days: int = 120,
    financial_years: int = 3,
    event_days: int = 90,
    forecast_days: int = 180,
    include_news: bool = False,
    news_sources: list[str] | tuple[str, ...] | None = None,
    max_rows_per_dataset: int = 240,
    provider: AShareProvider | None = None,
) -> dict[str, Any]:
    if profile not in {"basic", "standard", "full"}:
        raise ValueError("profile must be one of: basic, standard, full")
    if lookback_days <= 0:
        raise ValueError("lookback_days must be positive")
    if financial_years <= 0:
        raise ValueError("financial_years must be positive")
    if event_days <= 0:
        raise ValueError("event_days must be positive")
    if forecast_days <= 0:
        raise ValueError("forecast_days must be positive")

    resolved_provider = provider or AShareProvider()
    anchor_date = _parse_date(as_of)
    anchor_text = anchor_date.strftime("%Y%m%d")
    start_text = (anchor_date - timedelta(days=lookback_days)).strftime("%Y%m%d")
    financial_start_text = date(anchor_date.year - financial_years, 1, 1).strftime("%Y%m%d")
    symbol = _stock_symbol(ts_code)
    data_gaps: list[dict[str, Any]] = []

    context: dict[str, Any] = {
        "schema": "ashare.research_context.v1",
        "generated_at": _now_iso(),
        "as_of": anchor_date.isoformat(),
        "ts_code": ts_code,
        "symbol": symbol,
        "profile": profile,
        "lookback_days": lookback_days,
        "source_policy": _policy_summary(),
        "skipped_sources": [
            {
                "kind": "tushare",
                "api_name": api_name,
                "status": "blocked_by_source_policy",
            }
            for api_name in sorted(blocked_tushare_apis())
        ],
        "target": {
            "ts_code": ts_code,
            "symbol": symbol,
        },
        "calendar": {},
        "market": {},
        "macro": {},
        "industry": {},
        "fundamentals": {},
        "events": {},
        "trading": {},
        "data_gaps": data_gaps,
    }

    try:
        completed_trade_date = resolved_provider.previous_trade_date(as_of=as_of)
        context["calendar"]["completed_trade_date"] = completed_trade_date
        context["calendar"]["source"] = _source("trade_cal", {"as_of": anchor_text})
    except Exception as exc:  # noqa: BLE001
        completed_trade_date = anchor_text
        context["calendar"]["completed_trade_date"] = None
        _gap(data_gaps, "calendar", "completed_trade_date", _source("trade_cal", {"as_of": anchor_text}), exc)

    stock_basic_source = _source("stock_basic", {"exchange": "", "list_status": "L"}, default_fields("stock_basic"))
    stock_basic = _collect(
        data_gaps,
        "target",
        "stock_basic",
        stock_basic_source,
        lambda: resolved_provider.stock_basic(),
        max_rows=0,
    )
    if stock_basic is not None:
        matched = _first_record(stock_basic["records"], "ts_code", ts_code)
        context["target"]["stock_basic"] = matched
        context["target"]["stock_basic_source"] = stock_basic_source
        if matched is None:
            data_gaps.append(
                {
                    "section": "target",
                    "name": "stock_basic_match",
                    "source": stock_basic_source,
                    "status": "missing",
                    "error": f"{ts_code} not found in stock_basic result",
                }
            )

    market_specs = [
        (
            "daily_latest",
            "daily",
            {"ts_code": ts_code, "trade_date": completed_trade_date},
            default_fields("daily"),
        ),
        (
            "daily_basic_latest",
            "daily_basic",
            {"ts_code": ts_code, "trade_date": completed_trade_date},
            default_fields("daily_basic"),
        ),
        (
            "adj_factor_latest",
            "adj_factor",
            {"ts_code": ts_code, "trade_date": completed_trade_date},
            default_fields("adj_factor"),
        ),
        (
            "limit_price_latest",
            "stk_limit",
            {"ts_code": ts_code, "trade_date": completed_trade_date},
            default_fields("stk_limit"),
        ),
    ]
    if profile in {"standard", "full"}:
        market_specs.insert(
            0,
            (
                "daily_history",
                "daily",
                {"ts_code": ts_code, "start_date": start_text, "end_date": completed_trade_date},
                default_fields("daily"),
            ),
        )
    for name, api_name, params, fields in market_specs:
        result = _collect(
            data_gaps,
            "market",
            name,
            _source(api_name, params, fields),
            lambda api_name=api_name, params=params, fields=fields: resolved_provider.call(api_name, params=params, fields=fields),
            max_rows=max_rows_per_dataset,
        )
        if result is not None:
            context["market"][name] = result

    fundamentals_specs = [
        ("income", "income", {"ts_code": ts_code, "start_date": financial_start_text, "end_date": anchor_text}),
        ("balancesheet", "balancesheet", {"ts_code": ts_code, "start_date": financial_start_text, "end_date": anchor_text}),
        ("cashflow", "cashflow", {"ts_code": ts_code, "start_date": financial_start_text, "end_date": anchor_text}),
        ("fina_indicator", "fina_indicator", {"ts_code": ts_code, "start_date": financial_start_text, "end_date": anchor_text}),
        ("fina_mainbz", "fina_mainbz", {"ts_code": ts_code, "start_date": financial_start_text, "end_date": anchor_text}),
        ("express", "express", {"ts_code": ts_code, "start_date": financial_start_text, "end_date": anchor_text}),
        ("dividend", "dividend", {"ts_code": ts_code}),
        ("fina_audit", "fina_audit", {"ts_code": ts_code, "start_date": financial_start_text, "end_date": anchor_text}),
    ]
    if profile in {"standard", "full"}:
        selected_fundamentals = fundamentals_specs if profile == "full" else [
            ("income", "income", {"ts_code": ts_code, "start_date": financial_start_text, "end_date": anchor_text}),
            ("cashflow", "cashflow", {"ts_code": ts_code, "start_date": financial_start_text, "end_date": anchor_text}),
            ("fina_indicator", "fina_indicator", {"ts_code": ts_code, "start_date": financial_start_text, "end_date": anchor_text}),
        ]
        for name, api_name, params in selected_fundamentals:
            result = _collect(
                data_gaps,
                "fundamentals",
                name,
                _source(api_name, params),
                lambda api_name=api_name, params=params: resolved_provider.call(api_name, params=params),
                max_rows=max_rows_per_dataset,
            )
            if result is not None:
                context["fundamentals"][name] = result

        if profile == "full":
            disclosure_queries = [{"ts_code": ts_code, "end_date": period} for period in _report_periods(anchor_date)]
            disclosure_source = _source("disclosure_date", {"queries": disclosure_queries})
            context["fundamentals"]["disclosure_date"] = _collect_many(
                data_gaps,
                "fundamentals",
                "disclosure_date",
                disclosure_source,
                [
                    (
                        params,
                        lambda params=params: resolved_provider.call("disclosure_date", params=params),
                    )
                    for params in disclosure_queries
                ],
                max_rows=max_rows_per_dataset,
            )

    index_specs = [
        ("index_daily_sh", "index_daily", {"ts_code": "000001.SH", "start_date": start_text, "end_date": completed_trade_date}),
        ("index_daily_hs300", "index_daily", {"ts_code": "000300.SH", "start_date": start_text, "end_date": completed_trade_date}),
        ("index_dailybasic_latest", "index_dailybasic", {"trade_date": completed_trade_date}),
        ("sw_daily_latest", "sw_daily", {"trade_date": completed_trade_date}),
        ("ci_daily_latest", "ci_daily", {"trade_date": completed_trade_date}),
    ]
    if profile in {"standard", "full"}:
        for name, api_name, params in index_specs:
            result = _collect(
                data_gaps,
                "macro" if name.startswith("index_") else "industry",
                name,
                _source(api_name, params),
                lambda api_name=api_name, params=params: resolved_provider.call(api_name, params=params),
                max_rows=max_rows_per_dataset,
            )
            if result is not None:
                target_section = "macro" if name.startswith("index_") else "industry"
                context[target_section][name] = result

    macro_specs = [
        ("shibor", "shibor", {"start_date": start_text, "end_date": completed_trade_date}),
        ("shibor_quote", "shibor_quote", {"date": completed_trade_date}),
        ("cn_ppi", "cn_ppi", {}),
        ("cn_gdp", "cn_gdp", {}),
        ("sf_month", "sf_month", {}),
        ("us_tltr", "us_tltr", {}),
        ("us_trycr", "us_trycr", {}),
        ("eco_cal", "eco_cal", {"start_date": anchor_text, "end_date": (anchor_date + timedelta(days=30)).strftime("%Y%m%d")}),
    ]
    if profile in {"standard", "full"}:
        selected_macro = macro_specs if profile == "full" else [
            ("shibor", "shibor", {"start_date": start_text, "end_date": completed_trade_date}),
            ("eco_cal", "eco_cal", {"start_date": anchor_text, "end_date": (anchor_date + timedelta(days=30)).strftime("%Y%m%d")}),
        ]
        for name, api_name, params in selected_macro:
            result = _collect(
                data_gaps,
                "macro",
                name,
                _source(api_name, params),
                lambda api_name=api_name, params=params: resolved_provider.call(api_name, params=params),
                max_rows=max_rows_per_dataset,
            )
            if result is not None:
                context["macro"][name] = result

    trading_specs = [
        ("moneyflow_ind_ths_latest", "moneyflow_ind_ths", {"trade_date": completed_trade_date}),
        ("moneyflow_ind_dc_latest", "moneyflow_ind_dc", {"trade_date": completed_trade_date}),
        ("moneyflow_cnt_ths_latest", "moneyflow_cnt_ths", {"trade_date": completed_trade_date}),
        ("margin_detail_latest", "margin_detail", {"trade_date": completed_trade_date}),
        ("top_list_latest", "top_list", {"trade_date": completed_trade_date}),
        ("limit_list_d_latest", "limit_list_d", {"trade_date": completed_trade_date}),
        ("limit_step_latest", "limit_step", {"trade_date": completed_trade_date}),
        ("limit_cpt_list_latest", "limit_cpt_list", {"trade_date": completed_trade_date}),
    ]
    if profile in {"standard", "full"}:
        selected_trading = trading_specs if profile == "full" else [
            ("moneyflow_ind_ths_latest", "moneyflow_ind_ths", {"trade_date": completed_trade_date}),
            ("moneyflow_ind_dc_latest", "moneyflow_ind_dc", {"trade_date": completed_trade_date}),
            ("margin_detail_latest", "margin_detail", {"trade_date": completed_trade_date}),
        ]
        for name, api_name, params in selected_trading:
            result = _collect(
                data_gaps,
                "trading",
                name,
                _source(api_name, params),
                lambda api_name=api_name, params=params: resolved_provider.call(api_name, params=params),
                max_rows=max_rows_per_dataset,
            )
            if result is not None:
                context["trading"][name] = result

    shareholder_specs = [
        ("top10_holders", "top10_holders"),
        ("top10_floatholders", "top10_floatholders"),
        ("stk_holdertrade", "stk_holdertrade"),
        ("pledge_stat", "pledge_stat"),
        ("pledge_detail", "pledge_detail"),
        ("share_float", "share_float"),
        ("repurchase", "repurchase"),
    ]
    if profile == "full":
        for name, api_name in shareholder_specs:
            params = {"ts_code": ts_code}
            result = _collect(
                data_gaps,
                "fundamentals",
                name,
                _source(api_name, params),
                lambda api_name=api_name, params=params: resolved_provider.call(api_name, params=params),
                max_rows=max_rows_per_dataset,
            )
            if result is not None:
                context["fundamentals"][name] = result

    if profile in {"standard", "full"}:
        notice_params = {"days": event_days, "end_date": anchor_text, "stock": symbol, "category": "全部"}
        notices = _collect(
            data_gaps,
            "events",
            "announcements",
            _event_source("a_stock_notice", notice_params),
            lambda: resolved_provider.a_stock_notice(days=event_days, end_date=anchor_text, stock=symbol, category="全部"),
            max_rows=max_rows_per_dataset,
        )
        if notices is not None:
            context["events"]["announcements"] = notices

        forecast_params = {"days": forecast_days, "end_date": anchor_text, "stock": symbol}
        forecasts = _collect(
            data_gaps,
            "events",
            "earnings_forecast",
            _event_source("earnings_forecast", forecast_params),
            lambda: resolved_provider.earnings_forecast(days=forecast_days, end_date=anchor_text, stock=symbol),
            max_rows=max_rows_per_dataset,
        )
        if forecasts is not None:
            context["events"]["earnings_forecast"] = forecasts

    if include_news:
        news_params = {"sources": list(news_sources or []), "anchor_date": anchor_text}
        news = _collect(
            data_gaps,
            "events",
            "event_news",
            _event_source("event_news", news_params),
            lambda: resolved_provider.event_news(sources=news_sources, anchor_date=anchor_text),
            max_rows=max_rows_per_dataset,
        )
        if news is not None:
            context["events"]["event_news"] = news

    return context

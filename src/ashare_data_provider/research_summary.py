from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _records(dataset: Any) -> list[dict[str, Any]]:
    if not isinstance(dataset, dict):
        return []
    records = dataset.get("records")
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]


def _first_record(dataset: Any) -> dict[str, Any]:
    records = _records(dataset)
    return records[0] if records else {}


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _round(value: Any, digits: int = 2) -> float | None:
    number = _num(value)
    return None if number is None else round(number, digits)


def _pct_change(start: Any, end: Any) -> float | None:
    start_number = _num(start)
    end_number = _num(end)
    if start_number in {None, 0} or end_number is None:
        return None
    return round((end_number / start_number - 1) * 100, 4)


def _safe_div(numerator: Any, denominator: Any, multiplier: float = 1.0) -> float | None:
    top = _num(numerator)
    bottom = _num(denominator)
    if top is None or bottom in {None, 0}:
        return None
    return top / bottom * multiplier


def _sorted_by_date(records: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    return sorted(records, key=lambda record: str(record.get(key) or ""))


def _moving_average(values: list[Any], window: int) -> float | None:
    numbers = [_num(value) for value in values[-window:]]
    if len(numbers) < window or any(value is None for value in numbers):
        return None
    return round(sum(value for value in numbers if value is not None) / window, 4)


def _max_number(values: list[Any]) -> float | None:
    numbers = [_num(value) for value in values]
    clean_numbers = [value for value in numbers if value is not None]
    return max(clean_numbers) if clean_numbers else None


def _min_number(values: list[Any]) -> float | None:
    numbers = [_num(value) for value in values]
    clean_numbers = [value for value in numbers if value is not None]
    return min(clean_numbers) if clean_numbers else None


def _atr(records: list[dict[str, Any]], period: int = 14) -> float | None:
    if len(records) < period + 1:
        return None
    true_ranges: list[float] = []
    sorted_records = _sorted_by_date(records, "trade_date")
    for index, record in enumerate(sorted_records[1:], start=1):
        high = _num(record.get("high"))
        low = _num(record.get("low"))
        prev_close = _num(record.get("pre_close"))
        if prev_close is None:
            prev_close = _num(sorted_records[index - 1].get("close"))
        if high is None or low is None or prev_close is None:
            continue
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    if len(true_ranges) < period:
        return None
    return round(sum(true_ranges[-period:]) / period, 4)


def _match_end_date(dataset: Any, end_date: str | None) -> dict[str, Any]:
    if not end_date:
        return {}
    for record in _records(dataset):
        if str(record.get("end_date")) == str(end_date):
            return record
    return {}


def _latest_annual(records: list[dict[str, Any]]) -> dict[str, Any]:
    for record in records:
        if str(record.get("end_type")) == "4" or str(record.get("end_date", "")).endswith("1231"):
            return record
    return {}


def _source_ref(dataset: Any) -> dict[str, Any]:
    if not isinstance(dataset, dict):
        return {}
    source = dataset.get("source")
    if not isinstance(source, dict):
        return {}
    return {
        "kind": source.get("kind"),
        "api_name": source.get("api_name"),
        "source": source.get("source"),
        "params": source.get("params", {}),
    }


def _allowed_source_classes(context: dict[str, Any]) -> set[str]:
    discovery = context.get("source_policy", {}).get("dynamic_source_discovery", {})
    source_classes = discovery.get("source_classes", []) if isinstance(discovery, dict) else []
    return {str(item.get("id")) for item in source_classes if isinstance(item, dict) and item.get("id")}


def _market_summary(context: dict[str, Any]) -> dict[str, Any]:
    market = context.get("market", {})
    daily = _first_record(market.get("daily_latest"))
    daily_basic = _first_record(market.get("daily_basic_latest"))
    limit_price = _first_record(market.get("limit_price_latest"))
    history = _sorted_by_date(_records(market.get("daily_history")), "trade_date")

    closes = [record.get("close") for record in history]
    volumes = [record.get("vol") for record in history]
    latest_close = daily.get("close")
    avg_volume_20 = _moving_average(volumes, min(20, len(volumes))) if volumes else None
    atr14 = _atr(history, period=14)

    metrics: dict[str, Any] = {
        "latest_trade_date": daily.get("trade_date"),
        "latest_close": _round(latest_close, 4),
        "latest_pct_chg": _round(daily.get("pct_chg"), 4),
        "latest_amount_100m_yuan": _round(_safe_div(daily.get("amount"), 100000), 4),
        "turnover_rate": _round(daily_basic.get("turnover_rate"), 4),
        "volume_ratio": _round(daily_basic.get("volume_ratio"), 4),
        "pe_ttm": _round(daily_basic.get("pe_ttm"), 4),
        "pb": _round(daily_basic.get("pb"), 4),
        "total_mv_100m_yuan": _round(_safe_div(daily_basic.get("total_mv"), 10000), 4),
        "up_limit": _round(limit_price.get("up_limit"), 4),
        "down_limit": _round(limit_price.get("down_limit"), 4),
        "source": {
            "daily": _source_ref(market.get("daily_latest")),
            "daily_basic": _source_ref(market.get("daily_basic_latest")),
        },
    }

    if history:
        metrics.update(
            {
                "history_first_trade_date": history[0].get("trade_date"),
                "history_last_trade_date": history[-1].get("trade_date"),
                "history_row_count": len(history),
                "history_return_pct": _pct_change(history[0].get("close"), history[-1].get("close")),
                "return_5_session_pct": _pct_change(history[-6].get("close"), history[-1].get("close")) if len(history) >= 6 else None,
                "return_10_session_pct": _pct_change(history[-11].get("close"), history[-1].get("close")) if len(history) >= 11 else None,
                "ma5": _moving_average(closes, 5),
                "ma10": _moving_average(closes, 10),
                "ma20": _moving_average(closes, 20),
                "high_20": _round(_max_number([record.get("high") for record in history[-20:]]), 4),
                "low_20": _round(_min_number([record.get("low") for record in history[-20:]]), 4),
                "avg_volume_20": _round(avg_volume_20, 4),
                "latest_volume_vs_avg20": _round(_safe_div(daily.get("vol"), avg_volume_20), 4),
                "atr14": atr14,
                "atr14_pct_of_close": _round(_safe_div(atr14, latest_close, 100), 4),
            }
        )
    return metrics


def _main_business_summary(context: dict[str, Any], max_segments: int) -> dict[str, Any]:
    rows = _records(context.get("fundamentals", {}).get("fina_mainbz"))
    if not rows:
        return {"segments": [], "source": _source_ref(context.get("fundamentals", {}).get("fina_mainbz"))}
    latest_end_date = max(str(record.get("end_date") or "") for record in rows)
    latest_rows = [record for record in rows if str(record.get("end_date") or "") == latest_end_date]
    product_rows = [record for record in latest_rows if record.get("bz_code") == "P"]
    selected_rows = product_rows or latest_rows
    total_sales = sum(_num(record.get("bz_sales")) or 0 for record in product_rows)

    segments = []
    for record in selected_rows:
        sales = _num(record.get("bz_sales"))
        profit = _num(record.get("bz_profit"))
        segments.append(
            {
                "end_date": record.get("end_date"),
                "type": record.get("bz_code"),
                "item": record.get("bz_item"),
                "sales_100m_yuan": _round(_safe_div(sales, 100000000), 4),
                "profit_100m_yuan": _round(_safe_div(profit, 100000000), 4),
                "gross_margin_pct": _round(_safe_div(profit, sales, 100), 4),
                "sales_pct_of_product_total": _round(_safe_div(sales, total_sales, 100), 4) if record.get("bz_code") == "P" else None,
            }
        )
    segments.sort(key=lambda item: _num(item.get("sales_100m_yuan")) or 0, reverse=True)
    return {
        "latest_end_date": latest_end_date,
        "segments": segments[:max_segments],
        "source": _source_ref(context.get("fundamentals", {}).get("fina_mainbz")),
    }


def _fundamental_summary(context: dict[str, Any], max_segments: int) -> dict[str, Any]:
    fundamentals = context.get("fundamentals", {})
    income_records = _records(fundamentals.get("income"))
    latest_income = income_records[0] if income_records else {}
    annual_income = _latest_annual(income_records)
    latest_end_date = str(latest_income.get("end_date") or "")
    annual_end_date = str(annual_income.get("end_date") or "")
    latest_indicator = _match_end_date(fundamentals.get("fina_indicator"), latest_end_date)
    annual_indicator = _match_end_date(fundamentals.get("fina_indicator"), annual_end_date)
    latest_cashflow = _match_end_date(fundamentals.get("cashflow"), latest_end_date)
    annual_cashflow = _match_end_date(fundamentals.get("cashflow"), annual_end_date)
    latest_balance = _match_end_date(fundamentals.get("balancesheet"), latest_end_date)
    disclosure_records = _records(fundamentals.get("disclosure_date"))
    disclosure_source = _source_ref(fundamentals.get("disclosure_date"))
    disclosure_queries = disclosure_source.get("params", {}).get("queries", [])
    disclosure_periods = [
        str(query.get("end_date"))
        for query in disclosure_queries
        if isinstance(query, dict) and query.get("end_date")
    ]
    disclosure_record_periods = {str(record.get("end_date")) for record in disclosure_records if record.get("end_date")}
    latest_completed_report_period = disclosure_periods[0] if disclosure_periods else None
    latest_completed_report_record = _match_end_date(fundamentals.get("disclosure_date"), latest_completed_report_period)

    return {
        "latest_period": {
            "end_date": latest_end_date or None,
            "ann_date": latest_income.get("ann_date"),
            "revenue_100m_yuan": _round(_safe_div(latest_income.get("revenue") or latest_income.get("total_revenue"), 100000000), 4),
            "n_income_attr_p_100m_yuan": _round(_safe_div(latest_income.get("n_income_attr_p"), 100000000), 4),
            "profit_dedt_100m_yuan": _round(_safe_div(latest_indicator.get("profit_dedt"), 100000000), 4),
            "revenue_yoy_pct": _round(latest_indicator.get("or_yoy") or latest_indicator.get("tr_yoy"), 4),
            "netprofit_yoy_pct": _round(latest_indicator.get("netprofit_yoy"), 4),
            "dt_netprofit_yoy_pct": _round(latest_indicator.get("dt_netprofit_yoy"), 4),
            "grossprofit_margin_pct": _round(latest_indicator.get("grossprofit_margin"), 4),
            "netprofit_margin_pct": _round(latest_indicator.get("netprofit_margin"), 4),
            "roe_pct": _round(latest_indicator.get("roe"), 4),
            "debt_to_assets_pct": _round(latest_indicator.get("debt_to_assets"), 4),
            "operating_cashflow_100m_yuan": _round(_safe_div(latest_cashflow.get("n_cashflow_act"), 100000000), 4),
            "sales_cash_to_revenue_pct": _round(
                _safe_div(latest_cashflow.get("c_fr_sale_sg"), latest_income.get("revenue") or latest_income.get("total_revenue"), 100),
                4,
            ),
            "ar_turn": _round(latest_indicator.get("ar_turn"), 4),
            "accounts_receiv_100m_yuan": _round(
                _safe_div(
                    latest_balance.get("accounts_receiv")
                    or latest_balance.get("acct_rcv")
                    or latest_balance.get("notes_receiv"),
                    100000000,
                ),
                4,
            ),
        },
        "latest_annual": {
            "end_date": annual_end_date or None,
            "revenue_100m_yuan": _round(_safe_div(annual_income.get("revenue") or annual_income.get("total_revenue"), 100000000), 4),
            "n_income_attr_p_100m_yuan": _round(_safe_div(annual_income.get("n_income_attr_p"), 100000000), 4),
            "operating_cashflow_100m_yuan": _round(_safe_div(annual_cashflow.get("n_cashflow_act"), 100000000), 4),
            "revenue_yoy_pct": _round(annual_indicator.get("or_yoy") or annual_indicator.get("tr_yoy"), 4),
            "netprofit_yoy_pct": _round(annual_indicator.get("netprofit_yoy"), 4),
            "dt_netprofit_yoy_pct": _round(annual_indicator.get("dt_netprofit_yoy"), 4),
            "grossprofit_margin_pct": _round(annual_indicator.get("grossprofit_margin"), 4),
            "netprofit_margin_pct": _round(annual_indicator.get("netprofit_margin"), 4),
        },
        "main_business": _main_business_summary(context, max_segments=max_segments),
        "disclosure_date": {
            "latest_completed_report_period": latest_completed_report_period,
            "latest_completed_report_record": latest_completed_report_record or None,
            "queried_periods": disclosure_periods,
            "missing_queried_periods": [period for period in disclosure_periods if period not in disclosure_record_periods],
            "records": disclosure_records[:5],
            "source": disclosure_source,
        },
        "dividend": {
            "records": _records(fundamentals.get("dividend"))[:5],
            "source": _source_ref(fundamentals.get("dividend")),
        },
        "fina_audit": {
            "records": _records(fundamentals.get("fina_audit"))[:5],
            "source": _source_ref(fundamentals.get("fina_audit")),
        },
        "sources": {
            "income": _source_ref(fundamentals.get("income")),
            "cashflow": _source_ref(fundamentals.get("cashflow")),
            "fina_indicator": _source_ref(fundamentals.get("fina_indicator")),
            "balancesheet": _source_ref(fundamentals.get("balancesheet")),
        },
    }


EVENT_KEYWORDS = {
    "orders_contracts": ["中标", "合同", "订单", "项目"],
    "customers_capacity": ["客户", "产能", "产量", "销量", "利用率"],
    "cash_collection": ["回款", "应收", "合同资产", "现金流"],
    "shareholder_changes": ["减持", "增持", "质押", "回购"],
    "dividend": ["权益分派", "分红", "利润分配"],
    "reports_ir": ["年度报告", "半年度报告", "季度报告", "投资者关系", "调研"],
    "governance": ["董事", "监事", "高管", "股东会", "ESG"],
}


def _event_summary(context: dict[str, Any], max_events: int) -> dict[str, Any]:
    events = context.get("events", {})
    announcements = _records(events.get("announcements"))
    clues: dict[str, list[dict[str, Any]]] = {name: [] for name in EVENT_KEYWORDS}
    for record in announcements:
        title = str(record.get("title") or "")
        notice_type = str(record.get("notice_type") or "")
        searchable = title + " " + notice_type
        for category, keywords in EVENT_KEYWORDS.items():
            if any(keyword in searchable for keyword in keywords):
                clues[category].append(
                    {
                        "publish_date": record.get("publish_date"),
                        "title": record.get("title"),
                        "notice_type": record.get("notice_type"),
                        "url": record.get("url"),
                    }
                )

    return {
        "recent_announcements": [
            {
                "publish_date": record.get("publish_date"),
                "title": record.get("title"),
                "notice_type": record.get("notice_type"),
                "url": record.get("url"),
            }
            for record in announcements[:max_events]
        ],
        "announcement_clues": {category: records[:max_events] for category, records in clues.items() if records},
        "earnings_forecast": {
            "records": _records(events.get("earnings_forecast"))[:max_events],
            "source": _source_ref(events.get("earnings_forecast")),
        },
        "source": _source_ref(events.get("announcements")),
    }


EXTERNAL_EVIDENCE_REQUIRED_FIELDS = ("fact", "source_class", "url", "query_time")


def _external_evidence_summary(context: dict[str, Any]) -> dict[str, Any]:
    raw_records = context.get("external_evidence", [])
    if not isinstance(raw_records, list):
        raw_records = []
    allowed_source_classes = _allowed_source_classes(context)
    records = []
    for raw_record in raw_records:
        if not isinstance(raw_record, dict):
            continue
        missing_fields = [field for field in EXTERNAL_EVIDENCE_REQUIRED_FIELDS if not raw_record.get(field)]
        source_class = str(raw_record.get("source_class") or "")
        if allowed_source_classes and source_class and source_class not in allowed_source_classes:
            missing_fields.append("known_source_class")
        records.append(
            {
                "fact": raw_record.get("fact"),
                "source_class": raw_record.get("source_class"),
                "source_name": raw_record.get("source_name"),
                "url": raw_record.get("url"),
                "query_time": raw_record.get("query_time"),
                "publish_date": raw_record.get("publish_date"),
                "business_segment": raw_record.get("business_segment"),
                "supports_need": raw_record.get("supports_need"),
                "evidence_level": raw_record.get("evidence_level"),
                "confidence": raw_record.get("confidence"),
                "missing_fields": missing_fields,
                "valid": not missing_fields,
            }
        )
    return {
        "schema": "ashare.external_evidence.v1",
        "required_fields": list(EXTERNAL_EVIDENCE_REQUIRED_FIELDS),
        "allowed_source_classes": sorted(allowed_source_classes),
        "count": len(records),
        "valid_count": sum(1 for record in records if record["valid"]),
        "records": records,
    }


def _evidence_covers(external_evidence: dict[str, Any], need_id: str) -> bool:
    for record in external_evidence.get("records", []):
        if not record.get("valid"):
            continue
        supports_need = record.get("supports_need")
        if supports_need == need_id:
            return True
        if isinstance(supports_need, list) and need_id in supports_need:
            return True
    return False


def _top_business_segments(fundamentals: dict[str, Any], limit: int = 5) -> list[str]:
    segments = fundamentals.get("main_business", {}).get("segments", [])
    if not isinstance(segments, list):
        return []
    return [str(segment.get("item")) for segment in segments[:limit] if isinstance(segment, dict) and segment.get("item")]


def _research_needs(summary: dict[str, Any]) -> dict[str, Any]:
    fundamentals = summary.get("fundamentals", {})
    events = summary.get("events", {})
    external_evidence = summary.get("external_evidence", {})
    segments = _top_business_segments(fundamentals)
    announcement_clues = events.get("announcement_clues", {}) if isinstance(events.get("announcement_clues"), dict) else {}
    disclosure_records = fundamentals.get("disclosure_date", {}).get("records", [])
    latest_completed_report_period = fundamentals.get("disclosure_date", {}).get("latest_completed_report_period")
    latest_completed_report_record = fundamentals.get("disclosure_date", {}).get("latest_completed_report_record")
    latest_period = fundamentals.get("latest_period", {})

    definitions = [
        {
            "id": "financial_disclosure_schedule",
            "layer": "fundamentals",
            "question": "最新已完成报告期及最近几期财报披露计划和实际披露状态。",
            "default_status": "covered_by_project_data" if latest_completed_report_record else "needs_official_filing_evidence",
            "reason": f"full context 已采集最新已完成报告期 {latest_completed_report_period} 的 disclosure_date。"
            if latest_completed_report_record
            else f"未找到最新已完成报告期 {latest_completed_report_period or '未知'} 的 disclosure_date 记录，需要交易所、巨潮或公司公告补充。",
            "suggested_source_classes": ["exchange_or_disclosure_platform", "listed_company_official"],
            "business_segments": [],
        },
        {
            "id": "cash_collection_quality",
            "layer": "fundamentals",
            "question": "经营现金流、销售收现、应收账款和合同资产是否验证回款质量。",
            "default_status": "covered_by_project_data"
            if latest_period.get("operating_cashflow_100m_yuan") is not None or latest_period.get("sales_cash_to_revenue_pct") is not None
            else "needs_filing_extraction",
            "reason": "summary 已从 cashflow/income/balancesheet 计算回款指标。"
            if latest_period.get("operating_cashflow_100m_yuan") is not None or latest_period.get("sales_cash_to_revenue_pct") is not None
            else "结构化财报未覆盖足够回款指标，需要定期报告正文补充。",
            "suggested_source_classes": ["exchange_or_disclosure_platform", "listed_company_official"],
            "business_segments": segments,
        },
        {
            "id": "industry_policy",
            "layer": "macro_policy",
            "question": "目标公司主营分部对应的行业政策、补贴、监管或标准是否构成顺风/逆风。",
            "default_status": "needs_external_evidence",
            "reason": "通用 Provider 只能覆盖宏观数据，行业政策需要按行业动态发现规则查官方或监管来源。",
            "suggested_source_classes": ["official_government_or_regulator"],
            "business_segments": segments,
        },
        {
            "id": "industry_supply_demand",
            "layer": "industry_fundamentals",
            "question": "主营分部对应行业的供需、装机/产销、价格、利用率或招投标景气度。",
            "default_status": "needs_external_evidence",
            "reason": "通用 Provider 不覆盖所有行业供需指标，需要官方、协会、指定发布方或交易所/指数来源。",
            "suggested_source_classes": [
                "official_government_or_regulator",
                "industry_association_or_designated_publisher",
                "commodity_exchange_or_index_provider",
            ],
            "business_segments": segments,
        },
        {
            "id": "orders_contracts",
            "layer": "industry_fundamentals",
            "question": "公司级中标、重大合同、在手订单和新签订单是否验证增长。",
            "default_status": "has_announcement_clues_needs_filing_extraction"
            if announcement_clues.get("orders_contracts")
            else "needs_company_or_filing_evidence",
            "reason": "公告标题存在订单/合同线索，需要抽取正文金额、期限、客户和项目地。"
            if announcement_clues.get("orders_contracts")
            else "未发现订单/合同结构化事实，不能用行业新闻替代公司披露。",
            "suggested_source_classes": ["exchange_or_disclosure_platform", "listed_company_official"],
            "business_segments": segments,
        },
        {
            "id": "customers_capacity",
            "layer": "industry_fundamentals",
            "question": "客户集中度、核心客户、产能、产量、销量和利用率。",
            "default_status": "has_announcement_clues_needs_filing_extraction"
            if announcement_clues.get("customers_capacity")
            else "needs_company_or_filing_evidence",
            "reason": "客户/产能属于公司级事实，必须来自年报、公告、公司 IR 或官方互动平台。",
            "suggested_source_classes": ["exchange_or_disclosure_platform", "listed_company_official"],
            "business_segments": segments,
        },
        {
            "id": "market_share_competition",
            "layer": "industry_fundamentals",
            "question": "公司市场份额、竞争格局和可比公司位置。",
            "default_status": "needs_external_evidence",
            "reason": "通用财务数据不能证明市占率或竞争壁垒，需要行业协会、公司披露或权威研究材料。",
            "suggested_source_classes": [
                "industry_association_or_designated_publisher",
                "exchange_or_disclosure_platform",
                "listed_company_official",
            ],
            "business_segments": segments,
        },
    ]

    needs = []
    for definition in definitions:
        status = "covered_by_external_evidence" if _evidence_covers(external_evidence, definition["id"]) else definition["default_status"]
        needs.append({**definition, "status": status})

    analysis_gaps = [
        need
        for need in needs
        if need["status"]
        not in {
            "covered_by_project_data",
            "covered_by_external_evidence",
        }
    ]
    return {
        "schema": "ashare.research_needs.v1",
        "items": needs,
        "analysis_gaps": analysis_gaps,
    }


def build_research_summary(context: dict[str, Any], max_events: int = 10, max_segments: int = 12) -> dict[str, Any]:
    target = context.get("target", {})
    stock_basic = target.get("stock_basic") if isinstance(target.get("stock_basic"), dict) else {}
    summary = {
        "schema": "ashare.research_summary.v1",
        "generated_from": {
            "context_schema": context.get("schema"),
            "context_generated_at": context.get("generated_at"),
            "as_of": context.get("as_of"),
            "completed_trade_date": context.get("calendar", {}).get("completed_trade_date"),
            "profile": context.get("profile"),
        },
        "target": {
            "ts_code": context.get("ts_code"),
            "symbol": context.get("symbol"),
            "name": stock_basic.get("name"),
            "industry": stock_basic.get("industry"),
            "area": stock_basic.get("area"),
            "market": stock_basic.get("market"),
            "list_status": stock_basic.get("list_status"),
        },
        "market": _market_summary(context),
        "fundamentals": _fundamental_summary(context, max_segments=max_segments),
        "events": _event_summary(context, max_events=max_events),
        "external_evidence": _external_evidence_summary(context),
        "data_gaps": {
            "count": len(context.get("data_gaps", [])) if isinstance(context.get("data_gaps"), list) else 0,
            "items": context.get("data_gaps", []) if isinstance(context.get("data_gaps"), list) else [],
        },
        "skipped_sources": context.get("skipped_sources", []),
    }
    summary["research_needs"] = _research_needs(summary)
    return summary


def load_research_context(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def render_research_summary_markdown(summary: dict[str, Any]) -> str:
    target = summary.get("target", {})
    generated_from = summary.get("generated_from", {})
    market = summary.get("market", {})
    latest_period = summary.get("fundamentals", {}).get("latest_period", {})
    annual = summary.get("fundamentals", {}).get("latest_annual", {})
    main_business = summary.get("fundamentals", {}).get("main_business", {})
    events = summary.get("events", {})
    research_needs = summary.get("research_needs", {})
    external_evidence = summary.get("external_evidence", {})

    lines = [
        "# Research Summary",
        "",
        f"- 标的：{target.get('name') or ''} `{target.get('ts_code') or ''}`",
        f"- 行业/市场：{target.get('industry') or '暂无可靠数据'} / {target.get('market') or '暂无可靠数据'}",
        f"- Context：{generated_from.get('context_generated_at') or '暂无可靠数据'}，最近完成交易日：{generated_from.get('completed_trade_date') or '暂无可靠数据'}",
        f"- Data gaps：{summary.get('data_gaps', {}).get('count', 0)}",
        "",
        "## Market",
        "",
        f"- 收盘：{market.get('latest_close')}，涨跌幅：{market.get('latest_pct_chg')}%，成交额：{market.get('latest_amount_100m_yuan')}亿元",
        f"- 估值：PE TTM {market.get('pe_ttm')}，PB {market.get('pb')}，总市值 {market.get('total_mv_100m_yuan')}亿元",
        f"- 结构：区间收益 {market.get('history_return_pct')}%，MA5/10/20={market.get('ma5')}/{market.get('ma10')}/{market.get('ma20')}，ATR14={market.get('atr14')}",
        "",
        "## Fundamentals",
        "",
        f"- 最新报告期：{latest_period.get('end_date')}，营收 {latest_period.get('revenue_100m_yuan')}亿元，归母净利 {latest_period.get('n_income_attr_p_100m_yuan')}亿元",
        f"- 同比：营收 {latest_period.get('revenue_yoy_pct')}%，归母净利 {latest_period.get('netprofit_yoy_pct')}%，扣非净利 {latest_period.get('dt_netprofit_yoy_pct')}%",
        f"- 现金流：经营现金流 {latest_period.get('operating_cashflow_100m_yuan')}亿元，销售收现/营收 {latest_period.get('sales_cash_to_revenue_pct')}%",
        f"- 最新年报：{annual.get('end_date')}，营收 {annual.get('revenue_100m_yuan')}亿元，归母净利 {annual.get('n_income_attr_p_100m_yuan')}亿元",
        "",
        "## Main Business",
        "",
    ]
    for segment in main_business.get("segments", [])[:8]:
        lines.append(
            f"- {segment.get('item')}：收入 {segment.get('sales_100m_yuan')}亿元，毛利率 {segment.get('gross_margin_pct')}%"
        )

    lines.extend(["", "## Event Clues", ""])
    for category, records in events.get("announcement_clues", {}).items():
        titles = "；".join(str(record.get("title") or "") for record in records[:3])
        lines.append(f"- {category}: {titles or '暂无可靠数据'}")
    if not events.get("announcement_clues"):
        lines.append("- 暂无公告标题线索")

    lines.extend(["", "## Research Needs", ""])
    needs = research_needs.get("items", []) if isinstance(research_needs, dict) else []
    for need in needs:
        if not isinstance(need, dict):
            continue
        lines.append(f"- {need.get('id')}: {need.get('status')}，{need.get('question')}")
    lines.extend(["", "## External Evidence", ""])
    lines.append(
        f"- records={external_evidence.get('count', 0) if isinstance(external_evidence, dict) else 0}, "
        f"valid={external_evidence.get('valid_count', 0) if isinstance(external_evidence, dict) else 0}"
    )

    return "\n".join(lines).rstrip() + "\n"

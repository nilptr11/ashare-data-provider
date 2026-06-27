from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from ..storage import SourceFetchResult
from .base import SourceAdapterError
from .http import HttpTransport, urllib_get_text


TENCENT_GLOBAL_QUOTE_URL = "https://qt.gtimg.cn/"


class TencentGlobalQuoteAdapter:
    def __init__(
        self,
        *,
        source_id: str = "global_tencent_quote",
        timeout: int = 30,
        transport: HttpTransport | None = None,
    ) -> None:
        self.source_id = source_id
        self.timeout = timeout
        self.transport = transport or urllib_get_text

    def fetch(
        self,
        api_name: str,
        params: dict[str, Any],
        fields: tuple[str, ...] | list[str] | None = None,
    ) -> SourceFetchResult:
        if api_name != "qt.global_quote_snapshot":
            raise SourceAdapterError(f"Tencent global quote api is not implemented: {api_name}")
        symbols = resolve_global_tencent_symbols(params)
        snapshot_at = str(params.get("snapshot_at", "") or now_iso())
        response = self.transport(
            TENCENT_GLOBAL_QUOTE_URL,
            {"q": ",".join(symbols)},
            {"User-Agent": "research-data-foundation/0.1", "Referer": "https://gu.qq.com/"},
            self.timeout,
        )
        rows = parse_global_tencent_quote_response(response.text, snapshot_at=snapshot_at, source_url=response.url)
        return SourceFetchResult(
            source_id=self.source_id,
            api_name=api_name,
            params={"symbols": ",".join(symbols), "snapshot_at": snapshot_at},
            requested_at=now_iso(),
            frame=pd.DataFrame(rows),
            metadata={"adapter": "TencentGlobalQuoteAdapter", "endpoint": TENCENT_GLOBAL_QUOTE_URL},
        )


def resolve_global_tencent_symbols(params: dict[str, Any]) -> tuple[str, ...]:
    raw_symbols = str(params.get("symbols", "") or "").strip()
    if raw_symbols:
        symbols = tuple(item.strip() for item in raw_symbols.split(",") if item.strip())
    else:
        raw_tickers = params.get("tickers") or params.get("ticker") or ""
        if isinstance(raw_tickers, str):
            tickers = [item.strip() for item in raw_tickers.split(",") if item.strip()]
        else:
            tickers = [str(item).strip() for item in raw_tickers if str(item).strip()]
        symbols = tuple(ticker_to_global_tencent_symbol(value) for value in tickers)
    if not symbols:
        raise SourceAdapterError("qt.global_quote_snapshot requires symbols or tickers")
    return tuple(normalize_global_tencent_symbol(value) for value in symbols)


def ticker_to_global_tencent_symbol(value: str) -> str:
    text = value.strip()
    lower = text.lower()
    if lower.startswith("us") and len(text) > 2:
        return f"us{text[2:].upper()}"
    if lower.startswith("r_hk") and len(text) > 4:
        return f"r_hk{digits_5(text[4:])}"
    upper = text.upper()
    if upper.endswith(".HK"):
        return f"r_hk{digits_5(upper[:-3])}"
    if upper.startswith("HK.") or upper.startswith("HK:"):
        return f"r_hk{digits_5(upper[3:])}"
    if upper.isdigit():
        return f"r_hk{digits_5(upper)}"
    if re.fullmatch(r"[A-Z][A-Z0-9.\-]*", upper):
        return f"us{upper}"
    raise SourceAdapterError(f"unsupported global ticker for Tencent quote: {value}")


def normalize_global_tencent_symbol(value: str) -> str:
    text = value.strip()
    lower = text.lower()
    if lower.startswith("r_hk"):
        return f"r_hk{digits_5(text[4:])}"
    if lower.startswith("us") and len(text) > 2:
        return f"us{text[2:].upper()}"
    return ticker_to_global_tencent_symbol(text)


def digits_5(value: str) -> str:
    digits = "".join(char for char in str(value) if char.isdigit())
    if not digits:
        raise SourceAdapterError(f"invalid HK ticker: {value}")
    return digits.zfill(5)


def parse_global_tencent_quote_response(text: str, *, snapshot_at: str, source_url: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for symbol, payload in re.findall(r"v_([A-Za-z0-9_]+)=\"(.*?)\";", text, flags=re.DOTALL):
        fields = payload.split("~")
        if len(fields) < 35:
            continue
        market = "hk" if symbol.lower().startswith("r_hk") else "us"
        currency = fields[75] if market == "hk" and len(fields) > 75 else fields[35] if len(fields) > 35 else ""
        rows.append(
            {
                "symbol": normalized_output_symbol(symbol),
                "market": market,
                "code": fields[2],
                "name": fields[46] if len(fields) > 46 and fields[46] else fields[1],
                "local_name": fields[1],
                "price": to_number(fields[3]),
                "prev_close": to_number(fields[4]),
                "open": to_number(fields[5]),
                "volume": to_number(fields[36] if len(fields) > 36 else fields[6]),
                "amount": to_number(fields[37] if len(fields) > 37 else ""),
                "change": to_number(fields[31] if len(fields) > 31 else ""),
                "pct_chg": to_number(fields[32] if len(fields) > 32 else ""),
                "high": to_number(fields[33] if len(fields) > 33 else ""),
                "low": to_number(fields[34] if len(fields) > 34 else ""),
                "currency": currency,
                "quote_time": fields[30] if len(fields) > 30 else "",
                "snapshot_at": snapshot_at,
                "quote_source": "global_tencent_quote",
                "source_url": source_url,
            }
        )
    return rows


def normalized_output_symbol(symbol: str) -> str:
    lower = symbol.lower()
    if lower.startswith("r_hk"):
        return f"{lower[4:]}.HK"
    if lower.startswith("us"):
        return symbol[2:].upper()
    return symbol.upper()


def to_number(value: Any) -> float | None:
    text = str(value).strip()
    if not text or text == "-":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")

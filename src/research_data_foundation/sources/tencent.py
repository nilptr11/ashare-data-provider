from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from ..storage import SourceFetchResult
from .base import SourceAdapterError
from .http import HttpTransport, urllib_get_text


TENCENT_QUOTE_URL = "https://qt.gtimg.cn/"


class TencentQuoteAdapter:
    def __init__(
        self,
        *,
        source_id: str = "tencent_quote",
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
        if api_name != "qt.quote_snapshot":
            raise SourceAdapterError(f"Tencent quote api is not implemented: {api_name}")
        symbols = resolve_tencent_symbols(params)
        snapshot_at = str(params.get("snapshot_at", "") or now_iso())
        request_params = {"q": ",".join(symbols)}
        response = self.transport(
            TENCENT_QUOTE_URL,
            request_params,
            {"User-Agent": "research-data-foundation/0.1", "Referer": "https://stockapp.finance.qq.com/"},
            self.timeout,
        )
        rows = parse_tencent_quote_response(response.text, snapshot_at=snapshot_at, source_url=response.url)
        return SourceFetchResult(
            source_id=self.source_id,
            api_name=api_name,
            params={"symbols": request_params["q"], "snapshot_at": snapshot_at},
            requested_at=now_iso(),
            frame=pd.DataFrame(rows),
            metadata={"adapter": "TencentQuoteAdapter", "endpoint": TENCENT_QUOTE_URL},
        )


def resolve_tencent_symbols(params: dict[str, Any]) -> tuple[str, ...]:
    raw_symbols = str(params.get("symbols", "") or "").strip()
    if raw_symbols:
        return tuple(symbol.strip().lower() for symbol in raw_symbols.split(",") if symbol.strip())
    raw_security_ids = params.get("security_ids") or params.get("security_id") or ""
    if isinstance(raw_security_ids, str):
        values = [item.strip() for item in raw_security_ids.split(",") if item.strip()]
    else:
        values = [str(item).strip() for item in raw_security_ids if str(item).strip()]
    symbols = tuple(security_id_to_tencent_symbol(value) for value in values)
    if not symbols:
        raise SourceAdapterError("qt.quote_snapshot requires symbols or security_ids")
    return symbols


def security_id_to_tencent_symbol(value: str) -> str:
    item = value.strip().lower()
    if re.fullmatch(r"(sh|sz|bj)\d{6}", item):
        return item
    normalized = item.upper()
    if "." in normalized:
        code, exchange = normalized.split(".", 1)
        prefix = {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(exchange)
        if not prefix:
            raise SourceAdapterError(f"unsupported exchange for Tencent quote: {value}")
        return f"{prefix}{code}"
    if re.fullmatch(r"\d{6}", normalized):
        if normalized.startswith(("6", "5", "9")):
            return f"sh{normalized}"
        if normalized.startswith(("0", "2", "3")):
            return f"sz{normalized}"
        if normalized.startswith(("4", "8")):
            return f"bj{normalized}"
    raise SourceAdapterError(f"unsupported security id for Tencent quote: {value}")


def tencent_symbol_to_security_id(symbol: str) -> str:
    prefix = symbol[:2].lower()
    code = symbol[2:]
    exchange = {"sh": "SH", "sz": "SZ", "bj": "BJ"}.get(prefix, "")
    return f"{code}.{exchange}" if exchange else code


def parse_tencent_quote_response(text: str, *, snapshot_at: str, source_url: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for symbol, payload in re.findall(r"v_([a-z]{2}\d{6})=\"(.*?)\";", text, flags=re.IGNORECASE | re.DOTALL):
        fields = payload.split("~")
        if len(fields) < 35:
            continue
        volume, amount = parse_deal_triplet(fields[35] if len(fields) > 35 else "")
        rows.append(
            {
                "snapshot_at": snapshot_at,
                "security_id": tencent_symbol_to_security_id(symbol),
                "symbol": symbol.lower(),
                "name": fields[1],
                "code": fields[2],
                "price": to_number(fields[3]),
                "prev_close": to_number(fields[4]),
                "open": to_number(fields[5]),
                "volume": volume if volume is not None else to_number(fields[6]),
                "amount": amount if amount is not None else to_number(fields[37] if len(fields) > 37 else ""),
                "change": to_number(fields[31] if len(fields) > 31 else ""),
                "pct_chg": to_number(fields[32] if len(fields) > 32 else ""),
                "high": to_number(fields[33] if len(fields) > 33 else ""),
                "low": to_number(fields[34] if len(fields) > 34 else ""),
                "quote_time": parse_quote_time(fields[30] if len(fields) > 30 else ""),
                "quote_source": "tencent_quote",
                "source_url": source_url,
            }
        )
    return rows


def parse_deal_triplet(value: str) -> tuple[float | None, float | None]:
    parts = value.split("/")
    if len(parts) < 3:
        return None, None
    return to_number(parts[1]), to_number(parts[2])


def parse_quote_time(value: str) -> str:
    text = value.strip()
    if not re.fullmatch(r"\d{14}", text):
        return text
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}T{text[8:10]}:{text[10:12]}:{text[12:14]}+08:00"


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

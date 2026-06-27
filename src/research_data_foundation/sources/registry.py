from __future__ import annotations

from .base import SourceAdapter
from .cninfo import CninfoSourceAdapter
from .eastmoney import EastmoneySourceAdapter
from .sec_edgar import SecEdgarSourceAdapter
from .tencent import TencentQuoteAdapter
from .tushare import TushareSourceAdapter


def default_source_adapters() -> dict[str, SourceAdapter]:
    return {
        "eastmoney_direct": EastmoneySourceAdapter(),
        "eastmoney_intraday": EastmoneySourceAdapter(source_id="eastmoney_intraday"),
        "tencent_quote": TencentQuoteAdapter(),
        "cninfo": CninfoSourceAdapter(),
        "sec_edgar": SecEdgarSourceAdapter(),
        "tushare": TushareSourceAdapter(),
    }

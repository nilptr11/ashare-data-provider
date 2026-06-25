from __future__ import annotations

import os
from typing import Any

import pandas as pd

from ..schemas import ConnectorError, SourceResponse
from .base import now_iso


class TushareConnector:
    source = "tushare"

    def __init__(
        self,
        *,
        token: str | None = None,
        proxy_url: str | None = None,
        timeout: int = 30,
        client: Any = None,
    ) -> None:
        self.token = _normalize(token) or _normalize(os.environ.get("TUSHARE_TOKEN"))
        self.proxy_url = _normalize(proxy_url) if proxy_url is not None else _normalize(os.environ.get("TUSHARE_PROXY_URL"))
        self.timeout = _positive_int(timeout, "timeout")
        self._client = client

    def fetch(self, api_name: str, params: dict[str, Any], fields: list[str] | tuple[str, ...] | None = None) -> SourceResponse:
        client = self._client or self._build_client()
        requested_at = now_iso()
        requested_fields = tuple(fields or ())
        try:
            frame = client.query(api_name, fields=",".join(requested_fields) if requested_fields else None, **params)
        except Exception as error:  # pragma: no cover - wraps external SDK errors.
            raise ConnectorError(f"Tushare query failed for {api_name}: {error}") from error
        if frame is None:
            frame = pd.DataFrame()
        if not isinstance(frame, pd.DataFrame):
            frame = pd.DataFrame(frame)
        return SourceResponse(
            source=self.source,
            api_name=api_name,
            params=dict(params),
            fields=requested_fields,
            rows=len(frame),
            columns=tuple(str(column) for column in frame.columns),
            requested_at=requested_at,
            frame=frame,
        )

    def _build_client(self) -> Any:
        if not self.token:
            raise ConnectorError("TUSHARE_TOKEN is required for TushareConnector")
        try:
            import tushare as ts
        except ImportError as error:  # pragma: no cover - dependency exists in normal env.
            raise ConnectorError("tushare is not installed") from error
        configure_tushare_proxy(self.proxy_url)
        if self.proxy_url:
            os.environ["TUSHARE_PROXY_URL"] = self.proxy_url
        return ts.pro_api(self.token, timeout=self.timeout)


_TUSHARE_DEFAULT_HTTP_URL: str | None = None


def _normalize(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _positive_int(value: int, field_name: str) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as error:
        raise ConnectorError(f"{field_name} must be a positive integer") from error
    if normalized <= 0:
        raise ConnectorError(f"{field_name} must be a positive integer")
    return normalized


def configure_tushare_proxy(proxy_url: str | None) -> None:
    """Route Tushare SDK requests through the configured Pro HTTP endpoint."""
    global _TUSHARE_DEFAULT_HTTP_URL
    try:
        from tushare.pro import client as ts_client
    except ImportError as error:  # pragma: no cover - dependency exists in normal env.
        raise ConnectorError("tushare.pro.client is not installed") from error

    if _TUSHARE_DEFAULT_HTTP_URL is None:
        _TUSHARE_DEFAULT_HTTP_URL = ts_client.DataApi._DataApi__http_url
    ts_client.DataApi._DataApi__http_url = _normalize(proxy_url) or _TUSHARE_DEFAULT_HTTP_URL

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .local_store import (
    LocalDataEmptyError,
    LocalDataFormatError,
    LocalDataMissError,
    LocalDataStore,
    normalize_cache_mode,
    normalize_cache_params,
)


STABLE_CACHE_DATE_PARAMS = {"trade_date", "cal_date", "ann_date", "period"}


RemoteCall = Callable[[str, dict[str, Any] | None, str | None], Any]
TradeDatesBetween = Callable[[str, str], list[str]]


@dataclass(frozen=True)
class CachePolicy:
    split_by_trade_date: bool = True

    def enabled_for(self, api_name: str, params: dict[str, Any] | None, supports_trade_date: bool) -> bool:
        normalized_params = normalize_cache_params(params)
        if api_name == "trade_cal":
            return "cal_date" in normalized_params or ("start_date" in normalized_params and "end_date" in normalized_params)
        if any(param in normalized_params for param in STABLE_CACHE_DATE_PARAMS):
            return True
        return "start_date" in normalized_params and "end_date" in normalized_params and supports_trade_date

    def request_params(
        self,
        api_name: str,
        params: dict[str, Any] | None,
        supports_trade_date: bool,
        trade_dates_between: TradeDatesBetween,
    ) -> list[dict[str, Any]]:
        normalized_params = normalize_cache_params(params)
        if not self.split_by_trade_date or "trade_date" in normalized_params or api_name == "trade_cal":
            return [normalized_params]
        start_text = normalized_params.get("start_date")
        end_text = normalized_params.get("end_date")
        if not start_text or not end_text or not supports_trade_date:
            return [normalized_params]

        base_params = {
            param_key: param_value
            for param_key, param_value in normalized_params.items()
            if param_key not in {"start_date", "end_date"}
        }
        return [
            {**base_params, "trade_date": trade_date}
            for trade_date in trade_dates_between(str(start_text), str(end_text))
        ]


class LocalCacheExecutor:
    def __init__(self, store: LocalDataStore, remote_call: RemoteCall) -> None:
        self.store = store
        self.remote_call = remote_call

    def call(
        self,
        api_name: str,
        request_params: list[dict[str, Any]],
        fields: str | None = None,
        mode: str = "prefer",
    ) -> Any:
        cache_mode = normalize_cache_mode(mode)
        if not request_params:
            return self._empty_frame()

        chunks: list[Any] = []
        for request_param in request_params:
            chunk: Any | None = None
            if cache_mode != "refresh":
                try:
                    chunk = self.store.read(api_name, params=request_param, fields=fields)
                except LocalDataMissError:
                    if cache_mode == "local_only":
                        raise

            if chunk is None:
                result = self.remote_call(api_name, request_param, fields)
                try:
                    chunk = self.store.write(api_name, params=request_param, fields=fields, value=result)
                except LocalDataEmptyError:
                    chunk = result
            chunks.append(chunk)

        if len(chunks) == 1:
            return chunks[0]
        return self._concat_chunks(chunks)

    @staticmethod
    def _empty_frame() -> Any:
        try:
            import pandas as pd
        except ImportError as exc:  # pragma: no cover - pandas is a project dependency
            raise RuntimeError("返回空本地数据需要 pandas") from exc
        return pd.DataFrame()

    @staticmethod
    def _concat_chunks(chunks: list[Any]) -> Any:
        if all(isinstance(chunk, list) for chunk in chunks):
            records: list[Any] = []
            for chunk in chunks:
                records.extend(chunk)
            return records

        try:
            import pandas as pd
        except ImportError as exc:  # pragma: no cover - pandas is a project dependency
            raise RuntimeError("拼接本地数据需要 pandas") from exc

        frames: list[Any] = []
        for chunk in chunks:
            if isinstance(chunk, pd.DataFrame):
                frames.append(chunk)
            elif isinstance(chunk, list) and all(isinstance(item, dict) for item in chunk):
                frames.append(pd.DataFrame(chunk))
            else:
                raise LocalDataFormatError("多日拆分结果只能拼接 pandas DataFrame 或 list[dict]")
        return pd.concat(frames, ignore_index=True)

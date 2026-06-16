from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote


CACHE_MODES = {"prefer", "refresh", "local_only"}
DATE_PARAM_NAMES = {"trade_date", "start_date", "end_date", "cal_date", "ann_date", "period"}
CURRENT_FILE = "current.json"
SNAPSHOTS_DIR = "snapshots"
VALUE_TYPES = {"dataframe", "records"}


class LocalDataStoreError(RuntimeError):
    pass


class LocalDataMissError(LocalDataStoreError):
    def __init__(self, api_name: str, params: dict[str, Any], fields: str | None) -> None:
        self.api_name = api_name
        self.params = params
        self.fields = fields
        super().__init__(f"本地数据不存在：api={api_name}, params={params}, fields={fields or 'all'}")


class LocalDataFormatError(LocalDataStoreError):
    pass


class LocalDataEmptyError(LocalDataStoreError):
    def __init__(self, api_name: str, params: dict[str, Any], fields: str | None) -> None:
        self.api_name = api_name
        self.params = params
        self.fields = fields
        super().__init__(f"本地数据不缓存空结果：api={api_name}, params={params}, fields={fields or 'all'}")


@dataclass(frozen=True)
class LocalDataEntry:
    data_path: Path
    meta_path: Path
    meta: dict[str, Any]


def normalize_cache_mode(mode: str) -> str:
    normalized = mode.strip().replace("-", "_")
    if normalized not in CACHE_MODES:
        raise LocalDataStoreError(f"未知缓存模式：{mode}，可选：prefer/refresh/local_only")
    return normalized


def normalize_fields(fields: str | None) -> str | None:
    if fields is None:
        return None
    columns = [column.strip() for column in fields.split(",") if column.strip()]
    if not columns:
        return None
    return ",".join(columns)


def normalize_date_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.strftime("%Y%m%d")
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    text = str(value).strip()
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return text.replace("-", "")
    return text


def normalize_cache_params(params: dict[str, Any] | None) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in sorted((params or {}).items()):
        if value is None:
            continue
        if key in DATE_PARAM_NAMES or key.endswith("_date"):
            normalized[key] = normalize_date_value(value)
        else:
            normalized[key] = value
    return normalized


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def _field_key(fields: str | None) -> str:
    normalized = normalize_fields(fields)
    if normalized is None:
        return "all"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return digest


def _path_value(value: Any) -> str:
    if isinstance(value, bool):
        type_name = "bool"
        text = "true" if value else "false"
    elif isinstance(value, int):
        type_name = "int"
        text = str(value)
    elif isinstance(value, float):
        type_name = "float"
        text = repr(value)
    elif isinstance(value, str):
        type_name = "str"
        text = value
    elif isinstance(value, (dict, list, tuple)):
        type_name = type(value).__name__
        text = _canonical_json(value)
    else:
        type_name = type(value).__name__
        text = str(value)
    return quote(f"{type_name}:{text}", safe="-_.~")


def _path_part(key: str, value: Any) -> str:
    return f"{quote(str(key), safe='-_.~')}={_path_value(value)}"


def _api_path_part(api_name: str) -> str:
    text = str(api_name).strip()
    if not text or text in {".", ".."}:
        raise LocalDataStoreError(f"本地数据接口名不合法：{api_name!r}")
    return quote(text, safe="-_.~")


def _source_path_part(source: str) -> str:
    text = str(source).strip()
    if not text or text in {".", ".."}:
        raise LocalDataStoreError(f"本地数据源名不合法：{source!r}")
    return quote(text, safe="-_.~")


def _cache_key(api_name: str, params: dict[str, Any], fields: str | None) -> str:
    payload = {
        "api_name": api_name,
        "params": params,
        "fields": normalize_fields(fields),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def default_data_dir(env_file: str | Path = ".env") -> Path:
    env_value = os.getenv("ASHARE_DATA_DIR")
    if env_value is not None:
        return Path(env_value or "data").expanduser()

    from .config import read_env_file, resolve_env_file

    resolved_env_file = resolve_env_file(env_file)
    env_value = read_env_file(resolved_env_file).get("ASHARE_DATA_DIR")
    data_dir = Path(env_value or "data").expanduser()
    if data_dir.is_absolute():
        return data_dir

    base_dir = resolved_env_file.parent
    if not base_dir.is_absolute():
        base_dir = Path.cwd() / base_dir
    return base_dir / data_dir


class LocalDataStore:
    def __init__(self, data_dir: str | Path | None = None, source: str = "tushare", env_file: str | Path = ".env") -> None:
        self.data_dir = Path(data_dir).expanduser() if data_dir is not None else default_data_dir(env_file)
        self.source = str(source).strip()
        self.root = self.data_dir / _source_path_part(self.source)

    def request_dir(self, api_name: str, params: dict[str, Any] | None = None, fields: str | None = None) -> Path:
        normalized_params = normalize_cache_params(params)
        parts = [self.root, Path(_api_path_part(api_name))]
        if "trade_date" in normalized_params:
            parts.append(Path(_path_part("trade_date", normalized_params["trade_date"])))
            iterable = ((key, value) for key, value in normalized_params.items() if key != "trade_date")
        else:
            iterable = normalized_params.items()
        for key, value in iterable:
            parts.append(Path(_path_part(key, value)))
        parts.append(Path(_path_part("fields", _field_key(fields))))
        return Path(*parts)

    def entry(self, api_name: str, params: dict[str, Any] | None = None, fields: str | None = None) -> LocalDataEntry:
        directory = self.request_dir(api_name, params=params, fields=fields)
        current_path = directory / CURRENT_FILE
        meta = self._read_json(current_path) if current_path.exists() else {}
        data_path = self._data_path_from_meta(directory, meta) or directory / SNAPSHOTS_DIR / "__missing__.parquet"
        return LocalDataEntry(data_path=data_path, meta_path=current_path, meta=meta)

    def exists(self, api_name: str, params: dict[str, Any] | None = None, fields: str | None = None) -> bool:
        try:
            self.read(api_name, params=params, fields=fields)
        except LocalDataMissError:
            return False
        return True

    def read(self, api_name: str, params: dict[str, Any] | None = None, fields: str | None = None) -> Any:
        normalized_params = normalize_cache_params(params)
        normalized_fields = normalize_fields(fields)
        entry = self.entry(api_name, params=normalized_params, fields=normalized_fields)
        if not self._is_valid_meta(entry.meta, self.source, api_name, normalized_params, normalized_fields):
            raise LocalDataMissError(api_name, normalized_params, normalized_fields)
        if not entry.data_path.exists():
            raise LocalDataMissError(api_name, normalized_params, normalized_fields)

        try:
            import pandas as pd
        except ImportError as exc:  # pragma: no cover - pandas is a project dependency
            raise LocalDataStoreError("读取本地 Parquet 需要 pandas") from exc

        try:
            frame = pd.read_parquet(entry.data_path)
        except Exception as exc:  # noqa: BLE001
            raise LocalDataMissError(api_name, normalized_params, normalized_fields) from exc

        if len(frame) <= 0:
            raise LocalDataMissError(api_name, normalized_params, normalized_fields)
        if int(entry.meta.get("rows", -1)) != len(frame):
            raise LocalDataMissError(api_name, normalized_params, normalized_fields)
        columns = entry.meta.get("columns")
        if not isinstance(columns, list):
            raise LocalDataMissError(api_name, normalized_params, normalized_fields)
        if [str(column) for column in frame.columns] != [str(column) for column in columns]:
            raise LocalDataMissError(api_name, normalized_params, normalized_fields)
        return self._from_dataframe(frame, str(entry.meta["value_type"]))

    def write(
        self,
        api_name: str,
        params: dict[str, Any] | None,
        fields: str | None,
        value: Any,
    ) -> Any:
        frame, value_type = self._to_dataframe(value)
        normalized_params = normalize_cache_params(params)
        normalized_fields = normalize_fields(fields)
        if len(frame) <= 0:
            raise LocalDataEmptyError(api_name, normalized_params, normalized_fields)

        directory = self.request_dir(api_name, params=normalized_params, fields=normalized_fields)
        snapshots_dir = directory / SNAPSHOTS_DIR
        snapshots_dir.mkdir(parents=True, exist_ok=True)

        snapshot_id = uuid.uuid4().hex
        data_name = f"{snapshot_id}.parquet"
        data_rel_path = Path(SNAPSHOTS_DIR) / data_name
        data_path = directory / data_rel_path
        tmp_data_path = snapshots_dir / f".{data_name}.tmp"
        tmp_current_path = directory / f".{CURRENT_FILE}.{snapshot_id}.tmp"
        current_path = directory / CURRENT_FILE

        meta = {
            "success": True,
            "source": self.source,
            "api_name": api_name,
            "params": normalized_params,
            "fields": normalized_fields,
            "value_type": value_type,
            "cache_key": _cache_key(api_name, normalized_params, normalized_fields),
            "snapshot_id": snapshot_id,
            "data_file": data_rel_path.as_posix(),
            "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "rows": int(len(frame)),
            "columns": [str(column) for column in frame.columns],
        }

        try:
            frame.to_parquet(tmp_data_path, index=False)
            tmp_data_path.replace(data_path)
            tmp_current_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
            tmp_current_path.replace(current_path)
        finally:
            for path in [tmp_data_path, tmp_current_path]:
                if path.exists():
                    path.unlink()
        return self._from_dataframe(frame, value_type)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _data_path_from_meta(directory: Path, meta: dict[str, Any]) -> Path | None:
        data_file = meta.get("data_file")
        if not isinstance(data_file, str):
            return None
        relative_path = Path(data_file)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            return None
        return directory / relative_path

    @staticmethod
    def _to_dataframe(value: Any) -> tuple[Any, str]:
        try:
            import pandas as pd
        except ImportError as exc:  # pragma: no cover - pandas is a project dependency
            raise LocalDataStoreError("写入本地 Parquet 需要 pandas") from exc

        if isinstance(value, pd.DataFrame):
            return value, "dataframe"
        if isinstance(value, list) and all(isinstance(item, dict) for item in value):
            if value:
                first_keys = set(value[0].keys())
                if any(set(item.keys()) != first_keys for item in value):
                    raise LocalDataFormatError("本地持久化 list[dict] 需要每行字段完全一致")
            return pd.DataFrame(value), "records"
        raise LocalDataFormatError("本地持久化目前只支持 pandas DataFrame 或 list[dict]")

    @staticmethod
    def _from_dataframe(frame: Any, value_type: str) -> Any:
        if value_type == "dataframe":
            return frame
        if value_type == "records":
            return frame.to_dict("records")
        raise LocalDataFormatError(f"本地持久化 value_type 不支持：{value_type}")

    @staticmethod
    def _is_valid_meta(meta: dict[str, Any], source: str, api_name: str, params: dict[str, Any], fields: str | None) -> bool:
        if meta.get("value_type") not in VALUE_TYPES:
            return False
        snapshot_id = meta.get("snapshot_id")
        if not isinstance(snapshot_id, str) or not snapshot_id:
            return False
        if len(snapshot_id) != 32 or any(char not in "0123456789abcdef" for char in snapshot_id):
            return False
        if meta.get("data_file") != f"{SNAPSHOTS_DIR}/{snapshot_id}.parquet":
            return False
        if meta.get("success") is not True:
            return False
        if meta.get("source") != source:
            return False
        try:
            rows = int(meta.get("rows", 0))
        except (TypeError, ValueError):
            return False
        if rows <= 0:
            return False
        if meta.get("api_name") != api_name:
            return False
        if meta.get("params") != params:
            return False
        if meta.get("fields") != fields:
            return False
        return meta.get("cache_key") == _cache_key(api_name, params, fields)

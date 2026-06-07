from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from .news import TushareNewsError, merge_news_records, read_news_records


DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
COMPACT_DATE_PATTERN = re.compile(r"^\d{8}$")


def _validated_date(text: str) -> str:
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise TushareNewsError(f"无法识别时讯日期：{text}") from exc


def normalize_news_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()

    text = str(value or "").strip()
    if DATE_PATTERN.fullmatch(text):
        return _validated_date(text)
    if COMPACT_DATE_PATTERN.fullmatch(text):
        return _validated_date(f"{text[:4]}-{text[4:6]}-{text[6:8]}")
    if re.match(r"^\d{4}-\d{2}-\d{2}(?:\s|T|$)", text):
        return _validated_date(text[:10])
    if re.match(r"^\d{8}(?:\s|T|$)", text):
        return _validated_date(f"{text[:4]}-{text[4:6]}-{text[6:8]}")

    raise TushareNewsError(f"无法识别时讯日期：{value}")


def news_record_date(record: dict[str, Any]) -> str:
    for key in ("date", "datetime"):
        value = record.get(key)
        if value:
            return normalize_news_date(value)
    raise TushareNewsError("时讯 record 缺少 date/datetime，无法按真实日期分区")


def partition_news_records_by_date(records: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    partitions: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        partitions.setdefault(news_record_date(record), []).append(record)
    return partitions


def news_date_partition_path(base_dir: str | Path, record_date: str | date | datetime) -> Path:
    return Path(base_dir) / f"{normalize_news_date(record_date)}.jsonl"


def write_news_records(path: str | Path, records: list[dict[str, Any]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(json.dumps(record, ensure_ascii=False, default=str) for record in records), encoding="utf-8")


def merge_news_date_partitions(
    base_dir: str | Path,
    records: list[dict[str, Any]],
    snapshot_file: str | None = None,
) -> list[Path]:
    written_paths = []
    for record_date, group in partition_news_records_by_date(records).items():
        path = news_date_partition_path(base_dir, record_date)
        existing = read_news_records(path) if path.exists() else []
        merged = merge_news_records([existing, group], snapshot_files=[None, snapshot_file or "current-run"])
        write_news_records(path, merged)
        written_paths.append(path)
    return written_paths


def read_news_date_partitions(base_dir: str | Path, dates: Iterable[str | date | datetime]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for record_date in dates:
        path = news_date_partition_path(base_dir, record_date)
        if path.exists():
            records.extend(read_news_records(path))
    return records

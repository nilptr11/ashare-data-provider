from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path
from typing import Any


def _is_dataframe(value: Any) -> bool:
    return all(hasattr(value, attr) for attr in ["to_dict", "to_json", "to_csv", "to_string"])


def _is_records(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, dict) for item in value)


def _record_columns(records: list[dict[str, Any]]) -> list[str]:
    columns: list[str] = []
    for record in records:
        for key in record:
            if key not in columns:
                columns.append(key)
    return columns


def _flat_cell(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    if value is None:
        return ""
    return str(value)


def _records_to_csv(records: list[dict[str, Any]]) -> str:
    columns = _record_columns(records)
    if not columns:
        return ""

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    for record in records:
        writer.writerow({key: _flat_cell(record.get(key)) for key in columns})
    return buffer.getvalue()


def _shorten_cell(value: Any, width: int = 72) -> str:
    text = _flat_cell(value)
    text = " ".join(text.split())
    if len(text) <= width:
        return text
    return text[: width - 1] + "…"


def _records_to_table(records: list[dict[str, Any]]) -> str:
    if not records:
        return "无匹配记录"

    columns = [column for column in _record_columns(records) if column != "raw"]
    if "raw" in _record_columns(records):
        columns.append("raw")
    rows = [{column: _shorten_cell(record.get(column), width=96 if column in {"title", "change_summary"} else 48) for column in columns} for record in records]
    widths = {
        column: max(len(str(column)), *(len(row.get(column, "")) for row in rows))
        for column in columns
    }
    header = "  ".join(str(column).ljust(widths[column]) for column in columns)
    separator = "  ".join("-" * widths[column] for column in columns)
    body = ["  ".join(row.get(column, "").ljust(widths[column]) for column in columns) for row in rows]
    return "\n".join([header, separator, *body])


def limit_rows(value: Any, max_rows: int | None) -> Any:
    if max_rows is None or max_rows <= 0:
        return value
    if _is_dataframe(value):
        return value.head(max_rows)
    if isinstance(value, list):
        return value[:max_rows]
    return value


def render(value: Any, output_format: str) -> str:
    if _is_dataframe(value):
        if output_format == "json":
            return value.to_json(orient="records", force_ascii=False, date_format="iso")
        if output_format == "jsonl":
            return value.to_json(orient="records", lines=True, force_ascii=False, date_format="iso")
        if output_format == "csv":
            return value.to_csv(index=False)
        return value.to_string(index=False)

    if output_format == "jsonl" and isinstance(value, list):
        return "\n".join(json.dumps(item, ensure_ascii=False, default=str) for item in value)
    if output_format == "csv" and _is_records(value):
        return _records_to_csv(value)
    if output_format == "csv":
        raise ValueError("CSV 输出要求 Tushare 返回 DataFrame 或 list[dict]")
    if output_format == "table" and _is_records(value):
        return _records_to_table(value)
    if output_format == "table":
        return str(value)
    return json.dumps(value, ensure_ascii=False, default=str)


def emit(text: str, output: str | Path | None = None) -> None:
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return
    sys.stdout.write(text)
    if text and not text.endswith("\n"):
        sys.stdout.write("\n")

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..paths import default_data_dir
from .schemas import (
    RelationError,
    RelationRecord,
    records_digest,
    validate_relation_record,
)


@dataclass(frozen=True)
class RelationIngestResult:
    inserted: int
    path: str
    record_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "ashare.relation_ingest_result.v1",
            "inserted": self.inserted,
            "path": self.path,
            "record_ids": list(self.record_ids),
        }


class RelationStore:
    def __init__(self, data_dir: Path | str | None = None) -> None:
        self.data_dir = Path(data_dir) if data_dir is not None else default_data_dir()
        self.relations_root = self.data_dir / "relations"
        self.records_path = self.relations_root / "records.jsonl"
        self.snapshots_dir = self.relations_root / "snapshots"
        self.meta_path = self.relations_root / "_meta.json"

    def ingest_records(
        self,
        payload: dict[str, Any] | list[dict[str, Any]],
    ) -> RelationIngestResult:
        raw_records = payload if isinstance(payload, list) else [payload]
        record_ids: list[str] = []
        self.relations_root.mkdir(parents=True, exist_ok=True)
        with self.records_path.open("a", encoding="utf-8") as file:
            for raw in raw_records:
                record = self._record_from_payload(raw)
                file.write(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
                record_ids.append(record.id)
        self._write_meta()
        return RelationIngestResult(
            inserted=len(record_ids),
            path=str(self.records_path),
            record_ids=tuple(record_ids),
        )

    def ingest(
        self,
        payload: dict[str, Any] | RelationRecord,
        *,
        write: bool = True,
    ) -> RelationRecord:
        record = self._record_from_payload(payload)
        if write:
            self.relations_root.mkdir(parents=True, exist_ok=True)
            with self.records_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
            self._write_meta()
        return record

    def read_records(self) -> list[RelationRecord]:
        records_by_id: dict[str, RelationRecord] = {}
        for record in self._read_jsonl(self.records_path, RelationRecord.from_dict):
            records_by_id[record.id] = validate_relation_record(record)
        return [records_by_id[key] for key in sorted(records_by_id)]

    def search(
        self,
        *,
        entity: str | None = None,
        predicate: str | None = None,
        source_type: str | None = None,
        evidence_id: str | None = None,
        limit: int | None = None,
    ) -> list[RelationRecord]:
        filters = {
            "entity": entity,
            "predicate": predicate,
            "source_type": source_type,
            "evidence_id": evidence_id,
        }
        records = [record for record in self.read_records() if _matches(record, filters)]
        if limit and limit > 0:
            return records[:limit]
        return records

    def snapshot(self, output_path: Path | str | None = None) -> dict[str, Any]:
        records = self.read_records()
        generated_at = _now_iso()
        payload = {
            "schema": "ashare.relation_snapshot.v1",
            "generated_at": generated_at,
            "records": [record.to_dict() for record in records],
            "record_count": len(records),
            "records_sha256": records_digest(records),
        }
        if output_path is None:
            safe_time = generated_at.replace(":", "").replace("+", "_")
            output = self.snapshots_dir / f"{safe_time}.json"
        else:
            output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return payload | {"path": str(output)}

    def _write_meta(self) -> None:
        self.relations_root.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": "ashare.relation_store_meta.v1",
            "records": len(self.read_records()),
            "records_path": str(self.records_path),
            "updated_at": _now_iso(),
        }
        self.meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _record_from_payload(self, payload: dict[str, Any] | RelationRecord) -> RelationRecord:
        record = payload if isinstance(payload, RelationRecord) else RelationRecord.from_dict(payload)
        return validate_relation_record(record)

    def _read_jsonl(self, path: Path, factory: Any) -> list[Any]:
        if not path.exists():
            return []
        rows: list[Any] = []
        with path.open("r", encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                if not line.strip():
                    continue
                try:
                    rows.append(factory(json.loads(line)))
                except (TypeError, ValueError, KeyError) as error:
                    raise RelationError(f"Invalid relations JSONL at {path}:{line_number}: {error}") from error
        return rows


def _matches(record: RelationRecord, filters: dict[str, str | None]) -> bool:
    if filters["predicate"] and filters["predicate"] != record.predicate:
        return False
    if filters["source_type"] and filters["source_type"] != record.source.source_type:
        return False
    if filters["evidence_id"] and filters["evidence_id"] != record.source.evidence_id:
        return False
    entity = filters["entity"]
    if entity:
        needle = entity.lower()
        terms = (*record.subject.searchable_terms(), *record.object_ref.searchable_terms())
        if not any(needle in term.lower() for term in terms):
            return False
    return True


def _now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")

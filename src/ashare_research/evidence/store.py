from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..paths import default_data_dir
from .quality import validate_evidence
from .schemas import EvidenceError, EvidenceRecord, compute_evidence_id
from .scoring import score_confidence


@dataclass(frozen=True)
class EvidenceIngestResult:
    inserted: int
    skipped_duplicates: int
    path: str
    evidence_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "ashare.evidence_ingest_result.v1",
            "inserted": self.inserted,
            "skipped_duplicates": self.skipped_duplicates,
            "path": self.path,
            "evidence_ids": list(self.evidence_ids),
        }


class EvidenceStore:
    def __init__(self, data_dir: Path | str | None = None) -> None:
        self.data_dir = Path(data_dir) if data_dir is not None else default_data_dir()
        self.evidence_root = self.data_dir / "evidence"
        self.records_path = self.evidence_root / "records.jsonl"
        self.meta_path = self.evidence_root / "_meta.json"

    def ingest_evidence(self, payload: dict[str, Any] | list[dict[str, Any]]) -> EvidenceIngestResult:
        records = payload if isinstance(payload, list) else [payload]
        existing = {record.evidence_id for record in self.read_records() if record.evidence_id}
        inserted_ids: list[str] = []
        skipped = 0
        self.evidence_root.mkdir(parents=True, exist_ok=True)
        with self.records_path.open("a", encoding="utf-8") as file:
            for raw in records:
                normalized = self._normalize_record(raw)
                if normalized.evidence_id in existing:
                    skipped += 1
                    continue
                file.write(json.dumps(normalized.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
                existing.add(normalized.evidence_id)
                if normalized.evidence_id:
                    inserted_ids.append(normalized.evidence_id)
        self._write_meta(record_count=len(existing))
        return EvidenceIngestResult(
            inserted=len(inserted_ids),
            skipped_duplicates=skipped,
            path=str(self.records_path),
            evidence_ids=tuple(inserted_ids),
        )

    def validate_evidence(self, record: dict[str, Any]) -> EvidenceRecord:
        return self._normalize_record(record)

    def dedupe_evidence(self, records: list[dict[str, Any]]) -> list[EvidenceRecord]:
        seen: set[str] = set()
        output: list[EvidenceRecord] = []
        for record in records:
            normalized = self._normalize_record(record)
            if normalized.evidence_id in seen:
                continue
            if normalized.evidence_id:
                seen.add(normalized.evidence_id)
            output.append(normalized)
        return output

    def find_evidence(
        self,
        *,
        topic: str | None = None,
        industry: str | None = None,
        company: str | None = None,
        product: str | None = None,
        period: str | None = None,
        limit: int | None = None,
    ) -> list[EvidenceRecord]:
        records = self.read_records()
        filters = {
            "topic": topic,
            "industry": industry,
            "company": company,
            "product": product,
            "period": period,
        }
        matched = [record for record in records if _matches(record, filters)]
        matched.sort(key=lambda record: (record.confidence_score or 0.0, record.published_at), reverse=True)
        if limit and limit > 0:
            return matched[:limit]
        return matched

    def export_evidence_records(self, output_path: Path, **query: Any) -> int:
        records = self.find_evidence(**query)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            "\n".join(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True) for record in records) + "\n",
            encoding="utf-8",
        )
        return len(records)

    def collect_evidence(self, question: str, as_of: str | None = None) -> dict[str, Any]:
        return {
            "schema": "ashare.evidence_collection_gap.v1",
            "question": question,
            "as_of": as_of,
            "status": "needs_external_sources",
            "message": "Fetch authoritative sources from the source registry, then ingest curated evidence or run accepted adapter specs.",
        }

    def adapter_candidates(self, *, min_records: int = 3) -> list[dict[str, Any]]:
        groups: dict[tuple[str, str, str, str, str, str], list[EvidenceRecord]] = {}
        for record in self.read_records():
            if not _is_numerical_adapter_candidate(record):
                continue
            key = (
                record.source_type,
                record.source_name,
                record.topic,
                record.industry,
                record.metric or "",
                record.frequency or "",
            )
            groups.setdefault(key, []).append(record)

        candidates: list[dict[str, Any]] = []
        for key, records in groups.items():
            if len(records) < min_records and not any(record.needs_adapter for record in records):
                continue
            source_type, source_name, topic, industry, metric, frequency = key
            periods = sorted({str(record.period) for record in records if record.period})
            candidates.append(
                {
                    "schema": "ashare.evidence_adapter_candidate.v1",
                    "source_type": source_type,
                    "source_name": source_name,
                    "topic": topic,
                    "industry": industry,
                    "metric": metric,
                    "frequency": frequency,
                    "records": len(records),
                    "periods": periods,
                    "evidence_ids": [record.evidence_id for record in records if record.evidence_id],
                    "needs_adapter_count": sum(1 for record in records if record.needs_adapter),
                    "status": "candidate",
                }
            )
        candidates.sort(key=lambda row: (row["records"], row["needs_adapter_count"]), reverse=True)
        return candidates

    def read_records(self) -> list[EvidenceRecord]:
        if not self.records_path.exists():
            return []
        records: list[EvidenceRecord] = []
        with self.records_path.open("r", encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                    payload.pop("schema", None)
                    records.append(EvidenceRecord.from_dict(payload))
                except (TypeError, ValueError) as error:
                    raise EvidenceError(f"Invalid evidence JSONL at line {line_number}: {error}") from error
        return records

    def _normalize_record(self, raw: dict[str, Any]) -> EvidenceRecord:
        payload = dict(raw)
        payload.pop("schema", None)
        record = validate_evidence(payload)
        normalized = record.to_dict()
        if not normalized.get("evidence_id"):
            normalized["evidence_id"] = compute_evidence_id(normalized)
        scored_record = EvidenceRecord.from_dict(normalized)
        return scored_record.with_quality(
            confidence_score=score_confidence(scored_record),
            quality_flags=list(scored_record.quality_flags),
        )

    def _write_meta(self, *, record_count: int) -> None:
        payload = {
            "schema": "ashare.evidence_store_meta.v1",
            "records": record_count,
            "records_path": str(self.records_path),
            "updated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        }
        self.meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _matches(record: EvidenceRecord, filters: dict[str, str | None]) -> bool:
    payload = record.to_dict()
    for key, expected in filters.items():
        if not expected:
            continue
        actual = payload.get(key)
        if actual is None:
            return False
        if str(expected).lower() not in str(actual).lower():
            return False
    return True


def _is_numerical_adapter_candidate(record: EvidenceRecord) -> bool:
    return bool(record.metric and record.frequency and record.value is not None)

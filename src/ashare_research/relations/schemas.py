from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ..schemas import AShareResearchError
from .taxonomy import is_known_entity_type, is_known_predicate, relation_errors


class RelationError(AShareResearchError):
    """Raised when relation records are invalid or cannot be stored."""


CONFIDENCE_VALUES = {"low", "medium", "high"}
INFERENCE_SOURCE_TYPES = {"codex_inference", "llm_inference", "inference"}


@dataclass(frozen=True)
class RelationEntityRef:
    type: str
    id: str
    name: str
    aliases: tuple[str, ...] = ()
    attributes: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RelationEntityRef":
        aliases = payload.get("aliases") or ()
        return cls(
            type=str(payload["type"]),
            id=str(payload["id"]),
            name=str(payload["name"]),
            aliases=tuple(str(alias) for alias in aliases),
            attributes=dict(payload.get("attributes") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "id": self.id,
            "name": self.name,
            "aliases": list(self.aliases),
            "attributes": dict(self.attributes),
        }

    def searchable_terms(self) -> tuple[str, ...]:
        return (self.type, self.id, self.name, *self.aliases)


@dataclass(frozen=True)
class RelationSource:
    source_type: str
    source_url: str | None = None
    evidence_id: str | None = None
    source_name: str | None = None
    published_at: str | None = None
    query_time: str | None = None
    raw_ref: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RelationSource":
        return cls(
            source_type=str(payload["source_type"]),
            source_url=_optional_str(payload.get("source_url")),
            evidence_id=_optional_str(payload.get("evidence_id")),
            source_name=_optional_str(payload.get("source_name")),
            published_at=_optional_str(payload.get("published_at")),
            query_time=_optional_str(payload.get("query_time")),
            raw_ref=_optional_str(payload.get("raw_ref")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "source_url": self.source_url,
            "evidence_id": self.evidence_id,
            "source_name": self.source_name,
            "published_at": self.published_at,
            "query_time": self.query_time,
            "raw_ref": self.raw_ref,
        }


@dataclass(frozen=True)
class RelationRecord:
    id: str
    subject: RelationEntityRef
    predicate: str
    object_ref: RelationEntityRef
    confidence: str
    source: RelationSource
    valid_from: str
    valid_to: str | None = None
    updated_at: str | None = None
    tags: tuple[str, ...] = ()
    note: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RelationRecord":
        normalized = dict(payload)
        normalized.pop("schema", None)
        return cls(
            id=str(normalized["id"]),
            subject=RelationEntityRef.from_dict(dict(normalized["subject"])),
            predicate=str(normalized["predicate"]),
            object_ref=RelationEntityRef.from_dict(dict(normalized["object"])),
            confidence=str(normalized["confidence"]),
            source=RelationSource.from_dict(dict(normalized["source"])),
            valid_from=str(normalized["valid_from"]),
            valid_to=_optional_str(normalized.get("valid_to")),
            updated_at=_optional_str(normalized.get("updated_at")) or _now_iso(),
            tags=tuple(str(tag) for tag in normalized.get("tags") or ()),
            note=_optional_str(normalized.get("note")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "ashare.relation_record.v1",
            "id": self.id,
            "subject": self.subject.to_dict(),
            "predicate": self.predicate,
            "object": self.object_ref.to_dict(),
            "confidence": self.confidence,
            "source": self.source.to_dict(),
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "updated_at": self.updated_at,
            "tags": list(self.tags),
            "note": self.note,
        }


def validate_relation_record(record: RelationRecord) -> RelationRecord:
    if not record.id:
        raise RelationError("RelationRecord.id is required")
    if not record.predicate:
        raise RelationError(f"{record.id}: predicate is required")
    if not is_known_predicate(record.predicate):
        raise RelationError(f"{record.id}: unknown predicate {record.predicate!r}")
    if record.confidence not in CONFIDENCE_VALUES:
        raise RelationError(f"{record.id}: invalid confidence {record.confidence!r}")
    _validate_entity(record.id, "subject", record.subject)
    _validate_entity(record.id, "object", record.object_ref)
    relation_violations = relation_errors(record.predicate, record.subject.type, record.object_ref.type)
    if relation_violations:
        raise RelationError(f"{record.id}: {'; '.join(relation_violations)}")
    _validate_source(record)
    if not record.valid_from:
        raise RelationError(f"{record.id}: valid_from is required")
    return record


def records_digest(records: list[RelationRecord]) -> str:
    payload = [record.to_dict() for record in sorted(records, key=lambda item: item.id)]
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _validate_entity(record_id: str, role: str, entity: RelationEntityRef) -> None:
    if not entity.type or not entity.id or not entity.name:
        raise RelationError(f"{record_id}: {role} requires type/id/name")
    if not is_known_entity_type(entity.type):
        raise RelationError(f"{record_id}: {role} has unknown entity type {entity.type!r}")


def _validate_source(record: RelationRecord) -> None:
    source = record.source
    if not source.source_type:
        raise RelationError(f"{record.id}: source.source_type is required")
    if source.source_type in INFERENCE_SOURCE_TYPES and not source.raw_ref:
        raise RelationError(f"{record.id}: source.raw_ref is required for inference relations")
    if source.source_type in INFERENCE_SOURCE_TYPES and not record.note:
        raise RelationError(f"{record.id}: note is required for inference relations")
    if not source.source_url and not source.evidence_id and not source.raw_ref:
        raise RelationError(f"{record.id}: source requires source_url, evidence_id, or raw_ref")
    if source.source_url and not source.source_name:
        raise RelationError(f"{record.id}: source.source_name is required when source_url is provided")
    if source.source_url and not source.published_at:
        raise RelationError(f"{record.id}: source.published_at is required when source_url is provided")
    if source.source_url and not source.query_time:
        raise RelationError(f"{record.id}: source.query_time is required when source_url is provided")


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...schemas import AShareResearchError


class EvidenceSourceError(AShareResearchError):
    """Raised when saved evidence sources are invalid."""


@dataclass(frozen=True)
class EvidenceSource:
    source_id: str
    source_type: str
    source_name: str
    topic: str
    industry: str
    metric: str
    frequency: str
    connector: str
    api_name: str
    params_template: dict[str, Any] = field(default_factory=dict)
    field_mapping: dict[str, str] = field(default_factory=dict)
    claim_template: str = ""
    evidence_ids: tuple[str, ...] = ()
    notes: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EvidenceSource":
        normalized = dict(payload)
        normalized.pop("schema", None)
        normalized.pop("status", None)
        return cls(
            source_id=str(normalized["source_id"]),
            source_type=str(normalized["source_type"]),
            source_name=str(normalized["source_name"]),
            topic=str(normalized["topic"]),
            industry=str(normalized["industry"]),
            metric=str(normalized["metric"]),
            frequency=str(normalized.get("frequency", "")),
            connector=str(normalized.get("connector", "")),
            api_name=str(normalized.get("api_name", "")),
            params_template=dict(normalized.get("params_template") or {}),
            field_mapping={str(key): str(value) for key, value in (normalized.get("field_mapping") or {}).items()},
            claim_template=str(normalized.get("claim_template", "")),
            evidence_ids=tuple(str(item) for item in normalized.get("evidence_ids") or ()),
            notes=str(normalized.get("notes", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "ashare.evidence_source.v1",
            "source_id": self.source_id,
            "source_type": self.source_type,
            "source_name": self.source_name,
            "topic": self.topic,
            "industry": self.industry,
            "metric": self.metric,
            "frequency": self.frequency,
            "connector": self.connector,
            "api_name": self.api_name,
            "params_template": dict(self.params_template),
            "field_mapping": dict(self.field_mapping),
            "claim_template": self.claim_template,
            "evidence_ids": list(self.evidence_ids),
            "notes": self.notes,
        }


def validate_source(source: EvidenceSource) -> EvidenceSource:
    if not source.source_id:
        raise EvidenceSourceError("source_id is required")
    for field_name in ("source_type", "source_name", "topic", "industry", "metric"):
        if not getattr(source, field_name):
            raise EvidenceSourceError(f"{source.source_id}: {field_name} is required")
    if not source.connector:
        raise EvidenceSourceError(f"{source.source_id}: connector is required")
    if not source.api_name:
        raise EvidenceSourceError(f"{source.source_id}: api_name is required")
    for field_name in ("claim", "source_url", "published_at", "query_time", "value", "unit", "period"):
        if field_name not in source.field_mapping:
            raise EvidenceSourceError(f"{source.source_id}: field_mapping.{field_name} is required")
    return source

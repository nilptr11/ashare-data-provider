from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from ..schemas import AShareResearchError


class EvidenceError(AShareResearchError):
    """Raised when evidence records are invalid or cannot be stored."""


CONFIDENCE_VALUES = {"low", "medium", "high"}
MATURITY_VALUES = {"prompt", "curated", "fetched"}
ALLOWED_SOURCE_TYPES = {
    "official",
    "exchange",
    "regulator",
    "company_filing",
    "company_ir",
    "association",
    "industry_association",
    "tender_platform",
    "official_platform",
    "gov_policy",
    "price_index",
    "vendor",
    "other",
}
OFFICIAL_SOURCE_TYPES = {
    "company_filing",
    "company_ir",
    "exchange",
    "regulator",
    "gov_policy",
    "official",
    "official_platform",
    "association",
    "industry_association",
    "price_index",
}


@dataclass(frozen=True)
class EvidenceRecord:
    claim: str
    topic: str
    industry: str
    source_type: str
    source_name: str
    source_url: str
    published_at: str
    query_time: str
    confidence: str
    verification: str
    evidence_id: str | None = None
    product: str | None = None
    company: str | None = None
    region: str | None = None
    metric: str | None = None
    value: Any = None
    unit: str | None = None
    period: str | None = None
    frequency: str | None = None
    needs_source: bool = False
    raw_excerpt: str | None = None
    supports: tuple[str, ...] = ()
    confidence_score: float | None = None
    quality_flags: tuple[str, ...] = ()
    maturity: str = "curated"
    source_id: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EvidenceRecord":
        normalized = dict(payload)
        normalized.pop("schema", None)
        if "supports" in normalized and normalized["supports"] is not None:
            normalized["supports"] = tuple(str(item) for item in normalized["supports"])
        else:
            normalized["supports"] = ()
        if "quality_flags" in normalized and normalized["quality_flags"] is not None:
            normalized["quality_flags"] = tuple(str(item) for item in normalized["quality_flags"])
        else:
            normalized["quality_flags"] = ()
        return cls(**normalized)

    def with_quality(self, *, confidence_score: float, quality_flags: list[str]) -> "EvidenceRecord":
        payload = self.to_dict()
        payload["confidence_score"] = confidence_score
        payload["quality_flags"] = quality_flags
        if not payload.get("evidence_id"):
            payload["evidence_id"] = compute_evidence_id(payload)
        return EvidenceRecord.from_dict(payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "ashare.evidence_record.v1",
            "evidence_id": self.evidence_id,
            "claim": self.claim,
            "topic": self.topic,
            "industry": self.industry,
            "product": self.product,
            "company": self.company,
            "region": self.region,
            "metric": self.metric,
            "value": self.value,
            "unit": self.unit,
            "period": self.period,
            "frequency": self.frequency,
            "source_type": self.source_type,
            "source_name": self.source_name,
            "source_url": self.source_url,
            "published_at": self.published_at,
            "query_time": self.query_time,
            "confidence": self.confidence,
            "verification": self.verification,
            "needs_source": self.needs_source,
            "raw_excerpt": self.raw_excerpt,
            "supports": list(self.supports),
            "confidence_score": self.confidence_score,
            "quality_flags": list(self.quality_flags),
            "maturity": self.maturity,
            "source_id": self.source_id,
        }


def compute_evidence_id(payload: dict[str, Any]) -> str:
    parts = [
        str(payload.get("source_url") or ""),
        str(payload.get("metric") or ""),
        str(payload.get("period") or ""),
        str(payload.get("value") or ""),
        str(payload.get("claim") or ""),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()

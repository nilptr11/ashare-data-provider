from __future__ import annotations

from typing import Any

from ...connectors import ConnectorRegistry
from ...schemas import ConnectorError
from ..store import EvidenceIngestResult, EvidenceStore
from .registry import EvidenceSourceRegistry
from .schemas import EvidenceSource, validate_source


class EvidenceSourceFetcher:
    def __init__(
        self,
        *,
        evidence_store: EvidenceStore,
        source_registry: EvidenceSourceRegistry,
        connector_registry: ConnectorRegistry | None = None,
    ) -> None:
        self.evidence_store = evidence_store
        self.source_registry = source_registry
        self.connector_registry = connector_registry or ConnectorRegistry.builtin()

    def fetch(self, source_id: str, *, params: dict[str, Any] | None = None) -> EvidenceIngestResult:
        source = validate_source(self.source_registry.require(source_id))
        request_params = dict(source.params_template)
        request_params.update(params or {})
        connector = self.connector_registry.create(source.connector)
        try:
            response = connector.fetch(source.api_name, params=request_params)
        except ConnectorError:
            raise
        records = [_record_from_row(source, row) for row in response.frame.to_dict(orient="records")]
        return self.evidence_store.ingest_evidence(records)


def _record_from_row(source: EvidenceSource, row: dict[str, Any]) -> dict[str, Any]:
    mapping = source.field_mapping
    payload = {
        "claim": _claim(source, row),
        "topic": source.topic,
        "industry": source.industry,
        "source_type": source.source_type,
        "source_name": source.source_name,
        "source_url": _mapped(mapping, row, "source_url"),
        "published_at": _mapped(mapping, row, "published_at"),
        "query_time": _mapped(mapping, row, "query_time"),
        "confidence": _mapped(mapping, row, "confidence", default="medium"),
        "verification": _mapped(mapping, row, "verification", default="source_mapped"),
        "metric": source.metric,
        "value": _mapped(mapping, row, "value"),
        "unit": _mapped(mapping, row, "unit"),
        "period": _mapped(mapping, row, "period"),
        "frequency": source.frequency,
        "maturity": "fetched",
        "source_id": source.source_id,
    }
    for optional in ("product", "company", "region", "raw_excerpt"):
        if optional in mapping:
            payload[optional] = _mapped(mapping, row, optional)
    return payload


def _claim(source: EvidenceSource, row: dict[str, Any]) -> str:
    if source.claim_template:
        return source.claim_template.format_map(_SafeRow(row))
    return str(_mapped(source.field_mapping, row, "claim"))


def _mapped(mapping: dict[str, str], row: dict[str, Any], field: str, default: Any = None) -> Any:
    column = mapping.get(field)
    if column is None:
        return default
    return row.get(column, default)


class _SafeRow(dict[str, Any]):
    def __init__(self, row: dict[str, Any]) -> None:
        super().__init__(row)

    def __missing__(self, key: str) -> str:
        return ""

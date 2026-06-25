from __future__ import annotations

from collections import defaultdict

from .schemas import RelationEntityRef, RelationRecord


def build_alias_index(records: list[RelationRecord]) -> dict[str, list[dict[str, str]]]:
    index: dict[str, list[dict[str, str]]] = defaultdict(list)
    for record in records:
        for entity in (record.subject, record.object_ref):
            payload = _entity_payload(entity)
            for term in entity.searchable_terms():
                index[term.lower()].append(payload)
    return dict(index)


def _entity_payload(entity: RelationEntityRef) -> dict[str, str]:
    return {
        "type": entity.type,
        "id": entity.id,
        "name": entity.name,
    }

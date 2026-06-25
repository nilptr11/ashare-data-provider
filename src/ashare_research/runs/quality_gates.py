from __future__ import annotations

from typing import Any

STRONG_EXPOSURE_SOURCE_KINDS = {"mart", "evidence", "relations"}
AUDIT_REQUIRED_SOURCE_KINDS = {
    "evidence",
    "company_filing",
    "company_ir",
    "exchange",
    "regulator",
    "gov_policy",
    "official",
    "official_platform",
    "association",
    "industry_association",
    "tender_platform",
    "price_index",
    "vendor",
    "media",
    "research_report",
    "web",
    "other",
}
AUDIT_FIELD_ALIASES = {
    "source_name": ("source_name", "source_title", "title"),
    "source_url": ("source_url", "url", "source_api", "interface"),
    "published_at": ("published_at", "publish_date", "published_date", "date"),
    "query_time": ("query_time", "fetched_at", "accessed_at"),
}
WEAK_VERIFICATIONS = {"unverified", "stale"}
HIGH_PRIORITY_VALUES = {"core", "high", "primary", "重点", "优先"}


def evaluate_quality_gates(
    *,
    data_refs: dict[str, Any],
    as_of: str,
    has_validated_output: bool,
    validated_output: dict[str, Any] | None = None,
    evidence_artifact: dict[str, Any] | None = None,
    relations_artifact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    output = validated_output if has_validated_output else None
    gates = {
        "output_gate": _output_gate(as_of, output),
        "freshness_gate": _freshness_gate(data_refs, as_of),
        "data_refs_gate": _data_refs_gate(data_refs),
        "gap_gate": _gap_gate(data_refs, output),
        "source_gate": _source_gate(evidence_artifact, relations_artifact, output),
        "source_audit_gate": _source_audit_gate(output),
        "confidence_gate": _confidence_gate(output),
    }
    status = "passed"
    if any(gate["status"] == "blocked" for gate in gates.values()):
        status = "blocked"
    elif any(gate["status"] in {"warning", "degraded", "not_evaluated"} for gate in gates.values()):
        status = "warning"
    return {
        "schema": "ashare.run_quality_gates.v1",
        "status": status,
        "gates": gates,
    }


def _output_gate(as_of: str, output: dict[str, Any] | None) -> dict[str, Any]:
    if output is None:
        return _gate("not_evaluated", "validated output not provided")
    if not isinstance(output, dict):
        return _gate("blocked", "validated output must be a JSON object")

    errors: list[str] = []
    output_as_of = output.get("as_of")
    if output_as_of is not None and str(output_as_of) != as_of:
        errors.append(f"as_of mismatch: expected {as_of}, got {output_as_of}")

    if errors:
        return _gate("blocked", "; ".join(errors), {"errors": errors})
    return _gate("passed", "")


def _freshness_gate(data_refs: dict[str, Any], as_of: str) -> dict[str, Any]:
    stale = []
    for ref in [*data_refs.get("marts", []), *data_refs.get("features", [])]:
        partition = dict(ref.get("partition") or {})
        ref_date = partition.get("trade_date") or partition.get("as_of") or partition.get("snapshot_date")
        if ref_date and str(ref_date) != as_of:
            stale.append(ref.get("raw") or ref.get("name"))
    if stale:
        return _gate("blocked", f"data ref date mismatch: {stale}")
    return _gate("passed", "")


def _gap_gate(data_refs: dict[str, Any], output: dict[str, Any] | None) -> dict[str, Any]:
    marts = data_refs.get("marts", [])
    features = data_refs.get("features", [])
    gaps = _items(output, "data_gaps")
    blocking_gaps = [gap for gap in gaps if gap.get("impact") == "block"]
    degraded_gaps = [gap for gap in gaps if gap.get("impact") == "degrade"]
    if blocking_gaps:
        return _gate("blocked", "validated output contains blocking data gaps", {"gaps": blocking_gaps})
    if not marts and not features:
        return _gate("warning", "no mart or feature refs recorded; data coverage must be checked from run notes")
    if degraded_gaps:
        return _gate("degraded", "validated output contains degrading data gaps", {"gaps": degraded_gaps})
    return _gate("passed", "")


def _data_refs_gate(data_refs: dict[str, Any]) -> dict[str, Any]:
    refs = [*data_refs.get("marts", []), *data_refs.get("features", [])]
    blocked = [
        ref.get("raw") or ref.get("name")
        for ref in refs
        if ref.get("status") in {"missing", "invalid", "unregistered", "schema_mismatch", "empty", "read_error"}
    ]
    if blocked:
        return _gate("blocked", f"data refs not usable: {blocked}")
    degraded = [ref.get("raw") or ref.get("name") for ref in refs if ref.get("status") == "degraded"]
    if degraded:
        return _gate("warning", f"data refs degraded: {degraded}")
    return _gate("passed", "")


def _source_gate(
    evidence_artifact: dict[str, Any] | None,
    relations_artifact: dict[str, Any] | None,
    output: dict[str, Any] | None,
) -> dict[str, Any]:
    missing = []
    if not evidence_artifact:
        missing.append("evidence")
    if not relations_artifact:
        missing.append("relations")
    if missing:
        return _gate("warning", f"source artifacts missing: {missing}")

    unsupported_exposure = _unsupported_company_exposure(output)
    if unsupported_exposure:
        return _gate(
            "blocked",
            "company exposure claims require mart, evidence, or traceable relations support",
            {"items": unsupported_exposure},
        )
    return _gate("passed", "")


def _source_audit_gate(output: dict[str, Any] | None) -> dict[str, Any]:
    if output is None:
        return _gate("not_evaluated", "validated output not provided")

    missing = []
    for ref in _external_source_refs(output):
        missing_fields = _missing_audit_fields(ref)
        if missing_fields:
            missing.append(
                {
                    "source": _source_ref_label(ref),
                    "source_kind": ref.get("source_kind") or ref.get("source_type"),
                    "missing_fields": missing_fields,
                }
            )
    if missing:
        return _gate("blocked", "external evidence references require source name, URL, publish date, and fetch time", {"items": missing})
    return _gate("passed", "")


def _confidence_gate(output: dict[str, Any] | None) -> dict[str, Any]:
    if output is None:
        return _gate("not_evaluated", "validated output not provided")

    candidates = _items(output, "candidate_pool")
    evidence = _items(output, "evidence_matrix")
    weak_high_priority = [
        _candidate_label(candidate)
        for candidate in candidates
        if _is_high_priority(candidate) and candidate.get("evidence_strength") == "weak"
    ]
    if weak_high_priority:
        return _gate("blocked", "high-priority candidates cannot have weak evidence", {"candidates": weak_high_priority})

    warnings: dict[str, Any] = {}
    priority_with_missing = [
        _candidate_label(candidate)
        for candidate in candidates
        if _is_high_priority(candidate) and candidate.get("missing_evidence")
    ]
    if priority_with_missing:
        warnings["high_priority_candidates_with_missing_evidence"] = priority_with_missing

    weak_high_evidence = [
        item.get("source_id") or item.get("claim") or item.get("topic")
        for item in evidence
        if item.get("confidence") == "high" and item.get("verification") in WEAK_VERIFICATIONS
    ]
    if weak_high_evidence:
        warnings["high_confidence_evidence_with_weak_verification"] = weak_high_evidence

    if output.get("confidence") == "high" and not evidence:
        warnings["high_confidence_without_evidence_matrix"] = True

    if warnings:
        return _gate("warning", "validated output confidence requires review", warnings)
    return _gate("passed", "")


def _unsupported_company_exposure(output: dict[str, Any] | None) -> list[dict[str, Any]]:
    if output is None:
        return []
    mappings = _items(output, "company_mapping")
    mapping_by_code = {str(item.get("ts_code") or ""): item for item in mappings if item.get("ts_code")}
    unsupported: list[dict[str, Any]] = []

    for mapping in mappings:
        exposure_level = mapping.get("exposure_level")
        if exposure_level in {"core", "direct"} and not _has_strong_exposure(mapping):
            unsupported.append(
                {
                    "scope": "company_mapping",
                    "ts_code": mapping.get("ts_code"),
                    "name": mapping.get("name"),
                    "exposure_level": exposure_level,
                    "source_kinds": _source_kinds(mapping.get("exposure_evidence")),
                }
            )

    for candidate in _items(output, "candidate_pool"):
        mapping = mapping_by_code.get(str(candidate.get("ts_code") or ""))
        has_claim = candidate.get("exposure_level") in {"core", "direct"} or _is_high_priority(candidate)
        if has_claim and (not mapping or not _has_strong_exposure(mapping)):
            unsupported.append(
                {
                    "scope": "candidate_pool",
                    "ts_code": candidate.get("ts_code"),
                    "name": candidate.get("name"),
                    "priority": candidate.get("priority") or candidate.get("research_priority") or candidate.get("tier"),
                    "source_kinds": _source_kinds((mapping or {}).get("exposure_evidence")),
                }
            )
    return unsupported


def _has_strong_exposure(mapping: dict[str, Any]) -> bool:
    refs = _list_of_dicts(mapping.get("exposure_evidence"))
    if not refs:
        return False
    return any(_is_strong_exposure_ref(ref) for ref in refs)


def _is_strong_exposure_ref(ref: dict[str, Any]) -> bool:
    source_kind = str(ref.get("source_kind") or "")
    if source_kind not in STRONG_EXPOSURE_SOURCE_KINDS:
        return False
    if not _has_source_reference(ref):
        return False
    if _requires_audit(ref) and _missing_audit_fields(ref):
        return False
    return True


def _source_kinds(value: Any) -> list[str]:
    return sorted({str(item.get("source_kind") or "") for item in _list_of_dicts(value) if item.get("source_kind")})


def _has_source_reference(ref: dict[str, Any]) -> bool:
    source_kind = str(ref.get("source_kind") or "")
    if source_kind == "relations":
        keys = ("source_id", "relation_id", "raw_ref")
    elif source_kind == "evidence":
        keys = ("source_id", "evidence_id", "raw_ref", "url", "source_url")
    else:
        keys = ("source_id", "raw_ref", "url", "source_url")
    return any(str(ref.get(key) or "").strip() for key in keys)


def _external_source_refs(output: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for item in _walk_dicts(output):
        if _requires_audit(item):
            refs.append(item)
    return refs


def _walk_dicts(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        found.append(value)
        for child in value.values():
            found.extend(_walk_dicts(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_walk_dicts(child))
    return found


def _requires_audit(ref: dict[str, Any]) -> bool:
    source_kind = str(ref.get("source_kind") or ref.get("source_type") or "").lower()
    return source_kind in AUDIT_REQUIRED_SOURCE_KINDS


def _missing_audit_fields(ref: dict[str, Any]) -> list[str]:
    missing = []
    for field_name, aliases in AUDIT_FIELD_ALIASES.items():
        if not any(str(ref.get(alias) or "").strip() for alias in aliases):
            missing.append(field_name)
    return missing


def _source_ref_label(ref: dict[str, Any]) -> str:
    return str(ref.get("source_id") or ref.get("evidence_id") or ref.get("claim") or ref.get("title") or "<unknown>")


def _items(output: dict[str, Any] | None, key: str) -> list[dict[str, Any]]:
    if output is None:
        return []
    return _list_of_dicts(output.get(key))


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _candidate_label(candidate: dict[str, Any]) -> str:
    return str(candidate.get("ts_code") or candidate.get("name") or "<unknown>")


def _is_high_priority(candidate: dict[str, Any]) -> bool:
    values = {
        str(candidate.get("priority") or "").lower(),
        str(candidate.get("research_priority") or "").lower(),
        str(candidate.get("tier") or "").lower(),
    }
    return bool(values & HIGH_PRIORITY_VALUES)


def _gate(status: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "message": message,
        "details": details or {},
    }

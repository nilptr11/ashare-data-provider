from __future__ import annotations

from typing import Any

from ..protocols import ProtocolSpec


INDUSTRY_CHAIN_SCHEMA = "ashare.protocol_output.industry_chain_selection.v1"
INDUSTRY_CHAIN_REQUIRED_FIELDS = (
    "schema",
    "as_of",
    "question",
    "research_scope",
    "theme_identification",
    "industry_chain_map",
    "revaluation_segments",
    "company_mapping",
    "candidate_pool",
    "evidence_matrix",
    "data_gaps",
    "follow_up_plan",
    "invalid_if",
    "confidence",
)
STRONG_EXPOSURE_SOURCE_KINDS = {"mart", "evidence", "knowledge"}
WEAK_VERIFICATIONS = {"unverified", "stale"}


def evaluate_quality_gates(
    *,
    protocol: ProtocolSpec,
    data_refs: dict[str, Any],
    as_of: str,
    has_validated_output: bool,
    validated_output: dict[str, Any] | None = None,
    evidence_artifact: dict[str, Any] | None = None,
    knowledge_artifact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    output = validated_output if has_validated_output else None
    gates = {
        "schema_gate": _schema_gate(protocol, as_of, output),
        "freshness_gate": _freshness_gate(data_refs, as_of),
        "data_refs_gate": _data_refs_gate(data_refs),
        "gap_gate": _gap_gate(protocol, data_refs, output),
        "source_gate": _source_gate(evidence_artifact, knowledge_artifact, output),
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


def _schema_gate(protocol: ProtocolSpec, as_of: str, output: dict[str, Any] | None) -> dict[str, Any]:
    if output is None:
        return _gate("not_evaluated", "validated output not provided")
    if not isinstance(output, dict):
        return _gate("blocked", "validated output must be a JSON object")

    errors: list[str] = []
    expected_schema = protocol.output_schema
    actual_schema = str(output.get("schema") or "")
    if expected_schema and actual_schema != expected_schema:
        errors.append(f"schema mismatch: expected {expected_schema}, got {actual_schema or '<missing>'}")
    if actual_schema == INDUSTRY_CHAIN_SCHEMA:
        missing = [field for field in INDUSTRY_CHAIN_REQUIRED_FIELDS if field not in output]
        if missing:
            errors.append(f"missing required output fields: {missing}")
        if str(output.get("as_of") or "") != as_of:
            errors.append(f"as_of mismatch: expected {as_of}, got {output.get('as_of') or '<missing>'}")

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


def _gap_gate(protocol: ProtocolSpec, data_refs: dict[str, Any], output: dict[str, Any] | None) -> dict[str, Any]:
    marts = data_refs.get("marts", [])
    features = data_refs.get("features", [])
    gaps = _items(output, "data_gaps")
    blocking_gaps = [gap for gap in gaps if gap.get("impact") == "block"]
    degraded_gaps = [gap for gap in gaps if gap.get("impact") == "degrade"]
    if blocking_gaps:
        return _gate("blocked", "validated output contains blocking data gaps", {"gaps": blocking_gaps})
    if protocol.required_inputs and not marts and not features:
        return _gate("warning", "no mart or feature refs recorded; data coverage must be checked from run notes")
    if degraded_gaps:
        return _gate("degraded", "validated output contains degrading data gaps", {"gaps": degraded_gaps})
    evidence_needed = [
        _candidate_label(candidate)
        for candidate in _items(output, "candidate_pool")
        if candidate.get("research_state") == "evidence_needed"
    ]
    if evidence_needed and not _items(output, "follow_up_plan"):
        return _gate(
            "warning",
            "evidence_needed candidates require follow_up_plan items",
            {"candidates": evidence_needed},
        )
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
    knowledge_artifact: dict[str, Any] | None,
    output: dict[str, Any] | None,
) -> dict[str, Any]:
    missing = []
    if not evidence_artifact:
        missing.append("evidence")
    if not knowledge_artifact:
        missing.append("knowledge")
    if missing:
        return _gate("warning", f"source artifacts missing: {missing}")

    unsupported_exposure = _unsupported_company_exposure(output)
    if unsupported_exposure:
        return _gate(
            "blocked",
            "company exposure claims require mart, evidence, or accepted knowledge support",
            {"items": unsupported_exposure},
        )
    return _gate("passed", "")


def _confidence_gate(output: dict[str, Any] | None) -> dict[str, Any]:
    if output is None:
        return _gate("not_evaluated", "validated output not provided")

    candidates = _items(output, "candidate_pool")
    evidence = _items(output, "evidence_matrix")
    weak_core = [
        _candidate_label(candidate)
        for candidate in candidates
        if candidate.get("research_state") == "core_research" and candidate.get("evidence_strength") == "weak"
    ]
    if weak_core:
        return _gate("blocked", "core_research candidates cannot have weak evidence", {"candidates": weak_core})

    warnings: dict[str, Any] = {}
    core_with_missing = [
        _candidate_label(candidate)
        for candidate in candidates
        if candidate.get("research_state") == "core_research" and candidate.get("missing_evidence")
    ]
    if core_with_missing:
        warnings["core_candidates_with_missing_evidence"] = core_with_missing

    weak_watch = [
        _candidate_label(candidate)
        for candidate in candidates
        if candidate.get("research_state") in {"elastic_watch", "laggard_watch"}
        and candidate.get("evidence_strength") == "weak"
    ]
    if weak_watch:
        warnings["watch_candidates_with_weak_evidence"] = weak_watch

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
        if candidate.get("research_state") != "core_research":
            continue
        mapping = mapping_by_code.get(str(candidate.get("ts_code") or ""))
        if not mapping or not _has_strong_exposure(mapping):
            unsupported.append(
                {
                    "scope": "candidate_pool",
                    "ts_code": candidate.get("ts_code"),
                    "name": candidate.get("name"),
                    "research_state": candidate.get("research_state"),
                    "source_kinds": _source_kinds((mapping or {}).get("exposure_evidence")),
                }
            )
    return unsupported


def _has_strong_exposure(mapping: dict[str, Any]) -> bool:
    refs = _list_of_dicts(mapping.get("exposure_evidence"))
    if not refs:
        return False
    return any(str(ref.get("source_kind") or "") in STRONG_EXPOSURE_SOURCE_KINDS for ref in refs)


def _source_kinds(value: Any) -> list[str]:
    return sorted({str(item.get("source_kind") or "") for item in _list_of_dicts(value) if item.get("source_kind")})


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


def _gate(status: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "message": message,
        "details": details or {},
    }

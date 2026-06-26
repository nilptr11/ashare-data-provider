from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..evidence import EvidenceStore
from ..features import FeatureRegistry, FeatureStore
from ..relations import RelationStore
from ..marts.reader import MartReader
from ..paths import default_data_dir, default_runs_dir
from ..reports import render_trace_report
from ..schemas import AShareResearchError
from .manifest import RunArtifact, RunManifest
from .quality_gates import evaluate_quality_gates


class RunError(AShareResearchError):
    """Raised when a run cannot be recorded or replayed."""


class RunRecorder:
    def __init__(self, data_dir: Path | str | None = None, runs_dir: Path | str | None = None) -> None:
        self.data_dir = Path(data_dir) if data_dir is not None else default_data_dir()
        self.runs_dir = Path(runs_dir) if runs_dir is not None else default_runs_dir(self.data_dir)

    def record(
        self,
        *,
        question: str,
        as_of: str,
        mart_refs: list[str] | None = None,
        feature_refs: list[str] | None = None,
        evidence_path: Path | str | None = None,
        relations_path: Path | str | None = None,
        model_output: str | None = None,
        validated_output: dict[str, Any] | None = None,
        agent_reasoning: dict[str, Any] | None = None,
        report: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        created_at = _now_iso()
        run_name = run_id or f"{_timestamp_for_id(created_at)}_{_slug(question)[:48]}"
        run_dir = self.runs_dir / run_name
        run_dir.mkdir(parents=True, exist_ok=False)

        question_artifact = self._write_text(run_dir / "question.md", question, kind="question")
        data_refs_payload = self._data_refs_payload(as_of=as_of, mart_refs=mart_refs or [], feature_refs=feature_refs or [])
        data_refs_artifact = self._write_json(run_dir / "data_refs.json", data_refs_payload, kind="data_refs")
        evidence_artifact = self._copy_or_create_evidence(run_dir, evidence_path)
        relations_artifact = self._copy_or_create_relations(run_dir, relations_path)
        evidence_context = evidence_artifact.to_dict() | _evidence_artifact_context(run_dir / evidence_artifact.path)
        relations_context = relations_artifact.to_dict() | _relations_artifact_context(run_dir / relations_artifact.path)
        raw_output_artifact = self._write_text(run_dir / "model_output.raw.md", model_output or "", kind="model_output_raw")
        validated_payload = validated_output or {"schema": "ashare.model_output.validated.v1", "status": "not_provided"}
        validated_artifact = self._write_json(run_dir / "model_output.validated.json", validated_payload, kind="model_output_validated")
        reasoning_payload = agent_reasoning or _empty_agent_reasoning()
        reasoning_artifact = self._write_json(run_dir / "agent_reasoning.json", reasoning_payload, kind="agent_reasoning")
        quality_payload = evaluate_quality_gates(
            data_refs=data_refs_payload,
            as_of=as_of,
            has_validated_output=validated_output is not None,
            validated_output=validated_output,
            evidence_artifact=evidence_context,
            relations_artifact=relations_context,
        )
        quality_artifact = self._write_json(run_dir / "quality_gates.json", quality_payload, kind="quality_gates")
        report_text = report or render_trace_report(
            run_id=run_name,
            question=question,
            as_of=as_of,
            data_refs_artifact=data_refs_artifact,
            evidence_artifact=evidence_artifact,
            relations_artifact=relations_artifact,
            quality_gates=quality_payload,
        )
        report_artifact = self._write_text(run_dir / "report.md", report_text, kind="report")

        manifest = RunManifest(
            run_id=run_name,
            created_at=created_at,
            as_of=as_of,
            question=question_artifact,
            data_refs=data_refs_artifact,
            evidence=evidence_artifact,
            relations=relations_artifact,
            model={"provider": "llm_agent", "name": "unspecified", "temperature": None},
            agent_reasoning=reasoning_payload,
            quality_gates=quality_payload,
            outputs={
                "raw_model_output": raw_output_artifact.to_dict(),
                "validated_json": validated_artifact.to_dict(),
                "agent_reasoning": reasoning_artifact.to_dict(),
                "quality_gates": quality_artifact.to_dict(),
                "report": report_artifact.to_dict(),
            },
        )
        manifest_artifact = self._write_json(run_dir / "run.json", manifest.to_dict(), kind="run_manifest")
        return manifest.to_dict() | {"path": str(run_dir), "manifest_sha256": manifest_artifact.sha256}

    def list_runs(self) -> list[dict[str, Any]]:
        if not self.runs_dir.exists():
            return []
        rows: list[dict[str, Any]] = []
        for run_dir in sorted(path for path in self.runs_dir.iterdir() if path.is_dir()):
            manifest_path = run_dir / "run.json"
            if not manifest_path.exists():
                continue
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            rows.append(
                {
                    "run_id": payload.get("run_id", run_dir.name),
                    "as_of": payload.get("as_of"),
                    "quality_status": payload.get("quality_gates", {}).get("status"),
                    "path": str(run_dir),
                }
            )
        return rows

    def _data_refs_payload(self, *, as_of: str, mart_refs: list[str], feature_refs: list[str]) -> dict[str, Any]:
        marts = [self._validated_mart_ref(ref) for ref in mart_refs]
        features = [self._validated_feature_ref(ref) for ref in feature_refs]
        return {
            "schema": "ashare.run_data_refs.v1",
            "as_of": as_of,
            "marts": marts,
            "features": features,
            "validation": _data_refs_validation([*marts, *features]),
        }

    def _validated_mart_ref(self, raw: str) -> dict[str, Any]:
        ref = _parse_data_ref(raw, kind="mart")
        if not ref["partition"]:
            return ref | {"status": "invalid", "message": "mart ref partition is required"}
        reader = MartReader(self.data_dir)
        try:
            reader.catalog.require(ref["name"])
            meta = reader.load_meta(ref["name"], ref["partition"])
        except Exception as error:
            return ref | {"status": _missing_status(error), "message": str(error)}
        quality_status = str(meta.quality_status or meta.quality.get("status") or "ok")
        return ref | {
            "status": _ready_status(quality_status),
            "message": str(meta.quality.get("reason") or ""),
            "rows": meta.rows,
            "columns": list(meta.columns),
            "path": str(reader.partition_path(ref["name"], ref["partition"])),
            "quality_status": quality_status,
            "published_at": meta.published_at,
        }

    def _validated_feature_ref(self, raw: str) -> dict[str, Any]:
        ref = _parse_data_ref(raw, kind="feature")
        as_of = ref["partition"].get("as_of")
        window_text = ref["partition"].get("window")
        if not as_of or not window_text:
            return ref | {"status": "invalid", "message": "feature ref requires as_of and window"}
        if FeatureRegistry.builtin().get(ref["name"]) is None:
            return ref | {"status": "unregistered", "message": f"feature not registered: {ref['name']}"}
        try:
            window = int(window_text)
        except ValueError:
            return ref | {"status": "invalid", "message": f"invalid feature window: {window_text}"}
        store = FeatureStore(self.data_dir)
        try:
            meta = store.load_meta(ref["name"], as_of=as_of, window=window)
        except Exception as error:
            return ref | {"status": _missing_status(error), "message": str(error)}
        quality_status = str(meta.quality_status or meta.quality.get("status") or "ok")
        return ref | {
            "status": _ready_status(quality_status),
            "message": str(meta.quality.get("reason") or ""),
            "rows": meta.rows,
            "columns": list(meta.columns),
            "path": str(store.partition_path(ref["name"], as_of=as_of, window=window)),
            "quality_status": quality_status,
            "generated_at": meta.generated_at,
        }

    def _copy_or_create_evidence(self, run_dir: Path, evidence_path: Path | str | None) -> RunArtifact:
        target = run_dir / "evidence.jsonl"
        source = Path(evidence_path) if evidence_path else EvidenceStore(self.data_dir).records_path
        if source.exists():
            shutil.copyfile(source, target)
        else:
            target.write_text("", encoding="utf-8")
        return _artifact(target, "evidence")

    def _copy_or_create_relations(self, run_dir: Path, relations_path: Path | str | None) -> RunArtifact:
        target = run_dir / "relations_snapshot.json"
        if relations_path:
            source = Path(relations_path)
            if not source.exists():
                raise RunError(f"relations snapshot not found: {source}")
            shutil.copyfile(source, target)
        else:
            snapshot = RelationStore(self.data_dir).snapshot(output_path=target)
            snapshot.pop("path", None)
            target.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return _artifact(target, "relations")

    def _write_text(self, path: Path, text: str, *, kind: str) -> RunArtifact:
        path.write_text(text, encoding="utf-8")
        return _artifact(path, kind)

    def _write_json(self, path: Path, payload: dict[str, Any], *, kind: str) -> RunArtifact:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return _artifact(path, kind)


def _artifact(path: Path, kind: str) -> RunArtifact:
    return RunArtifact(path=path.name, sha256=_file_sha256(path), kind=kind)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _slug(value: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z._-]+", "_", value.strip())
    return slug.strip("_") or "run"


def _timestamp_for_id(value: str) -> str:
    return value.replace("-", "").replace(":", "").replace("+", "_")


def _parse_data_ref(raw: str, *, kind: str) -> dict[str, Any]:
    name, _, partition_text = raw.partition(":")
    partition: dict[str, str] = {}
    if partition_text:
        for item in partition_text.split(","):
            key, separator, value = item.partition("=")
            if separator and key.strip():
                partition[key.strip()] = value.strip()
    return {
        "kind": kind,
        "name": name.strip() or raw,
        "raw": raw,
        "partition": partition,
    }


def _data_refs_validation(refs: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for ref in refs:
        status = str(ref.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    if any(status in counts for status in _BLOCKING_REF_STATUSES):
        status = "blocked"
    elif counts.get("degraded"):
        status = "degraded"
    else:
        status = "ready"
    return {
        "status": status,
        "total": len(refs),
        "counts": counts,
    }


_BLOCKING_REF_STATUSES = {"missing", "invalid", "unregistered", "schema_mismatch", "empty", "read_error"}


def _ready_status(quality_status: str) -> str:
    if quality_status in {"ok", "ready"}:
        return "ready"
    if quality_status in {"degraded"}:
        return "degraded"
    return quality_status or "ready"


def _missing_status(error: Exception) -> str:
    text = str(error).lower()
    if "not registered" in text or "not found" in text:
        return "unregistered"
    if "missing" in text or "no mart partition" in text:
        return "missing"
    return "read_error"


def _empty_agent_reasoning() -> dict[str, Any]:
    return {
        "schema": "ashare.agent_reasoning.v1",
        "status": "not_provided",
        "facts_used": [],
        "inferences": [],
        "hypotheses": [],
        "unverified_claims": [],
        "validation_steps": [],
        "open_questions": [],
    }


def _evidence_artifact_context(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"record_count": 0, "evidence_ids": [], "source_ids": [], "read_error": f"missing artifact: {path}"}
    evidence_ids: list[str] = []
    source_ids: list[str] = []
    record_count = 0
    try:
        with path.open("r", encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                if not line.strip():
                    continue
                payload = json.loads(line)
                record_count += 1
                evidence_id = str(payload.get("evidence_id") or "").strip()
                source_id = str(payload.get("source_id") or "").strip()
                if evidence_id:
                    evidence_ids.append(evidence_id)
                if source_id:
                    source_ids.append(source_id)
    except (OSError, TypeError, ValueError) as error:
        return {"record_count": record_count, "evidence_ids": evidence_ids, "source_ids": source_ids, "read_error": f"{path}: {error}"}
    return {
        "record_count": record_count,
        "evidence_ids": sorted(set(evidence_ids)),
        "source_ids": sorted(set(source_ids)),
    }


def _relations_artifact_context(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"record_count": 0, "relation_ids": [], "read_error": f"missing artifact: {path}"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as error:
        return {"record_count": 0, "relation_ids": [], "read_error": f"{path}: {error}"}
    records = payload.get("records") if isinstance(payload, dict) else []
    if not isinstance(records, list):
        return {"record_count": 0, "relation_ids": [], "read_error": f"{path}: records must be a list"}
    relation_ids = sorted({str(record.get("id") or "").strip() for record in records if isinstance(record, dict) and record.get("id")})
    return {
        "record_count": len(records),
        "relation_ids": relation_ids,
    }


def _now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")

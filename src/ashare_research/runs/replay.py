from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .recorder import RunError


def replay_run(run_dir: Path | str) -> dict[str, Any]:
    root = Path(run_dir)
    manifest_path = root / "run.json"
    if not manifest_path.exists():
        raise RunError(f"run manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifacts = _collect_artifacts(manifest)
    checks: list[dict[str, Any]] = []
    for artifact in artifacts:
        path = root / artifact["path"]
        actual = _file_sha256(path) if path.exists() else None
        checks.append(
            {
                "kind": artifact["kind"],
                "path": artifact["path"],
                "expected_sha256": artifact.get("sha256"),
                "actual_sha256": actual,
                "status": "matched" if actual and actual == artifact.get("sha256") else "mismatch",
            }
        )
    status = "replayable" if checks and all(check["status"] == "matched" for check in checks) else "mismatch"
    return {
        "schema": "ashare.run_replay.v1",
        "run_id": manifest.get("run_id"),
        "status": status,
        "quality_status": manifest.get("quality_gates", {}).get("status"),
        "artifacts": checks,
    }


def _collect_artifacts(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for key in ("question", "data_refs", "evidence", "relations"):
        artifact = manifest.get(key)
        if artifact:
            artifacts.append(artifact)
    for artifact in manifest.get("outputs", {}).values():
        if isinstance(artifact, dict):
            artifacts.append(artifact)
    return [artifact for artifact in artifacts if artifact.get("path")]


def _file_sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

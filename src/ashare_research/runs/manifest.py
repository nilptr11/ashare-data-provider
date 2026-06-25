from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RunArtifact:
    path: str
    sha256: str | None
    kind: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": self.path,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class RunManifest:
    run_id: str
    created_at: str
    as_of: str
    question: RunArtifact
    data_refs: RunArtifact | None = None
    evidence: RunArtifact | None = None
    knowledge: RunArtifact | None = None
    model: dict[str, Any] = field(default_factory=dict)
    agent_reasoning: dict[str, Any] = field(default_factory=dict)
    quality_gates: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "ashare.run_manifest.v1",
            "run_id": self.run_id,
            "created_at": self.created_at,
            "as_of": self.as_of,
            "question": self.question.to_dict(),
            "data_refs": self.data_refs.to_dict() if self.data_refs else None,
            "evidence": self.evidence.to_dict() if self.evidence else None,
            "knowledge": self.knowledge.to_dict() if self.knowledge else None,
            "model": dict(self.model),
            "agent_reasoning": dict(self.agent_reasoning),
            "quality_gates": dict(self.quality_gates),
            "outputs": dict(self.outputs),
        }

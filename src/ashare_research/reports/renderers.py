from __future__ import annotations

from typing import Any

from ..runs.manifest import RunArtifact


def render_trace_report(
    *,
    run_id: str,
    question: str,
    as_of: str,
    data_refs_artifact: RunArtifact,
    evidence_artifact: RunArtifact,
    relations_artifact: RunArtifact,
    quality_gates: dict[str, Any],
) -> str:
    lines = [
        f"# Run Trace Report: {run_id}",
        "",
        "This report is an output artifact, not a factual source.",
        "",
        "## Question",
        "",
        question.strip(),
        "",
        "## Inputs",
        "",
    ]
    lines.extend(
        [
            f"- data_refs: `{data_refs_artifact.path}` sha256=`{data_refs_artifact.sha256}`",
            f"- evidence: `{evidence_artifact.path}` sha256=`{evidence_artifact.sha256}`",
            f"- relations: `{relations_artifact.path}` sha256=`{relations_artifact.sha256}`",
            f"- as_of: `{as_of}`",
            "",
            "## Quality Gates",
            "",
            f"- status: `{quality_gates.get('status')}`",
        ]
    )
    for name, gate in quality_gates.get("gates", {}).items():
        message = gate.get("message") or ""
        lines.append(f"- {name}: `{gate.get('status')}` {message}".rstrip())
    lines.extend(["", "## Traceability", "", "- Market facts: mart and feature refs.", "- External industry claims: evidence artifact.", "- Slow variable mappings: relations artifact.", "- Model inference: model output artifacts."])
    return "\n".join(lines) + "\n"

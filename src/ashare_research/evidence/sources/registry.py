from __future__ import annotations

import json
from pathlib import Path

from ...paths import default_data_dir
from .schemas import EvidenceSource, EvidenceSourceError, validate_source


class EvidenceSourceRegistry:
    def __init__(self, data_dir: Path | str | None = None) -> None:
        self.data_dir = Path(data_dir) if data_dir is not None else default_data_dir()
        self.sources_dir = self.data_dir / "evidence" / "sources"

    def list(self) -> list[EvidenceSource]:
        if not self.sources_dir.exists():
            return []
        sources: list[EvidenceSource] = []
        for path in sorted(self.sources_dir.glob("*.json")):
            source = validate_source(EvidenceSource.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            sources.append(source)
        return sources

    def require(self, source_id: str) -> EvidenceSource:
        for source in self.list():
            if source.source_id == source_id:
                return source
        raise EvidenceSourceError(f"evidence source not found: {source_id}")

    def add(self, source: EvidenceSource, *, overwrite: bool = False) -> Path:
        source = validate_source(source)
        self.sources_dir.mkdir(parents=True, exist_ok=True)
        path = self.sources_dir / f"{_safe_filename(source.source_id)}.json"
        if path.exists() and not overwrite:
            raise EvidenceSourceError(f"evidence source already exists: {path}")
        path.write_text(json.dumps(source.to_dict(), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return path


def _safe_filename(value: str) -> str:
    return value.replace(":", "_").replace("/", "_")

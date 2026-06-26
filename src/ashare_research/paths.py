from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_data_dir() -> Path:
    configured = os.environ.get("ASHARE_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    return project_root() / "data"


def default_runs_dir(data_dir: Path | str | None = None) -> Path:
    configured = os.environ.get("ASHARE_RUNS_DIR")
    if configured:
        return Path(configured).expanduser()
    base_dir = Path(data_dir) if data_dir is not None else default_data_dir()
    return base_dir / "runs"

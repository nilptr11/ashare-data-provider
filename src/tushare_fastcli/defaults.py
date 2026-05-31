from __future__ import annotations

import json
from importlib.resources import files
from typing import Any


def load_api_defaults() -> dict[str, dict[str, Any]]:
    data_path = files("tushare_fastcli").joinpath("api_defaults.json")
    return json.loads(data_path.read_text(encoding="utf-8"))


def default_params(
    api_name: str,
    doc_id: str | None = None,
    key: str | None = None,
) -> dict[str, Any]:
    defaults = load_api_defaults()
    candidates = [
        key,
        f"{api_name}:{doc_id}" if doc_id else None,
        api_name,
    ]
    for candidate in candidates:
        if candidate and candidate in defaults:
            return dict(defaults[candidate])
    return {}

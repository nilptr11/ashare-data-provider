from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from typing import Any


def load_source_policy(path: str | Path | None = None) -> dict[str, Any]:
    if path is None:
        data_path = files("ashare_data_provider").joinpath("source_policy.json")
        return json.loads(data_path.read_text(encoding="utf-8"))
    return json.loads(Path(path).read_text(encoding="utf-8"))


def blocked_tushare_apis(path: str | Path | None = None) -> set[str]:
    policy = load_source_policy(path)
    blocked = policy.get("tushare", {}).get("blocked_apis", {})
    return {str(api_name) for api_name in blocked}


def _matches_rule(gap: dict[str, Any], rule: dict[str, Any]) -> bool:
    match = rule.get("match", {})
    section = match.get("section")
    if section is not None and str(gap.get("section")) != str(section):
        return False

    names = {str(name) for name in match.get("names", [])}
    if names and str(gap.get("name")) in names:
        return True

    source = gap.get("source", {})
    if not isinstance(source, dict):
        source = {}
    api_names = {str(api_name) for api_name in match.get("api_names", [])}
    return bool(api_names and str(source.get("api_name")) in api_names)


def resolve_gap_sources(gap: dict[str, Any], path: str | Path | None = None) -> dict[str, Any]:
    policy = load_source_policy(path)
    gap_resolution = policy.get("gap_resolution", {})
    for rule in gap_resolution.get("rules", []):
        if _matches_rule(gap, rule):
            return dict(rule)
    return dict(gap_resolution.get("default", {}))

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class InterfaceEntry:
    api_name: str
    title: str
    category: str
    description: str
    doc_url: str
    doc_id: str
    key: str
    eligibility: str = "unknown"
    required_points: int | None = None
    permission_note: str = ""
    permission_checked_at: str = ""

    @property
    def category_parts(self) -> list[str]:
        return [part.strip() for part in self.category.split(",") if part.strip()]


class InterfaceRegistry:
    def __init__(self, entries: Iterable[InterfaceEntry]) -> None:
        self._entries = list(entries)
        self._by_name: dict[str, list[InterfaceEntry]] = {}
        for entry in self._entries:
            self._by_name.setdefault(entry.api_name, []).append(entry)

    @classmethod
    def from_dicts(cls, items: Iterable[dict[str, str]]) -> "InterfaceRegistry":
        return cls(InterfaceEntry(**item) for item in items)

    @property
    def entries(self) -> list[InterfaceEntry]:
        return list(self._entries)

    def names(self) -> list[str]:
        return sorted(self._by_name)

    def categories(self) -> list[str]:
        categories = {part for entry in self._entries for part in entry.category_parts}
        return sorted(categories)

    def find(
        self,
        api_name: str,
        doc_id: str | None = None,
        key: str | None = None,
    ) -> list[InterfaceEntry]:
        entries = list(self._by_name.get(api_name, []))
        if doc_id is not None:
            entries = [entry for entry in entries if entry.doc_id == doc_id]
        if key is not None:
            entries = [entry for entry in entries if entry.key == key]
        return entries

    def exists(self, api_name: str) -> bool:
        return api_name in self._by_name

    def search(
        self,
        query: str | None = None,
        category: str | None = None,
        eligibility: str | None = None,
    ) -> list[InterfaceEntry]:
        query_text = (query or "").strip().lower()
        category_text = (category or "").strip().lower()
        eligibility_text = (eligibility or "").strip().lower()

        results = self._entries
        if query_text:
            results = [
                entry
                for entry in results
                if query_text in " ".join(
                    [
                        entry.api_name,
                        entry.title,
                        entry.category,
                        entry.description,
                        entry.doc_id,
                    ]
                ).lower()
            ]
        if category_text:
            results = [
                entry
                for entry in results
                if category_text in entry.category.lower()
            ]
        if eligibility_text:
            results = [
                entry
                for entry in results
                if entry.eligibility.lower() == eligibility_text
            ]
        return list(results)


def load_registry(path: str | Path | None = None) -> InterfaceRegistry:
    if path is None:
        data_path = files("tushare_fastcli").joinpath("interfaces.json")
        payload = json.loads(data_path.read_text(encoding="utf-8"))
    else:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))

    entries = [InterfaceEntry(**item) for item in payload["interfaces"]]
    return InterfaceRegistry(entries)

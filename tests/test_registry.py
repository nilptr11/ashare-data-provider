import unittest
from pathlib import Path

from scripts.generate_interfaces import parse_markdown
from tushare_fastcli.registry import InterfaceRegistry


ROOT = Path(__file__).resolve().parents[1]


class RegistryTest(unittest.TestCase):
    def test_parse_markdown_keeps_all_interface_rows(self) -> None:
        interfaces = parse_markdown(ROOT / "references" / "data-interfaces.md")
        self.assertEqual(len(interfaces), 229)
        self.assertEqual(interfaces[0]["api_name"], "rt_min")
        self.assertEqual(interfaces[-1]["api_name"], "fund_sales_vol")

    def test_registry_handles_duplicate_api_names(self) -> None:
        interfaces = parse_markdown(ROOT / "references" / "data-interfaces.md")
        registry = InterfaceRegistry.from_dicts(interfaces)
        self.assertEqual(len(registry.find("rt_min")), 2)

    def test_registry_can_filter_duplicate_api_names_by_doc_id_or_key(self) -> None:
        interfaces = parse_markdown(ROOT / "references" / "data-interfaces.md")
        registry = InterfaceRegistry.from_dicts(interfaces)

        self.assertEqual([entry.doc_id for entry in registry.find("rt_min", doc_id="416")], ["416"])
        self.assertEqual([entry.key for entry in registry.find("rt_min", key="rt_min:374")], ["rt_min:374"])

    def test_registry_filters_by_eligibility(self) -> None:
        registry = InterfaceRegistry.from_dicts(
            [
                {
                    "api_name": "a",
                    "title": "A",
                    "category": "C",
                    "description": "",
                    "doc_url": "https://example.com/1.md",
                    "doc_id": "1",
                    "key": "a:1",
                    "eligibility": "points_ok",
                },
                {
                    "api_name": "b",
                    "title": "B",
                    "category": "C",
                    "description": "",
                    "doc_url": "https://example.com/2.md",
                    "doc_id": "2",
                    "key": "b:2",
                    "eligibility": "points_insufficient",
                    "required_points": 15000,
                },
            ]
        )

        self.assertEqual([entry.api_name for entry in registry.search(eligibility="points_ok")], ["a"])


if __name__ == "__main__":
    unittest.main()

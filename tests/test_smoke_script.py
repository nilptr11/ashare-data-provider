import unittest
from unittest.mock import patch

from scripts.smoke_all_interfaces import selected_entries
from tushare_fastcli.registry import InterfaceRegistry


def registry_from_keys(keys: list[str]) -> InterfaceRegistry:
    return InterfaceRegistry.from_dicts(
        [
            {
                "api_name": key.split(":", maxsplit=1)[0],
                "title": key,
                "category": "C",
                "description": "",
                "doc_url": f"https://example.com/{key}.md",
                "doc_id": key.split(":", maxsplit=1)[1],
                "key": key,
                "eligibility": "points_ok",
            }
            for key in keys
        ]
    )


class SmokeScriptTest(unittest.TestCase):
    def test_selected_entries_filters_by_keys_in_requested_order(self) -> None:
        registry = registry_from_keys(["a:1", "b:2", "c:3"])

        with patch("scripts.smoke_all_interfaces.load_registry", return_value=registry):
            entries = selected_entries(
                unique_api=False,
                limit=0,
                include_restricted=True,
                current_points=0,
                allow_separate_permission=False,
                keys=["c:3", "a:1"],
            )

        self.assertEqual([entry.key for entry in entries], ["c:3", "a:1"])


if __name__ == "__main__":
    unittest.main()

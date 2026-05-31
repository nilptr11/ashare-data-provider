import unittest

from tushare_fastcli.defaults import default_params
from tushare_fastcli.issues import known_issues


class DefaultsAndIssuesTest(unittest.TestCase):
    def test_default_params_returns_copy(self) -> None:
        params = default_params("daily")
        params["trade_date"] = "changed"

        self.assertEqual(default_params("daily")["trade_date"], "20260423")

    def test_unknown_default_params_is_empty(self) -> None:
        self.assertEqual(default_params("not_exists"), {})

    def test_default_params_can_use_doc_id(self) -> None:
        self.assertEqual(
            default_params("rt_min", doc_id="416"),
            {"ts_code": "510300.SH", "freq": "1MIN"},
        )

    def test_default_params_prefers_key_over_doc_id(self) -> None:
        self.assertEqual(
            default_params("rt_min", doc_id="416", key="rt_min:374"),
            {"ts_code": "000001.SZ", "freq": "1MIN"},
        )

    def test_known_issues_for_cyq_chips(self) -> None:
        issues = known_issues("cyq_chips")

        self.assertTrue(issues)
        self.assertIn("ts_code", issues[0]["summary"])


if __name__ == "__main__":
    unittest.main()

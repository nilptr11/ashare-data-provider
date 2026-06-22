import unittest

from ashare_data_provider.source_policy import blocked_tushare_apis, load_source_policy, resolve_gap_sources


class SourcePolicyTest(unittest.TestCase):
    def test_load_source_policy_contains_blocked_permission_apis(self) -> None:
        policy = load_source_policy()

        self.assertIn("needs_separate_permission", policy["tushare"]["blocked_eligibility"])
        self.assertIn("news", policy["tushare"]["blocked_apis"])
        self.assertIn("research_report", policy["tushare"]["blocked_apis"])

    def test_blocked_tushare_apis_returns_api_names(self) -> None:
        blocked = blocked_tushare_apis()

        self.assertIn("news", blocked)
        self.assertIn("irm_qa_sz", blocked)
        self.assertIn("stk_mins", blocked)

    def test_external_sources_include_official_macro_and_announcement_sources(self) -> None:
        policy = load_source_policy()

        announcements = policy["external_source_groups"]["announcements"]
        macro = policy["external_source_groups"]["macro"]
        policy_sources = policy["external_source_groups"]["policy"]

        self.assertTrue(any(source["id"] == "cninfo_disclosure" for source in announcements))
        self.assertTrue(any(source["id"] == "stats_national_data" for source in macro))
        self.assertTrue(any(source["id"] == "pbc_interest_rates" for source in macro))
        self.assertTrue(any(source["id"] == "mee" for source in policy_sources))
        self.assertTrue(any(source["id"] == "nea" for source in policy_sources))

    def test_dynamic_source_discovery_allows_industry_specific_research(self) -> None:
        policy = load_source_policy()
        discovery = policy["dynamic_source_discovery"]

        self.assertIn("source_classes", discovery)
        self.assertTrue(any(source["id"] == "industry_association_or_designated_publisher" for source in discovery["source_classes"]))
        self.assertTrue(any("Record URL" in requirement for requirement in discovery["evidence_requirements"]))

    def test_gap_resolution_maps_index_gap_to_market_sources(self) -> None:
        rule = resolve_gap_sources(
            {
                "section": "macro",
                "name": "index_daily_hs300",
                "source": {"kind": "tushare", "api_name": "index_daily"},
            }
        )

        self.assertEqual(rule["id"], "index_market_gap")
        self.assertIn("industry_market", rule["allowed_source_groups"])
        self.assertEqual(rule["critical_use"], "auxiliary_only")

    def test_gap_resolution_maps_financial_gap_to_official_announcements(self) -> None:
        rule = resolve_gap_sources(
            {
                "section": "fundamentals",
                "name": "income",
                "source": {"kind": "tushare", "api_name": "income"},
            }
        )

        self.assertEqual(rule["id"], "financial_statement_gap")
        self.assertIn("announcements", rule["allowed_source_groups"])
        self.assertEqual(rule["critical_use"], "official_required")

    def test_unknown_gap_does_not_allow_free_search(self) -> None:
        rule = resolve_gap_sources(
            {
                "section": "unknown",
                "name": "mystery",
                "source": {"kind": "tushare", "api_name": "mystery"},
            }
        )

        self.assertEqual(rule["action"], "mark_unavailable")
        self.assertEqual(rule["allowed_source_groups"], [])


if __name__ == "__main__":
    unittest.main()

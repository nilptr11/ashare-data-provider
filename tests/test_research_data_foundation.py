import json
import os
from pathlib import Path

import pandas as pd
import pytest

from research_data_foundation.cli import main
from research_data_foundation.cli.main import load_project_env
from research_data_foundation.domains import default_registry
from research_data_foundation.evidence import (
    EvidenceProfiler,
    EvidenceSourceFetcher,
    EvidenceSourceRegistry,
    EvidenceSourceSpec,
    EvidenceStore,
    announcement_text_snippet_candidates,
    evidence_from_table,
)
from research_data_foundation.evidence.schemas import EvidenceRecord, EvidenceSourceRef, validate_evidence
from research_data_foundation.features import FeatureBuilder, FeatureRegistry, FeatureStore
from research_data_foundation.ingestion import IngestionError, IngestionRunner, resolve_params
from research_data_foundation.inventory import DataInventory
from research_data_foundation.maintenance import (
    AShareAnnouncementTextMaintainer,
    AShareConceptMembersMaintainer,
    AShareCoreMaintainer,
    AShareFinancialsMaintainer,
    AShareIndexWeightsMaintainer,
    AShareMainBusinessMaintainer,
    AShareThsConceptsMaintainer,
    IndustryReportIndexMaintainer,
    financial_period_for_as_of,
)
from research_data_foundation.relations import RelationProfiler, RelationStore
from research_data_foundation.relations.schemas import EntityRef, RelationRecord, RelationSource, validate_relation
from research_data_foundation.runs import RunRecorder
from research_data_foundation.sources import (
    CninfoSourceAdapter,
    EastmoneySourceAdapter,
    SecEdgarSourceAdapter,
    TencentQuoteAdapter,
    TushareSourceAdapter,
)
from research_data_foundation.sources.http import HttpBinaryResponse, HttpResponse
from research_data_foundation.storage import MartStore, RawStore, SourceArtifact, SourceFetchResult, StagingStore, StorageError


def test_default_registry_models_first_phase_boundaries():
    registry = default_registry()

    assert registry.validate_integrity() == []

    ashare_daily = registry.require_dataset("ashare.daily")
    assert ashare_daily.domain == "ashare_core"
    assert ashare_daily.temporal.temporal_mode == "eod"
    assert ashare_daily.temporal.finality == "final"
    assert ashare_daily.permits("candidate_generation")
    assert not ashare_daily.permits("company_business_exposure")

    trade_calendar = registry.require_dataset("ashare.trade_calendar")
    assert trade_calendar.partition_keys == ("exchange",)
    assert trade_calendar.temporal.as_of_policy == "range"

    stock_basic_contract = registry.require_dataset("ashare.stock_basic")
    assert "fullname" in stock_basic_contract.analysis_columns
    assert "exchange" in stock_basic_contract.analysis_columns

    daily_basic = registry.require_dataset("ashare.daily_basic")
    assert daily_basic.permits("feature_input")
    assert not daily_basic.permits("company_business_exposure")

    price_limits = registry.require_dataset("ashare.price_limits")
    assert price_limits.domain == "ashare_core"
    assert price_limits.role == "core_fact"
    assert price_limits.partition_keys == ("trade_date",)
    assert price_limits.permits("market_validation")
    assert not price_limits.permits("company_business_exposure")
    assert not price_limits.permits("trade_execution")

    limit_list_ths = registry.require_dataset("ashare.limit_list_ths")
    assert limit_list_ths.domain == "ashare_core"
    assert limit_list_ths.role == "enrichment_fact"
    assert limit_list_ths.partition_keys == ("trade_date",)
    assert limit_list_ths.permits("candidate_generation")
    assert limit_list_ths.permits("feature_input")
    assert not limit_list_ths.permits("company_business_exposure")
    assert not limit_list_ths.permits("trade_execution")

    assert "board_tag" in limit_list_ths.required_columns

    limit_step = registry.require_dataset("ashare.limit_step")
    assert limit_step.domain == "ashare_enrichment"
    assert limit_step.primary_key == ("trade_date", "security_id")
    assert limit_step.permits("research_prioritization")
    assert not limit_step.permits("evidence")
    assert not limit_step.permits("company_business_exposure")

    limit_concept_rank = registry.require_dataset("ashare.limit_concept_rank")
    assert limit_concept_rank.domain == "ashare_enrichment"
    assert limit_concept_rank.primary_key == ("trade_date", "concept_id")
    assert limit_concept_rank.permits("candidate_generation")
    assert not limit_concept_rank.permits("company_business_exposure")

    kpl_limit_list = registry.require_dataset("ashare.kpl_limit_list")
    assert kpl_limit_list.domain == "ashare_enrichment"
    assert kpl_limit_list.primary_key == ("trade_date", "security_id")
    assert kpl_limit_list.permits("market_validation")
    assert not kpl_limit_list.permits("evidence")
    assert not kpl_limit_list.permits("company_business_exposure")

    kpl_concept_members = registry.require_dataset("ashare.kpl_concept_members")
    assert kpl_concept_members.domain == "ashare_enrichment"
    assert kpl_concept_members.primary_key == ("trade_date", "concept_id", "security_id")
    assert kpl_concept_members.permits("evidence_triage")
    assert not kpl_concept_members.permits("evidence")
    assert not kpl_concept_members.permits("company_business_exposure")

    index_weights = registry.require_dataset("ashare.index_weights")
    assert index_weights.domain == "ashare_core"
    assert index_weights.role == "reference_fact"
    assert index_weights.partition_keys == ("snapshot_date",)
    assert index_weights.permits("candidate_generation")
    assert index_weights.permits("market_validation")
    assert not index_weights.permits("company_business_exposure")

    sec_filings = registry.require_dataset("global.sec_filings")
    assert sec_filings.domain == "global_reference"
    assert sec_filings.temporal.temporal_mode == "filing"
    assert sec_filings.temporal.finality == "final"
    assert sec_filings.permits("evidence")
    assert not sec_filings.permits("candidate_generation")

    sec_ticker_cik = registry.require_dataset("global.sec_ticker_cik")
    assert sec_ticker_cik.domain == "global_reference"
    assert sec_ticker_cik.role == "reference_fact"
    assert sec_ticker_cik.partition_keys == ("snapshot_date",)
    assert sec_ticker_cik.permits("cross_market_context")
    assert not sec_ticker_cik.permits("candidate_generation")

    sec_companyfacts = registry.require_dataset("global.sec_companyfacts")
    assert sec_companyfacts.domain == "global_reference"
    assert sec_companyfacts.role == "financial_fact"
    assert sec_companyfacts.temporal.temporal_mode == "filing"
    assert sec_companyfacts.permits("financial_analysis")
    assert not sec_companyfacts.permits("candidate_generation")

    report_index = registry.require_dataset("industry.eastmoney_report_index")
    assert report_index.role == "evidence_seed"
    assert report_index.permits("evidence")
    assert not report_index.permits("candidate_generation")

    intraday = registry.require_dataset("ashare.intraday_snapshot")
    assert intraday.domain == "ashare_intraday"
    assert intraday.temporal.temporal_mode == "intraday_snapshot"
    assert intraday.temporal.finality == "provisional"
    assert intraday.permits("market_validation")
    assert not intraday.permits("candidate_generation")

    tencent_quote = registry.require_source("tencent_quote")
    assert tencent_quote.source_role == "intraday_observation"
    assert tencent_quote.authority_tier == "S3"

    hsgt_top10 = registry.require_dataset("ashare.hsgt_top10")
    assert hsgt_top10.domain == "ashare_core"
    assert hsgt_top10.role == "enrichment_fact"
    assert hsgt_top10.partition_keys == ("trade_date",)
    assert hsgt_top10.permits("cross_market_validation")
    assert hsgt_top10.permits("candidate_generation")
    assert not hsgt_top10.permits("company_business_exposure")

    northbound_eligible = registry.require_dataset("ashare.northbound_eligible")
    assert northbound_eligible.domain == "ashare_core"
    assert northbound_eligible.role == "reference_fact"
    assert northbound_eligible.partition_keys == ("trade_date",)
    assert northbound_eligible.primary_key == ("trade_date", "connect_type", "security_id")
    assert northbound_eligible.permits("cross_market_validation")
    assert northbound_eligible.permits("candidate_generation")
    assert not northbound_eligible.permits("company_business_exposure")
    assert not northbound_eligible.permits("evidence")

    margin_detail = registry.require_dataset("ashare.margin_detail")
    assert margin_detail.domain == "ashare_core"
    assert margin_detail.role == "enrichment_fact"
    assert margin_detail.partition_keys == ("trade_date",)
    assert margin_detail.permits("market_validation")
    assert margin_detail.permits("candidate_generation")
    assert not margin_detail.permits("company_business_exposure")

    top_list = registry.require_dataset("ashare.top_list")
    assert top_list.primary_key == ("trade_date", "security_id", "reason")

    chip_perf = registry.require_dataset("ashare.chip_distribution_perf")
    assert chip_perf.domain == "ashare_enrichment"
    assert chip_perf.partition_keys == ("trade_date", "security_id")
    assert chip_perf.primary_key == ("trade_date", "security_id")
    assert chip_perf.permits("research_prioritization")
    assert not chip_perf.permits("company_business_exposure")
    assert not chip_perf.permits("evidence")

    chip_detail = registry.require_dataset("ashare.chip_distribution_detail")
    assert chip_detail.domain == "ashare_enrichment"
    assert chip_detail.primary_key == ("trade_date", "security_id", "price")
    assert chip_detail.permits("market_validation")
    assert not chip_detail.permits("company_business_exposure")

    shareholder_count = registry.require_dataset("ashare.shareholder_count")
    assert shareholder_count.domain == "ashare_enrichment"
    assert shareholder_count.partition_keys == ("period",)
    assert shareholder_count.primary_key == ("period", "security_id", "ann_date")
    assert shareholder_count.permits("research_prioritization")
    assert not shareholder_count.permits("company_business_exposure")
    assert not shareholder_count.permits("evidence")

    top10_holders = registry.require_dataset("ashare.top10_holders")
    assert top10_holders.domain == "ashare_enrichment"
    assert top10_holders.partition_keys == ("period",)
    assert top10_holders.permits("feature_input")
    assert not top10_holders.permits("company_business_exposure")
    assert not top10_holders.permits("evidence")

    top10_float_holders = registry.require_dataset("ashare.top10_float_holders")
    assert top10_float_holders.domain == "ashare_enrichment"
    assert top10_float_holders.partition_keys == ("period",)
    assert top10_float_holders.permits("research_prioritization")
    assert not top10_float_holders.permits("company_business_exposure")

    share_pledge_stats = registry.require_dataset("ashare.share_pledge_stats")
    assert share_pledge_stats.domain == "ashare_enrichment"
    assert share_pledge_stats.partition_keys == ("end_date",)
    assert share_pledge_stats.temporal.as_of_policy == "latest_before"
    assert share_pledge_stats.permits("market_validation")
    assert not share_pledge_stats.permits("company_business_exposure")

    shareholder_trades = registry.require_dataset("ashare.shareholder_trades")
    assert shareholder_trades.domain == "ashare_enrichment"
    assert shareholder_trades.partition_keys == ("ann_date",)
    assert shareholder_trades.primary_key == (
        "ann_date",
        "security_id",
        "holder_name",
        "in_de",
        "change_vol",
        "change_ratio",
        "after_share",
        "after_ratio",
        "avg_price",
        "total_share",
    )
    assert shareholder_trades.permits("evidence_triage")
    assert not shareholder_trades.permits("company_business_exposure")
    assert not shareholder_trades.permits("evidence")

    repurchase_events = registry.require_dataset("ashare.repurchase_events")
    assert repurchase_events.domain == "ashare_enrichment"
    assert repurchase_events.partition_keys == ("ann_date",)
    assert repurchase_events.primary_key == (
        "ann_date",
        "security_id",
        "end_date",
        "process_status",
        "expected_end_date",
        "volume",
        "amount",
        "high_limit",
        "low_limit",
    )
    assert repurchase_events.permits("evidence_triage")
    assert not repurchase_events.permits("company_business_exposure")
    assert not repurchase_events.permits("evidence")

    earnings_forecast_events = registry.require_dataset("ashare.earnings_forecast_events")
    assert earnings_forecast_events.domain == "ashare_enrichment"
    assert earnings_forecast_events.partition_keys == ("ann_date",)
    assert earnings_forecast_events.primary_key == (
        "ann_date",
        "security_id",
        "period",
        "forecast_type",
        "forecast_summary",
        "change_reason",
    )
    assert earnings_forecast_events.permits("financial_analysis")
    assert earnings_forecast_events.permits("evidence_triage")
    assert not earnings_forecast_events.permits("company_business_exposure")
    assert not earnings_forecast_events.permits("evidence")

    block_trades = registry.require_dataset("ashare.block_trades")
    assert block_trades.domain == "ashare_enrichment"
    assert block_trades.partition_keys == ("trade_date",)
    assert block_trades.permits("market_validation")
    assert not block_trades.permits("company_business_exposure")

    moneyflow_dc = registry.require_dataset("ashare.moneyflow_dc")
    assert moneyflow_dc.domain == "ashare_core"
    assert moneyflow_dc.role == "enrichment_fact"
    assert moneyflow_dc.primary_key == ("trade_date", "security_id")
    assert "net_amount_rate" in moneyflow_dc.analysis_columns
    assert moneyflow_dc.permits("market_validation")
    assert not moneyflow_dc.permits("company_business_exposure")
    assert not moneyflow_dc.permits("trade_execution")

    moneyflow_tushare = registry.require_dataset("ashare.moneyflow_tushare")
    assert moneyflow_tushare.domain == "ashare_enrichment"
    assert moneyflow_tushare.partition_keys == ("trade_date",)
    assert moneyflow_tushare.primary_key == ("trade_date", "security_id")
    assert moneyflow_tushare.permits("feature_input")
    assert not moneyflow_tushare.permits("company_business_exposure")

    moneyflow_board_dc = registry.require_dataset("ashare.moneyflow_board_dc")
    assert moneyflow_board_dc.domain == "ashare_enrichment"
    assert moneyflow_board_dc.primary_key == ("trade_date", "board_type", "subject_id")
    assert moneyflow_board_dc.permits("research_prioritization")
    assert not moneyflow_board_dc.permits("company_business_exposure")

    moneyflow_hsgt = registry.require_dataset("ashare.moneyflow_hsgt")
    assert moneyflow_hsgt.domain == "ashare_enrichment"
    assert moneyflow_hsgt.primary_key == ("trade_date",)
    assert moneyflow_hsgt.permits("cross_market_validation")
    assert not moneyflow_hsgt.permits("company_business_exposure")

    sw_classification = registry.require_dataset("ashare.sw_industry_classification")
    assert sw_classification.domain == "ashare_enrichment"
    assert sw_classification.role == "reference_fact"
    assert sw_classification.partition_keys == ("snapshot_date",)
    assert sw_classification.primary_key == ("snapshot_date", "source_system", "index_id")
    assert sw_classification.permits("context")
    assert sw_classification.permits("candidate_generation")
    assert not sw_classification.permits("company_business_exposure")
    assert not sw_classification.permits("evidence")

    industry_members = registry.require_dataset("ashare.industry_members")
    assert industry_members.domain == "ashare_enrichment"
    assert industry_members.role == "reference_fact"
    assert industry_members.permits("context")
    assert not industry_members.permits("company_business_exposure")

    ci_industry_members = registry.require_dataset("ashare.ci_industry_members")
    assert ci_industry_members.domain == "ashare_enrichment"
    assert ci_industry_members.role == "reference_fact"
    assert ci_industry_members.partition_keys == ("snapshot_date",)
    assert ci_industry_members.permits("context")
    assert ci_industry_members.permits("candidate_generation")
    assert not ci_industry_members.permits("company_business_exposure")

    concept_members = registry.require_dataset("ashare.concept_members")
    assert concept_members.domain == "ashare_enrichment"
    assert concept_members.role == "enrichment_fact"
    assert concept_members.partition_keys == ("snapshot_date", "concept_id")
    assert concept_members.permits("candidate_generation")
    assert not concept_members.permits("company_business_exposure")

    ths_index = registry.require_dataset("ashare.ths_index")
    assert ths_index.domain == "ashare_enrichment"
    assert ths_index.role == "reference_fact"
    assert ths_index.partition_keys == ("snapshot_date",)
    assert ths_index.permits("candidate_generation")
    assert not ths_index.permits("company_business_exposure")

    ths_concept_members = registry.require_dataset("ashare.ths_concept_members")
    assert ths_concept_members.domain == "ashare_enrichment"
    assert ths_concept_members.role == "enrichment_fact"
    assert ths_concept_members.partition_keys == ("snapshot_date", "concept_id")
    assert ths_concept_members.permits("candidate_generation")
    assert not ths_concept_members.permits("company_business_exposure")

    ths_hot_rank = registry.require_dataset("ashare.ths_hot_rank")
    assert ths_hot_rank.domain == "ashare_enrichment"
    assert ths_hot_rank.role == "enrichment_fact"
    assert ths_hot_rank.partition_keys == ("trade_date",)
    assert ths_hot_rank.permits("research_prioritization")
    assert not ths_hot_rank.permits("evidence")
    assert not ths_hot_rank.permits("company_business_exposure")

    dc_hot_rank = registry.require_dataset("ashare.dc_hot_rank")
    assert dc_hot_rank.domain == "ashare_enrichment"
    assert dc_hot_rank.role == "enrichment_fact"
    assert dc_hot_rank.partition_keys == ("trade_date",)
    assert dc_hot_rank.permits("candidate_generation")
    assert not dc_hot_rank.permits("evidence")
    assert not dc_hot_rank.permits("company_business_exposure")

    name_changes = registry.require_dataset("ashare.name_changes")
    assert name_changes.domain == "ashare_enrichment"
    assert name_changes.role == "reference_fact"
    assert name_changes.partition_keys == ("snapshot_date",)
    assert name_changes.primary_key == ("snapshot_date", "security_id", "name", "start_date", "end_date", "ann_date")
    assert name_changes.permits("context")
    assert not name_changes.permits("company_business_exposure")

    company_profile = registry.require_dataset("ashare.company_profile")
    assert company_profile.domain == "ashare_enrichment"
    assert company_profile.role == "reference_fact"
    assert company_profile.partition_keys == ("snapshot_date",)
    assert company_profile.permits("evidence_triage")
    assert not company_profile.permits("company_business_exposure")

    main_business = registry.require_dataset("ashare.main_business")
    assert main_business.role == "evidence_seed"
    assert main_business.permits("company_business_exposure")
    assert main_business.partition_keys == ("period", "security_id", "segment_type")

    income_statement = registry.require_dataset("ashare.income_statement")
    assert income_statement.domain == "ashare_financials"
    assert income_statement.role == "financial_fact"
    assert income_statement.permits("financial_analysis")
    assert not income_statement.permits("trade_execution")
    assert income_statement.partition_keys == ("period", "security_id")

    earnings_forecast = registry.require_dataset("ashare.earnings_forecast")
    assert earnings_forecast.domain == "ashare_financials"
    assert earnings_forecast.permits("financial_analysis")

    announcements = registry.require_dataset("ashare.announcements")
    assert announcements.role == "evidence_seed"
    assert announcements.permits("evidence")
    assert not announcements.permits("candidate_generation")

    announcement_text = registry.require_dataset("ashare.announcement_text")
    assert announcement_text.role == "evidence_seed"
    assert announcement_text.partition_keys == ("publish_date", "announcement_id")
    assert announcement_text.permits("company_business_exposure")
    assert not announcement_text.permits("trade_execution")

    recipe = registry.require_recipe("tushare.daily.to_ashare_daily")
    assert recipe.source_id == "tushare"
    assert recipe.target_dataset_id == "ashare.daily"
    assert recipe.lineage.raw_required is True
    assert recipe.lineage.staging_required is True

    pipeline = registry.require_pipeline("ashare_core_eod_daily")
    pipeline_recipes = {step.recipe_id for step in pipeline.steps}
    assert "tushare.daily_basic.to_ashare_daily_basic" in pipeline_recipes
    assert "tushare.limit_list_d.to_ashare_limit_list_d" in pipeline_recipes
    assert "tushare.limit_list_ths.to_ashare_limit_list_ths" in pipeline_recipes
    assert "tushare.stk_limit.to_ashare_price_limits" in pipeline_recipes
    assert "tushare.hsgt_top10.to_ashare_hsgt_top10" in pipeline_recipes
    assert "tushare.stock_hsgt.to_ashare_northbound_eligible" in pipeline_recipes
    assert "tushare.margin_detail.to_ashare_margin_detail" in pipeline_recipes
    chips_pipeline = registry.require_pipeline("ashare_chips_on_demand")
    assert chips_pipeline.domain == "ashare_enrichment"
    assert chips_pipeline.cadence == "on_demand"
    assert {step.recipe_id for step in chips_pipeline.steps} == {
        "tushare.cyq_perf.to_ashare_chip_distribution_perf",
        "tushare.cyq_chips.to_ashare_chip_distribution_detail",
    }
    ownership_pipeline = registry.require_pipeline("ashare_ownership_periodic")
    assert ownership_pipeline.cadence == "on_demand"
    assert {step.recipe_id for step in ownership_pipeline.steps} == {
        "tushare.stk_holdernumber.to_ashare_shareholder_count",
        "tushare.top10_holders.to_ashare_top10_holders",
        "tushare.top10_floatholders.to_ashare_top10_float_holders",
    }
    assert registry.require_pipeline("ashare_share_pledge_weekly").cadence == "weekly"
    corporate_action_pipeline = registry.require_pipeline("ashare_corporate_action_events_daily")
    assert corporate_action_pipeline.cadence == "daily_eod"
    assert {step.recipe_id for step in corporate_action_pipeline.steps} == {
        "tushare.repurchase.to_ashare_repurchase_events",
        "tushare.stk_holdertrade.to_ashare_shareholder_trades",
    }
    financial_event_pipeline = registry.require_pipeline("ashare_financial_event_daily")
    assert financial_event_pipeline.cadence == "daily_eod"
    assert {step.recipe_id for step in financial_event_pipeline.steps} == {
        "tushare.forecast.to_ashare_earnings_forecast_events",
    }
    assert registry.require_pipeline("ashare_block_trades_daily").cadence == "daily_eod"
    assert set(pipeline.feature_ids) == {
        "ashare.daily_momentum",
        "ashare.market_strength",
        "ashare.industry_strength",
        "ashare.concept_strength",
        "ashare.limit_sentiment",
    }
    feature_registry = FeatureRegistry.builtin()
    for feature_id in pipeline.feature_ids:
        feature_spec = feature_registry.require(feature_id)
        assert feature_spec.domain == "ashare_core"
        assert not feature_spec.permits("company_business_exposure")
        assert not feature_spec.permits("trade_execution")
    assert not feature_registry.require("ashare.market_strength").permits("candidate_generation")
    assert feature_registry.require("ashare.industry_strength").permits("candidate_generation")
    membership_pipeline = registry.require_pipeline("ashare_membership_weekly")
    assert membership_pipeline.domain == "ashare_enrichment"
    assert {step.recipe_id for step in membership_pipeline.steps} >= {
        "tushare.index_classify.to_ashare_sw_industry_classification",
        "tushare.index_member_all.to_ashare_industry_members",
        "tushare.ci_index_member.to_ashare_ci_industry_members",
    }
    identity_pipeline = registry.require_pipeline("ashare_identity_weekly")
    assert identity_pipeline.domain == "ashare_enrichment"
    assert {step.recipe_id for step in identity_pipeline.steps} >= {
        "tushare.stock_company.to_ashare_company_profile",
        "tushare.namechange.to_ashare_name_changes",
    }
    assert registry.require_pipeline("ashare_concept_members_weekly").domain == "ashare_enrichment"
    assert registry.require_pipeline("ashare_ths_index_weekly").domain == "ashare_enrichment"
    assert registry.require_pipeline("ashare_ths_concept_members_weekly").domain == "ashare_enrichment"
    attention_pipeline = registry.require_pipeline("ashare_market_attention_daily")
    assert attention_pipeline.domain == "ashare_enrichment"
    assert {step.recipe_id for step in attention_pipeline.steps} == {
        "tushare.ths_hot.to_ashare_ths_hot_rank",
        "tushare.dc_hot.to_ashare_dc_hot_rank",
    }
    short_term_pipeline = registry.require_pipeline("ashare_short_term_sentiment_daily")
    assert short_term_pipeline.domain == "ashare_enrichment"
    assert {step.recipe_id for step in short_term_pipeline.steps} == {
        "tushare.limit_step.to_ashare_limit_step",
        "tushare.limit_cpt_list.to_ashare_limit_concept_rank",
        "tushare.kpl_list.to_ashare_kpl_limit_list",
        "tushare.kpl_concept_cons.to_ashare_kpl_concept_members",
    }
    moneyflow_pipeline = registry.require_pipeline("ashare_moneyflow_daily")
    assert moneyflow_pipeline.domain == "ashare_enrichment"
    assert {step.recipe_id for step in moneyflow_pipeline.steps} >= {
        "tushare.moneyflow_dc.to_ashare_moneyflow_dc",
        "tushare.moneyflow.to_ashare_moneyflow_tushare",
        "tushare.moneyflow_ths.to_ashare_moneyflow_ths",
        "tushare.moneyflow_ind_dc.to_ashare_moneyflow_board_dc",
        "tushare.moneyflow_hsgt.to_ashare_moneyflow_hsgt",
    }
    assert registry.require_pipeline("ashare_main_business_on_demand").domain == "ashare_enrichment"
    assert registry.require_pipeline("ashare_disclosure_daily").domain == "ashare_enrichment"
    assert registry.require_pipeline("ashare_disclosure_text_on_demand").domain == "ashare_enrichment"
    assert registry.require_pipeline("ashare_financials_on_demand").domain == "ashare_financials"


def test_financial_period_for_as_of_uses_latest_fully_due_quarter():
    assert financial_period_for_as_of("20260430") == "20250930"
    assert financial_period_for_as_of("20260501") == "20260331"
    assert financial_period_for_as_of("20260624") == "20260331"
    assert financial_period_for_as_of("20260901") == "20260630"
    assert financial_period_for_as_of("20261101") == "20260930"


def test_table_storage_writes_raw_staging_mart_with_lineage(tmp_path):
    registry = default_registry()
    frame = pd.DataFrame(
        [
            {
                "security_id": "000001.SZ",
                "trade_date": "20260626",
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.2,
                "pct_chg": 2.0,
                "volume": 1000.0,
                "amount": 10000.0,
            }
        ]
    )
    raw_result = SourceFetchResult(
        source_id="tushare",
        api_name="daily",
        params={"trade_date": "20260626"},
        requested_at="2026-06-26T18:00:00+08:00",
        frame=frame,
    )
    raw_path = RawStore(tmp_path).write(raw_result)

    lineage = {
        "source_id": "tushare",
        "recipe_id": "tushare.daily.to_ashare_daily",
        "raw_path": str(raw_path),
    }
    staging_path = StagingStore(tmp_path, registry).publish(
        "ashare.daily",
        frame,
        partition={"trade_date": "20260626"},
        lineage=lineage,
    )
    mart_path = MartStore(tmp_path, registry).publish(
        "ashare.daily",
        frame,
        partition={"trade_date": "20260626"},
        lineage={**lineage, "staging_path": str(staging_path)},
    )

    assert (raw_path / "request.json").exists()
    assert (raw_path / "response.jsonl").exists()
    assert (staging_path / "part.parquet").exists()
    assert (mart_path / "part.parquet").exists()

    meta = MartStore(tmp_path, registry).read_meta("ashare.daily", {"trade_date": "20260626"})
    assert meta["schema"] == "rdf.table_partition.v1"
    assert meta["layer"] == "mart"
    assert meta["domain"] == "ashare_core"
    assert meta["temporal"]["finality"] == "final"
    assert meta["lineage"]["raw_path"] == str(raw_path)
    assert meta["quality"]["status"] == "ok"


def test_table_store_and_inventory_default_to_project_registry(tmp_path):
    frame = pd.DataFrame(
        [
            {
                "security_id": "000001.SZ",
                "trade_date": "20260626",
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.2,
                "pct_chg": 2.0,
                "volume": 1000.0,
                "amount": 10000.0,
            }
        ]
    )
    lineage = {"source_id": "tushare", "recipe_id": "tushare.daily.to_ashare_daily"}
    staging_path = StagingStore(tmp_path).publish(
        "ashare.daily",
        frame,
        partition={"trade_date": "20260626"},
        lineage=lineage,
    )
    mart = MartStore(tmp_path)
    mart.publish(
        "ashare.daily",
        frame,
        partition={"trade_date": "20260626"},
        lineage={**lineage, "staging_path": str(staging_path)},
    )

    assert len(mart.read("ashare.daily", {"trade_date": "20260626"})) == 1
    daily = next(item for item in DataInventory(tmp_path).datasets(as_of="20260626") if item["dataset_id"] == "ashare.daily")
    assert daily["status"] == "ready"


def test_table_store_reads_matching_multi_key_partitions(tmp_path):
    registry = default_registry()
    mart = MartStore(tmp_path, registry)
    for period, security_id, revenue in (
        ("20260331", "000001.SZ", 1000.0),
        ("20260331", "000002.SZ", 2000.0),
        ("20251231", "000001.SZ", 900.0),
    ):
        mart.publish(
            "ashare.income_statement",
            pd.DataFrame(
                [
                    {
                        "period": period,
                        "security_id": security_id,
                        "ann_date": "20260430",
                        "total_revenue": revenue,
                        "n_income": revenue / 10,
                    }
                ]
            ),
            partition={"period": period, "security_id": security_id},
            lineage={"source_id": "tushare"},
        )

    frame = mart.read_matching(
        "ashare.income_statement",
        {"period": "20260331"},
        columns=["period", "security_id", "total_revenue"],
    )

    assert sorted(frame["security_id"].tolist()) == ["000001.SZ", "000002.SZ"]
    assert frame["total_revenue"].sum() == 3000.0


def test_table_store_rejects_duplicate_primary_key_rows(tmp_path):
    registry = default_registry()
    frame = pd.DataFrame(
        [
            {
                "security_id": "000001.SZ",
                "trade_date": "20260626",
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.2,
                "pct_chg": 2.0,
                "volume": 1000.0,
                "amount": 10000.0,
            },
            {
                "security_id": "000001.SZ",
                "trade_date": "20260626",
                "open": 10.1,
                "high": 10.6,
                "low": 9.9,
                "close": 10.3,
                "pct_chg": 2.1,
                "volume": 1001.0,
                "amount": 10001.0,
            },
        ]
    )

    with pytest.raises(StorageError, match="duplicate primary key rows"):
        MartStore(tmp_path, registry).publish(
            "ashare.daily",
            frame,
            partition={"trade_date": "20260626"},
            lineage={"source_id": "tushare"},
        )


def test_table_store_rejects_partition_value_mismatches(tmp_path):
    registry = default_registry()
    frame = pd.DataFrame(
        [
            {
                "security_id": "000001.SZ",
                "trade_date": "20260625",
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.2,
                "pct_chg": 2.0,
                "volume": 1000.0,
                "amount": 10000.0,
            }
        ]
    )

    with pytest.raises(StorageError, match="partition column values do not match partition"):
        MartStore(tmp_path, registry).publish(
            "ashare.daily",
            frame,
            partition={"trade_date": "20260626"},
            lineage={"source_id": "tushare"},
        )


def test_data_inventory_reports_mart_feature_evidence_and_relation_availability(tmp_path):
    registry = default_registry()
    MartStore(tmp_path, registry).publish(
        "ashare.daily",
        pd.DataFrame(
            [
                {
                    "security_id": "000001.SZ",
                    "trade_date": "20260624",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.2,
                    "pct_chg": 2.0,
                    "volume": 1000.0,
                    "amount": 10000.0,
                }
            ]
        ),
        partition={"trade_date": "20260624"},
        lineage={"source_id": "tushare", "raw_path": "raw/tushare/daily/example"},
    )

    inventory = DataInventory(tmp_path, registry=registry)
    datasets = inventory.datasets(as_of="20260624", domain="ashare_core")
    daily = next(item for item in datasets if item["dataset_id"] == "ashare.daily")
    stock_basic = next(item for item in datasets if item["dataset_id"] == "ashare.stock_basic")
    candidate_datasets = inventory.datasets(as_of="20260624", use="candidate_generation")
    summary = inventory.summary(as_of="20260624")

    assert daily["status"] == "ready"
    assert daily["requested_partition"] == {"trade_date": "20260624"}
    assert daily["coverage"]["status"] == "full"
    assert daily["coverage"]["target_complete"] is True
    assert daily["active_rows"] == 1
    assert daily["latest_quality_status"] == "ok"
    assert daily["source_ids"] == ["tushare"]
    assert stock_basic["status"] == "missing"
    assert stock_basic["coverage"]["status"] == "none"
    assert {item["dataset_id"] for item in candidate_datasets} >= {"ashare.daily"}
    assert summary["datasets"]["ready"] >= 1
    assert summary["dataset_coverage"]["full"] >= 1
    assert summary["dataset_coverage"]["none"] >= 1
    assert summary["features"]["total"] == 6
    assert summary["evidence"]["records"] == 0
    assert summary["relations"]["records"] == 0

    plan = inventory.plan(as_of="20260624", domain="ashare_core", include_features=False)
    stock_basic_plan = next(item for item in plan["items"] if item["id"] == "ashare.stock_basic")
    index_weights_plan = next(item for item in plan["items"] if item["id"] == "ashare.index_weights")
    assert plan["schema"] == "rdf.inventory_recovery_plan.v1"
    assert stock_basic_plan["action"]["action_type"] == "maintain"
    assert "maintain ashare-core" in stock_basic_plan["action"]["execute_command"]["text"]
    assert "company business exposure" in stock_basic_plan["boundary"]
    assert index_weights_plan["action"]["action_type"] == "maintain"
    assert "maintain ashare-index-weights" in index_weights_plan["action"]["execute_command"]["text"]
    assert "business exposure" in index_weights_plan["boundary"]

    exposure_plan = inventory.plan(as_of="20260624", use="company_business_exposure", include_features=False)
    announcement_text_plan = next(item for item in exposure_plan["items"] if item["id"] == "ashare.announcement_text")
    main_business_plan = next(item for item in exposure_plan["items"] if item["id"] == "ashare.main_business")
    assert announcement_text_plan["action"]["action_type"] == "discover"
    assert "announcements discover" in announcement_text_plan["action"]["dry_run_command"]["text"]
    assert "announcements discover" in announcement_text_plan["action"]["execute_command"]["text"]
    assert "--start-date 20260624" in announcement_text_plan["action"]["execute_command"]["text"]
    assert "--keyword KEYWORD" in announcement_text_plan["action"]["execute_command"]["text"]
    assert "--limit 20" in announcement_text_plan["action"]["execute_command"]["text"]
    assert "ANNOUNCEMENT_ID" not in announcement_text_plan["action"]["execute_command"]["text"]
    assert "maintain ashare-main-business" in main_business_plan["action"]["execute_command"]["text"]
    assert main_business_plan["requested_partition"] == {"period": "20260331"}
    assert "--partition period=20260331" in main_business_plan["action"]["dry_run_command"]["text"]
    assert "--period 20260331" in main_business_plan["action"]["execute_command"]["text"]
    assert "--stock-snapshot-date 20260624" in main_business_plan["action"]["execute_command"]["text"]
    assert "--limit 20" in main_business_plan["action"]["execute_command"]["text"]
    assert "--segment-types P,D" in main_business_plan["action"]["execute_command"]["text"]

    financial_plan = inventory.plan(as_of="20260624", domain="ashare_financials", include_features=False)
    income_statement_plan = next(item for item in financial_plan["items"] if item["id"] == "ashare.income_statement")
    assert "maintain ashare-financials" in income_statement_plan["action"]["execute_command"]["text"]
    assert "--period 20260331" in income_statement_plan["action"]["execute_command"]["text"]
    assert "--stock-snapshot-date 20260624" in income_statement_plan["action"]["execute_command"]["text"]
    assert "--limit 20" in income_statement_plan["action"]["execute_command"]["text"]
    assert "--dataset-id ashare.income_statement" in income_statement_plan["action"]["execute_command"]["text"]
    assert "--partition period=20260331" in income_statement_plan["action"]["dry_run_command"]["text"]
    assert "PERIOD" not in income_statement_plan["action"]["execute_command"]["text"]

    industry_plan = inventory.plan(as_of="20260624", domain="industry_evidence", include_features=False)
    report_index_plan = next(item for item in industry_plan["items"] if item["id"] == "industry.eastmoney_report_index")
    assert "maintain industry-report-index" in report_index_plan["action"]["execute_command"]["text"]
    assert "--query-date 20260624" in report_index_plan["action"]["execute_command"]["text"]
    assert "--lookback-days 30" in report_index_plan["action"]["execute_command"]["text"]
    assert "--param end=2026-06-24" in report_index_plan["action"]["dry_run_command"]["text"]
    assert "as-of leakage" in report_index_plan["action"]["message"]

    enrichment_plan = inventory.plan(as_of="20260624", domain="ashare_enrichment", include_features=False)
    name_changes_plan = next(item for item in enrichment_plan["items"] if item["id"] == "ashare.name_changes")
    company_profile_plan = next(item for item in enrichment_plan["items"] if item["id"] == "ashare.company_profile")
    sw_classification_plan = next(item for item in enrichment_plan["items"] if item["id"] == "ashare.sw_industry_classification")
    ci_industry_members_plan = next(item for item in enrichment_plan["items"] if item["id"] == "ashare.ci_industry_members")
    concept_members_plan = next(item for item in enrichment_plan["items"] if item["id"] == "ashare.concept_members")
    ths_index_plan = next(item for item in enrichment_plan["items"] if item["id"] == "ashare.ths_index")
    ths_members_plan = next(item for item in enrichment_plan["items"] if item["id"] == "ashare.ths_concept_members")
    ths_hot_plan = next(item for item in enrichment_plan["items"] if item["id"] == "ashare.ths_hot_rank")
    dc_hot_plan = next(item for item in enrichment_plan["items"] if item["id"] == "ashare.dc_hot_rank")
    limit_step_plan = next(item for item in enrichment_plan["items"] if item["id"] == "ashare.limit_step")
    kpl_members_plan = next(item for item in enrichment_plan["items"] if item["id"] == "ashare.kpl_concept_members")
    moneyflow_tushare_plan = next(item for item in enrichment_plan["items"] if item["id"] == "ashare.moneyflow_tushare")
    moneyflow_board_plan = next(item for item in enrichment_plan["items"] if item["id"] == "ashare.moneyflow_board_dc")
    chip_perf_plan = next(item for item in enrichment_plan["items"] if item["id"] == "ashare.chip_distribution_perf")
    shareholder_count_plan = next(item for item in enrichment_plan["items"] if item["id"] == "ashare.shareholder_count")
    top10_holders_plan = next(item for item in enrichment_plan["items"] if item["id"] == "ashare.top10_holders")
    top10_float_holders_plan = next(item for item in enrichment_plan["items"] if item["id"] == "ashare.top10_float_holders")
    share_pledge_plan = next(item for item in enrichment_plan["items"] if item["id"] == "ashare.share_pledge_stats")
    shareholder_trades_plan = next(item for item in enrichment_plan["items"] if item["id"] == "ashare.shareholder_trades")
    repurchase_plan = next(item for item in enrichment_plan["items"] if item["id"] == "ashare.repurchase_events")
    earnings_forecast_events_plan = next(item for item in enrichment_plan["items"] if item["id"] == "ashare.earnings_forecast_events")
    block_trades_plan = next(item for item in enrichment_plan["items"] if item["id"] == "ashare.block_trades")
    assert company_profile_plan["action"]["action_type"] == "ingest_pipeline"
    assert "ashare_identity_weekly" in company_profile_plan["action"]["execute_command"]["text"]
    assert name_changes_plan["action"]["action_type"] == "ingest_pipeline"
    assert "ashare_identity_weekly" in name_changes_plan["action"]["execute_command"]["text"]
    assert sw_classification_plan["action"]["action_type"] == "ingest_pipeline"
    assert "ashare_membership_weekly" in sw_classification_plan["action"]["execute_command"]["text"]
    assert "business exposure" in sw_classification_plan["boundary"]
    assert ci_industry_members_plan["action"]["action_type"] == "ingest_pipeline"
    assert "ashare_membership_weekly" in ci_industry_members_plan["action"]["execute_command"]["text"]
    assert "business exposure" in ci_industry_members_plan["boundary"]
    assert concept_members_plan["action"]["action_type"] == "maintain"
    assert "maintain ashare-concept-members" in concept_members_plan["action"]["execute_command"]["text"]
    assert "CONCEPT_ID" not in concept_members_plan["action"]["execute_command"]["text"]
    assert "CONCEPT_ID" in concept_members_plan["action"]["dry_run_command"]["text"]
    assert ths_index_plan["action"]["action_type"] == "maintain"
    assert "maintain ashare-ths-concepts" in ths_index_plan["action"]["execute_command"]["text"]
    assert "business exposure" in ths_index_plan["boundary"]
    assert ths_members_plan["action"]["action_type"] == "maintain"
    assert "maintain ashare-ths-concepts" in ths_members_plan["action"]["execute_command"]["text"]
    assert "CONCEPT_ID" in ths_members_plan["action"]["dry_run_command"]["text"]
    assert ths_hot_plan["action"]["action_type"] == "ingest_pipeline"
    assert "ashare_market_attention_daily" in ths_hot_plan["action"]["execute_command"]["text"]
    assert "company evidence" in ths_hot_plan["action"]["message"]
    assert dc_hot_plan["action"]["action_type"] == "ingest_pipeline"
    assert "ashare_market_attention_daily" in dc_hot_plan["action"]["execute_command"]["text"]
    assert limit_step_plan["action"]["action_type"] == "ingest_pipeline"
    assert "ashare_short_term_sentiment_daily" in limit_step_plan["action"]["execute_command"]["text"]
    assert "company evidence" in limit_step_plan["action"]["message"]
    assert kpl_members_plan["action"]["action_type"] == "ingest_pipeline"
    assert "ashare_short_term_sentiment_daily" in kpl_members_plan["action"]["execute_command"]["text"]
    assert moneyflow_tushare_plan["action"]["action_type"] == "ingest_pipeline"
    assert "ashare_moneyflow_daily" in moneyflow_tushare_plan["action"]["execute_command"]["text"]
    assert "business exposure" in moneyflow_tushare_plan["action"]["message"]
    assert moneyflow_board_plan["action"]["action_type"] == "ingest_pipeline"
    assert "ashare_moneyflow_daily" in moneyflow_board_plan["action"]["execute_command"]["text"]
    assert chip_perf_plan["action"]["action_type"] == "ingest_pipeline"
    assert "ashare_chips_on_demand" in chip_perf_plan["action"]["execute_command"]["text"]
    assert "--partition security_id=SECURITY_ID" in chip_perf_plan["action"]["dry_run_command"]["text"]
    assert any("full-market fanout" in item for item in chip_perf_plan["action"]["requires"])
    assert "business exposure" in chip_perf_plan["boundary"]
    assert shareholder_count_plan["action"]["action_type"] == "ingest_pipeline"
    assert "ashare_ownership_periodic" in shareholder_count_plan["action"]["execute_command"]["text"]
    assert "--partition period=20260331" in shareholder_count_plan["action"]["dry_run_command"]["text"]
    assert top10_holders_plan["action"]["action_type"] == "ingest_pipeline"
    assert "ashare_ownership_periodic" in top10_holders_plan["action"]["execute_command"]["text"]
    assert "business exposure" in top10_holders_plan["boundary"]
    assert top10_float_holders_plan["action"]["action_type"] == "ingest_pipeline"
    assert "ashare_ownership_periodic" in top10_float_holders_plan["action"]["execute_command"]["text"]
    assert share_pledge_plan["action"]["action_type"] == "ingest_pipeline"
    assert "ashare_share_pledge_weekly" in share_pledge_plan["action"]["execute_command"]["text"]
    assert "--partition end_date=20260624" in share_pledge_plan["action"]["dry_run_command"]["text"]
    assert "business exposure" in share_pledge_plan["boundary"]
    assert shareholder_trades_plan["action"]["action_type"] == "ingest_pipeline"
    assert "ashare_corporate_action_events_daily" in shareholder_trades_plan["action"]["execute_command"]["text"]
    assert "--partition ann_date=20260624" in shareholder_trades_plan["action"]["dry_run_command"]["text"]
    assert repurchase_plan["action"]["action_type"] == "ingest_pipeline"
    assert "ashare_corporate_action_events_daily" in repurchase_plan["action"]["execute_command"]["text"]
    assert "--partition ann_date=20260624" in repurchase_plan["action"]["dry_run_command"]["text"]
    assert "official announcements" in repurchase_plan["action"]["requires"][1]
    assert earnings_forecast_events_plan["action"]["action_type"] == "ingest_pipeline"
    assert "ashare_financial_event_daily" in earnings_forecast_events_plan["action"]["execute_command"]["text"]
    assert "--partition ann_date=20260624" in earnings_forecast_events_plan["action"]["dry_run_command"]["text"]
    assert "financial event triage" in earnings_forecast_events_plan["action"]["message"]
    assert block_trades_plan["action"]["action_type"] == "ingest_pipeline"
    assert "ashare_block_trades_daily" in block_trades_plan["action"]["execute_command"]["text"]
    assert "business exposure" in block_trades_plan["boundary"]

    global_plan = inventory.plan(as_of="20260624", domain="global_reference", include_features=False)
    ticker_plan = next(item for item in global_plan["items"] if item["id"] == "global.sec_ticker_cik")
    companyfacts_plan = next(item for item in global_plan["items"] if item["id"] == "global.sec_companyfacts")
    assert ticker_plan["action"]["action_type"] == "ingest_pipeline"
    assert "global_reference_universe_weekly" in ticker_plan["action"]["execute_command"]["text"]
    assert companyfacts_plan["action"]["action_type"] == "ingest_pipeline"
    assert "global_reference_companyfacts_on_demand" in companyfacts_plan["action"]["execute_command"]["text"]


def test_inventory_period_datasets_use_as_of_target_period_not_stale_latest(tmp_path):
    registry = default_registry()
    mart = MartStore(tmp_path, registry)
    mart.publish(
        "ashare.income_statement",
        pd.DataFrame(
            [
                {
                    "period": "20251231",
                    "security_id": "000001.SZ",
                    "ann_date": "20260425",
                    "total_revenue": 1000.0,
                    "n_income": 100.0,
                }
            ]
        ),
        partition={"period": "20251231", "security_id": "000001.SZ"},
        lineage={"source_id": "tushare"},
    )
    mart.publish(
        "ashare.main_business",
        pd.DataFrame(
            [
                {
                    "period": "20251231",
                    "security_id": "000001.SZ",
                    "segment_type": "P",
                    "item_name": "个人贷款业务",
                    "sales": 100.0,
                    "gross_profit": 20.0,
                    "cost": 80.0,
                    "currency": "CNY",
                }
            ]
        ),
        partition={"period": "20251231", "security_id": "000001.SZ", "segment_type": "P"},
        lineage={"source_id": "tushare"},
    )

    inventory = DataInventory(tmp_path, registry=registry)
    financial_entries = inventory.datasets(as_of="20260624", domain="ashare_financials")
    enrichment_entries = inventory.datasets(as_of="20260624", domain="ashare_enrichment")
    income_statement = next(item for item in financial_entries if item["dataset_id"] == "ashare.income_statement")
    main_business = next(item for item in enrichment_entries if item["dataset_id"] == "ashare.main_business")
    plan = inventory.plan(as_of="20260624", domain="ashare_financials", include_features=False)
    income_statement_plan = next(item for item in plan["items"] if item["id"] == "ashare.income_statement")

    assert income_statement["status"] == "missing"
    assert income_statement["requested_partition"] == {"period": "20260331"}
    assert income_statement["requested_partition_count"] == 0
    assert income_statement["latest_partition"] == {"period": "20251231", "security_id": "000001.SZ"}
    assert "--partition period=20260331" in income_statement_plan["action"]["dry_run_command"]["text"]
    assert "--period 20260331" in income_statement_plan["action"]["execute_command"]["text"]
    assert main_business["status"] == "missing"
    assert main_business["requested_partition"] == {"period": "20260331"}
    assert main_business["coverage"]["status"] == "none"
    assert main_business["latest_partition"] == {
        "period": "20251231",
        "security_id": "000001.SZ",
        "segment_type": "P",
    }


def test_inventory_plan_includes_ready_datasets_with_partial_target_coverage(tmp_path):
    registry = default_registry()
    MartStore(tmp_path, registry).publish(
        "ashare.announcement_text",
        pd.DataFrame(
            [
                {
                    "publish_date": "20260624",
                    "announcement_id": "1225000001",
                    "security_id": "000001.SZ",
                    "title": "年度报告",
                    "source_url": "https://static.cninfo.com.cn/finalpage/2026-06-24/1225000001.PDF",
                    "pdf_sha256": "abc",
                    "text": "年度报告正文",
                    "text_length": 1200,
                    "parse_status": "ok",
                }
            ]
        ),
        partition={"publish_date": "20260624", "announcement_id": "1225000001"},
        lineage={"source_id": "cninfo"},
    )

    plan = DataInventory(tmp_path, registry=registry).plan(as_of="20260624", use="evidence", include_features=False)
    announcement_text_plan = next(item for item in plan["items"] if item["id"] == "ashare.announcement_text")

    assert plan["filters"]["coverage_statuses"] == ["none", "partial"]
    assert announcement_text_plan["status"] == "ready"
    assert announcement_text_plan["coverage"]["status"] == "partial"
    assert announcement_text_plan["coverage"]["missing_partition_keys"] == ["announcement_id"]
    assert "only covers 1 matched subpartitions" in announcement_text_plan["reason"]
    assert announcement_text_plan["action"]["action_type"] == "discover"
    assert "announcements discover" in announcement_text_plan["action"]["execute_command"]["text"]
    assert "--start-date 20260624" in announcement_text_plan["action"]["execute_command"]["text"]


def test_inventory_does_not_use_future_latest_partition_for_exact_or_latest_before(tmp_path):
    registry = default_registry()
    mart = MartStore(tmp_path, registry)
    mart.publish(
        "ashare.daily",
        pd.DataFrame(
            [
                {
                    "security_id": "000001.SZ",
                    "trade_date": "20260624",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.2,
                    "pct_chg": 2.0,
                    "volume": 1000.0,
                    "amount": 10000.0,
                }
            ]
        ),
        partition={"trade_date": "20260624"},
        lineage={"source_id": "tushare"},
    )
    mart.publish(
        "ashare.stock_basic",
        pd.DataFrame(
            [
                {
                    "snapshot_date": "20260624",
                    "security_id": "000001.SZ",
                    "symbol": "000001",
                    "name": "平安银行",
                    "fullname": "平安银行股份有限公司",
                    "market": "主板",
                    "exchange": "SZSE",
                    "list_date": "19910403",
                    "list_status": "L",
                }
            ]
        ),
        partition={"snapshot_date": "20260624"},
        lineage={"source_id": "tushare"},
    )

    inventory = DataInventory(tmp_path, registry=registry)
    before = {item["dataset_id"]: item for item in inventory.datasets(as_of="20260623", domain="ashare_core")}
    after = {item["dataset_id"]: item for item in inventory.datasets(as_of="20260625", domain="ashare_core")}

    assert before["ashare.daily"]["status"] == "missing"
    assert before["ashare.daily"]["requested_partition"] == {"trade_date": "20260623"}
    assert before["ashare.daily"]["active_partition"] is None
    assert before["ashare.stock_basic"]["status"] == "missing"
    assert before["ashare.stock_basic"]["active_partition"] is None
    assert after["ashare.stock_basic"]["status"] == "ready"
    assert after["ashare.stock_basic"]["active_partition"] == {"snapshot_date": "20260624"}
    assert after["ashare.stock_basic"]["coverage"]["status"] == "latest_before"
    assert after["ashare.stock_basic"]["coverage"]["target_complete"] is False


def test_rdf_cli_reports_inventory(capsys, tmp_path):
    registry = default_registry()
    MartStore(tmp_path, registry).publish(
        "ashare.daily",
        pd.DataFrame(
            [
                {
                    "security_id": "000001.SZ",
                    "trade_date": "20260624",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.2,
                    "pct_chg": 2.0,
                    "volume": 1000.0,
                    "amount": 10000.0,
                }
            ]
        ),
        partition={"trade_date": "20260624"},
        lineage={"source_id": "tushare"},
    )

    datasets_exit = main(
        [
            "--data-dir",
            str(tmp_path),
            "inventory",
            "datasets",
            "--as-of",
            "20260624",
            "--domain",
            "ashare_core",
        ]
    )
    datasets_payload = json.loads(capsys.readouterr().out)
    summary_exit = main(["--data-dir", str(tmp_path), "inventory", "summary", "--as-of", "20260624"])
    summary_payload = json.loads(capsys.readouterr().out)
    plan_exit = main(["--data-dir", str(tmp_path), "inventory", "plan", "--as-of", "20260624", "--domain", "ashare_core", "--limit", "1"])
    plan_payload = json.loads(capsys.readouterr().out)
    coverage_plan_exit = main(
        [
            "--data-dir",
            str(tmp_path),
            "inventory",
            "plan",
            "--as-of",
            "20260624",
            "--domain",
            "ashare_core",
            "--status",
            "ready",
            "--coverage-status",
            "full",
            "--no-features",
            "--limit",
            "1",
        ]
    )
    coverage_plan_payload = json.loads(capsys.readouterr().out)

    assert datasets_exit == 0
    assert next(item for item in datasets_payload if item["dataset_id"] == "ashare.daily")["status"] == "ready"
    assert summary_exit == 0
    assert summary_payload["schema"] == "rdf.data_inventory_summary.v1"
    assert summary_payload["datasets"]["ready"] >= 1
    assert summary_payload["dataset_coverage"]["full"] >= 1
    assert plan_exit == 0
    assert plan_payload["schema"] == "rdf.inventory_recovery_plan.v1"
    assert plan_payload["filters"]["coverage_statuses"] == ["none", "partial"]
    assert plan_payload["items"][0]["action"]["execute_command"]["text"].startswith("uv run rdf")
    assert coverage_plan_exit == 0
    assert coverage_plan_payload["filters"]["statuses"] == ["ready"]
    assert coverage_plan_payload["filters"]["coverage_statuses"] == ["full"]


def test_feature_inventory_reports_recommended_window_readiness(tmp_path):
    registry = default_registry()
    mart = MartStore(tmp_path, registry)
    for index, trade_date in enumerate(("20260618", "20260619", "20260622", "20260623", "20260624"), start=1):
        mart.publish(
            "ashare.daily",
            pd.DataFrame(
                [
                    {
                        "security_id": "000001.SZ",
                        "trade_date": trade_date,
                        "open": 10.0,
                        "high": 10.5,
                        "low": 9.8,
                        "close": 10.0 + index,
                        "pct_chg": 1.0,
                        "volume": 1000.0,
                        "amount": 10000.0,
                    }
                ]
            ),
            partition={"trade_date": trade_date},
            lineage={"source_id": "tushare"},
        )

    inventory = DataInventory(tmp_path, registry=registry)
    feature_entry = next(item for item in inventory.feature_partitions(as_of="20260624") if item["feature_id"] == "ashare.daily_momentum")
    by_window = {item["window"]: item for item in feature_entry["window_status"]}
    plan_item = next(item for item in inventory.plan(as_of="20260624", domain="ashare_core")["items"] if item["id"] == "ashare.daily_momentum")

    assert feature_entry["recommended_windows"] == [5, 20, 60]
    assert by_window[5]["buildable"] is True
    assert by_window[5]["inputs"][0]["available_partitions"] == 5
    assert by_window[20]["input_status"] == "degraded"
    assert by_window[20]["inputs"][0]["available_partitions"] == 5
    assert plan_item["action"]["buildable_windows"] == [5]
    assert "--window 5" in plan_item["action"]["execute_command"]["text"]


def test_rdf_cli_reads_dataset_window_by_available_partitions(capsys, tmp_path):
    registry = default_registry()
    mart = MartStore(tmp_path, registry)
    for trade_date, close in (("20260622", 10.0), ("20260624", 10.5), ("20260625", 11.0)):
        mart.publish(
            "ashare.daily",
            pd.DataFrame(
                [
                    {
                        "security_id": "000001.SZ",
                        "trade_date": trade_date,
                        "open": close,
                        "high": close,
                        "low": close,
                        "close": close,
                        "pct_chg": 1.0,
                        "volume": 1000.0,
                        "amount": 10000.0,
                    }
                ]
            ),
            partition={"trade_date": trade_date},
            lineage={"source_id": "tushare"},
        )

    exit_code = main(
        [
            "--data-dir",
            str(tmp_path),
            "datasets",
            "read-window",
            "ashare.daily",
            "--as-of",
            "20260624",
            "--count",
            "2",
            "--columns",
            "security_id",
            "trade_date",
            "close",
            "--limit",
            "10",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["schema"] == "rdf.dataset_read_window.v1"
    assert payload["dataset_id"] == "ashare.daily"
    assert payload["partition_key"] == "trade_date"
    assert [item["partition"]["trade_date"] for item in payload["partitions"]] == ["20260622", "20260624"]
    assert [row["trade_date"] for row in payload["records"]] == ["20260622", "20260624"]
    assert [row["close"] for row in payload["records"]] == [10.0, 10.5]
    assert payload["usage"]["allowed_uses"] == [
        "candidate_generation",
        "market_context",
        "feature_input",
        "market_validation",
    ]


def test_rdf_cli_lists_partitions_and_reads_latest_dataset_partition(capsys, tmp_path):
    registry = default_registry()
    mart = MartStore(tmp_path, registry)
    for trade_date, close in (("20260622", 10.0), ("20260625", 11.0)):
        mart.publish(
            "ashare.daily",
            pd.DataFrame(
                [
                    {
                        "security_id": "000001.SZ",
                        "trade_date": trade_date,
                        "open": close,
                        "high": close,
                        "low": close,
                        "close": close,
                        "pct_chg": 1.0,
                        "volume": 1000.0,
                        "amount": 10000.0,
                    }
                ]
            ),
            partition={"trade_date": trade_date},
            lineage={"source_id": "tushare", "raw_path": f"raw/tushare/daily/{trade_date}"},
        )

    partitions_exit = main(["--data-dir", str(tmp_path), "datasets", "partitions", "ashare.daily", "--limit", "1"])
    partitions_payload = json.loads(capsys.readouterr().out)
    latest_exit = main(
        [
            "--data-dir",
            str(tmp_path),
            "datasets",
            "latest",
            "ashare.daily",
            "--columns",
            "security_id",
            "trade_date",
            "close",
        ]
    )
    latest_payload = json.loads(capsys.readouterr().out)
    read_exit = main(
        [
            "--data-dir",
            str(tmp_path),
            "datasets",
            "read",
            "ashare.daily",
            "--partition",
            "trade_date=20260625",
            "--columns",
            "security_id",
            "trade_date",
            "close",
        ]
    )
    read_payload = json.loads(capsys.readouterr().out)

    assert partitions_exit == 0
    assert partitions_payload["schema"] == "rdf.dataset_partitions.v1"
    assert partitions_payload["partitions_total"] == 2
    assert partitions_payload["partitions"][0]["partition"] == {"trade_date": "20260625"}
    assert latest_exit == 0
    assert latest_payload["schema"] == "rdf.dataset_latest_read.v1"
    assert latest_payload["partition"] == {"trade_date": "20260625"}
    assert latest_payload["records"][0]["close"] == 11.0
    assert latest_payload["lineage"]["raw_path"] == "raw/tushare/daily/20260625"
    assert read_exit == 0
    assert read_payload["schema"] == "rdf.dataset_read.v1"
    assert read_payload["partition"] == {"trade_date": "20260625"}
    assert read_payload["partition_meta"]["lineage"]["raw_path"] == "raw/tushare/daily/20260625"
    assert read_payload["records"][0]["close"] == 11.0


def test_rdf_cli_dataset_read_uses_event_specific_boundary(capsys, tmp_path):
    registry = default_registry()
    MartStore(tmp_path, registry).publish(
        "ashare.earnings_forecast_events",
        pd.DataFrame(
            [
                {
                    "ann_date": "20260423",
                    "security_id": "000001.SZ",
                    "period": "20260331",
                    "forecast_type": "预增",
                    "forecast_summary": "预计净利润增长",
                    "change_reason": "主营业务收入增长",
                }
            ]
        ),
        partition={"ann_date": "20260423"},
        lineage={"source_id": "tushare"},
    )

    exit_code = main(
        [
            "--data-dir",
            str(tmp_path),
            "datasets",
            "read",
            "ashare.earnings_forecast_events",
            "--partition",
            "ann_date=20260423",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["schema"] == "rdf.dataset_read.v1"
    assert "financial event triage" in payload["boundary"]
    assert "official announcements" in payload["boundary"]


def test_rdf_cli_searches_datasets_by_chinese_research_intent(capsys, tmp_path):
    exit_code = main(
        [
            "--data-dir",
            str(tmp_path),
            "datasets",
            "search",
            "资金流",
            "--use",
            "market_validation",
            "--limit",
            "5",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["schema"] == "rdf.dataset_search.v1"
    dataset_ids = [item["dataset_id"] for item in payload["items"]]
    assert "ashare.moneyflow_dc" in dataset_ids
    moneyflow = next(item for item in payload["items"] if item["dataset_id"] == "ashare.moneyflow_dc")
    assert moneyflow["commands"]["partitions"]["text"] == "uv run rdf datasets partitions ashare.moneyflow_dc --limit 10"
    assert "company business exposure" in moneyflow["boundary"]


def test_rdf_cli_dataset_search_includes_active_partition_read_command(capsys, tmp_path):
    registry = default_registry()
    MartStore(tmp_path, registry).publish(
        "ashare.daily",
        pd.DataFrame(
            [
                {
                    "security_id": "000001.SZ",
                    "trade_date": "20260624",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.2,
                    "pct_chg": 2.0,
                    "volume": 1000.0,
                    "amount": 10000.0,
                }
            ]
        ),
        partition={"trade_date": "20260624"},
        lineage={"source_id": "tushare"},
    )

    exit_code = main(
        [
            "--data-dir",
            str(tmp_path),
            "datasets",
            "search",
            "日线",
            "--as-of",
            "20260624",
            "--limit",
            "10",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    daily = next(item for item in payload["items"] if item["dataset_id"] == "ashare.daily")
    assert daily["status"] == "ready"
    assert daily["coverage"]["status"] == "full"
    assert daily["active_partition"] == {"trade_date": "20260624"}
    assert daily["commands"]["read_active"]["argv"][:5] == ["uv", "run", "rdf", "datasets", "read"]
    assert "--partition" in daily["commands"]["read_active"]["argv"]
    assert "trade_date=20260624" in daily["commands"]["read_active"]["argv"]
    assert daily["commands"]["read_window"]["text"].startswith("uv run rdf datasets read-window ashare.daily --as-of 20260624")


def test_rdf_cli_scans_partial_multi_key_partition(capsys, tmp_path):
    registry = default_registry()
    mart = MartStore(tmp_path, registry)
    for announcement_id, title, text_length in (
        ("1225000001", "年度报告", 1200),
        ("1225000002", "投资者关系活动记录表", 800),
    ):
        mart.publish(
            "ashare.announcement_text",
            pd.DataFrame(
                [
                    {
                        "publish_date": "20260624",
                        "announcement_id": announcement_id,
                        "security_id": "000001.SZ",
                        "title": title,
                        "source_url": f"https://static.cninfo.com.cn/finalpage/2026-06-24/{announcement_id}.PDF",
                        "pdf_sha256": announcement_id,
                        "text": title,
                        "text_length": text_length,
                        "parse_status": "ok",
                    }
                ]
            ),
            partition={"publish_date": "20260624", "announcement_id": announcement_id},
            lineage={"source_id": "cninfo"},
        )

    exit_code = main(
        [
            "--data-dir",
            str(tmp_path),
            "datasets",
            "scan",
            "ashare.announcement_text",
            "--partition",
            "publish_date=20260624",
            "--columns",
            "announcement_id",
            "text_length",
            "parse_status",
            "--limit",
            "10",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["schema"] == "rdf.dataset_scan.v1"
    assert payload["partition_filter"] == {"publish_date": "20260624"}
    assert payload["partitions_total_matching"] == 2
    assert payload["records_returned"] == 2
    assert sorted(row["announcement_id"] for row in payload["records"]) == ["1225000001", "1225000002"]


def test_rdf_cli_searches_official_announcement_index_by_keyword_and_category(capsys, tmp_path):
    registry = default_registry()
    mart = MartStore(tmp_path, registry)
    mart.publish(
        "ashare.announcements",
        pd.DataFrame(
            [
                {
                    "publish_date": "20260624",
                    "publish_time": "2026-06-24T19:54:08+08:00",
                    "announcement_id": "1225386424",
                    "security_code": "301332",
                    "security_id": "301332.SZ",
                    "security_name": "德尔玛",
                    "org_id": "9900047227",
                    "title": "关于持股5%以上股东股份减持计划预披露的公告",
                    "short_title": "关于股东减持计划预披露的公告",
                    "announcement_type": "011501",
                    "announcement_type_name": "持股变动",
                    "source_url": "https://static.cninfo.com.cn/finalpage/2026-06-24/1225386424.PDF",
                },
                {
                    "publish_date": "20260624",
                    "publish_time": "2026-06-24T18:00:08+08:00",
                    "announcement_id": "1225386000",
                    "security_code": "000001",
                    "security_id": "000001.SZ",
                    "security_name": "平安银行",
                    "org_id": "gssz0000001",
                    "title": "2026年第一次临时股东大会决议公告",
                    "short_title": "股东大会决议公告",
                    "announcement_type": "01010503",
                    "announcement_type_name": "股东大会",
                    "source_url": "https://static.cninfo.com.cn/finalpage/2026-06-24/1225386000.PDF",
                },
            ]
        ),
        partition={"publish_date": "20260624"},
        lineage={"source_id": "cninfo"},
    )
    mart.publish(
        "ashare.announcements",
        pd.DataFrame(
            [
                {
                    "publish_date": "20260623",
                    "publish_time": "2026-06-23T18:00:08+08:00",
                    "announcement_id": "1225370000",
                    "security_code": "600000",
                    "security_id": "600000.SH",
                    "security_name": "浦发银行",
                    "org_id": "gssh0600000",
                    "title": "年度报告摘要",
                    "short_title": "年度报告摘要",
                    "announcement_type": "010301",
                    "announcement_type_name": "财务报告",
                    "source_url": "https://static.cninfo.com.cn/finalpage/2026-06-23/1225370000.PDF",
                }
            ]
        ),
        partition={"publish_date": "20260623"},
        lineage={"source_id": "cninfo"},
    )

    exit_code = main(
        [
            "--data-dir",
            str(tmp_path),
            "announcements",
            "search",
            "--as-of",
            "20260624",
            "--lookback-days",
            "2",
            "--category",
            "持股变动",
            "--keyword",
            "预披露",
            "--security-id",
            "301332.SZ",
            "--limit",
            "10",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["schema"] == "rdf.announcement_index_search.v1"
    assert payload["dataset_id"] == "ashare.announcements"
    assert payload["partitions_scanned"] == 2
    assert payload["rows_total_scanned"] == 3
    assert payload["records_total_matched"] == 1
    assert payload["records"][0]["announcement_id"] == "1225386424"
    assert payload["records"][0]["source_url"].endswith("1225386424.PDF")
    assert "announcements fetch-text" in payload["records"][0]["read_text_command"]
    assert "--source-url" in payload["records"][0]["read_text_command"]
    assert "from-announcement-text" in payload["records"][0]["snippet_command_template"]
    assert payload["category_filter_mode"] == "title_or_type_keyword_heuristic"
    assert "triage signals" in payload["boundary"]


def test_rdf_cli_plans_remote_announcement_discovery_without_fetch(capsys, tmp_path):
    exit_code = main(
        [
            "--data-dir",
            str(tmp_path),
            "announcements",
            "discover",
            "--start-date",
            "20260624",
            "--end-date",
            "20260624",
            "--security-id",
            "000001.SZ",
            "--org-id",
            "gssz0000001",
            "--keyword",
            "减持",
            "--dry-run",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["schema"] == "rdf.announcement_discovery_plan.v1"
    assert payload["will_fetch"] is False
    assert payload["will_write"] is False
    assert payload["request_params"]["stock"] == "000001,gssz0000001"
    assert payload["request_params"]["keyword"] == "减持"
    assert payload["follow_up"]["fetch_text_command_template"].startswith("uv run rdf announcements fetch-text")
    assert "does not write local mart data" in payload["boundary"]


def test_rdf_cli_discovers_remote_announcements_without_writing_mart(monkeypatch, capsys, tmp_path):
    import importlib

    calls = []

    class FakeCninfoSourceAdapter:
        def fetch(self, api_name, params):
            calls.append({"api_name": api_name, "params": params})
            return SourceFetchResult(
                source_id="cninfo",
                api_name=api_name,
                params=params,
                requested_at="2026-06-26T12:00:00+08:00",
                frame=pd.DataFrame(
                    [
                        {
                            "publish_date": "20260624",
                            "publish_time": "2026-06-24T19:54:08+08:00",
                            "announcement_id": "1225386424",
                            "security_code": "301332",
                            "security_id": "301332.SZ",
                            "security_name": "德尔玛",
                            "org_id": "9900047227",
                            "title": "关于持股5%以上股东股份减持计划预披露的公告",
                            "short_title": "关于股东减持计划预披露的公告",
                            "announcement_type": "011501",
                            "announcement_type_name": "持股变动",
                            "source_url": "https://static.cninfo.com.cn/finalpage/2026-06-24/1225386424.PDF",
                        },
                        {
                            "publish_date": "20260624",
                            "publish_time": "2026-06-24T18:00:08+08:00",
                            "announcement_id": "1225386000",
                            "security_code": "000001",
                            "security_id": "000001.SZ",
                            "security_name": "平安银行",
                            "org_id": "gssz0000001",
                            "title": "2026年第一次临时股东大会决议公告",
                            "short_title": "股东大会决议公告",
                            "announcement_type": "01010503",
                            "announcement_type_name": "股东大会",
                            "source_url": "https://static.cninfo.com.cn/finalpage/2026-06-24/1225386000.PDF",
                        },
                    ]
                ),
                metadata={},
            )

    cli_main_module = importlib.import_module("research_data_foundation.cli.main")
    monkeypatch.setattr(cli_main_module, "CninfoSourceAdapter", FakeCninfoSourceAdapter)

    exit_code = main(
        [
            "--data-dir",
            str(tmp_path),
            "announcements",
            "discover",
            "--start-date",
            "20260624",
            "--keyword",
            "预披露",
            "--category",
            "持股变动",
            "--limit",
            "10",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert calls[0]["api_name"] == "announcements"
    assert calls[0]["params"]["start_date"] == "20260624"
    assert payload["schema"] == "rdf.announcement_discovery_result.v1"
    assert payload["will_write"] is False
    assert payload["rows_total_fetched"] == 2
    assert payload["records_total_matched"] == 1
    assert payload["records"][0]["announcement_id"] == "1225386424"
    assert "announcements fetch-text" in payload["records"][0]["read_text_command"]
    assert "from-announcement-text" in payload["records"][0]["snippet_command_template"]
    assert MartStore(tmp_path, default_registry()).list_partitions("ashare.announcements") == []


def test_rdf_cli_dataset_search_suggests_scan_for_partial_date_partition(capsys, tmp_path):
    registry = default_registry()
    MartStore(tmp_path, registry).publish(
        "ashare.announcement_text",
        pd.DataFrame(
            [
                {
                    "publish_date": "20260624",
                    "announcement_id": "1225000001",
                    "security_id": "000001.SZ",
                    "title": "年度报告",
                    "source_url": "https://static.cninfo.com.cn/finalpage/2026-06-24/1225000001.PDF",
                    "pdf_sha256": "abc",
                    "text": "年度报告正文",
                    "text_length": 1200,
                    "parse_status": "ok",
                }
            ]
        ),
        partition={"publish_date": "20260624", "announcement_id": "1225000001"},
        lineage={"source_id": "cninfo"},
    )

    exit_code = main(
        [
            "--data-dir",
            str(tmp_path),
            "datasets",
            "search",
            "公告正文",
            "--as-of",
            "20260624",
            "--use",
            "evidence",
            "--limit",
            "10",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    announcement_text = next(item for item in payload["items"] if item["dataset_id"] == "ashare.announcement_text")
    assert announcement_text["requested_partition"] == {"publish_date": "20260624"}
    assert announcement_text["requested_partition_count"] == 1
    assert announcement_text["coverage"]["status"] == "partial"
    assert announcement_text["coverage"]["missing_partition_keys"] == ["announcement_id"]
    assert announcement_text["coverage"]["target_complete"] is False
    assert announcement_text["commands"]["scan_requested"]["text"].startswith(
        "uv run rdf datasets scan ashare.announcement_text --partition publish_date=20260624"
    )


def test_raw_store_writes_binary_artifacts(tmp_path):
    result = SourceFetchResult(
        source_id="cninfo",
        api_name="announcement_pdf_text",
        params={"announcement_id": "1219999999"},
        requested_at="2026-06-26T18:00:00+08:00",
        frame=pd.DataFrame([{"announcement_id": "1219999999", "parse_status": "ok"}]),
        artifacts=(SourceArtifact(filename="1219999999.pdf", content=b"%PDF-1.4 test", content_type="application/pdf"),),
    )

    raw_path = RawStore(tmp_path).write(result)
    request_payload = json.loads((raw_path / "request.json").read_text(encoding="utf-8"))

    artifact_path = raw_path / "artifacts" / "1219999999.pdf"
    assert artifact_path.read_bytes() == b"%PDF-1.4 test"
    assert request_payload["artifacts"][0]["filename"] == "1219999999.pdf"
    assert request_payload["artifacts"][0]["content_type"] == "application/pdf"
    assert request_payload["artifacts"][0]["path"] == "artifacts/1219999999.pdf"


def test_table_storage_rejects_schema_mismatch(tmp_path):
    registry = default_registry()
    frame = pd.DataFrame([{"security_id": "000001.SZ", "trade_date": "20260626"}])

    with pytest.raises(StorageError, match="missing required columns"):
        MartStore(tmp_path, registry).publish(
            "ashare.daily",
            frame,
            partition={"trade_date": "20260626"},
            lineage={"source_id": "tushare"},
        )


def test_rdf_cli_lists_registry_entries(capsys):
    exit_code = main(["registry", "list", "datasets"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    ids = {item["id"] for item in payload}
    assert {"ashare.daily", "global.sec_filings", "industry.eastmoney_report_index"} <= ids


def test_project_env_loader_sets_missing_values_without_overrides(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "# comment",
                "RDF_TEST_ENV=from_file",
                "export RDF_QUOTED='quoted value'",
                "RDF_KEEP=file_value",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("RDF_TEST_ENV", raising=False)
    monkeypatch.delenv("RDF_QUOTED", raising=False)
    monkeypatch.setenv("RDF_KEEP", "existing_value")

    load_project_env(env_path)

    assert os.environ["RDF_TEST_ENV"] == "from_file"
    assert os.environ["RDF_QUOTED"] == "quoted value"
    assert os.environ["RDF_KEEP"] == "existing_value"


def test_evidence_and_relation_ids_ignore_refresh_time_fields():
    base_source = EvidenceSourceRef(
        source_type="official_platform",
        source_name="CNINFO",
        source_url="https://static.cninfo.com.cn/finalpage/2026-06-24/1219999999.PDF",
        published_at="20260624",
        query_time="2026-06-26T14:00:00+08:00",
    )
    refreshed_source = EvidenceSourceRef(
        source_type=base_source.source_type,
        source_name=base_source.source_name,
        source_url=base_source.source_url,
        published_at=base_source.published_at,
        query_time="2026-06-26T15:00:00+08:00",
    )
    first = validate_evidence(
        EvidenceRecord(
            claim="CNINFO records 平安银行 (000001.SZ) announcement '2025年年度报告' disclosed on 20260624.",
            topic="company_announcement",
            source=base_source,
            confidence="high",
            verification="official_disclosure_index",
        )
    )
    second = validate_evidence(
        EvidenceRecord(
            claim=first.claim,
            topic=first.topic,
            source=refreshed_source,
            confidence=first.confidence,
            verification=first.verification,
        )
    )

    assert first.evidence_id == second.evidence_id

    first_relation = validate_relation(
        RelationRecord(
            subject=EntityRef("security", "ashare:security:000001.SZ", "平安银行"),
            predicate="has_filing_id",
            object=EntityRef("filing_entity", "cninfo:announcement:1219999999", "2025年年度报告"),
            confidence="high",
            source=RelationSource(raw_ref="raw/cninfo/announcements/first"),
            claim="CNINFO records 平安银行 (000001.SZ) announcement '2025年年度报告' disclosed on 20260624.",
            valid_from="20260624",
        )
    )
    second_relation = validate_relation(
        RelationRecord(
            subject=first_relation.subject,
            predicate=first_relation.predicate,
            object=first_relation.object,
            confidence=first_relation.confidence,
            source=RelationSource(raw_ref="raw/cninfo/announcements/refreshed"),
            claim=first_relation.claim,
            valid_from=first_relation.valid_from,
        )
    )

    assert first_relation.relation_id == second_relation.relation_id


def test_rdf_cli_target_shape_lists_sources_and_reads_datasets(capsys, tmp_path):
    sources_exit = main(["--data-dir", str(tmp_path), "sources", "list", "--as-of", "20260626", "--limit-datasets", "3"])
    source_payload = json.loads(capsys.readouterr().out)
    source_ids = {item["id"] for item in source_payload["sources"]}

    assert sources_exit == 0
    assert source_payload["schema"] == "rdf.source_map.v1"
    assert {"tushare", "sec_edgar", "eastmoney_direct"} <= source_ids
    tushare_source = next(item for item in source_payload["sources"] if item["id"] == "tushare")
    assert tushare_source["boundary"].startswith("Stable post-close source")
    assert tushare_source["datasets_total"] >= 50
    assert tushare_source["datasets_returned"] == 3
    assert "none" in tushare_source["dataset_coverage_counts"]
    assert all("coverage" in dataset for dataset in tushare_source["datasets"])
    assert "ashare_core_eod_daily" in {item["id"] for item in tushare_source["pipelines"]}

    source_show_exit = main(
        [
            "--data-dir",
            str(tmp_path),
            "sources",
            "show",
            "tushare",
            "--as-of",
            "20260626",
            "--use",
            "evidence",
            "--limit-datasets",
            "2",
        ]
    )
    source_show_payload = json.loads(capsys.readouterr().out)

    assert source_show_exit == 0
    assert source_show_payload["schema"] == "rdf.source_detail.v1"
    assert source_show_payload["source"]["id"] == "tushare"
    assert source_show_payload["source"]["datasets_total"] >= 1
    assert all("evidence" in dataset["usage"]["allowed_uses"] for dataset in source_show_payload["source"]["datasets"])

    datasets_exit = main(["datasets", "list", "--domain", "ashare_core"])
    dataset_payload = json.loads(capsys.readouterr().out)

    assert datasets_exit == 0
    dataset_ids = {item["id"] for item in dataset_payload}
    assert {"ashare.trade_calendar", "ashare.stock_basic", "ashare.daily", "ashare.daily_basic"} <= dataset_ids

    MartStore(tmp_path, default_registry()).publish(
        "ashare.daily",
        pd.DataFrame(
            [
                {
                    "security_id": "000001.SZ",
                    "trade_date": "20260626",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.2,
                    "pct_chg": 2.0,
                    "volume": 1000.0,
                    "amount": 10000.0,
                }
            ]
        ),
        partition={"trade_date": "20260626"},
        lineage={"source_id": "tushare"},
    )

    meta_exit = main(
        [
            "--data-dir",
            str(tmp_path),
            "datasets",
            "meta",
            "ashare.daily",
            "--partition",
            "trade_date=20260626",
        ]
    )
    meta_payload = json.loads(capsys.readouterr().out)

    assert meta_exit == 0
    assert meta_payload["dataset_id"] == "ashare.daily"

    read_exit = main(
        [
            "--data-dir",
            str(tmp_path),
            "datasets",
            "read",
            "ashare.daily",
            "--partition",
            "trade_date=20260626",
            "--columns",
            "security_id",
            "close",
        ]
    )
    read_payload = json.loads(capsys.readouterr().out)

    assert read_exit == 0
    assert read_payload["schema"] == "rdf.dataset_read.v1"
    assert read_payload["dataset_id"] == "ashare.daily"
    assert read_payload["partition"] == {"trade_date": "20260626"}
    assert read_payload["partition_meta"]["lineage"]["source_id"] == "tushare"
    assert read_payload["records"] == [{"close": 10.2, "security_id": "000001.SZ"}]
    assert "company business exposure" in read_payload["boundary"]


def test_rdf_cli_fetches_current_quote_context(monkeypatch, capsys, tmp_path):
    import importlib

    cli_module = importlib.import_module("research_data_foundation.cli.main")

    MartStore(tmp_path, default_registry()).publish(
        "ashare.daily",
        pd.DataFrame(
            [
                {
                    "security_id": "000001.SZ",
                    "trade_date": "20260626",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.2,
                    "pct_chg": 2.0,
                    "volume": 1000.0,
                    "amount": 10000.0,
                }
            ]
        ),
        partition={"trade_date": "20260626"},
        lineage={"source_id": "tushare"},
    )

    class FakeTencentQuoteAdapter:
        def fetch(self, api_name, params, fields=None):
            assert api_name == "qt.quote_snapshot"
            assert params == {"security_ids": "000001.SZ"}
            return SourceFetchResult(
                source_id="tencent_quote",
                api_name=api_name,
                params=params,
                requested_at="2026-06-27T10:00:00+08:00",
                frame=pd.DataFrame(
                    [
                        {
                            "security_id": "000001.SZ",
                            "name": "平安银行",
                            "snapshot_at": "2026-06-27T10:00:00+08:00",
                            "quote_time": "2026-06-27T10:00:00+08:00",
                            "price": 10.3,
                            "pct_chg": 0.98,
                            "change": 0.1,
                            "open": 10.2,
                            "high": 10.4,
                            "low": 10.1,
                            "prev_close": 10.2,
                            "volume": 1000.0,
                            "amount": 10000.0,
                            "quote_source": "tencent_quote",
                            "source_url": "https://qt.gtimg.cn/?q=sz000001",
                        }
                    ]
                ),
            )

    monkeypatch.setattr(cli_module, "TencentQuoteAdapter", FakeTencentQuoteAdapter)

    exit_code = cli_module.main(
        [
            "--data-dir",
            str(tmp_path),
            "quotes",
            "current",
            "--security-id",
            "000001.SZ",
            "--source",
            "tencent",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["schema"] == "rdf.current_quote.v1"
    assert payload["source"] == "tencent_quote"
    assert payload["canonical_eod"]["latest_trade_date"] == "20260626"
    assert payload["current_quote"]["finality"] == "provisional"
    assert "candidate_generation" in payload["current_quote"]["forbidden_uses"]
    assert payload["current_quote"]["records"][0]["price"] == 10.3


def test_feature_builder_builds_ashare_daily_momentum_from_canonical_daily(tmp_path):
    registry = default_registry()
    mart = MartStore(tmp_path, registry)
    for trade_date, rows in {
        "20260625": [
            {"security_id": "000001.SZ", "pct_chg": 1.0, "volume": 1000.0, "amount": 100.0},
            {"security_id": "000002.SZ", "pct_chg": 3.0, "volume": 2000.0, "amount": 200.0},
        ],
        "20260626": [
            {"security_id": "000001.SZ", "pct_chg": 2.0, "volume": 3000.0, "amount": 300.0},
            {"security_id": "000002.SZ", "pct_chg": -1.0, "volume": 1800.0, "amount": 180.0},
        ],
    }.items():
        mart.publish(
            "ashare.daily",
            pd.DataFrame(
                [
                    {
                        **row,
                        "trade_date": trade_date,
                        "open": 10.0,
                        "high": 10.5,
                        "low": 9.8,
                        "close": 10.2,
                    }
                    for row in rows
                ]
            ),
            partition={"trade_date": trade_date},
            lineage={"source_id": "tushare"},
        )

    result = FeatureBuilder(data_dir=tmp_path, registry=registry).build("ashare.daily_momentum", as_of="20260626", window=2)
    frame = FeatureStore(tmp_path).read_partition("ashare.daily_momentum", domain="ashare_core", as_of="20260626", window=2)
    meta = FeatureStore(tmp_path).load_meta("ashare.daily_momentum", domain="ashare_core", as_of="20260626", window=2)

    assert result.rows == 2
    assert frame.iloc[0]["security_id"] == "000001.SZ"
    assert frame.iloc[0]["momentum_score"] > frame.iloc[1]["momentum_score"]
    assert meta.quality["status"] == "ok"
    assert "company_business_exposure" in meta.quality["usage"]["forbidden_uses"]


def test_feature_builder_builds_ashare_market_strength_from_index_daily(tmp_path):
    registry = default_registry()
    mart = MartStore(tmp_path, registry)
    for trade_date, rows in {
        "20260625": [
            {"index_id": "000001.SH", "close": 3000.0, "pct_chg": 1.0, "volume": 100.0, "amount": 1000.0},
            {"index_id": "000300.SH", "close": 4000.0, "pct_chg": -1.0, "volume": 200.0, "amount": 2000.0},
        ],
        "20260626": [
            {"index_id": "000001.SH", "close": 3060.0, "pct_chg": 2.0, "volume": 500.0, "amount": 5000.0},
            {"index_id": "000300.SH", "close": 4000.0, "pct_chg": 0.0, "volume": 180.0, "amount": 1800.0},
        ],
    }.items():
        mart.publish(
            "ashare.index_daily",
            pd.DataFrame([{**row, "trade_date": trade_date} for row in rows]),
            partition={"trade_date": trade_date},
            lineage={"source_id": "tushare"},
        )
    mart.publish(
        "ashare.index_daily_basic",
        pd.DataFrame(
            [
                {"index_id": "000001.SH", "trade_date": "20260626", "total_mv": 100000.0},
                {"index_id": "000300.SH", "trade_date": "20260626", "total_mv": 200000.0},
            ]
        ),
        partition={"trade_date": "20260626"},
        lineage={"source_id": "tushare"},
    )

    result = FeatureBuilder(data_dir=tmp_path, registry=registry).build("ashare.market_strength", as_of="20260626", window=2)
    frame = FeatureStore(tmp_path).read_partition("ashare.market_strength", domain="ashare_core", as_of="20260626", window=2)
    meta = FeatureStore(tmp_path).load_meta("ashare.market_strength", domain="ashare_core", as_of="20260626", window=2)

    assert result.rows == 2
    assert frame.iloc[0]["index_id"] == "000001.SH"
    assert frame.iloc[0]["strength_score"] > frame.iloc[1]["strength_score"]
    assert frame.iloc[0]["latest_total_mv"] == 100000.0
    assert meta.quality["status"] == "ok"
    assert "candidate_generation" in meta.quality["usage"]["forbidden_uses"]


def test_feature_builder_builds_ashare_industry_and_concept_strength(tmp_path):
    registry = default_registry()
    mart = MartStore(tmp_path, registry)
    for trade_date in ("20260625", "20260626"):
        mart.publish(
            "ashare.sw_daily",
            pd.DataFrame(
                [
                    {
                        "index_id": "801010.SI",
                        "trade_date": trade_date,
                        "close": 100.0,
                        "pct_chg": 2.0 if trade_date == "20260626" else 1.0,
                        "volume": 500.0 if trade_date == "20260626" else 100.0,
                    }
                ]
            ),
            partition={"trade_date": trade_date},
            lineage={"source_id": "tushare"},
        )
        mart.publish(
            "ashare.ci_daily",
            pd.DataFrame(
                [
                    {
                        "index_id": "CI005001.CI",
                        "trade_date": trade_date,
                        "close": 100.0,
                        "pct_chg": 0.5,
                        "volume": 100.0,
                    }
                ]
            ),
            partition={"trade_date": trade_date},
            lineage={"source_id": "tushare"},
        )
        mart.publish(
            "ashare.dc_index",
            pd.DataFrame(
                [
                    {"concept_id": "BK001", "trade_date": trade_date, "name": "AI算力", "pct_chg": 3.0 if trade_date == "20260626" else 1.0},
                    {"concept_id": "BK002", "trade_date": trade_date, "name": "机器人", "pct_chg": 0.5},
                ]
            ),
            partition={"trade_date": trade_date},
            lineage={"source_id": "tushare"},
        )

    builder = FeatureBuilder(data_dir=tmp_path, registry=registry)
    industry_result = builder.build("ashare.industry_strength", as_of="20260626", window=2)
    concept_result = builder.build("ashare.concept_strength", as_of="20260626", window=2)
    industry = FeatureStore(tmp_path).read_partition("ashare.industry_strength", domain="ashare_core", as_of="20260626", window=2)
    concepts = FeatureStore(tmp_path).read_partition("ashare.concept_strength", domain="ashare_core", as_of="20260626", window=2)

    assert industry_result.rows == 2
    assert set(industry["source_dataset"]) == {"ashare.sw_daily", "ashare.ci_daily"}
    assert industry.iloc[0]["index_id"] == "801010.SI"
    assert concept_result.rows == 2
    assert concepts.iloc[0]["concept_id"] == "BK001"
    assert concepts.iloc[0]["name"] == "AI算力"


def test_feature_builder_builds_ashare_limit_sentiment_from_d_and_ths_sources(tmp_path):
    registry = default_registry()
    mart = MartStore(tmp_path, registry)
    mart.publish(
        "ashare.limit_list_d",
        pd.DataFrame(
            [
                {
                    "security_id": "000001.SZ",
                    "trade_date": "20260625",
                    "name": "平安银行",
                    "close": 10.2,
                    "pct_chg": 10.0,
                    "limit": "U",
                }
            ]
        ),
        partition={"trade_date": "20260625"},
        lineage={"source_id": "tushare"},
    )
    mart.publish(
        "ashare.limit_list_ths",
        pd.DataFrame(
            [
                {
                    "security_id": "000002.SZ",
                    "trade_date": "20260626",
                    "name": "万科A",
                    "price": 8.8,
                    "pct_chg": 10.0,
                    "limit_type": "涨停池",
                    "board_tag": "3天2板",
                    "open_num": 1.0,
                    "limit_order": 1000.0,
                    "limit_amount": 8800.0,
                },
                {
                    "security_id": "000003.SZ",
                    "trade_date": "20260626",
                    "name": "测试股份",
                    "price": 12.0,
                    "pct_chg": 10.0,
                    "limit_type": "涨停池",
                    "board_tag": "首板",
                    "open_num": 0.0,
                    "limit_order": 500.0,
                    "limit_amount": 6000.0,
                },
            ]
        ),
        partition={"trade_date": "20260626"},
        lineage={"source_id": "tushare"},
    )

    result = FeatureBuilder(data_dir=tmp_path, registry=registry).build("ashare.limit_sentiment", as_of="20260626", window=2)
    frame = FeatureStore(tmp_path).read_partition("ashare.limit_sentiment", domain="ashare_core", as_of="20260626", window=2)
    meta = FeatureStore(tmp_path).load_meta("ashare.limit_sentiment", domain="ashare_core", as_of="20260626", window=2)
    by_date = {row["trade_date"]: row for row in frame.to_dict("records")}

    assert result.rows == 2
    assert by_date["20260625"]["limit_up_count"] == 1
    assert by_date["20260625"]["ths_limit_up_count"] == 0
    assert by_date["20260626"]["ths_limit_up_count"] == 2
    assert by_date["20260626"]["max_board_height"] == 2
    assert meta.quality["status"] == "degraded"
    assert meta.quality["component_quality"]["ashare.limit_list_d"]["status"] == "partial_window"
    assert meta.quality["component_quality"]["ashare.limit_list_ths"]["status"] == "partial_window"


def test_ashare_core_maintainer_runs_rolling_window_and_skips_existing_partitions(tmp_path):
    registry = default_registry()
    adapter = FakeTushareMaintenanceAdapter()
    maintainer = AShareCoreMaintainer(data_dir=tmp_path, registry=registry, adapters={"tushare": adapter})

    result = maintainer.maintain(as_of="20260626", lookback_trading_days=3, windows=(2,))

    assert result["schema"] == "rdf.ashare_core_maintenance_run.v1"
    assert result["trade_dates"] == ["20260623", "20260624", "20260626"]
    assert result["status"] == "ready"
    assert ("daily", {"trade_date": "20260625"}) not in adapter.calls
    assert ("daily", {"trade_date": "20260623"}) in adapter.calls
    assert ("daily", {"trade_date": "20260624"}) in adapter.calls
    assert ("daily", {"trade_date": "20260626"}) in adapter.calls

    mart = MartStore(tmp_path, registry)
    daily = mart.read("ashare.daily", {"trade_date": "20260626"})
    assert daily.iloc[0]["security_id"] == "000001.SZ"
    stock_basic = mart.read("ashare.stock_basic", {"snapshot_date": "20260626"})
    assert stock_basic.iloc[0]["security_id"] == "000001.SZ"
    limit_ths = mart.read("ashare.limit_list_ths", {"trade_date": "20260626"})
    assert limit_ths.iloc[0]["board_tag"] == "首板"
    assert ("limit_list_ths", {"trade_date": "20260626"}) in adapter.calls
    northbound = mart.read("ashare.northbound_eligible", {"trade_date": "20260626"})
    assert set(northbound["connect_type"]) == {"HK_SH", "HK_SZ"}
    assert ("stock_hsgt", {"trade_date": "20260623", "type": "HK_SH"}) in adapter.calls
    assert ("stock_hsgt", {"trade_date": "20260624", "type": "HK_SZ"}) in adapter.calls
    assert ("stock_hsgt", {"trade_date": "20260626", "type": "HK_SH"}) in adapter.calls
    feature_store = FeatureStore(tmp_path)
    feature = feature_store.read_partition("ashare.daily_momentum", domain="ashare_core", as_of="20260626", window=2)
    assert feature.iloc[0]["security_id"] == "000001.SZ"
    assert feature_store.read_partition("ashare.market_strength", domain="ashare_core", as_of="20260626", window=2).iloc[0]["index_id"] == "000001.SH"
    assert feature_store.read_partition("ashare.industry_strength", domain="ashare_core", as_of="20260626", window=2).iloc[0]["source_dataset"] in {
        "ashare.sw_daily",
        "ashare.ci_daily",
    }
    assert feature_store.read_partition("ashare.concept_strength", domain="ashare_core", as_of="20260626", window=2).iloc[0]["concept_id"] == "BK001"
    assert feature_store.read_partition("ashare.limit_sentiment", domain="ashare_core", as_of="20260626", window=2).iloc[-1]["ths_limit_up_count"] == 1

    status = maintainer.status(as_of="20260626", lookback_trading_days=3, windows=(2,))
    assert status["status"] == "ready"
    assert not status["blocking"]

    adapter.calls.clear()
    second = maintainer.maintain(as_of="20260626", lookback_trading_days=3, windows=(2,))

    assert second["status"] == "ready"
    assert adapter.calls == []
    assert {item["status"] for item in second["tasks"]} == {"skipped"}
    assert {item["status"] for item in second["features"]} == {"skipped"}


def test_ashare_core_maintenance_status_blocks_missing_required_data(tmp_path):
    status = AShareCoreMaintainer(data_dir=tmp_path, registry=default_registry()).status(
        as_of="20260626",
        lookback_trading_days=3,
        windows=(2,),
    )

    assert status["status"] == "blocked"
    blocking_ids = {item["dataset_id"] for item in status["blocking"]}
    assert "ashare.trade_calendar" in blocking_ids
    assert "ashare.daily" in blocking_ids


def test_ashare_core_maintainer_returns_blocked_payload_when_calendar_unusable(tmp_path):
    class EmptyCalendarAdapter(FakeTushareMaintenanceAdapter):
        def fetch(self, api_name, params, fields=None):
            if api_name == "trade_cal":
                self.calls.append((api_name, dict(params)))
                return SourceFetchResult(
                    source_id="tushare",
                    api_name=api_name,
                    params=dict(params),
                    requested_at="2026-06-26T18:00:00+08:00",
                    frame=pd.DataFrame(),
                )
            return super().fetch(api_name, params)

    maintainer = AShareCoreMaintainer(
        data_dir=tmp_path,
        registry=default_registry(),
        adapters={"tushare": EmptyCalendarAdapter()},
    )

    result = maintainer.maintain(
        as_of="20260626",
        lookback_trading_days=3,
        windows=(2,),
        continue_on_error=True,
        build_features=False,
    )

    assert result["status"] == "blocked"
    assert result["trade_dates"] == []
    assert result["message"] == "trade calendar has 0 open dates in requested window; expected 3"
    assert result["tasks"][0]["dataset_id"] == "ashare.trade_calendar"
    assert result["tasks"][0]["status"] == "failed"


def test_ashare_main_business_maintainer_uses_stock_pool_and_skips_existing_partitions(tmp_path):
    class FakeMainBusinessAdapter:
        source_id = "tushare"

        def __init__(self):
            self.calls = []

        def fetch(self, api_name, params, fields=None):
            self.calls.append((api_name, dict(params)))
            segment_type = params["type"]
            return SourceFetchResult(
                source_id="tushare",
                api_name=api_name,
                params=dict(params),
                requested_at="2026-06-26T18:00:00+08:00",
                frame=pd.DataFrame(
                    [
                        {
                            "ts_code": params["ts_code"],
                            "end_date": params["period"],
                            "bz_item": "个人贷款业务" if segment_type == "P" else "东区",
                            "bz_code": segment_type,
                            "bz_sales": 100.0,
                            "bz_profit": 20.0,
                            "bz_cost": 80.0,
                            "curr_type": "CNY",
                        }
                    ]
                ),
            )

    registry = default_registry()
    MartStore(tmp_path, registry).publish(
        "ashare.stock_basic",
        pd.DataFrame(
            [
                {
                    "snapshot_date": "20260624",
                    "security_id": "000001.SZ",
                    "symbol": "000001",
                    "name": "平安银行",
                    "market": "主板",
                    "list_status": "L",
                }
            ]
        ),
        partition={"snapshot_date": "20260624"},
        lineage={"source_id": "tushare"},
    )
    adapter = FakeMainBusinessAdapter()
    maintainer = AShareMainBusinessMaintainer(data_dir=tmp_path, registry=registry, adapters={"tushare": adapter})

    result = maintainer.maintain(period="20251231", stock_snapshot_date="20260624", limit=1)

    assert result["schema"] == "rdf.ashare_main_business_maintenance_run.v1"
    assert result["status"] == "ready"
    assert len(result["tasks"]) == 2
    assert ("fina_mainbz", {"ts_code": "000001.SZ", "period": "20251231", "type": "P"}) in adapter.calls
    frame = MartStore(tmp_path, registry).read(
        "ashare.main_business",
        {"period": "20251231", "security_id": "000001.SZ", "segment_type": "P"},
    )
    assert frame.iloc[0]["item_name"] == "个人贷款业务"

    adapter.calls.clear()
    second = maintainer.maintain(period="20251231", security_ids=("000001.SZ",), segment_types=("P",))

    assert second["status"] == "ready"
    assert second["tasks"][0]["status"] == "skipped"
    assert adapter.calls == []


def test_ashare_concept_members_maintainer_uses_dc_index_pool_and_skips_existing_partitions(tmp_path):
    class FakeConceptMemberAdapter:
        source_id = "tushare"

        def __init__(self):
            self.calls = []

        def fetch(self, api_name, params, fields=None):
            self.calls.append((api_name, dict(params)))
            return SourceFetchResult(
                source_id="tushare",
                api_name=api_name,
                params=dict(params),
                requested_at="2026-06-26T18:00:00+08:00",
                frame=pd.DataFrame(
                    [
                        {
                            "ts_code": params["ts_code"],
                            "con_code": "000001.SZ",
                            "name": "平安银行",
                        }
                    ]
                ),
            )

    registry = default_registry()
    MartStore(tmp_path, registry).publish(
        "ashare.dc_index",
        pd.DataFrame(
            [
                {"trade_date": "20260624", "concept_id": "BK1234", "name": "AI算力", "pct_chg": 2.0},
                {"trade_date": "20260624", "concept_id": "BK5678", "name": "机器人", "pct_chg": 1.0},
            ]
        ),
        partition={"trade_date": "20260624"},
        lineage={"source_id": "tushare"},
    )
    adapter = FakeConceptMemberAdapter()
    maintainer = AShareConceptMembersMaintainer(data_dir=tmp_path, registry=registry, adapters={"tushare": adapter})

    result = maintainer.maintain(snapshot_date="20260624", limit=1)

    assert result["schema"] == "rdf.ashare_concept_members_maintenance_run.v1"
    assert result["status"] == "ready"
    assert result["concept_ids"] == ["BK1234"]
    assert ("dc_member", {"ts_code": "BK1234"}) in adapter.calls
    frame = MartStore(tmp_path, registry).read(
        "ashare.concept_members",
        {"snapshot_date": "20260624", "concept_id": "BK1234"},
    )
    assert frame.iloc[0]["security_id"] == "000001.SZ"
    assert frame.iloc[0]["security_name"] == "平安银行"

    adapter.calls.clear()
    second = maintainer.maintain(snapshot_date="20260624", concept_ids=("BK1234",))

    assert second["status"] == "ready"
    assert second["tasks"][0]["status"] == "skipped"
    assert adapter.calls == []


def test_ashare_ths_concepts_maintainer_uses_ths_index_pool_and_skips_existing_partitions(tmp_path):
    class FakeThsConceptAdapter:
        source_id = "tushare"

        def __init__(self):
            self.calls = []

        def fetch(self, api_name, params, fields=None):
            self.calls.append((api_name, dict(params), tuple(fields or ())))
            if api_name == "ths_index":
                return SourceFetchResult(
                    source_id="tushare",
                    api_name=api_name,
                    params=dict(params),
                    requested_at="2026-06-26T18:00:00+08:00",
                    frame=pd.DataFrame(
                        [
                            {"ts_code": "885001.TI", "name": "人工智能", "count": 80, "exchange": "A", "list_date": "20200101", "type": "N"},
                            {"ts_code": "881001.TI", "name": "半导体", "count": 60, "exchange": "A", "list_date": "20200101", "type": "I"},
                        ]
                    ),
                )
            return SourceFetchResult(
                source_id="tushare",
                api_name=api_name,
                params=dict(params),
                requested_at="2026-06-26T18:00:00+08:00",
                frame=pd.DataFrame(
                    [
                        {
                            "ts_code": params["ts_code"],
                            "con_code": "000001.SZ",
                            "con_name": "平安银行",
                            "weight": 1.2,
                            "in_date": "20200101",
                            "out_date": "",
                            "is_new": "Y",
                        }
                    ]
                ),
            )

    registry = default_registry()
    adapter = FakeThsConceptAdapter()
    maintainer = AShareThsConceptsMaintainer(data_dir=tmp_path, registry=registry, adapters={"tushare": adapter})

    result = maintainer.maintain(snapshot_date="20260624", limit=1)

    assert result["schema"] == "rdf.ashare_ths_concepts_maintenance_run.v1"
    assert result["status"] == "ready"
    assert result["concept_ids"] == ["885001.TI"]
    assert ("ths_index", {"exchange": "A"}, ("ts_code", "name", "count", "exchange", "list_date", "type")) in adapter.calls
    assert ("ths_member", {"ts_code": "885001.TI"}, ("ts_code", "con_code", "con_name", "weight", "in_date", "out_date", "is_new")) in adapter.calls
    index_frame = MartStore(tmp_path, registry).read("ashare.ths_index", {"snapshot_date": "20260624"})
    assert index_frame.iloc[0]["concept_id"] == "885001.TI"
    assert index_frame.iloc[0]["source_member_count"] == 80
    members = MartStore(tmp_path, registry).read(
        "ashare.ths_concept_members",
        {"snapshot_date": "20260624", "concept_id": "885001.TI"},
    )
    assert members.iloc[0]["security_id"] == "000001.SZ"
    assert members.iloc[0]["security_name"] == "平安银行"

    adapter.calls.clear()
    second = maintainer.maintain(snapshot_date="20260624", concept_ids=("885001.TI",))

    assert second["status"] == "ready"
    assert {task["status"] for task in second["tasks"]} == {"skipped"}
    assert adapter.calls == []


def test_ashare_index_weights_maintainer_keeps_latest_weight_date_per_index(tmp_path):
    class FakeIndexWeightAdapter:
        source_id = "tushare"

        def __init__(self):
            self.calls = []

        def fetch(self, api_name, params, fields=None):
            self.calls.append((api_name, dict(params), tuple(fields or ())))
            index_code = params["index_code"]
            if index_code == "000300.SH":
                rows = [
                    {"index_code": index_code, "con_code": "000001.SZ", "trade_date": "20260529", "weight": 0.5},
                    {"index_code": index_code, "con_code": "000001.SZ", "trade_date": "20260601", "weight": 0.7},
                    {"index_code": index_code, "con_code": "000002.SZ", "trade_date": "20260601", "weight": 0.3},
                ]
            else:
                rows = [
                    {"index_code": index_code, "con_code": "600000.SH", "trade_date": "20260529", "weight": 1.2},
                ]
            return SourceFetchResult(
                source_id="tushare",
                api_name=api_name,
                params=dict(params),
                requested_at="2026-06-26T18:00:00+08:00",
                frame=pd.DataFrame(rows),
            )

    registry = default_registry()
    adapter = FakeIndexWeightAdapter()
    maintainer = AShareIndexWeightsMaintainer(data_dir=tmp_path, registry=registry, adapters={"tushare": adapter})

    result = maintainer.maintain(
        snapshot_date="20260624",
        start_date="20260501",
        index_codes=("000300.SH", "000016.SH"),
    )

    assert result["schema"] == "rdf.ashare_index_weights_maintenance_run.v1"
    assert result["status"] == "ready"
    assert result["rows"] == 3
    assert result["latest_weight_dates"] == {"000300.SH": "20260601", "000016.SH": "20260529"}
    assert {call[1]["index_code"] for call in adapter.calls} == {"000300.SH", "000016.SH"}
    frame = MartStore(tmp_path, registry).read("ashare.index_weights", {"snapshot_date": "20260624"})
    assert set(frame["index_id"]) == {"000300.SH", "000016.SH"}
    assert set(frame["weight_trade_date"]) == {"20260601", "20260529"}
    assert "20260529" not in frame[frame["index_id"] == "000300.SH"]["weight_trade_date"].tolist()
    meta = MartStore(tmp_path, registry).read_meta("ashare.index_weights", {"snapshot_date": "20260624"})
    assert meta["lineage"]["latest_weight_dates"]["000300.SH"] == "20260601"
    assert meta["lineage"]["maintainer"] == "ashare-index-weights"

    adapter.calls.clear()
    second = maintainer.maintain(snapshot_date="20260624", index_codes=("000300.SH",))

    assert second["status"] == "ready"
    assert second["result"]["status"] == "skipped"
    assert adapter.calls == []


def test_ashare_financials_maintainer_uses_stock_pool_and_skips_existing_partitions(tmp_path):
    class FakeFinancialAdapter:
        source_id = "tushare"

        def __init__(self):
            self.calls = []

        def fetch(self, api_name, params, fields=None):
            self.calls.append((api_name, dict(params)))
            return SourceFetchResult(
                source_id="tushare",
                api_name=api_name,
                params=dict(params),
                requested_at="2026-06-26T18:00:00+08:00",
                frame=pd.DataFrame(
                    [
                        {
                            "ts_code": params["ts_code"],
                            "end_date": params.get("period") or params.get("end_date"),
                            "ann_date": "20260320",
                            "total_revenue": 1000.0,
                            "n_income": 100.0,
                        }
                    ]
                ),
            )

    registry = default_registry()
    MartStore(tmp_path, registry).publish(
        "ashare.stock_basic",
        pd.DataFrame(
            [
                {
                    "snapshot_date": "20260624",
                    "security_id": "000001.SZ",
                    "symbol": "000001",
                    "name": "平安银行",
                    "market": "主板",
                    "list_status": "L",
                }
            ]
        ),
        partition={"snapshot_date": "20260624"},
        lineage={"source_id": "tushare"},
    )
    adapter = FakeFinancialAdapter()
    maintainer = AShareFinancialsMaintainer(data_dir=tmp_path, registry=registry, adapters={"tushare": adapter})

    result = maintainer.maintain(
        period="20251231",
        stock_snapshot_date="20260624",
        dataset_ids=("ashare.income_statement",),
        limit=1,
    )

    assert result["schema"] == "rdf.ashare_financials_maintenance_run.v1"
    assert result["status"] == "ready"
    assert result["dataset_ids"] == ["ashare.income_statement"]
    assert ("income", {"ts_code": "000001.SZ", "period": "20251231"}) in adapter.calls
    frame = MartStore(tmp_path, registry).read("ashare.income_statement", {"period": "20251231", "security_id": "000001.SZ"})
    assert frame.iloc[0]["n_income"] == 100.0

    adapter.calls.clear()
    second = maintainer.maintain(period="20251231", security_ids=("000001.SZ",), dataset_ids=("ashare.income_statement",))

    assert second["status"] == "ready"
    assert second["tasks"][0]["status"] == "skipped"
    assert adapter.calls == []


def test_ashare_announcement_text_maintainer_uses_announcement_index_and_skips_existing(tmp_path):
    class FakeCninfoTextAdapter:
        source_id = "cninfo"

        def __init__(self):
            self.calls = []

        def fetch(self, api_name, params, fields=None):
            self.calls.append((api_name, dict(params)))
            return SourceFetchResult(
                source_id="cninfo",
                api_name=api_name,
                params=dict(params),
                requested_at="2026-06-26T18:00:00+08:00",
                frame=pd.DataFrame(
                    [
                        {
                            "publish_date": params["publish_date"],
                            "announcement_id": params["announcement_id"],
                            "security_id": params["security_id"],
                            "security_name": params["security_name"],
                            "title": params["title"],
                            "source_url": params["source_url"],
                            "pdf_sha256": "abc",
                            "pdf_bytes": 12,
                            "text": "年度报告正文",
                            "text_length": 6,
                            "page_count": 1,
                            "parse_status": "ok",
                            "parse_message": "",
                        }
                    ]
                ),
                artifacts=(SourceArtifact(filename=f"{params['announcement_id']}.pdf", content=b"%PDF-1.4 fake"),),
            )

    registry = default_registry()
    MartStore(tmp_path, registry).publish(
        "ashare.announcements",
        pd.DataFrame(
            [
                {
                    "publish_date": "20260624",
                    "announcement_id": "1219999999",
                    "security_id": "000001.SZ",
                    "security_name": "平安银行",
                    "title": "2025年年度报告",
                    "source_url": "https://static.cninfo.com.cn/finalpage/2026-06-24/1219999999.PDF",
                }
            ]
        ),
        partition={"publish_date": "20260624"},
        lineage={"source_id": "cninfo"},
    )
    adapter = FakeCninfoTextAdapter()
    maintainer = AShareAnnouncementTextMaintainer(data_dir=tmp_path, registry=registry, adapters={"cninfo": adapter})

    result = maintainer.maintain(publish_date="20260624", limit=1)

    assert result["schema"] == "rdf.ashare_announcement_text_maintenance_run.v1"
    assert result["status"] == "ready"
    assert ("announcement_pdf_text", {
        "publish_date": "20260624",
        "announcement_id": "1219999999",
        "security_id": "000001.SZ",
        "security_name": "平安银行",
        "title": "2025年年度报告",
        "source_url": "https://static.cninfo.com.cn/finalpage/2026-06-24/1219999999.PDF",
    }) in adapter.calls
    frame = MartStore(tmp_path, registry).read("ashare.announcement_text", {"publish_date": "20260624", "announcement_id": "1219999999"})
    assert frame.iloc[0]["text"] == "年度报告正文"

    adapter.calls.clear()
    second = maintainer.maintain(publish_date="20260624", announcement_ids=("1219999999",))

    assert second["status"] == "ready"
    assert second["tasks"][0]["status"] == "skipped"
    assert adapter.calls == []


def test_rdf_cli_runs_ashare_core_maintenance_with_fake_maintainer(monkeypatch, capsys, tmp_path):
    import importlib

    cli_module = importlib.import_module("research_data_foundation.cli.main")

    class FakeMaintainer:
        def __init__(self, *, data_dir, registry):
            self.data_dir = data_dir
            self.registry = registry

        def maintain(self, *, as_of, lookback_trading_days, refresh, continue_on_error, build_features, windows):
            assert as_of == "20260626"
            assert lookback_trading_days == 60
            assert refresh is True
            assert continue_on_error is True
            assert build_features is False
            assert windows == (5, 20, 60)
            return {"schema": "rdf.ashare_core_maintenance_run.v1", "status": "ready"}

        def status(self, *, as_of, lookback_trading_days, windows):
            assert as_of == "20260626"
            assert lookback_trading_days == 3
            assert windows == (2,)
            return {"schema": "rdf.ashare_core_maintenance_status.v1", "status": "ready"}

    monkeypatch.setattr(cli_module, "AShareCoreMaintainer", FakeMaintainer)

    run_exit = cli_module.main(
        [
            "--data-dir",
            str(tmp_path),
            "maintain",
            "ashare-core",
            "--as-of",
            "20260626",
            "--refresh",
            "--continue-on-error",
            "--skip-features",
        ]
    )
    run_payload = json.loads(capsys.readouterr().out)

    assert run_exit == 0
    assert run_payload["schema"] == "rdf.ashare_core_maintenance_run.v1"

    status_exit = cli_module.main(
        [
            "--data-dir",
            str(tmp_path),
            "maintain",
            "status",
            "ashare-core",
            "--as-of",
            "20260626",
            "--lookback-trading-days",
            "3",
            "--windows",
            "2",
        ]
    )
    status_payload = json.loads(capsys.readouterr().out)

    assert status_exit == 0
    assert status_payload["schema"] == "rdf.ashare_core_maintenance_status.v1"


def test_rdf_cli_runs_ashare_main_business_maintenance_with_fake_maintainer(monkeypatch, capsys, tmp_path):
    import importlib

    cli_module = importlib.import_module("research_data_foundation.cli.main")

    class FakeMaintainer:
        def __init__(self, *, data_dir, registry):
            self.data_dir = data_dir
            self.registry = registry

        def maintain(
            self,
            *,
            period,
            security_ids,
            stock_snapshot_date,
            segment_types,
            limit,
            refresh,
            continue_on_error,
        ):
            assert period == "20251231"
            assert security_ids == ("000001.SZ",)
            assert stock_snapshot_date is None
            assert segment_types == ("P", "D")
            assert limit == 0
            assert refresh is True
            assert continue_on_error is True
            return {"schema": "rdf.ashare_main_business_maintenance_run.v1", "status": "ready"}

    monkeypatch.setattr(cli_module, "AShareMainBusinessMaintainer", FakeMaintainer)

    exit_code = cli_module.main(
        [
            "--data-dir",
            str(tmp_path),
            "maintain",
            "ashare-main-business",
            "--period",
            "20251231",
            "--security-id",
            "000001.SZ",
            "--refresh",
            "--continue-on-error",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["schema"] == "rdf.ashare_main_business_maintenance_run.v1"


def test_rdf_cli_runs_ashare_concept_members_maintenance_with_fake_maintainer(monkeypatch, capsys, tmp_path):
    import importlib

    cli_module = importlib.import_module("research_data_foundation.cli.main")

    class FakeMaintainer:
        def __init__(self, *, data_dir, registry):
            self.data_dir = data_dir
            self.registry = registry

        def maintain(
            self,
            *,
            snapshot_date,
            concept_ids,
            dc_index_date,
            limit,
            refresh,
            continue_on_error,
        ):
            assert snapshot_date == "20260624"
            assert concept_ids == ("BK1234",)
            assert dc_index_date == "20260623"
            assert limit == 3
            assert refresh is True
            assert continue_on_error is True
            return {"schema": "rdf.ashare_concept_members_maintenance_run.v1", "status": "ready"}

    monkeypatch.setattr(cli_module, "AShareConceptMembersMaintainer", FakeMaintainer)

    exit_code = cli_module.main(
        [
            "--data-dir",
            str(tmp_path),
            "maintain",
            "ashare-concept-members",
            "--snapshot-date",
            "20260624",
            "--concept-id",
            "BK1234",
            "--dc-index-date",
            "20260623",
            "--limit",
            "3",
            "--refresh",
            "--continue-on-error",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["schema"] == "rdf.ashare_concept_members_maintenance_run.v1"


def test_rdf_cli_runs_ashare_ths_concepts_maintenance_with_fake_maintainer(monkeypatch, capsys, tmp_path):
    import importlib

    cli_module = importlib.import_module("research_data_foundation.cli.main")

    class FakeMaintainer:
        def __init__(self, *, data_dir, registry):
            self.data_dir = data_dir
            self.registry = registry

        def maintain(
            self,
            *,
            snapshot_date,
            concept_ids,
            limit,
            refresh,
            continue_on_error,
        ):
            assert snapshot_date == "20260624"
            assert concept_ids == ("885001.TI",)
            assert limit == 2
            assert refresh is True
            assert continue_on_error is True
            return {"schema": "rdf.ashare_ths_concepts_maintenance_run.v1", "status": "ready"}

    monkeypatch.setattr(cli_module, "AShareThsConceptsMaintainer", FakeMaintainer)

    exit_code = cli_module.main(
        [
            "--data-dir",
            str(tmp_path),
            "maintain",
            "ashare-ths-concepts",
            "--snapshot-date",
            "20260624",
            "--concept-id",
            "885001.TI",
            "--limit",
            "2",
            "--refresh",
            "--continue-on-error",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["schema"] == "rdf.ashare_ths_concepts_maintenance_run.v1"


def test_rdf_cli_runs_ashare_financials_maintenance_with_fake_maintainer(monkeypatch, capsys, tmp_path):
    import importlib

    cli_module = importlib.import_module("research_data_foundation.cli.main")

    class FakeMaintainer:
        def __init__(self, *, data_dir, registry):
            self.data_dir = data_dir
            self.registry = registry

        def maintain(
            self,
            *,
            period,
            as_of,
            security_ids,
            stock_snapshot_date,
            dataset_ids,
            limit,
            refresh,
            continue_on_error,
        ):
            assert period == "20251231"
            assert as_of is None
            assert security_ids == ("000001.SZ",)
            assert stock_snapshot_date is None
            assert dataset_ids == ("ashare.income_statement",)
            assert limit == 0
            assert refresh is True
            assert continue_on_error is True
            return {"schema": "rdf.ashare_financials_maintenance_run.v1", "status": "ready"}

    monkeypatch.setattr(cli_module, "AShareFinancialsMaintainer", FakeMaintainer)

    exit_code = cli_module.main(
        [
            "--data-dir",
            str(tmp_path),
            "maintain",
            "ashare-financials",
            "--period",
            "20251231",
            "--security-id",
            "000001.SZ",
            "--dataset-id",
            "ashare.income_statement",
            "--refresh",
            "--continue-on-error",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["schema"] == "rdf.ashare_financials_maintenance_run.v1"


def test_rdf_cli_runs_ashare_financials_maintenance_with_as_of(monkeypatch, capsys, tmp_path):
    import importlib

    cli_module = importlib.import_module("research_data_foundation.cli.main")

    class FakeMaintainer:
        def __init__(self, *, data_dir, registry):
            self.data_dir = data_dir
            self.registry = registry

        def maintain(
            self,
            *,
            period,
            as_of,
            security_ids,
            stock_snapshot_date,
            dataset_ids,
            limit,
            refresh,
            continue_on_error,
        ):
            assert period is None
            assert as_of == "20260624"
            assert security_ids == ()
            assert stock_snapshot_date == "20260624"
            assert dataset_ids == ("ashare.balance_sheet",)
            assert limit == 3
            assert refresh is True
            assert continue_on_error is False
            return {"schema": "rdf.ashare_financials_maintenance_run.v1", "status": "ready"}

    monkeypatch.setattr(cli_module, "AShareFinancialsMaintainer", FakeMaintainer)

    exit_code = cli_module.main(
        [
            "--data-dir",
            str(tmp_path),
            "maintain",
            "ashare-financials",
            "--as-of",
            "20260624",
            "--stock-snapshot-date",
            "20260624",
            "--dataset-id",
            "ashare.balance_sheet",
            "--limit",
            "3",
            "--refresh",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["schema"] == "rdf.ashare_financials_maintenance_run.v1"


def test_rdf_cli_runs_ashare_announcement_text_maintenance_with_fake_maintainer(monkeypatch, capsys, tmp_path):
    import importlib

    cli_module = importlib.import_module("research_data_foundation.cli.main")

    class FakeMaintainer:
        def __init__(self, *, data_dir, registry):
            self.data_dir = data_dir
            self.registry = registry

        def maintain(self, *, publish_date, announcement_ids, limit, refresh, continue_on_error):
            assert publish_date == "20260624"
            assert announcement_ids == ("1219999999",)
            assert limit == 1
            assert refresh is True
            assert continue_on_error is True
            return {"schema": "rdf.ashare_announcement_text_maintenance_run.v1", "status": "ready"}

    monkeypatch.setattr(cli_module, "AShareAnnouncementTextMaintainer", FakeMaintainer)

    exit_code = cli_module.main(
        [
            "--data-dir",
            str(tmp_path),
            "maintain",
            "ashare-announcement-text",
            "--publish-date",
            "20260624",
            "--announcement-id",
            "1219999999",
            "--limit",
            "1",
            "--refresh",
            "--continue-on-error",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["schema"] == "rdf.ashare_announcement_text_maintenance_run.v1"


def test_ingestion_runner_executes_tushare_daily_recipe(tmp_path):
    class FakeAdapter:
        source_id = "tushare"

        def __init__(self):
            self.calls = []

        def fetch(self, api_name, params, fields=None):
            self.calls.append({"api_name": api_name, "params": params, "fields": tuple(fields or ())})
            return SourceFetchResult(
                source_id="tushare",
                api_name=api_name,
                params=params,
                requested_at="2026-06-26T18:00:00+08:00",
                frame=pd.DataFrame(
                    [
                        {
                            "ts_code": "000001.SZ",
                            "trade_date": "20260626",
                            "open": 10.0,
                            "high": 10.5,
                            "low": 9.8,
                            "close": 10.2,
                            "pct_chg": 2.0,
                            "vol": 1000.0,
                            "amount": 10000.0,
                        }
                    ]
                ),
            )

    adapter = FakeAdapter()
    runner = IngestionRunner(data_dir=tmp_path, registry=default_registry(), adapters={"tushare": adapter})

    result = runner.run_recipe(
        "tushare.daily.to_ashare_daily",
        partition={"trade_date": "20260626"},
    )

    assert adapter.calls == [
        {
            "api_name": "daily",
            "params": {"trade_date": "20260626"},
            "fields": ("ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "change", "pct_chg", "vol", "amount"),
        }
    ]
    assert result.dataset_id == "ashare.daily"
    assert result.rows == 1
    assert result.raw_path is not None
    assert result.staging_path is not None

    frame = MartStore(tmp_path, default_registry()).read("ashare.daily", {"trade_date": "20260626"})
    assert frame.iloc[0]["security_id"] == "000001.SZ"
    assert frame.iloc[0]["volume"] == 1000.0
    assert "ts_code" not in frame.columns
    assert "vol" not in frame.columns

    meta = MartStore(tmp_path, default_registry()).read_meta("ashare.daily", {"trade_date": "20260626"})
    assert meta["lineage"]["recipe_id"] == "tushare.daily.to_ashare_daily"
    assert meta["lineage"]["source_api"] == "daily"
    assert meta["lineage"]["staging_path"] == result.staging_path


def test_ingestion_runner_deduplicates_primary_key_rows_by_update_flag(tmp_path):
    class FakeAdapter:
        source_id = "tushare"

        def fetch(self, api_name, params, fields=None):
            return SourceFetchResult(
                source_id="tushare",
                api_name=api_name,
                params=params,
                requested_at="2026-06-26T18:00:00+08:00",
                frame=pd.DataFrame(
                    [
                        {
                            "ts_code": "000001.SZ",
                            "end_date": "20260331",
                            "ann_date": "20260425",
                            "total_assets": 100.0,
                            "total_liab": 80.0,
                            "update_flag": "0",
                        },
                        {
                            "ts_code": "000001.SZ",
                            "end_date": "20260331",
                            "ann_date": "20260425",
                            "total_assets": 120.0,
                            "total_liab": 90.0,
                            "update_flag": "1",
                        },
                    ]
                ),
            )

    registry = default_registry()
    runner = IngestionRunner(data_dir=tmp_path, registry=registry, adapters={"tushare": FakeAdapter()})

    result = runner.run_recipe(
        "tushare.balancesheet.to_ashare_balance_sheet",
        partition={"period": "20260331", "security_id": "000001.SZ"},
    )

    assert result.rows == 1
    frame = MartStore(tmp_path, registry).read("ashare.balance_sheet", {"period": "20260331", "security_id": "000001.SZ"})
    assert len(frame) == 1
    assert frame.iloc[0]["total_assets"] == 120.0
    assert frame.iloc[0]["update_flag"] == "1"
    meta = MartStore(tmp_path, registry).read_meta("ashare.balance_sheet", {"period": "20260331", "security_id": "000001.SZ"})
    assert meta["quality"]["duplicate_primary_key_rows"] == 0


def test_ingestion_runner_filters_rows_to_requested_partition(tmp_path):
    class FakeAdapter:
        source_id = "tushare"

        def fetch(self, api_name, params, fields=None):
            return SourceFetchResult(
                source_id="tushare",
                api_name=api_name,
                params=params,
                requested_at="2026-06-26T18:00:00+08:00",
                frame=pd.DataFrame(
                    [
                        {
                            "ts_code": "000001.SZ",
                            "end_date": "20251231",
                            "ann_date": "20260425",
                            "div_proc": "实施",
                            "cash_div": 1.0,
                        },
                        {
                            "ts_code": "000001.SZ",
                            "end_date": "20260331",
                            "ann_date": "20260426",
                            "div_proc": "预案",
                            "cash_div": 2.0,
                        },
                    ]
                ),
            )

    registry = default_registry()
    runner = IngestionRunner(data_dir=tmp_path, registry=registry, adapters={"tushare": FakeAdapter()})

    result = runner.run_recipe(
        "tushare.dividend.to_ashare_dividend",
        partition={"period": "20260331", "security_id": "000001.SZ"},
    )

    assert result.rows == 1
    frame = MartStore(tmp_path, registry).read("ashare.dividend", {"period": "20260331", "security_id": "000001.SZ"})
    assert len(frame) == 1
    assert frame.iloc[0]["period"] == "20260331"
    assert frame.iloc[0]["cash_div"] == 2.0
    meta = MartStore(tmp_path, registry).read_meta("ashare.dividend", {"period": "20260331", "security_id": "000001.SZ"})
    assert meta["quality"]["partition_value_mismatches"] == {}


def test_ingestion_runner_executes_fanout_tushare_index_daily_recipe(tmp_path):
    class FakeAdapter:
        source_id = "tushare"

        def __init__(self):
            self.calls = []

        def fetch(self, api_name, params, fields=None):
            self.calls.append({"api_name": api_name, "params": params, "fields": tuple(fields or ())})
            return SourceFetchResult(
                source_id="tushare",
                api_name=api_name,
                params=params,
                requested_at="2026-06-26T18:00:00+08:00",
                frame=pd.DataFrame(
                    [
                        {
                            "ts_code": params["ts_code"],
                            "trade_date": params["trade_date"],
                            "close": 3000.0,
                            "pct_chg": 1.0,
                            "vol": 100.0,
                            "amount": 200.0,
                        }
                    ]
                ),
            )

    adapter = FakeAdapter()
    runner = IngestionRunner(data_dir=tmp_path, registry=default_registry(), adapters={"tushare": adapter})

    result = runner.run_recipe("tushare.index_daily.to_ashare_index_daily", partition={"trade_date": "20260625"})

    assert result.rows == 7
    assert {call["params"]["ts_code"] for call in adapter.calls} == {
        "000001.SH",
        "399001.SZ",
        "399006.SZ",
        "000300.SH",
        "000905.SH",
        "000852.SH",
        "000688.SH",
    }
    frame = MartStore(tmp_path, default_registry()).read("ashare.index_daily", {"trade_date": "20260625"})
    assert len(frame) == 7
    assert set(frame["index_id"]) == {call["params"]["ts_code"] for call in adapter.calls}
    meta = MartStore(tmp_path, default_registry()).read_meta("ashare.index_daily", {"trade_date": "20260625"})
    assert meta["lineage"]["fanout_params"]["ts_code"] == [
        "000001.SH",
        "399001.SZ",
        "399006.SZ",
        "000300.SH",
        "000905.SH",
        "000852.SH",
        "000688.SH",
    ]


def test_ingestion_runner_executes_fanout_hsgt_top10_recipe(tmp_path):
    class FakeAdapter:
        source_id = "tushare"

        def __init__(self):
            self.calls = []

        def fetch(self, api_name, params, fields=None):
            self.calls.append({"api_name": api_name, "params": dict(params), "fields": tuple(fields or ())})
            market_type = int(params["market_type"])
            security_id = "600183.SH" if market_type == 1 else "000725.SZ"
            name = "生益科技" if market_type == 1 else "京东方A"
            return SourceFetchResult(
                source_id="tushare",
                api_name=api_name,
                params=params,
                requested_at="2026-06-26T18:00:00+08:00",
                frame=pd.DataFrame(
                    [
                        {
                            "trade_date": params["trade_date"],
                            "ts_code": security_id,
                            "name": name,
                            "close": 10.0,
                            "change": 1.0,
                            "rank": 1,
                            "market_type": market_type,
                            "amount": 1000000.0,
                            "net_amount": 1000.0,
                            "buy": 600000.0,
                            "sell": 400000.0,
                        }
                    ]
                ),
            )

    adapter = FakeAdapter()
    registry = default_registry()
    runner = IngestionRunner(data_dir=tmp_path, registry=registry, adapters={"tushare": adapter})

    result = runner.run_recipe("tushare.hsgt_top10.to_ashare_hsgt_top10", partition={"trade_date": "20260624"})

    assert result.dataset_id == "ashare.hsgt_top10"
    assert result.rows == 2
    assert [call["params"]["market_type"] for call in adapter.calls] == ["1", "3"]
    frame = MartStore(tmp_path, registry).read("ashare.hsgt_top10", {"trade_date": "20260624"})
    assert set(frame["security_id"]) == {"600183.SH", "000725.SZ"}
    assert set(frame["market_type"]) == {1, 3}
    assert "ts_code" not in frame.columns
    meta = MartStore(tmp_path, registry).read_meta("ashare.hsgt_top10", {"trade_date": "20260624"})
    assert meta["lineage"]["fanout_params"]["market_type"] == ["1", "3"]


def test_ingestion_runner_executes_northbound_eligible_recipe(tmp_path):
    class FakeAdapter:
        source_id = "tushare"

        def __init__(self):
            self.calls = []

        def fetch(self, api_name, params, fields=None):
            self.calls.append({"api_name": api_name, "params": dict(params), "fields": tuple(fields or ())})
            connect_type = params["type"]
            security_id = "600021.SH" if connect_type == "HK_SH" else "000034.SZ"
            name = "上海电力" if connect_type == "HK_SH" else "神州数码"
            connect_type_name = "沪股通(港>沪)" if connect_type == "HK_SH" else "深股通(港>深)"
            return SourceFetchResult(
                source_id="tushare",
                api_name=api_name,
                params=params,
                requested_at="2026-06-26T18:00:00+08:00",
                frame=pd.DataFrame(
                    [
                        {
                            "ts_code": security_id,
                            "trade_date": params["trade_date"],
                            "type": connect_type,
                            "name": name,
                            "type_name": connect_type_name,
                        }
                    ]
                ),
            )

    adapter = FakeAdapter()
    registry = default_registry()
    runner = IngestionRunner(data_dir=tmp_path, registry=registry, adapters={"tushare": adapter})

    result = runner.run_recipe("tushare.stock_hsgt.to_ashare_northbound_eligible", partition={"trade_date": "20260624"})

    assert result.dataset_id == "ashare.northbound_eligible"
    assert result.rows == 2
    assert [call["params"]["type"] for call in adapter.calls] == ["HK_SH", "HK_SZ"]
    frame = MartStore(tmp_path, registry).read("ashare.northbound_eligible", {"trade_date": "20260624"})
    assert set(frame["security_id"]) == {"600021.SH", "000034.SZ"}
    assert set(frame["connect_type"]) == {"HK_SH", "HK_SZ"}
    assert set(frame["connect_type_name"]) == {"沪股通(港>沪)", "深股通(港>深)"}
    assert "ts_code" not in frame.columns
    assert "type" not in frame.columns
    meta = MartStore(tmp_path, registry).read_meta("ashare.northbound_eligible", {"trade_date": "20260624"})
    assert meta["lineage"]["fanout_params"]["type"] == ["HK_SH", "HK_SZ"]


def test_ingestion_runner_executes_chips_on_demand_pipeline(tmp_path):
    class FakeAdapter:
        source_id = "tushare"

        def __init__(self):
            self.calls = []

        def fetch(self, api_name, params, fields=None):
            self.calls.append({"api_name": api_name, "params": dict(params), "fields": tuple(fields or ())})
            if api_name == "cyq_perf":
                frame = pd.DataFrame(
                    [
                        {
                            "ts_code": params["ts_code"],
                            "trade_date": params["trade_date"],
                            "his_low": "0.2",
                            "his_high": "20.8",
                            "cost_5pct": "10.2",
                            "cost_15pct": "10.4",
                            "cost_50pct": "10.8",
                            "cost_85pct": "11.2",
                            "cost_95pct": "12.2",
                            "weight_avg": "10.97",
                            "winner_rate": "22.06",
                        }
                    ]
                )
            elif api_name == "cyq_chips":
                frame = pd.DataFrame(
                    [
                        {"ts_code": params["ts_code"], "trade_date": params["trade_date"], "price": "10.2", "percent": "0.13"},
                        {"ts_code": params["ts_code"], "trade_date": params["trade_date"], "price": "10.4", "percent": "0.18"},
                    ]
                )
            else:
                raise AssertionError(api_name)
            return SourceFetchResult(
                source_id="tushare",
                api_name=api_name,
                params=params,
                requested_at="2026-06-26T18:00:00+08:00",
                frame=frame,
            )

    adapter = FakeAdapter()
    registry = default_registry()
    runner = IngestionRunner(data_dir=tmp_path, registry=registry, adapters={"tushare": adapter})

    result = runner.run_pipeline("ashare_chips_on_demand", partition={"trade_date": "20260624", "security_id": "000001.SZ"})

    assert result.rows == 3
    assert [call["api_name"] for call in adapter.calls] == ["cyq_perf", "cyq_chips"]
    assert {call["params"]["ts_code"] for call in adapter.calls} == {"000001.SZ"}
    mart = MartStore(tmp_path, registry)
    perf = mart.read("ashare.chip_distribution_perf", {"trade_date": "20260624", "security_id": "000001.SZ"})
    detail = mart.read("ashare.chip_distribution_detail", {"trade_date": "20260624", "security_id": "000001.SZ"})
    assert perf.iloc[0]["security_id"] == "000001.SZ"
    assert perf.iloc[0]["winner_rate"] == 22.06
    assert detail.iloc[0]["price"] == 10.2
    assert detail["percent"].sum() == pytest.approx(0.31)
    assert "ts_code" not in perf.columns
    assert "ts_code" not in detail.columns


def test_ingestion_runner_executes_ownership_and_market_event_pipelines(tmp_path):
    class FakeAdapter:
        source_id = "tushare"

        def __init__(self):
            self.calls = []

        def fetch(self, api_name, params, fields=None):
            self.calls.append({"api_name": api_name, "params": dict(params), "fields": tuple(fields or ())})
            if api_name == "stk_holdernumber":
                frame = pd.DataFrame(
                    [
                        {"ts_code": "000001.SZ", "ann_date": "20260425", "end_date": params["enddate"], "holder_num": "457610"},
                        {"ts_code": "600519.SH", "ann_date": "20260425", "end_date": params["enddate"], "holder_num": "155000"},
                    ]
                )
            elif api_name == "top10_holders":
                frame = pd.DataFrame(
                    [
                        {
                            "ts_code": "000001.SZ",
                            "ann_date": "20260425",
                            "end_date": params["period"],
                            "holder_name": "holder-a",
                            "hold_amount": "1000000",
                            "hold_ratio": "5.5",
                            "hold_float_ratio": "4.5",
                            "hold_change": "1000",
                            "holder_type": "一般企业",
                        }
                    ]
                )
            elif api_name == "top10_floatholders":
                frame = pd.DataFrame(
                    [
                        {
                            "ts_code": "600519.SH",
                            "ann_date": "20260425",
                            "end_date": params["period"],
                            "holder_name": "holder-b",
                            "hold_amount": "2000000",
                            "hold_ratio": "6.5",
                            "hold_float_ratio": "6.5",
                            "hold_change": "0",
                            "holder_type": "基金",
                        }
                    ]
                )
            elif api_name == "pledge_stat":
                frame = pd.DataFrame(
                    [
                        {
                            "ts_code": "000001.SZ",
                            "end_date": params["end_date"],
                            "pledge_count": "9",
                            "unrest_pledge": "2460.5",
                            "rest_pledge": "0",
                            "total_share": "1940591.82",
                            "pledge_ratio": "0.13",
                        }
                    ]
                )
            elif api_name == "repurchase":
                frame = pd.DataFrame(
                    [
                        {
                            "ts_code": "603885.SH",
                            "ann_date": params["ann_date"],
                            "end_date": "20260623",
                            "proc": "完成",
                            "exp_date": None,
                            "vol": "43869704",
                            "amount": "499917060.74",
                            "high_limit": "12.36",
                            "low_limit": "10.47",
                        }
                    ]
                )
            elif api_name == "stk_holdertrade":
                frame = pd.DataFrame(
                    [
                        {
                            "ts_code": "688687.SH",
                            "ann_date": params["ann_date"],
                            "holder_name": "北京松安投资管理有限公司",
                            "holder_type": "C",
                            "in_de": "DE",
                            "change_vol": "1700000",
                            "change_ratio": "0.9945",
                            "after_share": "36700000",
                            "after_ratio": "21.469",
                            "avg_price": None,
                            "total_share": "36700000",
                        }
                    ]
                )
            elif api_name == "forecast":
                frame = pd.DataFrame(
                    [
                        {
                            "ts_code": "000001.SZ",
                            "ann_date": params["ann_date"],
                            "end_date": "20260630",
                            "type": "预增",
                            "p_change_min": "45.5",
                            "p_change_max": "65.0",
                            "net_profit_min": "120000000",
                            "net_profit_max": "135000000",
                            "last_parent_net": "82000000",
                            "first_ann_date": params["ann_date"],
                            "summary": "预计净利润增长",
                            "change_reason": "主营业务收入增长",
                        }
                    ]
                )
            elif api_name == "block_trade":
                frame = pd.DataFrame(
                    [
                        {
                            "ts_code": "300337.SZ",
                            "trade_date": params["trade_date"],
                            "price": "10.77",
                            "vol": "48",
                            "amount": "516.96",
                            "buyer": "浙商证券股份有限公司上海长乐路证券营业部",
                            "seller": "国联民生证券股份有限公司无锡中山路证券营业部",
                        }
                    ]
                )
            else:
                raise AssertionError(api_name)
            return SourceFetchResult(
                source_id="tushare",
                api_name=api_name,
                params=params,
                requested_at="2026-06-26T18:00:00+08:00",
                frame=frame,
            )

    adapter = FakeAdapter()
    registry = default_registry()
    runner = IngestionRunner(data_dir=tmp_path, registry=registry, adapters={"tushare": adapter})

    shareholder = runner.run_pipeline("ashare_ownership_periodic", partition={"period": "20260331"})
    pledge = runner.run_pipeline("ashare_share_pledge_weekly", partition={"end_date": "20260618"})
    repurchase = runner.run_pipeline("ashare_corporate_action_events_daily", partition={"ann_date": "20260624"})
    forecast = runner.run_pipeline("ashare_financial_event_daily", partition={"ann_date": "20260624"})
    block = runner.run_pipeline("ashare_block_trades_daily", partition={"trade_date": "20260624"})

    assert shareholder.rows == 4
    assert pledge.rows == 1
    assert repurchase.rows == 2
    assert forecast.rows == 1
    assert block.rows == 1
    mart = MartStore(tmp_path, registry)
    holders = mart.read("ashare.shareholder_count", {"period": "20260331"})
    top10_holders = mart.read("ashare.top10_holders", {"period": "20260331"})
    top10_float_holders = mart.read("ashare.top10_float_holders", {"period": "20260331"})
    pledge_stats = mart.read("ashare.share_pledge_stats", {"end_date": "20260618"})
    repurchases = mart.read("ashare.repurchase_events", {"ann_date": "20260624"})
    shareholder_trades = mart.read("ashare.shareholder_trades", {"ann_date": "20260624"})
    forecast_events = mart.read("ashare.earnings_forecast_events", {"ann_date": "20260624"})
    trades = mart.read("ashare.block_trades", {"trade_date": "20260624"})
    assert holders.iloc[0]["period"] == "20260331"
    assert holders.iloc[0]["holder_num"] == 457610
    assert top10_holders.iloc[0]["period"] == "20260331"
    assert top10_holders.iloc[0]["hold_amount"] == 1000000
    assert top10_float_holders.iloc[0]["hold_float_ratio"] == 6.5
    assert pledge_stats.iloc[0]["pledge_ratio"] == 0.13
    assert repurchases.iloc[0]["process_status"] == "完成"
    assert repurchases.iloc[0]["volume"] == 43869704
    assert shareholder_trades.iloc[0]["in_de"] == "DE"
    assert shareholder_trades.iloc[0]["change_vol"] == 1700000
    assert forecast_events.iloc[0]["period"] == "20260630"
    assert forecast_events.iloc[0]["forecast_type"] == "预增"
    assert forecast_events.iloc[0]["net_profit_min"] == 120000000
    assert trades.iloc[0]["security_id"] == "300337.SZ"
    assert trades.iloc[0]["volume"] == 48
    assert "ts_code" not in holders.columns
    assert "ts_code" not in top10_holders.columns
    assert "ts_code" not in pledge_stats.columns
    assert "proc" not in repurchases.columns
    assert "ts_code" not in shareholder_trades.columns
    assert "type" not in forecast_events.columns
    assert "ts_code" not in forecast_events.columns
    assert "vol" not in trades.columns


def test_ingestion_runner_executes_limit_list_ths_recipe(tmp_path):
    class FakeAdapter:
        source_id = "tushare"

        def __init__(self):
            self.calls = []

        def fetch(self, api_name, params, fields=None):
            self.calls.append({"api_name": api_name, "params": dict(params), "fields": tuple(fields or ())})
            return SourceFetchResult(
                source_id="tushare",
                api_name=api_name,
                params=params,
                requested_at="2026-06-26T18:00:00+08:00",
                frame=pd.DataFrame(
                    [
                        {
                            "trade_date": params["trade_date"],
                            "ts_code": "002631.SZ",
                            "name": "德尔未来",
                            "price": 9.89,
                            "pct_chg": 10.01,
                            "open_num": 7.0,
                            "lu_desc": "石墨烯+家居产品+亏损收窄",
                            "limit_type": "涨停池",
                            "tag": "2天2板",
                            "first_lu_time": "2026-06-24 14:39:42",
                            "last_lu_time": "2026-06-24 14:56:33",
                            "limit_order": 40000.0,
                            "limit_amount": 395600.0,
                            "turnover_rate": 12.38,
                            "free_float": 7848431000.0,
                            "status": "换手板",
                            "market_type": "HS",
                        }
                    ]
                ),
            )

    adapter = FakeAdapter()
    registry = default_registry()
    runner = IngestionRunner(data_dir=tmp_path, registry=registry, adapters={"tushare": adapter})

    result = runner.run_recipe("tushare.limit_list_ths.to_ashare_limit_list_ths", partition={"trade_date": "20260624"})

    assert result.dataset_id == "ashare.limit_list_ths"
    assert result.rows == 1
    assert adapter.calls == [
        {
            "api_name": "limit_list_ths",
            "params": {"trade_date": "20260624"},
            "fields": (
                "trade_date",
                "ts_code",
                "name",
                "price",
                "pct_chg",
                "open_num",
                "lu_desc",
                "limit_type",
                "tag",
                "first_lu_time",
                "last_lu_time",
                "limit_order",
                "limit_amount",
                "turnover_rate",
                "free_float",
                "status",
                "market_type",
            ),
        }
    ]
    frame = MartStore(tmp_path, registry).read("ashare.limit_list_ths", {"trade_date": "20260624"})
    assert frame.iloc[0]["security_id"] == "002631.SZ"
    assert frame.iloc[0]["limit_reason"] == "石墨烯+家居产品+亏损收窄"
    assert frame.iloc[0]["board_tag"] == "2天2板"
    assert frame.iloc[0]["first_limit_time"] == "2026-06-24 14:39:42"
    assert "ts_code" not in frame.columns
    assert "lu_desc" not in frame.columns
    assert "tag" not in frame.columns
    meta = MartStore(tmp_path, registry).read_meta("ashare.limit_list_ths", {"trade_date": "20260624"})
    assert meta["lineage"]["recipe_id"] == "tushare.limit_list_ths.to_ashare_limit_list_ths"
    assert meta["quality"]["status"] == "ok"


def test_ingestion_runner_executes_paginated_margin_detail_recipe(tmp_path):
    class FakeAdapter:
        source_id = "tushare"

        def __init__(self):
            self.calls = []

        def fetch(self, api_name, params, fields=None):
            self.calls.append({"api_name": api_name, "params": dict(params), "fields": tuple(fields or ())})
            offset = int(params["offset"])
            if offset == 0:
                rows = [
                    {
                        "trade_date": params["trade_date"],
                        "ts_code": f"60{i:04d}.SH",
                        "name": f"示例融资{i}",
                        "rzye": 1000000.0 + i,
                        "rqye": 1000.0,
                        "rzmre": 2000.0,
                        "rqyl": 10.0,
                        "rzche": 3000.0,
                        "rqchl": 1.0,
                        "rqmcl": 2.0,
                        "rzrqye": 1001000.0 + i,
                    }
                    for i in range(5000)
                ]
            elif offset == 5000:
                rows = [
                    {
                        "trade_date": params["trade_date"],
                        "ts_code": "000001.SZ",
                        "name": "平安银行",
                        "rzye": 123.0,
                        "rqye": 4.0,
                        "rzmre": 5.0,
                        "rqyl": 6.0,
                        "rzche": 7.0,
                        "rqchl": 8.0,
                        "rqmcl": 9.0,
                        "rzrqye": 127.0,
                    }
                ]
            else:
                rows = []
            return SourceFetchResult(
                source_id="tushare",
                api_name=api_name,
                params=params,
                requested_at="2026-06-26T18:00:00+08:00",
                frame=pd.DataFrame(rows),
            )

    adapter = FakeAdapter()
    registry = default_registry()
    runner = IngestionRunner(data_dir=tmp_path, registry=registry, adapters={"tushare": adapter})

    result = runner.run_recipe("tushare.margin_detail.to_ashare_margin_detail", partition={"trade_date": "20260624"})

    assert result.dataset_id == "ashare.margin_detail"
    assert result.rows == 5001
    assert [call["params"]["offset"] for call in adapter.calls] == [0, 5000]
    frame = MartStore(tmp_path, registry).read("ashare.margin_detail", {"trade_date": "20260624"})
    assert frame.iloc[-1]["security_id"] == "000001.SZ"
    assert frame.iloc[-1]["rzrqye"] == 127.0
    assert "ts_code" not in frame.columns
    meta = MartStore(tmp_path, registry).read_meta("ashare.margin_detail", {"trade_date": "20260624"})
    assert meta["lineage"]["pagination"]["limit"] == 5000
    assert meta["lineage"]["pagination"]["max_pages"] == 3


def test_ingestion_runner_executes_paginated_price_limits_recipe(tmp_path):
    class FakeAdapter:
        source_id = "tushare"

        def __init__(self):
            self.calls = []

        def fetch(self, api_name, params, fields=None):
            self.calls.append({"api_name": api_name, "params": dict(params), "fields": tuple(fields or ())})
            offset = int(params["offset"])
            if offset == 0:
                rows = [
                    {
                        "ts_code": f"00{i:04d}.SZ",
                        "trade_date": params["trade_date"],
                        "up_limit": 10.0,
                        "down_limit": 8.0,
                    }
                    for i in range(5000)
                ]
            elif offset == 5000:
                rows = [
                    {
                        "ts_code": "000001.SZ",
                        "trade_date": params["trade_date"],
                        "up_limit": 11.78,
                        "down_limit": 9.64,
                    }
                ]
            else:
                rows = []
            return SourceFetchResult(
                source_id="tushare",
                api_name=api_name,
                params=params,
                requested_at="2026-06-26T18:00:00+08:00",
                frame=pd.DataFrame(rows),
            )

    adapter = FakeAdapter()
    registry = default_registry()
    runner = IngestionRunner(data_dir=tmp_path, registry=registry, adapters={"tushare": adapter})

    result = runner.run_recipe("tushare.stk_limit.to_ashare_price_limits", partition={"trade_date": "20260624"})

    assert result.dataset_id == "ashare.price_limits"
    assert result.rows == 5000
    assert [call["params"]["offset"] for call in adapter.calls] == [0, 5000]
    frame = MartStore(tmp_path, registry).read("ashare.price_limits", {"trade_date": "20260624"})
    assert frame.iloc[-1]["security_id"] == "000001.SZ"
    assert frame.iloc[-1]["up_limit"] == 11.78
    assert "ts_code" not in frame.columns
    meta = MartStore(tmp_path, registry).read_meta("ashare.price_limits", {"trade_date": "20260624"})
    assert meta["lineage"]["pagination"]["limit"] == 5000
    assert meta["lineage"]["pagination"]["max_pages"] == 3
    assert meta["quality"]["duplicate_primary_key_rows"] == 0


def test_ingestion_runner_executes_sw_industry_classification_recipe(tmp_path):
    class FakeAdapter:
        source_id = "tushare"

        def __init__(self):
            self.calls = []

        def fetch(self, api_name, params, fields=None):
            self.calls.append({"api_name": api_name, "params": dict(params), "fields": tuple(fields or ())})
            return SourceFetchResult(
                source_id="tushare",
                api_name=api_name,
                params=params,
                requested_at="2026-06-26T18:00:00+08:00",
                frame=pd.DataFrame(
                    [
                        {
                            "index_code": "801080.SI",
                            "industry_name": "电子",
                            "level": "L1",
                            "industry_code": "270000",
                            "is_pub": "1",
                            "parent_code": "0",
                            "src": "SW2021",
                        },
                        {
                            "index_code": "801085.SI",
                            "industry_name": "消费电子",
                            "level": "L2",
                            "industry_code": "270100",
                            "is_pub": "1",
                            "parent_code": "270000",
                            "src": "SW2021",
                        },
                    ]
                ),
            )

    adapter = FakeAdapter()
    registry = default_registry()
    runner = IngestionRunner(data_dir=tmp_path, registry=registry, adapters={"tushare": adapter})

    result = runner.run_recipe(
        "tushare.index_classify.to_ashare_sw_industry_classification",
        partition={"snapshot_date": "20260625"},
    )

    assert result.dataset_id == "ashare.sw_industry_classification"
    assert result.rows == 2
    assert adapter.calls[0]["params"] == {"src": "SW2021"}
    frame = MartStore(tmp_path, registry).read("ashare.sw_industry_classification", {"snapshot_date": "20260625"})
    assert set(frame["index_id"]) == {"801080.SI", "801085.SI"}
    assert set(frame["source_system"]) == {"SW2021"}
    assert frame.iloc[1]["parent_code"] == "270000"
    assert "index_code" not in frame.columns
    assert "src" not in frame.columns


def test_ingestion_runner_executes_paginated_industry_members_recipe(tmp_path):
    class FakeAdapter:
        source_id = "tushare"

        def __init__(self):
            self.calls = []

        def fetch(self, api_name, params, fields=None):
            self.calls.append({"api_name": api_name, "params": dict(params), "fields": tuple(fields or ())})
            offset = int(params["offset"])
            rows = [
                {
                    "l1_code": "801080.SI",
                    "l1_name": "电子",
                    "l2_code": "801085.SI",
                    "l2_name": "消费电子",
                    "l3_code": "850854.SI",
                    "l3_name": "消费电子零部件及组装",
                    "ts_code": "300001.SZ",
                    "name": "示例科技",
                    "in_date": "20200101",
                    "out_date": None,
                    "is_new": "Y",
                }
            ]
            if offset:
                rows = []
            return SourceFetchResult(
                source_id="tushare",
                api_name=api_name,
                params=params,
                requested_at="2026-06-26T18:00:00+08:00",
                frame=pd.DataFrame(rows),
            )

    adapter = FakeAdapter()
    runner = IngestionRunner(data_dir=tmp_path, registry=default_registry(), adapters={"tushare": adapter})

    result = runner.run_recipe(
        "tushare.index_member_all.to_ashare_industry_members",
        partition={"snapshot_date": "20260625"},
    )

    assert result.dataset_id == "ashare.industry_members"
    assert result.rows == 1
    assert [call["params"]["offset"] for call in adapter.calls] == [0]
    frame = MartStore(tmp_path, default_registry()).read("ashare.industry_members", {"snapshot_date": "20260625"})
    assert frame.iloc[0]["security_id"] == "300001.SZ"
    assert frame.iloc[0]["security_name"] == "示例科技"
    meta = MartStore(tmp_path, default_registry()).read_meta("ashare.industry_members", {"snapshot_date": "20260625"})
    assert meta["lineage"]["pagination"]["limit"] == 3000


def test_ingestion_runner_executes_paginated_ci_industry_members_recipe(tmp_path):
    class FakeAdapter:
        source_id = "tushare"

        def __init__(self):
            self.calls = []

        def fetch(self, api_name, params, fields=None):
            self.calls.append({"api_name": api_name, "params": dict(params), "fields": tuple(fields or ())})
            offset = int(params["offset"])
            if offset == 0:
                rows = [
                    {
                        "l1_code": "CI005011.CI",
                        "l1_name": "电力设备及新能源",
                        "l2_code": "CI005809.CI",
                        "l2_name": "电源设备",
                        "l3_code": "CI005477.CI",
                        "l3_name": "储能",
                        "ts_code": f"30{i:04d}.SZ",
                        "name": f"示例新能源{i}",
                        "in_date": "20240102",
                        "out_date": None,
                        "is_new": "Y",
                    }
                    for i in range(5000)
                ]
            elif offset == 5000:
                rows = [
                    {
                        "l1_code": "CI005010.CI",
                        "l1_name": "机械",
                        "l2_code": "CI005805.CI",
                        "l2_name": "专用机械",
                        "l3_code": "CI005456.CI",
                        "l3_name": "其他专用机械",
                        "ts_code": "603325.SH",
                        "name": "博隆技术",
                        "in_date": "20240102",
                        "out_date": None,
                        "is_new": "Y",
                    }
                ]
            else:
                rows = []
            return SourceFetchResult(
                source_id="tushare",
                api_name=api_name,
                params=params,
                requested_at="2026-06-26T18:00:00+08:00",
                frame=pd.DataFrame(rows),
            )

    adapter = FakeAdapter()
    registry = default_registry()
    runner = IngestionRunner(data_dir=tmp_path, registry=registry, adapters={"tushare": adapter})

    result = runner.run_recipe(
        "tushare.ci_index_member.to_ashare_ci_industry_members",
        partition={"snapshot_date": "20260625"},
    )

    assert result.dataset_id == "ashare.ci_industry_members"
    assert result.rows == 5001
    assert [call["params"]["offset"] for call in adapter.calls] == [0, 5000]
    frame = MartStore(tmp_path, registry).read("ashare.ci_industry_members", {"snapshot_date": "20260625"})
    assert frame.iloc[-1]["security_id"] == "603325.SH"
    assert frame.iloc[-1]["security_name"] == "博隆技术"
    meta = MartStore(tmp_path, registry).read_meta("ashare.ci_industry_members", {"snapshot_date": "20260625"})
    assert meta["lineage"]["pagination"]["limit"] == 5000
    assert meta["lineage"]["pagination"]["max_pages"] == 10


def test_ingestion_runner_plans_recipe_without_fetching_or_writing(tmp_path):
    class FailIfFetched:
        def fetch(self, api_name, params, fields=None):
            raise AssertionError("dry-run plan must not call source adapters")

    runner = IngestionRunner(data_dir=tmp_path, registry=default_registry(), adapters={"sec_edgar": FailIfFetched()})

    plan = runner.plan_recipe("sec_edgar.submissions.to_global_sec_filings", partition={"cik": "0000320193"})
    payload = plan.to_dict()

    assert payload["schema"] == "rdf.ingestion_plan.v1"
    assert payload["will_fetch"] is False
    assert payload["will_write"] is False
    assert payload["would_write_layers"] == ["raw", "staging", "mart"]
    assert payload["source"]["id"] == "sec_edgar"
    assert payload["source"]["authority_tier"] == "S1"
    assert payload["dataset"]["id"] == "global.sec_filings"
    assert payload["dataset"]["temporal"]["temporal_mode"] == "filing"
    assert payload["dataset"]["usage"]["allowed_uses"] == [
        "evidence",
        "cross_market_context",
        "cross_market_validation",
    ]
    assert "candidate_generation" in payload["dataset"]["usage"]["forbidden_uses"]
    assert payload["params"] == {"cik": "0000320193"}
    assert not (tmp_path / "raw").exists()
    assert not (tmp_path / "mart").exists()


def test_ingestion_runner_plans_pipeline_steps_without_writing(tmp_path):
    runner = IngestionRunner(data_dir=tmp_path, registry=default_registry(), adapters={})

    plan = runner.plan_pipeline("global_reference_weekly", partition={"cik": "0000320193"}, continue_on_error=True)
    payload = plan.to_dict()

    assert payload["schema"] == "rdf.pipeline_ingestion_plan.v1"
    assert payload["pipeline_id"] == "global_reference_weekly"
    assert payload["continue_on_error"] is True
    assert payload["will_fetch"] is False
    assert payload["will_write"] is False
    assert payload["steps"][0]["recipe_id"] == "sec_edgar.submissions.to_global_sec_filings"
    assert payload["steps"][0]["plan"]["dataset"]["domain"] == "global_reference"
    assert not (tmp_path / "raw").exists()
    assert not (tmp_path / "mart").exists()


def test_ingestion_runner_rejects_adapter_source_mismatch(tmp_path):
    class WrongAdapter:
        source_id = "tushare"

        def fetch(self, api_name, params, fields=None):
            return SourceFetchResult(
                source_id="other_source",
                api_name=api_name,
                params=params,
                requested_at="2026-06-26T18:00:00+08:00",
                frame=pd.DataFrame(),
            )

    runner = IngestionRunner(data_dir=tmp_path, registry=default_registry(), adapters={"tushare": WrongAdapter()})

    with pytest.raises(IngestionError, match="expected 'tushare'"):
        runner.run_recipe("tushare.daily.to_ashare_daily", partition={"trade_date": "20260626"})


def test_recipe_param_resolution_requires_values():
    payload = resolve_params(
        {"trade_date": "${partition.trade_date}", "begin": "${params.begin}", "literal": "x-${partition.trade_date}"},
        partition={"trade_date": "20260626"},
        params={"begin": "2026-01-01"},
    )

    assert payload == {"trade_date": "20260626", "begin": "2026-01-01", "literal": "x-20260626"}

    with pytest.raises(IngestionError, match="missing template value"):
        resolve_params({"begin": "${params.begin}"}, partition={}, params={})


def test_tushare_source_adapter_wraps_client_dataframe():
    class FakeClient:
        def __init__(self):
            self.calls = []

        def query(self, api_name, **params):
            self.calls.append({"api_name": api_name, "params": params})
            return pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20260626"}])

    client = FakeClient()
    response = TushareSourceAdapter(client=client).fetch("daily", {"trade_date": "20260626"})

    assert client.calls == [{"api_name": "daily", "params": {"fields": "", "trade_date": "20260626"}}]
    assert response.source_id == "tushare"
    assert response.api_name == "daily"
    assert response.rows == 1


def test_tushare_source_adapter_uses_configured_http_endpoint(monkeypatch):
    calls: dict[str, object] = {}

    class FakeClient:
        def query(self, api_name, **params):
            calls["query"] = {"api_name": api_name, "params": params}
            return pd.DataFrame([{"exchange": "SSE", "cal_date": "20260626", "is_open": 1}])

    def fake_configure(proxy_url):
        calls["proxy_url"] = proxy_url

    def fake_pro_api(token, timeout):
        calls["pro_api"] = {"token": token, "timeout": timeout}
        return FakeClient()

    import tushare as ts

    monkeypatch.setattr("research_data_foundation.sources.tushare.configure_tushare_proxy", fake_configure)
    monkeypatch.setattr(ts, "pro_api", fake_pro_api)

    response = TushareSourceAdapter(token="token", proxy_url="http://proxy.example/tushare", timeout=12).fetch(
        "trade_cal",
        {"exchange": "SSE", "start_date": "20260626", "end_date": "20260626"},
        fields=("exchange", "cal_date", "is_open"),
    )

    assert calls["proxy_url"] == "http://proxy.example/tushare"
    assert calls["pro_api"] == {"token": "token", "timeout": 12}
    assert calls["query"] == {
        "api_name": "trade_cal",
        "params": {
            "exchange": "SSE",
            "start_date": "20260626",
            "end_date": "20260626",
            "fields": "exchange,cal_date,is_open",
        },
    }
    assert response.rows == 1
    assert response.frame.iloc[0]["cal_date"] == "20260626"


def test_sec_edgar_source_adapter_flattens_submissions():
    def fake_transport(url, params, headers, timeout):
        assert url == "https://data.sec.gov/submissions/CIK0000320193.json"
        assert params == {}
        assert "User-Agent" in headers
        return HttpResponse(
            status=200,
            url=url,
            text=json.dumps(
                {
                    "name": "Apple Inc.",
                    "filings": {
                        "recent": {
                            "form": ["10-K"],
                            "filingDate": ["2025-10-31"],
                            "accessionNumber": ["0000320193-25-000079"],
                            "primaryDocument": ["aapl-20250927.htm"],
                            "primaryDocDescription": ["10-K"],
                        }
                    },
                }
            ),
            headers={},
        )

    response = SecEdgarSourceAdapter(user_agent="test-agent", transport=fake_transport).fetch("submissions", {"cik": "320193"})

    assert response.source_id == "sec_edgar"
    assert response.api_name == "submissions"
    assert response.params == {"cik": "0000320193"}
    row = response.frame.iloc[0].to_dict()
    assert row["accessionNumber"] == "0000320193-25-000079"
    assert row["source_url"].endswith("/000032019325000079/aapl-20250927.htm")


def test_sec_edgar_source_adapter_flattens_ticker_mapping_and_companyfacts():
    def fake_transport(url, params, headers, timeout):
        assert params == {}
        assert "User-Agent" in headers
        if url == "https://www.sec.gov/files/company_tickers.json":
            return HttpResponse(
                status=200,
                url=url,
                text=json.dumps({"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}),
                headers={},
            )
        if url == "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json":
            return HttpResponse(
                status=200,
                url=url,
                text=json.dumps(
                    {
                        "entityName": "Apple Inc.",
                        "facts": {
                            "us-gaap": {
                                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                                    "label": "Revenue",
                                    "description": "Revenue from contracts with customers",
                                    "units": {
                                        "USD": [
                                            {
                                                "end": "2025-09-27",
                                                "fy": 2025,
                                                "fp": "FY",
                                                "form": "10-K",
                                                "filed": "2025-10-31",
                                                "accn": "0000320193-25-000079",
                                                "frame": "CY2025",
                                                "val": 391035000000,
                                            }
                                        ]
                                    },
                                }
                            }
                        },
                    }
                ),
                headers={},
            )
        raise AssertionError(url)

    adapter = SecEdgarSourceAdapter(user_agent="test-agent", transport=fake_transport)

    mapping = adapter.fetch("company_tickers", {})
    facts = adapter.fetch("companyfacts", {"cik": "320193"})

    assert mapping.api_name == "company_tickers"
    assert mapping.frame.iloc[0].to_dict() == {
        "cik": "0000320193",
        "ticker": "AAPL",
        "title": "Apple Inc.",
        "source_url": "https://www.sec.gov/edgar/browse/?CIK=0000320193",
    }
    fact = facts.frame.iloc[0].to_dict()
    assert facts.api_name == "companyfacts"
    assert facts.params == {"cik": "0000320193"}
    assert fact["entity_name"] == "Apple Inc."
    assert fact["concept"] == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert fact["value"] == 391035000000
    assert fact["filed_date"] == "2025-10-31"


def test_eastmoney_source_adapter_fetches_industry_reports():
    calls = []

    def fake_transport(url, params, headers, timeout):
        calls.append({"url": url, "params": params, "headers": headers})
        return HttpResponse(
            status=200,
            url=url,
            text=json.dumps(
                {
                    "TotalPage": 1,
                    "data": [
                        {
                            "infoCode": "AP202606260001",
                            "title": "AI算力行业跟踪",
                            "publishDate": "2026-06-26 00:00:00.000",
                            "orgSName": "示例证券",
                            "industryName": "IT服务",
                            "ratingChange": "",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            headers={},
        )

    response = EastmoneySourceAdapter(transport=fake_transport).fetch(
        "reportapi.industry_reports",
        {"industry_code": "*", "begin": "2026-06-01", "max_pages": "1"},
    )

    assert calls[0]["params"]["qType"] == "1"
    assert calls[0]["headers"]["Referer"] == "https://data.eastmoney.com/"
    row = response.frame.iloc[0].to_dict()
    assert row["infoCode"] == "AP202606260001"
    assert row["source_url"] == "https://pdf.dfcfw.com/pdf/H3_AP202606260001_1.pdf"


def test_eastmoney_source_adapter_fetches_intraday_snapshot():
    calls = []

    def fake_transport(url, params, headers, timeout):
        calls.append({"url": url, "params": params, "headers": headers})
        return HttpResponse(
            status=200,
            url=f"{url}?secids={params['secids']}",
            text=json.dumps(
                {
                    "data": {
                        "diff": [
                            {"f12": "000001", "f14": "平安银行", "f2": 10.2, "f3": 2.1, "f5": 1000, "f6": 200000}
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            headers={},
        )

    response = EastmoneySourceAdapter(source_id="eastmoney_intraday", transport=fake_transport).fetch(
        "push2.quote_snapshot",
        {"secids": "0.000001", "snapshot_at": "2026-06-26T10:30:00+08:00"},
    )

    assert response.source_id == "eastmoney_intraday"
    assert response.api_name == "push2.quote_snapshot"
    assert calls[0]["params"]["fields"] == "f12,f14,f2,f3,f5,f6"
    row = response.frame.iloc[0].to_dict()
    assert row["f12"] == "000001"
    assert row["snapshot_at"] == "2026-06-26T10:30:00+08:00"
    assert row["source_url"].startswith("https://push2.eastmoney.com/")


def test_tencent_quote_adapter_fetches_current_quote_snapshot():
    calls = []

    def fake_transport(url, params, headers, timeout):
        calls.append({"url": url, "params": params, "headers": headers})
        text = (
            'v_sz000001="51~平安银行~000001~10.23~10.42~10.42~1236482~480819~755663~'
            '10.23~236~10.22~7029~10.21~6570~10.20~16561~10.19~6604~10.24~2867~'
            '10.25~1783~10.26~1525~10.27~1022~10.28~2501~~20260626161457~'
            '-0.19~-1.82~10.47~10.19~10.23/1236482/1270902948~1236482~127090~";'
        )
        return HttpResponse(status=200, url=f"{url}?q={params['q']}", text=text, headers={})

    response = TencentQuoteAdapter(transport=fake_transport).fetch(
        "qt.quote_snapshot",
        {"security_ids": "000001.SZ", "snapshot_at": "2026-06-26T10:30:00+08:00"},
    )

    assert response.source_id == "tencent_quote"
    assert response.api_name == "qt.quote_snapshot"
    assert calls[0]["params"] == {"q": "sz000001"}
    assert calls[0]["headers"]["Referer"] == "https://stockapp.finance.qq.com/"
    row = response.frame.iloc[0].to_dict()
    assert row["security_id"] == "000001.SZ"
    assert row["name"] == "平安银行"
    assert row["price"] == 10.23
    assert row["pct_chg"] == -1.82
    assert row["amount"] == 1270902948.0
    assert row["quote_time"] == "2026-06-26T16:14:57+08:00"


def test_cninfo_source_adapter_fetches_announcement_index():
    calls = []

    def fake_transport(url, params, headers, timeout):
        calls.append({"url": url, "params": params, "headers": headers})
        return HttpResponse(
            status=200,
            url=url,
            text=json.dumps(
                {
                    "hasMore": False,
                    "totalpages": 1,
                    "announcements": [
                        {
                            "secCode": "000001",
                            "secName": "平安银行",
                            "orgId": "gssz0000001",
                            "announcementId": "1219999999",
                            "announcementTitle": "2025年年度报告",
                            "announcementTime": 1782268800000,
                            "adjunctUrl": "finalpage/2026-06-24/1219999999.PDF",
                            "adjunctSize": 1024,
                            "adjunctType": "PDF",
                            "columnId": "09020202",
                            "pageColumn": "SZSE",
                            "announcementType": "010301",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            headers={},
        )

    response = CninfoSourceAdapter(transport=fake_transport).fetch(
        "announcements",
        {"start_date": "20260624", "end_date": "20260624", "max_pages": 1},
    )

    assert response.source_id == "cninfo"
    assert response.api_name == "announcements"
    assert calls[0]["params"]["seDate"] == "2026-06-24~2026-06-24"
    assert calls[0]["headers"]["X-Requested-With"] == "XMLHttpRequest"
    row = response.frame.iloc[0].to_dict()
    assert row["publish_date"] == "20260624"
    assert row["security_id"] == "000001.SZ"
    assert row["org_id"] == "gssz0000001"
    assert row["source_url"] == "https://static.cninfo.com.cn/finalpage/2026-06-24/1219999999.PDF"


def test_cninfo_source_adapter_fetches_full_market_columns_with_dedupe():
    calls = []

    def fake_transport(url, params, headers, timeout):
        calls.append({"url": url, "params": params, "headers": headers})
        column = params["column"]
        payload_by_column = {
            "szse": [
                {
                    "secCode": "000001",
                    "secName": "平安银行",
                    "orgId": "gssz0000001",
                    "announcementId": "1219999999",
                    "announcementTitle": "2025年年度报告",
                    "announcementTime": 1782268800000,
                    "adjunctUrl": "finalpage/2026-06-24/1219999999.PDF",
                    "pageColumn": "SZSE",
                }
            ],
            "sse": [
                {
                    "secCode": "600000",
                    "secName": "浦发银行",
                    "orgId": "gssh0600000",
                    "announcementId": "1219999999",
                    "announcementTitle": "重复公告",
                    "announcementTime": 1782268800000,
                    "adjunctUrl": "finalpage/2026-06-24/1219999999.PDF",
                    "pageColumn": "SSE",
                }
            ],
            "bse": [
                {
                    "secCode": "833000",
                    "secName": "北交示例",
                    "orgId": "gfbj0833000",
                    "announcementId": "1220000001",
                    "announcementTitle": "2025年年度报告",
                    "announcementTime": 1782268800000,
                    "adjunctUrl": "finalpage/2026-06-24/1220000001.PDF",
                    "pageColumn": "BSE",
                }
            ],
        }
        return HttpResponse(
            status=200,
            url=url,
            text=json.dumps(
                {
                    "hasMore": False,
                    "totalpages": 1,
                    "announcements": payload_by_column[column],
                },
                ensure_ascii=False,
            ),
            headers={},
        )

    response = CninfoSourceAdapter(transport=fake_transport).fetch(
        "announcements",
        {"start_date": "20260624", "end_date": "20260624", "column": "szse,sse,bse", "max_pages": 1},
    )

    assert [call["params"]["column"] for call in calls] == ["szse", "sse", "bse"]
    assert [call["params"]["plate"] for call in calls] == ["sz", "sh", "bj"]
    assert len(response.frame) == 2
    assert set(response.frame["security_id"]) == {"000001.SZ", "833000.BJ"}


def test_cninfo_source_adapter_fetches_announcement_pdf_text(monkeypatch):
    calls = []

    def fake_binary_transport(url, params, headers, timeout):
        calls.append({"url": url, "params": params, "headers": headers})
        return HttpBinaryResponse(
            status=200,
            url=url,
            content=b"%PDF-1.4 fake",
            headers={"Content-Type": "application/pdf"},
        )

    monkeypatch.setattr(
        "research_data_foundation.sources.cninfo.extract_pdf_text",
        lambda content: {"text": "年度报告正文", "page_count": 1, "status": "ok", "message": ""},
    )

    response = CninfoSourceAdapter(binary_transport=fake_binary_transport).fetch(
        "announcement_pdf_text",
        {
            "publish_date": "20260624",
            "announcement_id": "1219999999",
            "security_id": "000001.SZ",
            "security_name": "平安银行",
            "title": "2025年年度报告",
            "source_url": "https://static.cninfo.com.cn/finalpage/2026-06-24/1219999999.PDF",
        },
    )

    row = response.frame.iloc[0].to_dict()
    assert calls[0]["url"] == "https://static.cninfo.com.cn/finalpage/2026-06-24/1219999999.PDF"
    assert row["parse_status"] == "ok"
    assert row["text"] == "年度报告正文"
    assert row["text_length"] == 6
    assert response.artifacts[0].filename == "1219999999.pdf"
    assert response.artifacts[0].content == b"%PDF-1.4 fake"


def test_ingestion_runner_executes_sec_recipe(tmp_path):
    def fake_transport(url, params, headers, timeout):
        return HttpResponse(
            status=200,
            url=url,
            text=json.dumps(
                {
                    "filings": {
                        "recent": {
                            "form": ["10-Q"],
                            "filingDate": ["2026-04-30"],
                            "accessionNumber": ["0000320193-26-000010"],
                            "primaryDocument": ["aapl-20260328.htm"],
                        }
                    }
                }
            ),
            headers={},
        )

    registry = default_registry()
    runner = IngestionRunner(
        data_dir=tmp_path,
        registry=registry,
        adapters={"sec_edgar": SecEdgarSourceAdapter(transport=fake_transport)},
    )

    result = runner.run_recipe("sec_edgar.submissions.to_global_sec_filings", partition={"cik": "0000320193"})

    assert result.dataset_id == "global.sec_filings"
    frame = MartStore(tmp_path, registry).read("global.sec_filings", {"cik": "0000320193"})
    assert frame.iloc[0]["accession_number"] == "0000320193-26-000010"
    assert frame.iloc[0]["filing_date"] == "2026-04-30"
    assert frame.iloc[0]["primary_document"] == "aapl-20260328.htm"


def test_ingestion_runner_executes_sec_ticker_mapping_and_companyfacts_recipes(tmp_path):
    def fake_transport(url, params, headers, timeout):
        if url == "https://www.sec.gov/files/company_tickers.json":
            return HttpResponse(
                status=200,
                url=url,
                text=json.dumps({"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}),
                headers={},
            )
        if url == "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json":
            return HttpResponse(
                status=200,
                url=url,
                text=json.dumps(
                    {
                        "entityName": "Apple Inc.",
                        "facts": {
                            "us-gaap": {
                                "Assets": {
                                    "label": "Assets",
                                    "description": "Assets",
                                    "units": {
                                        "USD": [
                                            {
                                                "end": "2025-09-27",
                                                "fy": 2025,
                                                "fp": "FY",
                                                "form": "10-K",
                                                "filed": "2025-10-31",
                                                "accn": "0000320193-25-000079",
                                                "frame": "CY2025",
                                                "val": 359241000000,
                                            }
                                        ]
                                    },
                                }
                            }
                        },
                    }
                ),
                headers={},
            )
        raise AssertionError(url)

    registry = default_registry()
    runner = IngestionRunner(
        data_dir=tmp_path,
        registry=registry,
        adapters={"sec_edgar": SecEdgarSourceAdapter(transport=fake_transport)},
    )

    mapping_result = runner.run_recipe(
        "sec_edgar.company_tickers.to_global_sec_ticker_cik",
        partition={"snapshot_date": "20260626"},
    )
    facts_result = runner.run_recipe("sec_edgar.companyfacts.to_global_sec_companyfacts", partition={"cik": "0000320193"})

    assert mapping_result.dataset_id == "global.sec_ticker_cik"
    assert facts_result.dataset_id == "global.sec_companyfacts"
    mart = MartStore(tmp_path, registry)
    mapping = mart.read("global.sec_ticker_cik", {"snapshot_date": "20260626"})
    facts = mart.read("global.sec_companyfacts", {"cik": "0000320193"})
    assert mapping.iloc[0]["ticker"] == "AAPL"
    assert mapping.iloc[0]["snapshot_date"] == "20260626"
    assert facts.iloc[0]["concept"] == "Assets"
    assert facts.iloc[0]["value"] == 359241000000


def test_ingestion_runner_executes_eastmoney_report_recipe(tmp_path):
    def fake_transport(url, params, headers, timeout):
        assert params["beginTime"] == "2026-06-01"
        assert params["endTime"] == "2026-06-26"
        return HttpResponse(
            status=200,
            url=url,
            text=json.dumps(
                {
                    "TotalPage": 1,
                    "data": [
                        {
                            "infoCode": "AP202606260001",
                            "title": "AI算力行业跟踪",
                            "publishDate": "2026-06-26 00:00:00.000",
                            "orgSName": "示例证券",
                            "industryName": "IT服务",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            headers={},
        )

    registry = default_registry()
    runner = IngestionRunner(
        data_dir=tmp_path,
        registry=registry,
        adapters={"eastmoney_direct": EastmoneySourceAdapter(transport=fake_transport)},
    )

    result = runner.run_recipe(
        "eastmoney.reportapi.industry_reports.to_report_index",
        partition={"query_date": "2026-06-26"},
        params={"begin": "2026-06-01", "end": "2026-06-26", "max_pages": "1"},
    )

    assert result.dataset_id == "industry.eastmoney_report_index"
    frame = MartStore(tmp_path, registry).read("industry.eastmoney_report_index", {"query_date": "2026-06-26"})
    assert frame.iloc[0]["report_id"] == "AP202606260001"
    assert frame.iloc[0]["source_name"] == "示例证券"
    assert frame.iloc[0]["industry_name"] == "IT服务"
    assert frame.iloc[0]["source_url"].endswith("AP202606260001_1.pdf")
    assert "ratingChange" not in frame.columns


def test_industry_report_index_maintainer_fetches_bounded_report_window(tmp_path):
    def fake_transport(url, params, headers, timeout):
        assert params["beginTime"] == "2026-05-25"
        assert params["endTime"] == "2026-06-24"
        return HttpResponse(
            status=200,
            url=url,
            text=json.dumps(
                {
                    "TotalPage": 1,
                    "data": [
                        {
                            "infoCode": "AP202606240001",
                            "title": "机器人行业跟踪",
                            "publishDate": "2026-06-24 00:00:00.000",
                            "orgSName": "示例证券",
                            "industryName": "自动化设备",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            headers={},
        )

    registry = default_registry()
    maintainer = IndustryReportIndexMaintainer(
        data_dir=tmp_path,
        registry=registry,
        adapters={"eastmoney_direct": EastmoneySourceAdapter(transport=fake_transport)},
    )

    result = maintainer.maintain(query_date="20260624", lookback_days=30, max_pages=1)

    assert result["schema"] == "rdf.industry_report_index_maintenance_run.v1"
    assert result["status"] == "ready"
    assert result["begin"] == "2026-05-25"
    assert result["end"] == "2026-06-24"
    assert "not company business exposure proof" in result["boundary"]
    frame = MartStore(tmp_path, registry).read("industry.eastmoney_report_index", {"query_date": "20260624"})
    assert frame.iloc[0]["report_id"] == "AP202606240001"
    meta = MartStore(tmp_path, registry).read_meta("industry.eastmoney_report_index", {"query_date": "20260624"})
    assert meta["lineage"]["requested_params"]["end"] == "2026-06-24"


def test_ingestion_runner_executes_main_business_recipe(tmp_path):
    class FakeAdapter:
        source_id = "tushare"

        def fetch(self, api_name, params, fields=None):
            assert api_name == "fina_mainbz"
            assert params == {"ts_code": "000001.SZ", "period": "20251231", "type": "P"}
            return SourceFetchResult(
                source_id="tushare",
                api_name=api_name,
                params=dict(params),
                requested_at="2026-06-26T18:00:00+08:00",
                frame=pd.DataFrame(
                    [
                        {
                            "ts_code": "000001.SZ",
                            "end_date": "20251231",
                            "bz_item": "个人贷款业务",
                            "bz_code": "P",
                            "bz_sales": 100.0,
                            "bz_profit": 20.0,
                            "bz_cost": 80.0,
                            "curr_type": "CNY",
                        }
                    ]
                ),
            )

    registry = default_registry()
    runner = IngestionRunner(data_dir=tmp_path, registry=registry, adapters={"tushare": FakeAdapter()})

    result = runner.run_recipe(
        "tushare.fina_mainbz.to_ashare_main_business",
        partition={"period": "20251231", "security_id": "000001.SZ", "segment_type": "P"},
    )

    assert result.dataset_id == "ashare.main_business"
    frame = MartStore(tmp_path, registry).read(
        "ashare.main_business",
        {"period": "20251231", "security_id": "000001.SZ", "segment_type": "P"},
    )
    assert frame.iloc[0]["item_name"] == "个人贷款业务"
    assert frame.iloc[0]["sales"] == 100.0
    assert frame.iloc[0]["currency"] == "CNY"


def test_ingestion_runner_executes_concept_members_recipe(tmp_path):
    class FakeAdapter:
        source_id = "tushare"

        def fetch(self, api_name, params, fields=None):
            assert api_name == "dc_member"
            assert params == {"ts_code": "BK1234"}
            return SourceFetchResult(
                source_id="tushare",
                api_name=api_name,
                params=dict(params),
                requested_at="2026-06-26T18:00:00+08:00",
                frame=pd.DataFrame(
                    [
                        {
                            "ts_code": "BK1234",
                            "con_code": "000001.SZ",
                            "name": "平安银行",
                        }
                    ]
                ),
            )

    registry = default_registry()
    runner = IngestionRunner(data_dir=tmp_path, registry=registry, adapters={"tushare": FakeAdapter()})

    result = runner.run_recipe(
        "tushare.dc_member.to_ashare_concept_members",
        partition={"snapshot_date": "20260624", "concept_id": "BK1234"},
    )

    assert result.dataset_id == "ashare.concept_members"
    frame = MartStore(tmp_path, registry).read(
        "ashare.concept_members",
        {"snapshot_date": "20260624", "concept_id": "BK1234"},
    )
    assert frame.iloc[0]["security_id"] == "000001.SZ"
    assert frame.iloc[0]["security_name"] == "平安银行"
    assert frame.iloc[0]["concept_id"] == "BK1234"


def test_ingestion_runner_executes_ths_index_and_member_recipes(tmp_path):
    class FakeAdapter:
        source_id = "tushare"

        def fetch(self, api_name, params, fields=None):
            if api_name == "ths_index":
                assert params == {"exchange": "A"}
                assert tuple(fields or ()) == ("ts_code", "name", "count", "exchange", "list_date", "type")
                frame = pd.DataFrame(
                    [
                        {"ts_code": "885001.TI", "name": "人工智能", "count": 80, "exchange": "A", "list_date": "20200101", "type": "N"}
                    ]
                )
            else:
                assert api_name == "ths_member"
                assert params == {"ts_code": "885001.TI"}
                assert tuple(fields or ()) == ("ts_code", "con_code", "con_name", "weight", "in_date", "out_date", "is_new")
                frame = pd.DataFrame(
                    [
                        {
                            "ts_code": "885001.TI",
                            "con_code": "000001.SZ",
                            "con_name": "平安银行",
                            "weight": 1.2,
                            "in_date": "20200101",
                            "out_date": "",
                            "is_new": "Y",
                        }
                    ]
                )
            return SourceFetchResult(
                source_id="tushare",
                api_name=api_name,
                params=dict(params),
                requested_at="2026-06-26T18:00:00+08:00",
                frame=frame,
            )

    registry = default_registry()
    runner = IngestionRunner(data_dir=tmp_path, registry=registry, adapters={"tushare": FakeAdapter()})

    index_result = runner.run_recipe("tushare.ths_index.to_ashare_ths_index", partition={"snapshot_date": "20260624"})
    member_result = runner.run_recipe(
        "tushare.ths_member.to_ashare_ths_concept_members",
        partition={"snapshot_date": "20260624", "concept_id": "885001.TI"},
    )

    assert index_result.dataset_id == "ashare.ths_index"
    assert member_result.dataset_id == "ashare.ths_concept_members"
    index_frame = MartStore(tmp_path, registry).read("ashare.ths_index", {"snapshot_date": "20260624"})
    member_frame = MartStore(tmp_path, registry).read(
        "ashare.ths_concept_members",
        {"snapshot_date": "20260624", "concept_id": "885001.TI"},
    )
    assert index_frame.iloc[0]["concept_id"] == "885001.TI"
    assert index_frame.iloc[0]["index_type"] == "N"
    assert index_frame.iloc[0]["source_member_count"] == 80
    assert member_frame.iloc[0]["security_id"] == "000001.SZ"
    assert member_frame.iloc[0]["security_name"] == "平安银行"
    meta = MartStore(tmp_path, registry).read_meta("ashare.ths_concept_members", {"snapshot_date": "20260624", "concept_id": "885001.TI"})
    assert meta["lineage"]["recipe_id"] == "tushare.ths_member.to_ashare_ths_concept_members"


def test_ingestion_runner_executes_market_attention_fanout_recipes(tmp_path):
    class FakeAdapter:
        source_id = "tushare"

        def fetch(self, api_name, params, fields=None):
            if api_name == "ths_hot":
                assert tuple(fields or ()) == (
                    "trade_date",
                    "data_type",
                    "ts_code",
                    "ts_name",
                    "rank",
                    "pct_change",
                    "current_price",
                    "hot",
                    "concept",
                    "rank_time",
                    "rank_reason",
                )
                frame = pd.DataFrame(
                    [
                        {
                            "trade_date": params["trade_date"],
                            "data_type": params["market"],
                            "ts_code": "600584.SH" if params["market"] == "热股" else "886042.TI",
                            "ts_name": "长电科技" if params["market"] == "热股" else "存储芯片",
                            "rank": 1,
                            "pct_change": 10.0,
                            "current_price": 94.7,
                            "hot": 1326348.0,
                            "concept": "[\"存储芯片\"]",
                            "rank_time": "2026-06-24 22:30:00",
                            "rank_reason": "vendor generated text",
                        }
                    ]
                )
            else:
                assert api_name == "dc_hot"
                assert params["market"] == "A股市场"
                assert params["hot_type"] in {"人气榜", "飙升榜"}
                assert tuple(fields or ()) == (
                    "trade_date",
                    "data_type",
                    "ts_code",
                    "ts_name",
                    "rank",
                    "pct_change",
                    "current_price",
                    "hot",
                    "concept",
                    "rank_time",
                )
                frame = pd.DataFrame(
                    [
                        {
                            "trade_date": params["trade_date"],
                            "data_type": params["market"],
                            "ts_code": "600584.SH" if params["hot_type"] == "人气榜" else "688593.SH",
                            "ts_name": "长电科技" if params["hot_type"] == "人气榜" else "新相微",
                            "rank": 1,
                            "pct_change": 10.0,
                            "current_price": 94.7,
                            "hot": None,
                            "concept": None,
                            "rank_time": "2026-06-24 22:30:08",
                        }
                    ]
                )
            return SourceFetchResult(
                source_id="tushare",
                api_name=api_name,
                params=dict(params),
                requested_at="2026-06-26T18:00:00+08:00",
                frame=frame,
            )

    registry = default_registry()
    runner = IngestionRunner(data_dir=tmp_path, registry=registry, adapters={"tushare": FakeAdapter()})

    ths_result = runner.run_recipe("tushare.ths_hot.to_ashare_ths_hot_rank", partition={"trade_date": "20260624"})
    dc_result = runner.run_recipe("tushare.dc_hot.to_ashare_dc_hot_rank", partition={"trade_date": "20260624"})

    assert ths_result.rows == 2
    assert dc_result.rows == 2
    ths_frame = MartStore(tmp_path, registry).read("ashare.ths_hot_rank", {"trade_date": "20260624"})
    dc_frame = MartStore(tmp_path, registry).read("ashare.dc_hot_rank", {"trade_date": "20260624"})
    assert set(ths_frame["rank_type"]) == {"热股", "概念板块"}
    assert set(ths_frame["subject_id"]) == {"600584.SH", "886042.TI"}
    assert ths_frame.loc[ths_frame["rank_type"] == "热股", "vendor_rank_reason"].iloc[0] == "vendor generated text"
    assert set(dc_frame["rank_type"]) == {"人气榜", "飙升榜"}
    assert set(dc_frame["security_id"]) == {"600584.SH", "688593.SH"}
    dc_raw = pd.read_json(Path(dc_result.raw_path or "") / "response.jsonl", lines=True)
    assert "hot_type" in dc_raw.columns


def test_ingestion_runner_executes_short_term_sentiment_pipeline_recipes(tmp_path):
    class FakeAdapter:
        source_id = "tushare"

        def fetch(self, api_name, params, fields=None):
            assert params == {"trade_date": "20260624"}
            if api_name == "limit_step":
                assert tuple(fields or ()) == ("ts_code", "name", "trade_date", "nums")
                frame = pd.DataFrame(
                    [{"ts_code": "002167.SZ", "name": "东方锆业", "trade_date": "20260624", "nums": "4"}]
                )
            elif api_name == "limit_cpt_list":
                assert tuple(fields or ()) == (
                    "ts_code",
                    "name",
                    "trade_date",
                    "days",
                    "up_stat",
                    "cons_nums",
                    "up_nums",
                    "pct_chg",
                    "rank",
                )
                frame = pd.DataFrame(
                    [
                        {
                            "ts_code": "885756.TI",
                            "name": "芯片概念",
                            "trade_date": "20260624",
                            "days": 16,
                            "up_stat": "5天3板",
                            "cons_nums": "12",
                            "up_nums": 39,
                            "pct_chg": 1.4508,
                            "rank": 1,
                        }
                    ]
                )
            elif api_name == "kpl_list":
                assert tuple(fields or ()) == (
                    "ts_code",
                    "name",
                    "trade_date",
                    "lu_time",
                    "ld_time",
                    "open_time",
                    "last_time",
                    "lu_desc",
                    "tag",
                    "theme",
                    "net_change",
                    "bid_amount",
                    "status",
                    "bid_change",
                    "bid_turnover",
                    "lu_bid_vol",
                    "pct_chg",
                    "bid_pct_chg",
                    "rt_pct_chg",
                    "limit_order",
                    "amount",
                    "turnover_rate",
                    "free_float",
                    "lu_limit_order",
                )
                frame = pd.DataFrame(
                    [
                        {
                            "ts_code": "603986.SH",
                            "name": "兆易创新",
                            "trade_date": "20260624",
                            "lu_time": "14:50:26",
                            "ld_time": None,
                            "open_time": None,
                            "last_time": "14:50:26",
                            "lu_desc": "芯片",
                            "tag": "涨停",
                            "theme": "存储、模拟芯片",
                            "net_change": 2081412588.0,
                            "bid_amount": None,
                            "status": "首板",
                            "bid_change": None,
                            "bid_turnover": None,
                            "lu_bid_vol": None,
                            "pct_chg": None,
                            "bid_pct_chg": None,
                            "rt_pct_chg": None,
                            "limit_order": 672796928.0,
                            "amount": 39944848280.0,
                            "turnover_rate": 9.66,
                            "free_float": 436750109577.0,
                            "lu_limit_order": 1284071168.0,
                        }
                    ]
                )
            else:
                assert api_name == "kpl_concept_cons"
                assert tuple(fields or ()) == ("ts_code", "name", "con_name", "con_code", "trade_date", "desc", "hot_num")
                frame = pd.DataFrame(
                    [
                        {
                            "ts_code": "000352.KP",
                            "name": "磷化铟概念",
                            "con_name": "三安光电",
                            "con_code": "600703.SH",
                            "trade_date": "20260624",
                            "desc": "供应商题材描述",
                            "hot_num": 19783,
                        }
                    ]
                )
            return SourceFetchResult(
                source_id="tushare",
                api_name=api_name,
                params=dict(params),
                requested_at="2026-06-26T18:00:00+08:00",
                frame=frame,
            )

    registry = default_registry()
    runner = IngestionRunner(data_dir=tmp_path, registry=registry, adapters={"tushare": FakeAdapter()})
    result = runner.run_pipeline("ashare_short_term_sentiment_daily", partition={"trade_date": "20260624"})

    assert result.rows == 4
    mart = MartStore(tmp_path, registry)
    limit_step = mart.read("ashare.limit_step", {"trade_date": "20260624"})
    assert limit_step.iloc[0]["security_id"] == "002167.SZ"
    assert limit_step.iloc[0]["limit_up_days"] == 4
    concept_rank = mart.read("ashare.limit_concept_rank", {"trade_date": "20260624"})
    assert concept_rank.iloc[0]["concept_id"] == "885756.TI"
    assert concept_rank.iloc[0]["consecutive_limit_count"] == 12
    kpl_list = mart.read("ashare.kpl_limit_list", {"trade_date": "20260624"})
    assert kpl_list.iloc[0]["security_id"] == "603986.SH"
    assert kpl_list.iloc[0]["board_status"] == "首板"
    assert kpl_list.iloc[0]["limit_reason"] == "芯片"
    kpl_members = mart.read("ashare.kpl_concept_members", {"trade_date": "20260624"})
    assert kpl_members.iloc[0]["concept_id"] == "000352.KP"
    assert kpl_members.iloc[0]["security_id"] == "600703.SH"
    assert kpl_members.iloc[0]["vendor_exposure_desc"] == "供应商题材描述"


def test_ingestion_runner_executes_moneyflow_pipeline_recipes(tmp_path):
    class FakeAdapter:
        source_id = "tushare"

        def fetch(self, api_name, params, fields=None):
            assert params == {"trade_date": "20260624"}
            if api_name == "moneyflow_dc":
                assert "net_amount_rate" in tuple(fields or ())
                frame = pd.DataFrame(
                    [
                        {
                            "trade_date": "20260624",
                            "ts_code": "600584.SH",
                            "name": "长电科技",
                            "pct_change": 10.0,
                            "close": 94.7,
                            "net_amount": 508388.15,
                            "net_amount_rate": 21.37,
                            "buy_elg_amount": 637250.36,
                            "buy_elg_amount_rate": 26.79,
                            "buy_lg_amount": -128862.21,
                            "buy_lg_amount_rate": -5.42,
                            "buy_md_amount": -303466.78,
                            "buy_md_amount_rate": -12.76,
                            "buy_sm_amount": -204921.36,
                            "buy_sm_amount_rate": -8.61,
                        }
                    ]
                )
            elif api_name == "moneyflow":
                frame = pd.DataFrame(
                    [
                        {
                            "ts_code": "300815.SZ",
                            "trade_date": "20260624",
                            "buy_sm_vol": 17175,
                            "buy_sm_amount": 3090.8,
                            "sell_sm_vol": 16062,
                            "sell_sm_amount": 2892.65,
                            "buy_md_vol": 19248,
                            "buy_md_amount": 3465.67,
                            "sell_md_vol": 19722,
                            "sell_md_amount": 3548.32,
                            "buy_lg_vol": 12042,
                            "buy_lg_amount": 2164.91,
                            "sell_lg_vol": 10951,
                            "sell_lg_amount": 1969.72,
                            "buy_elg_vol": 618,
                            "buy_elg_amount": 112.54,
                            "sell_elg_vol": 2349,
                            "sell_elg_amount": 423.22,
                            "net_mf_vol": -16614,
                            "net_mf_amount": -2988.9,
                        }
                    ]
                )
            elif api_name == "moneyflow_ths":
                frame = pd.DataFrame(
                    [
                        {
                            "trade_date": "20260624",
                            "ts_code": "688797.SH",
                            "name": "臻宝科技",
                            "pct_change": 1212.84,
                            "latest": 585.0,
                            "net_amount": 165058.68,
                            "net_d5_amount": 191443.92,
                            "buy_lg_amount": 259148.47,
                            "buy_lg_amount_rate": 26.39,
                            "buy_md_amount": -88895.59,
                            "buy_md_amount_rate": -9.05,
                            "buy_sm_amount": -5194.2,
                            "buy_sm_amount_rate": -0.53,
                        }
                    ]
                )
            elif api_name == "moneyflow_ind_dc":
                frame = pd.DataFrame(
                    [
                        {
                            "trade_date": "20260624",
                            "content_type": "行业",
                            "ts_code": "BK1201.DC",
                            "name": "电子",
                            "pct_change": 2.27,
                            "close": 15925.38,
                            "net_amount": 28223098880.0,
                            "net_amount_rate": 2.59,
                            "buy_elg_amount": 32926543872.0,
                            "buy_elg_amount_rate": 3.02,
                            "buy_lg_amount": -4703444992.0,
                            "buy_lg_amount_rate": -0.43,
                            "buy_md_amount": -23524384768.0,
                            "buy_md_amount_rate": -2.16,
                            "buy_sm_amount": -4442218496.0,
                            "buy_sm_amount_rate": -0.41,
                            "buy_sm_amount_stock": "长电科技",
                            "rank": 1,
                        }
                    ]
                )
            elif api_name == "moneyflow_ind_ths":
                frame = pd.DataFrame(
                    [
                        {
                            "trade_date": "20260624",
                            "ts_code": "881121.TI",
                            "industry": "半导体",
                            "lead_stock": "臻宝科技",
                            "close": 20640.0,
                            "pct_change": 3.8,
                            "company_num": 180,
                            "pct_change_stock": 1212.84,
                            "close_price": 585.0,
                            "net_buy_amount": 2543,
                            "net_sell_amount": 2229,
                            "net_amount": 313,
                        }
                    ]
                )
            elif api_name == "moneyflow_cnt_ths":
                frame = pd.DataFrame(
                    [
                        {
                            "trade_date": "20260624",
                            "ts_code": "885897.TI",
                            "name": "中芯国际概念",
                            "lead_stock": "聚辰股份",
                            "close_price": 165.88,
                            "pct_change": 4.9,
                            "industry_index": 4062.19,
                            "company_num": 89,
                            "pct_change_stock": 20.0,
                            "net_buy_amount": 1255.0,
                            "net_sell_amount": 1061.0,
                            "net_amount": 194.0,
                        }
                    ]
                )
            else:
                assert api_name == "moneyflow_hsgt"
                frame = pd.DataFrame(
                    [
                        {
                            "trade_date": "20260624",
                            "ggt_ss": "31235.54",
                            "ggt_sz": "22959.44",
                            "hgt": "187730.5",
                            "sgt": "236625.32",
                            "north_money": "424355.82",
                            "south_money": "54194.99",
                        }
                    ]
                )
            return SourceFetchResult(
                source_id="tushare",
                api_name=api_name,
                params=dict(params),
                requested_at="2026-06-26T18:00:00+08:00",
                frame=frame,
            )

    registry = default_registry()
    runner = IngestionRunner(data_dir=tmp_path, registry=registry, adapters={"tushare": FakeAdapter()})
    result = runner.run_pipeline("ashare_moneyflow_daily", partition={"trade_date": "20260624"})

    assert result.rows == 7
    mart = MartStore(tmp_path, registry)
    dc = mart.read("ashare.moneyflow_dc", {"trade_date": "20260624"})
    assert dc.iloc[0]["security_id"] == "600584.SH"
    assert dc.iloc[0]["security_name"] == "长电科技"
    assert dc.iloc[0]["net_amount_rate"] == 21.37
    ts_flow = mart.read("ashare.moneyflow_tushare", {"trade_date": "20260624"})
    assert ts_flow.iloc[0]["security_id"] == "300815.SZ"
    ths = mart.read("ashare.moneyflow_ths", {"trade_date": "20260624"})
    assert ths.iloc[0]["price"] == 585.0
    board = mart.read("ashare.moneyflow_board_dc", {"trade_date": "20260624"})
    assert board.iloc[0]["board_type"] == "行业"
    assert board.iloc[0]["subject_id"] == "BK1201.DC"
    industry = mart.read("ashare.moneyflow_industry_ths", {"trade_date": "20260624"})
    assert industry.iloc[0]["industry_id"] == "881121.TI"
    assert industry.iloc[0]["lead_stock_pct_chg"] == 1212.84
    concept = mart.read("ashare.moneyflow_concept_ths", {"trade_date": "20260624"})
    assert concept.iloc[0]["concept_id"] == "885897.TI"
    assert concept.iloc[0]["concept_index"] == 4062.19
    hsgt = mart.read("ashare.moneyflow_hsgt", {"trade_date": "20260624"})
    assert hsgt.iloc[0]["north_money"] == 424355.82


def test_ingestion_runner_executes_name_changes_recipe(tmp_path):
    class FakeAdapter:
        source_id = "tushare"

        def fetch(self, api_name, params, fields=None):
            assert api_name == "namechange"
            assert params == {}
            assert tuple(fields or ()) == ("ts_code", "name", "start_date", "end_date", "ann_date", "change_reason")
            return SourceFetchResult(
                source_id="tushare",
                api_name=api_name,
                params=dict(params),
                requested_at="2026-06-26T18:00:00+08:00",
                frame=pd.DataFrame(
                    [
                        {
                            "ts_code": "000001.SZ",
                            "name": "深发展A",
                            "start_date": "20070620",
                            "end_date": "20120801",
                            "ann_date": "",
                            "change_reason": "完成股改",
                        }
                    ]
                ),
            )

    registry = default_registry()
    runner = IngestionRunner(data_dir=tmp_path, registry=registry, adapters={"tushare": FakeAdapter()})

    result = runner.run_recipe("tushare.namechange.to_ashare_name_changes", partition={"snapshot_date": "20260624"})

    assert result.dataset_id == "ashare.name_changes"
    frame = MartStore(tmp_path, registry).read("ashare.name_changes", {"snapshot_date": "20260624"})
    assert frame.iloc[0]["security_id"] == "000001.SZ"
    assert frame.iloc[0]["name"] == "深发展A"
    assert frame.iloc[0]["snapshot_date"] == "20260624"


def test_ingestion_runner_executes_company_profile_recipe_with_exchange_fanout(tmp_path):
    class FakeAdapter:
        source_id = "tushare"

        def __init__(self):
            self.calls = []

        def fetch(self, api_name, params, fields=None):
            assert api_name == "stock_company"
            self.calls.append(dict(params))
            exchange = params["exchange"]
            security_id = {"SSE": "600000.SH", "SZSE": "000001.SZ", "BSE": "830001.BJ"}[exchange]
            return SourceFetchResult(
                source_id="tushare",
                api_name=api_name,
                params=dict(params),
                requested_at="2026-06-26T18:00:00+08:00",
                frame=pd.DataFrame(
                    [
                        {
                            "ts_code": security_id,
                            "exchange": exchange,
                            "chairman": "董事长",
                            "manager": "总经理",
                            "secretary": "董秘",
                            "reg_capital": 100.0,
                            "setup_date": "20000101",
                            "province": "广东",
                            "city": "深圳市",
                            "introduction": "公司简介",
                            "website": "www.example.com",
                            "email": "ir@example.com",
                            "office": "深圳市示例路1号",
                            "employees": 1000,
                            "main_business": "主营业务文本",
                            "business_scope": "经营范围文本",
                        }
                    ]
                ),
            )

    registry = default_registry()
    adapter = FakeAdapter()
    runner = IngestionRunner(data_dir=tmp_path, registry=registry, adapters={"tushare": adapter})

    result = runner.run_recipe("tushare.stock_company.to_ashare_company_profile", partition={"snapshot_date": "20260624"})

    assert result.dataset_id == "ashare.company_profile"
    assert adapter.calls == [{"exchange": "SSE"}, {"exchange": "SZSE"}, {"exchange": "BSE"}]
    frame = MartStore(tmp_path, registry).read("ashare.company_profile", {"snapshot_date": "20260624"})
    assert set(frame["security_id"]) == {"600000.SH", "000001.SZ", "830001.BJ"}
    assert set(frame["exchange"]) == {"SSE", "SZSE", "BSE"}
    assert frame.iloc[0]["snapshot_date"] == "20260624"


def test_ingestion_runner_executes_financial_statement_recipe(tmp_path):
    class FakeAdapter:
        source_id = "tushare"

        def __init__(self):
            self.calls = []

        def fetch(self, api_name, params, fields=None):
            self.calls.append({"api_name": api_name, "params": dict(params), "fields": tuple(fields or ())})
            return SourceFetchResult(
                source_id="tushare",
                api_name=api_name,
                params=dict(params),
                requested_at="2026-06-26T18:00:00+08:00",
                frame=pd.DataFrame(
                    [
                        {
                            "ts_code": "000001.SZ",
                            "end_date": "20251231",
                            "ann_date": "20260320",
                            "total_revenue": 1000.0,
                            "n_income": 100.0,
                        }
                    ]
                ),
            )

    registry = default_registry()
    adapter = FakeAdapter()
    runner = IngestionRunner(data_dir=tmp_path, registry=registry, adapters={"tushare": adapter})

    result = runner.run_recipe(
        "tushare.income.to_ashare_income_statement",
        partition={"period": "20251231", "security_id": "000001.SZ"},
    )

    assert adapter.calls == [
        {
            "api_name": "income",
            "params": {"ts_code": "000001.SZ", "period": "20251231"},
            "fields": (),
        }
    ]
    assert result.dataset_id == "ashare.income_statement"
    frame = MartStore(tmp_path, registry).read("ashare.income_statement", {"period": "20251231", "security_id": "000001.SZ"})
    assert frame.iloc[0]["security_id"] == "000001.SZ"
    assert frame.iloc[0]["period"] == "20251231"
    assert frame.iloc[0]["total_revenue"] == 1000.0
    meta = MartStore(tmp_path, registry).read_meta("ashare.income_statement", {"period": "20251231", "security_id": "000001.SZ"})
    assert meta["domain"] == "ashare_financials"
    assert meta["quality"]["status"] == "ok"


def test_ingestion_runner_executes_cninfo_announcement_recipe(tmp_path):
    def fake_transport(url, params, headers, timeout):
        return HttpResponse(
            status=200,
            url=url,
            text=json.dumps(
                {
                    "hasMore": False,
                    "totalpages": 1,
                    "announcements": [
                        {
                            "secCode": "000001",
                            "secName": "平安银行",
                            "orgId": "gssz0000001",
                            "announcementId": "1219999999",
                            "announcementTitle": "2025年年度报告",
                            "announcementTime": 1782268800000,
                            "adjunctUrl": "finalpage/2026-06-24/1219999999.PDF",
                            "adjunctType": "PDF",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            headers={},
        )

    registry = default_registry()
    runner = IngestionRunner(
        data_dir=tmp_path,
        registry=registry,
        adapters={"cninfo": CninfoSourceAdapter(transport=fake_transport)},
    )

    result = runner.run_recipe("cninfo.announcements.to_ashare_announcements", partition={"publish_date": "20260624"})

    assert result.dataset_id == "ashare.announcements"
    frame = MartStore(tmp_path, registry).read("ashare.announcements", {"publish_date": "20260624"})
    assert frame.iloc[0]["security_id"] == "000001.SZ"
    assert frame.iloc[0]["title"] == "2025年年度报告"


def test_ingestion_runner_executes_cninfo_announcement_text_recipe(monkeypatch, tmp_path):
    def fake_binary_transport(url, params, headers, timeout):
        return HttpBinaryResponse(
            status=200,
            url=url,
            content=b"%PDF-1.4 fake",
            headers={"Content-Type": "application/pdf"},
        )

    monkeypatch.setattr(
        "research_data_foundation.sources.cninfo.extract_pdf_text",
        lambda content: {"text": "年度报告正文", "page_count": 1, "status": "ok", "message": ""},
    )

    registry = default_registry()
    runner = IngestionRunner(
        data_dir=tmp_path,
        registry=registry,
        adapters={"cninfo": CninfoSourceAdapter(binary_transport=fake_binary_transport)},
    )

    result = runner.run_recipe(
        "cninfo.announcement_pdf_text.to_ashare_announcement_text",
        partition={"publish_date": "20260624", "announcement_id": "1219999999"},
        params={
            "security_id": "000001.SZ",
            "security_name": "平安银行",
            "title": "2025年年度报告",
            "source_url": "https://static.cninfo.com.cn/finalpage/2026-06-24/1219999999.PDF",
        },
    )

    assert result.dataset_id == "ashare.announcement_text"
    frame = MartStore(tmp_path, registry).read("ashare.announcement_text", {"publish_date": "20260624", "announcement_id": "1219999999"})
    assert frame.iloc[0]["parse_status"] == "ok"
    assert frame.iloc[0]["text"] == "年度报告正文"
    raw_meta = json.loads((Path(result.raw_path) / "request.json").read_text(encoding="utf-8"))
    assert raw_meta["artifacts"][0]["filename"] == "1219999999.pdf"
    assert (Path(result.raw_path) / raw_meta["artifacts"][0]["path"]).read_bytes() == b"%PDF-1.4 fake"


def test_ingestion_runner_executes_intraday_snapshot_recipe(tmp_path):
    def fake_transport(url, params, headers, timeout):
        return HttpResponse(
            status=200,
            url=f"{url}?secids={params['secids']}",
            text=json.dumps(
                {
                    "data": {
                        "diff": [
                            {"f12": "000001", "f14": "平安银行", "f2": 10.2, "f3": 2.1, "f5": 1000, "f6": 200000}
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            headers={},
        )

    registry = default_registry()
    runner = IngestionRunner(
        data_dir=tmp_path,
        registry=registry,
        adapters={"eastmoney_intraday": EastmoneySourceAdapter(source_id="eastmoney_intraday", transport=fake_transport)},
    )
    partition = {"snapshot_at": "2026-06-26T10:30:00+08:00"}

    result = runner.run_recipe(
        "eastmoney.push2.quote_snapshot.to_ashare_intraday_snapshot",
        partition=partition,
        params={"secids": "0.000001"},
    )

    assert result.dataset_id == "ashare.intraday_snapshot"
    frame = MartStore(tmp_path, registry).read("ashare.intraday_snapshot", partition)
    assert frame.iloc[0]["security_id"] == "000001"
    assert frame.iloc[0]["price"] == 10.2
    meta = MartStore(tmp_path, registry).read_meta("ashare.intraday_snapshot", partition)
    assert meta["temporal"]["finality"] == "provisional"
    assert meta["lineage"]["source_id"] == "eastmoney_intraday"


def test_ingestion_runner_executes_pipeline(tmp_path):
    class FakeAdapter:
        source_id = "sec_edgar"

        def fetch(self, api_name, params, fields=None):
            return SourceFetchResult(
                source_id="sec_edgar",
                api_name=api_name,
                params=params,
                requested_at="2026-06-26T18:00:00+08:00",
                frame=pd.DataFrame(
                    [
                        {
                            "cik": params["cik"],
                            "form": "10-K",
                            "filingDate": "2026-01-01",
                            "accessionNumber": "0000000000-26-000001",
                            "primaryDocument": "doc.htm",
                            "source_url": "https://www.sec.gov/doc.htm",
                        }
                    ]
                ),
            )

    runner = IngestionRunner(data_dir=tmp_path, registry=default_registry(), adapters={"sec_edgar": FakeAdapter()})

    result = runner.run_pipeline("global_reference_weekly", partition={"cik": "0000000001"})

    assert result.pipeline_id == "global_reference_weekly"
    assert result.rows == 1
    assert result.failures == ()
    assert result.results[0].dataset_id == "global.sec_filings"


def test_evidence_store_builds_records_from_reference_marts(tmp_path):
    registry = default_registry()
    mart = MartStore(tmp_path, registry)
    mart.publish(
        "global.sec_filings",
        pd.DataFrame(
            [
                {
                    "cik": "0000320193",
                    "accession_number": "0000320193-26-000010",
                    "form": "10-Q",
                    "filing_date": "2026-04-30",
                    "primary_document": "aapl-20260328.htm",
                    "source_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019326000010/aapl-20260328.htm",
                }
            ]
        ),
        partition={"cik": "0000320193"},
        lineage={"source_id": "sec_edgar"},
    )
    mart.publish(
        "industry.eastmoney_report_index",
        pd.DataFrame(
            [
                {
                    "query_date": "2026-06-26",
                    "report_id": "AP202606260001",
                    "title": "AI算力行业跟踪",
                    "published_at": "2026-06-26 00:00:00.000",
                    "source_name": "示例证券",
                    "source_url": "https://pdf.dfcfw.com/pdf/H3_AP202606260001_1.pdf",
                    "industry_name": "IT服务",
                }
            ]
        ),
        partition={"query_date": "2026-06-26"},
        lineage={"source_id": "eastmoney_direct"},
    )

    sec_frame = mart.read("global.sec_filings", {"cik": "0000320193"})
    sec_meta = mart.read_meta("global.sec_filings", {"cik": "0000320193"})
    report_frame = mart.read("industry.eastmoney_report_index", {"query_date": "2026-06-26"})
    report_meta = mart.read_meta("industry.eastmoney_report_index", {"query_date": "2026-06-26"})

    records = evidence_from_table("global.sec_filings", sec_frame, meta=sec_meta) + evidence_from_table(
        "industry.eastmoney_report_index",
        report_frame,
        meta=report_meta,
    )
    store = EvidenceStore(tmp_path)
    result = store.ingest(records)
    duplicate = store.ingest(records[:1])

    assert result.inserted == 2
    assert duplicate.skipped_duplicates == 1
    sec_record = store.search(topic="sec_filing")[0]
    assert sec_record.confidence == "high"
    assert sec_record.source.source_name == "SEC EDGAR"
    report_record = store.search(topic="industry_report", industry="IT服务")[0]
    assert report_record.confidence == "low"
    assert "report_is_not_company_disclosure" in report_record.quality_flags


def test_sec_reference_marts_build_evidence_and_relation_candidates(tmp_path):
    registry = default_registry()
    mart = MartStore(tmp_path, registry)
    mart.publish(
        "global.sec_ticker_cik",
        pd.DataFrame(
            [
                {
                    "snapshot_date": "20260626",
                    "cik": "0000320193",
                    "ticker": "AAPL",
                    "title": "Apple Inc.",
                    "source_url": "https://www.sec.gov/edgar/browse/?CIK=0000320193",
                }
            ]
        ),
        partition={"snapshot_date": "20260626"},
        lineage={"source_id": "sec_edgar"},
    )
    mart.publish(
        "global.sec_companyfacts",
        pd.DataFrame(
            [
                {
                    "cik": "0000320193",
                    "entity_name": "Apple Inc.",
                    "taxonomy": "us-gaap",
                    "concept": "Assets",
                    "label": "Assets",
                    "description": "Assets",
                    "unit": "USD",
                    "start_date": "",
                    "end_date": "2025-09-27",
                    "fiscal_year": "2025",
                    "fiscal_period": "FY",
                    "form": "10-K",
                    "filed_date": "2025-10-31",
                    "accession_number": "0000320193-25-000079",
                    "frame": "CY2025",
                    "value": 359241000000,
                    "source_url": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
                }
            ]
        ),
        partition={"cik": "0000320193"},
        lineage={"source_id": "sec_edgar"},
    )

    mapping_frame = mart.read("global.sec_ticker_cik", {"snapshot_date": "20260626"})
    mapping_meta = mart.read_meta("global.sec_ticker_cik", {"snapshot_date": "20260626"})
    facts_frame = mart.read("global.sec_companyfacts", {"cik": "0000320193"})
    facts_meta = mart.read_meta("global.sec_companyfacts", {"cik": "0000320193"})

    evidence = evidence_from_table("global.sec_ticker_cik", mapping_frame, meta=mapping_meta) + evidence_from_table(
        "global.sec_companyfacts",
        facts_frame,
        meta=facts_meta,
    )

    result = EvidenceStore(tmp_path).ingest(evidence)
    assert result.inserted == 2
    mapping_record = EvidenceStore(tmp_path).search(topic="sec_ticker_cik")[0]
    facts_record = EvidenceStore(tmp_path).search(topic="sec_companyfact")[0]
    assert mapping_record.supports == ("evidence", "cross_market_context")
    assert facts_record.metric == "Assets"
    assert facts_record.confidence == "high"
    assert RelationStore(tmp_path).read_records() == []


def test_financial_mart_builds_evidence_and_exports_jsonl(tmp_path):
    registry = default_registry()
    partition = {"period": "20251231", "security_id": "000001.SZ"}
    mart = MartStore(tmp_path, registry)
    mart.publish(
        "ashare.income_statement",
        pd.DataFrame(
            [
                {
                    "period": "20251231",
                    "security_id": "000001.SZ",
                    "ann_date": "20260320",
                    "total_revenue": 1000.0,
                    "n_income": 100.0,
                }
            ]
        ),
        partition=partition,
        lineage={"source_id": "tushare", "raw_path": "raw/tushare/income/example"},
    )

    frame = mart.read("ashare.income_statement", partition)
    meta = mart.read_meta("ashare.income_statement", partition)
    records = evidence_from_table("ashare.income_statement", frame, meta=meta)
    store = EvidenceStore(tmp_path)
    result = store.ingest(records)
    export_path = tmp_path / "exports" / "income.jsonl"
    exported = store.export_jsonl(export_path, store.search(topic="income_statement", period="20251231"))

    assert result.inserted == 1
    record = store.search(company="000001.SZ", metric="n_income")[0]
    assert record.topic == "income_statement"
    assert record.supports == ("evidence", "financial_analysis")
    assert "requires_official_filing_cross_check" in record.quality_flags
    assert exported == export_path
    assert "income_statement" in export_path.read_text(encoding="utf-8")


def test_structured_corporate_action_events_build_triage_evidence(tmp_path):
    registry = default_registry()
    mart = MartStore(tmp_path, registry)
    shareholder_partition = {"ann_date": "20260624"}
    repurchase_partition = {"ann_date": "20260624"}
    forecast_partition = {"ann_date": "20260624"}
    mart.publish(
        "ashare.shareholder_trades",
        pd.DataFrame(
            [
                {
                    "ann_date": "20260624",
                    "security_id": "000001.SZ",
                    "holder_name": "示例股东",
                    "holder_type": "高管",
                    "in_de": "DE",
                    "change_vol": 1700000.0,
                    "change_ratio": 0.35,
                    "after_share": 10000000.0,
                    "after_ratio": 2.1,
                    "avg_price": 12.5,
                    "total_share": 1000000000.0,
                }
            ]
        ),
        partition=shareholder_partition,
        lineage={"source_id": "tushare", "raw_path": "raw/tushare/stk_holdertrade/example"},
    )
    mart.publish(
        "ashare.repurchase_events",
        pd.DataFrame(
            [
                {
                    "ann_date": "20260624",
                    "security_id": "000001.SZ",
                    "end_date": "20261231",
                    "process_status": "实施中",
                    "expected_end_date": "20261231",
                    "volume": 1000000.0,
                    "amount": 12000000.0,
                    "high_limit": 13.0,
                    "low_limit": 10.0,
                }
            ]
        ),
        partition=repurchase_partition,
        lineage={"source_id": "tushare", "raw_path": "raw/tushare/repurchase/example"},
    )
    mart.publish(
        "ashare.earnings_forecast_events",
        pd.DataFrame(
            [
                {
                    "ann_date": "20260624",
                    "security_id": "000001.SZ",
                    "period": "20260630",
                    "forecast_type": "预增",
                    "p_change_min": 45.5,
                    "p_change_max": 65.0,
                    "net_profit_min": 120000000.0,
                    "net_profit_max": 135000000.0,
                    "last_parent_net": 82000000.0,
                    "first_ann_date": "20260624",
                    "forecast_summary": "预计净利润增长",
                    "change_reason": "主营业务收入增长",
                }
            ]
        ),
        partition=forecast_partition,
        lineage={"source_id": "tushare", "raw_path": "raw/tushare/forecast/example"},
    )

    records = evidence_from_table(
        "ashare.shareholder_trades",
        mart.read("ashare.shareholder_trades", shareholder_partition),
        meta=mart.read_meta("ashare.shareholder_trades", shareholder_partition),
    ) + evidence_from_table(
        "ashare.repurchase_events",
        mart.read("ashare.repurchase_events", repurchase_partition),
        meta=mart.read_meta("ashare.repurchase_events", repurchase_partition),
    ) + evidence_from_table(
        "ashare.earnings_forecast_events",
        mart.read("ashare.earnings_forecast_events", forecast_partition),
        meta=mart.read_meta("ashare.earnings_forecast_events", forecast_partition),
    )
    result = EvidenceStore(tmp_path).ingest(records)

    assert result.inserted == 3
    shareholder_record = EvidenceStore(tmp_path).search(topic="shareholder_trade_event")[0]
    repurchase_record = EvidenceStore(tmp_path).search(topic="share_repurchase_event")[0]
    forecast_record = EvidenceStore(tmp_path).search(topic="earnings_forecast_event")[0]
    assert shareholder_record.confidence == "low"
    assert shareholder_record.supports == ("evidence_triage", "market_context")
    assert shareholder_record.source.source_name == "Tushare Pro stk_holdertrade"
    assert "requires_official_announcement_text" in shareholder_record.quality_flags
    assert "not_company_business_exposure" in shareholder_record.quality_flags
    assert repurchase_record.confidence == "low"
    assert repurchase_record.supports == ("evidence_triage", "market_context")
    assert repurchase_record.metric == "amount"
    assert "requires_official_announcement_text" in repurchase_record.quality_flags
    assert forecast_record.confidence == "low"
    assert forecast_record.supports == ("evidence_triage", "financial_analysis", "market_context")
    assert forecast_record.metric == "p_change_min,p_change_max,net_profit_min,net_profit_max,last_parent_net"
    assert forecast_record.source.source_name == "Tushare Pro forecast"
    assert "requires_official_announcement_text" in forecast_record.quality_flags
    assert "not_company_business_exposure" in forecast_record.quality_flags


def test_evidence_source_registry_and_fetcher_ingest_http_json(tmp_path):
    spec = EvidenceSourceSpec(
        source_id="example.industry_output",
        title="Example official industry output API",
        source_type="official",
        source_name="Example Statistics Office",
        source_url="https://example.gov/api/output",
        topic="industry_output",
        claim_template="{industry} {period} {metric} is {value}{unit}.",
        params={"code": "solar"},
        records_path="data.items",
        field_map={
            "industry": "industry",
            "metric": "metric",
            "value": "value",
            "unit": "unit",
            "period": "period",
            "published_at": "published_at",
            "source_url": "url",
            "query_time": "queried_at",
        },
        market_scope="cn_ashare",
        confidence="high",
        verification="official_json_api",
        supports=("evidence", "industry_validation"),
    )
    registry = EvidenceSourceRegistry(tmp_path)
    registry.add(spec)

    def fake_get(url, params, headers, timeout):
        assert url == "https://example.gov/api/output"
        assert params == {"code": "solar", "month": "2026-06"}
        assert headers == {}
        assert timeout == 20
        payload = {
            "data": {
                "items": [
                    {
                        "industry": "光伏",
                        "metric": "output",
                        "value": 120.5,
                        "unit": "GW",
                        "period": "202606",
                        "published_at": "2026-06-25",
                        "queried_at": "2026-06-26T18:00:00+08:00",
                        "url": "https://example.gov/report/solar-202606",
                    }
                ]
            }
        }
        return HttpResponse(200, url, json.dumps(payload), {})

    result = EvidenceSourceFetcher(
        evidence_store=EvidenceStore(tmp_path),
        source_registry=registry,
        get_transport=fake_get,
    ).fetch("example.industry_output", params={"month": "2026-06"})

    records = EvidenceStore(tmp_path).search(topic="industry_output", industry="光伏")
    assert result.inserted == 1
    assert registry.require("example.industry_output").title == "Example official industry output API"
    assert records[0].claim == "光伏 202606 output is 120.5GW."
    assert records[0].source.source_name == "Example Statistics Office"
    assert records[0].source.source_url == "https://example.gov/report/solar-202606"
    assert records[0].supports == ("evidence", "industry_validation")


def test_evidence_profile_and_source_candidates_group_recurring_numerical_records(tmp_path):
    store = EvidenceStore(tmp_path)
    store.ingest(
        [
            {
                "claim": f"Example Exchange reports lithium carbonate price index {period}: {value}.",
                "topic": "commodity_price",
                "market_scope": "cn_ashare",
                "industry": "新能源",
                "metric": "lithium_carbonate_price",
                "value": value,
                "unit": "CNY/t",
                "period": period,
                "source": {
                    "source_type": "price_index",
                    "source_name": "Example Exchange",
                    "source_url": "https://example.com/price-index",
                    "published_at": period,
                    "query_time": "2026-06-26T18:00:00+08:00",
                },
                "confidence": "high",
                "verification": "official_price_index",
                "supports": ["evidence", "market_context"],
            }
            for period, value in (("20260622", 100.0), ("20260623", 101.0), ("20260624", 102.0))
        ]
    )

    profiler = EvidenceProfiler(store)
    profile = profiler.profile(topic="commodity_price")
    candidates = profiler.source_candidates(min_records=3)

    assert profile["records"] == 3
    assert profile["unique_counts"]["sources"] == 1
    assert profile["period_range"] == {"min": "20260622", "max": "20260624"}
    assert profile["metrics"][0]["metric"] == "lithium_carbonate_price"
    assert len(candidates) == 1
    assert candidates[0]["schema"] == "rdf.evidence_source_candidate.v1"
    assert candidates[0]["records"] == 3
    assert candidates[0]["period_count"] == 3
    assert candidates[0]["source_name"] == "Example Exchange"


def test_rdf_cli_manages_reusable_evidence_sources(capsys, tmp_path):
    source_path = tmp_path / "source.json"
    source_path.write_text(
        json.dumps(
            {
                "source_id": "example.price_index",
                "title": "Example price index",
                "source_type": "price_index",
                "source_name": "Example Exchange",
                "source_url": "https://example.com/index.json",
                "topic": "commodity_price",
                "claim_template": "{product} {period} price index is {value}.",
                "published_at": "2026-06-25",
                "field_map": {"product": "product", "period": "period", "value": "value"},
                "confidence": "medium",
                "verification": "registered_price_index_api",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    add_exit = main(["--data-dir", str(tmp_path), "evidence", "sources", "add", str(source_path)])
    add_payload = json.loads(capsys.readouterr().out)
    list_exit = main(["--data-dir", str(tmp_path), "evidence", "sources", "list"])
    list_payload = json.loads(capsys.readouterr().out)
    show_exit = main(["--data-dir", str(tmp_path), "evidence", "sources", "show", "example.price_index"])
    show_payload = json.loads(capsys.readouterr().out)

    assert add_exit == 0
    assert add_payload["sources"] == ["example.price_index"]
    assert list_exit == 0
    assert [item["source_id"] for item in list_payload] == ["example.price_index"]
    assert show_exit == 0
    assert show_payload["verification"] == "registered_price_index_api"


def test_rdf_cli_profiles_evidence_and_lists_source_candidates(capsys, tmp_path):
    EvidenceStore(tmp_path).ingest(
        [
            {
                "claim": f"Example Exchange reports silicon price index {period}: {value}.",
                "topic": "commodity_price",
                "market_scope": "cn_ashare",
                "industry": "光伏",
                "metric": "silicon_price",
                "value": value,
                "unit": "CNY/kg",
                "period": period,
                "source": {
                    "source_type": "price_index",
                    "source_name": "Example Exchange",
                    "source_url": "https://example.com/silicon",
                    "published_at": period,
                    "query_time": "2026-06-26T18:00:00+08:00",
                },
                "confidence": "medium",
                "verification": "registered_price_index_api",
                "supports": ["evidence"],
            }
            for period, value in (("20260622", 10.0), ("20260623", 11.0))
        ]
    )

    profile_exit = main(["--data-dir", str(tmp_path), "evidence", "profile", "--topic", "commodity_price"])
    profile_payload = json.loads(capsys.readouterr().out)
    candidates_exit = main(
        [
            "--data-dir",
            str(tmp_path),
            "evidence",
            "source-candidates",
            "--topic",
            "commodity_price",
            "--min-records",
            "2",
        ]
    )
    candidates_payload = json.loads(capsys.readouterr().out)

    assert profile_exit == 0
    assert profile_payload["schema"] == "rdf.evidence_profile.v1"
    assert profile_payload["records"] == 2
    assert candidates_exit == 0
    assert candidates_payload[0]["metric"] == "silicon_price"
    assert candidates_payload[0]["records"] == 2


def test_rdf_cli_exports_evidence_and_relation_snapshot(capsys, tmp_path):
    evidence_store = EvidenceStore(tmp_path)
    evidence_store.ingest(
        [
            {
                "claim": "Tushare income_statement reports 000001.SZ period 20251231: n_income=100.0.",
                "topic": "income_statement",
                "dataset_id": "ashare.income_statement",
                "market_scope": "cn_ashare",
                "company": "000001.SZ",
                "metric": "n_income",
                "period": "20251231",
                "source": {
                    "source_type": "vendor",
                    "source_name": "Tushare Pro income_statement",
                    "source_url": "https://tushare.pro/",
                    "published_at": "20260320",
                    "query_time": "2026-06-26T18:00:00+08:00",
                },
                "confidence": "medium",
                "verification": "vendor_financial_statement_interface",
                "supports": ["evidence", "financial_analysis"],
            }
        ]
    )
    RelationStore(tmp_path).ingest(
        [
            RelationRecord(
                subject=EntityRef("security", "ashare:security:000001.SZ", "平安银行"),
                predicate="has_filing_id",
                object=EntityRef("filing_entity", "cninfo:announcement:1219999999", "2025年年度报告"),
                confidence="high",
                source=RelationSource(raw_ref="raw/cninfo/announcements/example"),
                claim="CNINFO records 平安银行 annual report.",
                valid_from="20260624",
            )
        ]
    )

    evidence_path = tmp_path / "exports" / "evidence.jsonl"
    evidence_exit = main(
        [
            "--data-dir",
            str(tmp_path),
            "evidence",
            "export",
            str(evidence_path),
            "--company",
            "000001.SZ",
            "--period",
            "20251231",
        ]
    )
    evidence_payload = json.loads(capsys.readouterr().out)
    snapshot_path = tmp_path / "exports" / "relations.json"
    snapshot_exit = main(
        [
            "--data-dir",
            str(tmp_path),
            "relations",
            "snapshot",
            "--subject",
            "000001.SZ",
            "--output",
            str(snapshot_path),
        ]
    )
    snapshot_payload = json.loads(capsys.readouterr().out)

    assert evidence_exit == 0
    assert evidence_payload["records"] == 1
    assert "income_statement" in evidence_path.read_text(encoding="utf-8")
    assert snapshot_exit == 0
    assert snapshot_payload["record_count"] == 1
    assert snapshot_payload["path"] == str(snapshot_path)
    assert "000001.sz" in snapshot_payload["alias_index"]


def test_relation_store_ingests_curated_relations_with_evidence_refs(tmp_path):
    evidence_store = EvidenceStore(tmp_path)
    evidence_result = evidence_store.ingest(
        [
            {
                "claim": "SEC EDGAR records CIK 0000320193 filing form 10-Q with accession 0000320193-26-000010.",
                "topic": "sec_filing",
                "dataset_id": "global.sec_filings",
                "market_scope": "us",
                "company": "CIK 0000320193",
                "source": {
                    "source_type": "regulator",
                    "source_name": "SEC EDGAR",
                    "source_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019326000010/aapl.htm",
                    "published_at": "2026-04-30",
                    "query_time": "2026-06-26T18:00:00+08:00",
                },
                "confidence": "high",
                "verification": "official_filing_index",
                "supports": ["evidence", "cross_market_context"],
            },
            {
                "claim": "Eastmoney report index lists industry report 'AI算力行业跟踪' for IT服务.",
                "topic": "industry_report",
                "dataset_id": "industry.eastmoney_report_index",
                "market_scope": "cn_ashare",
                "industry": "IT服务",
                "source": {
                    "source_type": "research_report",
                    "source_name": "示例证券",
                    "source_url": "https://pdf.dfcfw.com/pdf/H3_AP202606260001_1.pdf",
                    "published_at": "2026-06-26",
                    "query_time": "2026-06-26T18:00:00+08:00",
                },
                "confidence": "low",
                "verification": "vendor_report_index",
                "supports": ["evidence", "context"],
                "quality_flags": ["report_is_not_company_disclosure"],
            },
        ]
    )

    sec_evidence_id, report_evidence_id = evidence_result.evidence_ids
    relations = [
        {
            "subject": {"entity_type": "issuer", "entity_id": "cik:0000320193", "name": "CIK 0000320193", "market_scope": "us"},
            "predicate": "has_filing_id",
            "object": {
                "entity_type": "filing_entity",
                "entity_id": "sec:accession:0000320193-26-000010",
                "name": "SEC filing 10-Q 0000320193-26-000010",
                "market_scope": "us",
            },
            "confidence": "high",
            "source": {"evidence_id": sec_evidence_id},
            "claim": "SEC EDGAR records CIK 0000320193 filing form 10-Q with accession 0000320193-26-000010.",
            "market_scope": "us",
            "valid_from": "2026-04-30",
            "tags": ["sec_edgar", "curated"],
        },
        {
            "subject": {
                "entity_type": "evidence_source_group",
                "entity_id": "eastmoney:industry_reports",
                "name": "Eastmoney industry report index",
                "market_scope": "cn_ashare",
            },
            "predicate": "preferred_source_for",
            "object": {"entity_type": "industry", "entity_id": "industry:it服务", "name": "IT服务", "market_scope": "cn_ashare"},
            "confidence": "low",
            "source": {"evidence_id": report_evidence_id},
            "claim": "Eastmoney report index can provide low-confidence context for IT服务 industry research.",
            "market_scope": "cn_ashare",
            "valid_from": "2026-06-26",
            "tags": ["eastmoney", "research_report", "curated"],
            "quality_flags": ["report_is_not_company_disclosure"],
        },
    ]
    store = RelationStore(tmp_path)
    result = store.ingest(relations)
    duplicate = store.ingest(relations[:1])

    assert result.inserted == 2
    assert duplicate.skipped_duplicates == 1
    sec_relation = store.search(predicate="has_filing_id")[0]
    assert sec_relation.subject.entity_id == "cik:0000320193"
    assert sec_relation.object.entity_id == "sec:accession:0000320193-26-000010"
    assert sec_relation.source.evidence_id
    industry_relation = store.search(predicate="preferred_source_for", object="IT服务")[0]
    assert industry_relation.object.entity_type == "industry"
    assert "report_is_not_company_disclosure" in industry_relation.quality_flags
    snapshot = store.snapshot(predicate="preferred_source_for", object="IT服务")
    assert snapshot["schema"] == "rdf.relation_snapshot.v1"
    assert snapshot["record_count"] == 1
    assert "it服务" in snapshot["alias_index"]


def test_relation_profile_and_neighborhood_summarize_curated_graph(tmp_path):
    store = RelationStore(tmp_path)
    store.ingest(
        [
            RelationRecord(
                subject=EntityRef("company", "company:a", "示例公司A", market_scope="cn_ashare"),
                predicate="has_product_exposure",
                object=EntityRef("product", "product:battery", "电池", market_scope="cn_ashare"),
                confidence="high",
                source=RelationSource(
                    source_name="CNINFO",
                    source_url="https://example.com/a.pdf",
                    published_at="20260624",
                    query_time="2026-06-26T18:00:00+08:00",
                ),
                claim="示例公司A生产电池。",
                valid_from="20260624",
                tags=("curated", "product_exposure"),
            ),
            RelationRecord(
                subject=EntityRef("product", "product:battery", "电池", market_scope="cn_ashare"),
                predicate="supplies_to",
                object=EntityRef("industry_chain_node", "node:ev", "新能源汽车", market_scope="cn_ashare"),
                confidence="medium",
                source=RelationSource(raw_ref="runs/example"),
                claim="电池用于新能源汽车产业链。",
                valid_from="20260624",
                tags=("curated", "industry_chain"),
            ),
        ]
    )

    profiler = RelationProfiler(store)
    profile = profiler.profile(limit=10)
    neighborhood = profiler.neighborhood(entity="battery", limit=10)

    assert profile["schema"] == "rdf.relation_profile.v1"
    assert profile["records"] == 2
    assert profile["unique_counts"]["entities"] == 3
    assert {row["predicate"] for row in profile["predicates"]} == {"has_product_exposure", "supplies_to"}
    assert neighborhood["schema"] == "rdf.relation_neighborhood.v1"
    assert neighborhood["incoming_count"] == 1
    assert neighborhood["outgoing_count"] == 1
    assert neighborhood["entities"][0]["entity_id"] == "product:battery"


def test_rdf_cli_profiles_relation_graph_and_reads_neighborhood(capsys, tmp_path):
    RelationStore(tmp_path).ingest(
        [
            RelationRecord(
                subject=EntityRef("company", "company:a", "示例公司A", market_scope="cn_ashare"),
                predicate="has_product_exposure",
                object=EntityRef("product", "product:battery", "电池", market_scope="cn_ashare"),
                confidence="high",
                source=RelationSource(raw_ref="runs/example"),
                claim="示例公司A生产电池。",
                valid_from="20260624",
                tags=("curated",),
            )
        ]
    )

    profile_exit = main(["--data-dir", str(tmp_path), "relations", "profile", "--predicate", "has_product_exposure"])
    profile_payload = json.loads(capsys.readouterr().out)
    neighborhood_exit = main(["--data-dir", str(tmp_path), "relations", "neighborhood", "--entity", "battery"])
    neighborhood_payload = json.loads(capsys.readouterr().out)

    assert profile_exit == 0
    assert profile_payload["records"] == 1
    assert profile_payload["predicates"][0]["predicate"] == "has_product_exposure"
    assert neighborhood_exit == 0
    assert neighborhood_payload["records"] == 1
    assert neighborhood_payload["incoming"][0]["subject"]["entity_id"] == "company:a"


def test_main_business_and_announcement_tables_build_evidence_without_relations(tmp_path):
    registry = default_registry()
    mart = MartStore(tmp_path, registry)
    mart.publish(
        "ashare.main_business",
        pd.DataFrame(
            [
                {
                    "period": "20251231",
                    "security_id": "000001.SZ",
                    "segment_type": "P",
                    "item_name": "个人贷款业务",
                    "sales": 100.0,
                    "gross_profit": 20.0,
                    "cost": 80.0,
                    "currency": "CNY",
                }
            ]
        ),
        partition={"period": "20251231", "security_id": "000001.SZ", "segment_type": "P"},
        lineage={"source_id": "tushare", "raw_path": "raw/tushare/fina_mainbz/example"},
    )
    mart.publish(
        "ashare.announcements",
        pd.DataFrame(
            [
                {
                    "publish_date": "20260624",
                    "publish_time": "2026-06-24T08:00:00+08:00",
                    "announcement_id": "1219999999",
                    "security_code": "000001",
                    "security_id": "000001.SZ",
                    "security_name": "平安银行",
                    "org_id": "gssz0000001",
                    "title": "2025年年度报告",
                    "short_title": "2025年年度报告",
                    "announcement_type": "010301",
                    "announcement_type_name": "",
                    "column_id": "09020202",
                    "page_column": "SZSE",
                    "adjunct_url": "finalpage/2026-06-24/1219999999.PDF",
                    "source_url": "https://static.cninfo.com.cn/finalpage/2026-06-24/1219999999.PDF",
                    "adjunct_type": "PDF",
                    "adjunct_size": 1024,
                }
            ]
        ),
        partition={"publish_date": "20260624"},
        lineage={"source_id": "cninfo", "raw_path": "raw/cninfo/announcements/example"},
    )

    main_frame = mart.read("ashare.main_business", {"period": "20251231", "security_id": "000001.SZ", "segment_type": "P"})
    main_meta = mart.read_meta("ashare.main_business", {"period": "20251231", "security_id": "000001.SZ", "segment_type": "P"})
    announcement_frame = mart.read("ashare.announcements", {"publish_date": "20260624"})
    announcement_meta = mart.read_meta("ashare.announcements", {"publish_date": "20260624"})

    evidence = evidence_from_table("ashare.main_business", main_frame, meta=main_meta) + evidence_from_table(
        "ashare.announcements",
        announcement_frame,
        meta=announcement_meta,
    )
    evidence_result = EvidenceStore(tmp_path).ingest(evidence)

    assert evidence_result.inserted == 2
    main_evidence = EvidenceStore(tmp_path).search(topic="main_business_segment")[0]
    assert main_evidence.supports == ("evidence", "company_business_exposure")
    assert "requires_official_report_cross_check" in main_evidence.quality_flags
    announcement_evidence = EvidenceStore(tmp_path).search(topic="company_announcement")[0]
    assert announcement_evidence.source.source_name == "CNINFO"
    assert "pdf_not_parsed" in announcement_evidence.quality_flags
    assert RelationStore(tmp_path).read_records() == []


def test_announcement_text_table_builds_high_confidence_evidence(tmp_path):
    registry = default_registry()
    partition = {"publish_date": "20260624", "announcement_id": "1219999999"}
    mart = MartStore(tmp_path, registry)
    mart.publish(
        "ashare.announcement_text",
        pd.DataFrame(
            [
                {
                    "publish_date": "20260624",
                    "announcement_id": "1219999999",
                    "security_id": "000001.SZ",
                    "security_name": "平安银行",
                    "title": "2025年年度报告",
                    "source_url": "https://static.cninfo.com.cn/finalpage/2026-06-24/1219999999.PDF",
                    "pdf_sha256": "abc",
                    "pdf_bytes": 12,
                    "text": "年度报告正文",
                    "text_length": 6,
                    "page_count": 1,
                    "parse_status": "ok",
                    "parse_message": "",
                }
            ]
        ),
        partition=partition,
        lineage={"source_id": "cninfo", "raw_path": "raw/cninfo/announcement_pdf_text/example"},
    )

    records = evidence_from_table("ashare.announcement_text", mart.read("ashare.announcement_text", partition), meta=mart.read_meta("ashare.announcement_text", partition))
    result = EvidenceStore(tmp_path).ingest(records)

    assert result.inserted == 1
    record = EvidenceStore(tmp_path).search(topic="company_announcement_text")[0]
    assert record.confidence == "high"
    assert record.source.source_type == "company_filing"
    assert record.supports == ("evidence", "context", "company_business_exposure")
    assert "raw_filing_text_requires_claim_extraction" in record.quality_flags


def test_announcement_text_snippet_candidates_locate_claim_context(tmp_path):
    registry = default_registry()
    partition = {"publish_date": "20260624", "announcement_id": "1219999999"}
    mart = MartStore(tmp_path, registry)
    mart.publish(
        "ashare.announcement_text",
        pd.DataFrame(
            [
                {
                    "publish_date": "20260624",
                    "announcement_id": "1219999999",
                    "security_id": "000001.SZ",
                    "security_name": "平安银行",
                    "title": "2025年年度报告",
                    "source_url": "https://static.cninfo.com.cn/finalpage/2026-06-24/1219999999.PDF",
                    "pdf_sha256": "abc",
                    "pdf_bytes": 12,
                    "text": "公司主营业务包括AI算力基础设施服务，并在报告期内形成收入。",
                    "text_length": 30,
                    "page_count": 1,
                    "parse_status": "ok",
                    "parse_message": "",
                }
            ]
        ),
        partition=partition,
        lineage={"source_id": "cninfo", "raw_path": "raw/cninfo/announcement_pdf_text/example"},
    )

    payload = announcement_text_snippet_candidates(
        mart.read("ashare.announcement_text", partition),
        meta=mart.read_meta("ashare.announcement_text", partition),
        query="AI算力",
        context_chars=6,
    )

    assert payload["schema"] == "rdf.announcement_text_snippet_candidates.v1"
    assert payload["ingested"] is False
    assert payload["records_total"] == 1
    record = payload["records"][0]
    assert record["announcement_id"] == "1219999999"
    assert record["security_id"] == "000001.SZ"
    assert record["match_text"] == "AI算力"
    assert "主营业务包括AI算力基础设施" in record["snippet"]
    assert record["source"]["source_type"] == "company_filing"
    assert "snippet_requires_claim_confirmation" in record["quality_flags"]
    assert EvidenceStore(tmp_path).search() == []


def test_rdf_cli_locates_announcement_text_snippets_without_ingesting(capsys, tmp_path):
    registry = default_registry()
    partition = {"publish_date": "20260624", "announcement_id": "1219999999"}
    MartStore(tmp_path, registry).publish(
        "ashare.announcement_text",
        pd.DataFrame(
            [
                {
                    "publish_date": "20260624",
                    "announcement_id": "1219999999",
                    "security_id": "000001.SZ",
                    "security_name": "平安银行",
                    "title": "2025年年度报告",
                    "source_url": "https://static.cninfo.com.cn/finalpage/2026-06-24/1219999999.PDF",
                    "pdf_sha256": "abc",
                    "pdf_bytes": 12,
                    "text": "公司主营业务包括AI算力基础设施服务，并在报告期内形成收入。",
                    "text_length": 30,
                    "page_count": 1,
                    "parse_status": "ok",
                    "parse_message": "",
                }
            ]
        ),
        partition=partition,
        lineage={"source_id": "cninfo", "raw_path": "raw/cninfo/announcement_pdf_text/example"},
    )

    exit_code = main(
        [
            "--data-dir",
            str(tmp_path),
            "evidence",
            "from-announcement-text",
            "--partition",
            "publish_date=20260624",
            "--partition",
            "announcement_id=1219999999",
            "--query",
            "AI算力",
            "--context-chars",
            "6",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["schema"] == "rdf.announcement_text_snippet_candidates.v1"
    assert payload["ingested"] is False
    assert payload["records"][0]["match_text"] == "AI算力"
    assert EvidenceStore(tmp_path).search() == []


def test_rdf_cli_runs_ingestion_recipe_with_fake_runner(monkeypatch, capsys, tmp_path):
    import importlib

    cli_module = importlib.import_module("research_data_foundation.cli.main")

    class FakeResult:
        def to_dict(self):
            return {"schema": "rdf.ingestion_result.v1", "dataset_id": "ashare.daily", "rows": 1}

    class FakeRunner:
        def __init__(self, *, data_dir, registry):
            self.data_dir = data_dir
            self.registry = registry

        def run_recipe(self, recipe_id, *, partition, params, refresh):
            assert recipe_id == "tushare.daily.to_ashare_daily"
            assert partition == {"trade_date": "20260626"}
            assert params == {}
            assert refresh is True
            return FakeResult()

    monkeypatch.setattr(cli_module, "IngestionRunner", FakeRunner)

    exit_code = cli_module.main(
        [
            "--data-dir",
            str(tmp_path),
            "ingest",
            "recipe",
            "tushare.daily.to_ashare_daily",
            "--partition",
            "trade_date=20260626",
            "--refresh",
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["dataset_id"] == "ashare.daily"


def test_rdf_cli_runs_ingest_dataset_alias_with_target_check(monkeypatch, capsys, tmp_path):
    import importlib

    cli_module = importlib.import_module("research_data_foundation.cli.main")

    class FakeResult:
        def to_dict(self):
            return {"schema": "rdf.ingestion_result.v1", "dataset_id": "ashare.daily", "rows": 1}

    class FakeRunner:
        def __init__(self, *, data_dir, registry):
            self.data_dir = data_dir
            self.registry = registry

        def run_recipe(self, recipe_id, *, partition, params, refresh):
            assert recipe_id == "tushare.daily.to_ashare_daily"
            assert partition == {"trade_date": "20260626"}
            assert params == {}
            assert refresh is False
            return FakeResult()

    monkeypatch.setattr(cli_module, "IngestionRunner", FakeRunner)

    exit_code = cli_module.main(
        [
            "--data-dir",
            str(tmp_path),
            "ingest",
            "dataset",
            "ashare.daily",
            "--recipe",
            "tushare.daily.to_ashare_daily",
            "--partition",
            "trade_date=20260626",
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["dataset_id"] == "ashare.daily"


def test_rdf_cli_runs_pipeline_with_fake_runner(monkeypatch, capsys, tmp_path):
    import importlib

    cli_module = importlib.import_module("research_data_foundation.cli.main")

    class FakeResult:
        def to_dict(self):
            return {"schema": "rdf.pipeline_run_result.v1", "pipeline_id": "global_reference_weekly", "rows": 1}

    class FakeRunner:
        def __init__(self, *, data_dir, registry):
            self.data_dir = data_dir
            self.registry = registry

        def run_pipeline(self, pipeline_id, *, partition, params, refresh, continue_on_error):
            assert pipeline_id == "global_reference_weekly"
            assert partition == {"cik": "0000320193"}
            assert params == {}
            assert refresh is False
            assert continue_on_error is True
            return FakeResult()

    monkeypatch.setattr(cli_module, "IngestionRunner", FakeRunner)

    exit_code = cli_module.main(
        [
            "--data-dir",
            str(tmp_path),
            "ingest",
            "pipeline",
            "global_reference_weekly",
            "--partition",
            "cik=0000320193",
            "--continue-on-error",
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["pipeline_id"] == "global_reference_weekly"


def test_rdf_cli_prints_ingestion_recipe_dry_run_plan(capsys, tmp_path):
    exit_code = main(
        [
            "--data-dir",
            str(tmp_path),
            "ingest",
            "recipe",
            "sec_edgar.submissions.to_global_sec_filings",
            "--partition",
            "cik=0000320193",
            "--dry-run",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["schema"] == "rdf.ingestion_plan.v1"
    assert payload["recipe_id"] == "sec_edgar.submissions.to_global_sec_filings"
    assert payload["dataset_id"] == "global.sec_filings"
    assert payload["will_fetch"] is False
    assert payload["will_write"] is False
    assert not (tmp_path / "raw").exists()
    assert not (tmp_path / "mart").exists()


def test_rdf_cli_prints_pipeline_dry_run_plan(capsys, tmp_path):
    exit_code = main(
        [
            "--data-dir",
            str(tmp_path),
            "ingest",
            "pipeline",
            "global_reference_weekly",
            "--partition",
            "cik=0000320193",
            "--continue-on-error",
            "--dry-run",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["schema"] == "rdf.pipeline_ingestion_plan.v1"
    assert payload["pipeline_id"] == "global_reference_weekly"
    assert payload["continue_on_error"] is True
    usage = payload["steps"][0]["plan"]["dataset"]["usage"]
    assert "candidate_generation" in usage["forbidden_uses"]
    assert "trade_execution" not in usage["allowed_uses"]
    assert not (tmp_path / "raw").exists()
    assert not (tmp_path / "mart").exists()


def test_rdf_cli_builds_evidence_from_mart_without_relations(capsys, tmp_path):
    registry = default_registry()
    MartStore(tmp_path, registry).publish(
        "global.sec_filings",
        pd.DataFrame(
            [
                {
                    "cik": "0000320193",
                    "accession_number": "0000320193-26-000010",
                    "form": "10-Q",
                    "filing_date": "2026-04-30",
                    "primary_document": "aapl-20260328.htm",
                    "source_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019326000010/aapl-20260328.htm",
                }
            ]
        ),
        partition={"cik": "0000320193"},
        lineage={"source_id": "sec_edgar"},
    )

    evidence_exit = main(
        [
            "--data-dir",
            str(tmp_path),
            "evidence",
            "from-dataset",
            "global.sec_filings",
            "--partition",
            "cik=0000320193",
        ]
    )
    evidence_payload = json.loads(capsys.readouterr().out)

    assert evidence_exit == 0
    assert evidence_payload["inserted"] == 1
    assert evidence_payload["evidence_ids_total"] == 1
    assert EvidenceStore(tmp_path).search(topic="sec_filing")[0].dataset_id == "global.sec_filings"
    assert RelationStore(tmp_path).search(predicate="has_filing_id") == []


def test_rdf_cli_builds_triage_evidence_from_corporate_action_events(capsys, tmp_path):
    registry = default_registry()
    MartStore(tmp_path, registry).publish(
        "ashare.repurchase_events",
        pd.DataFrame(
            [
                {
                    "ann_date": "20260624",
                    "security_id": "000001.SZ",
                    "end_date": "20261231",
                    "process_status": "实施中",
                    "expected_end_date": "20261231",
                    "volume": 1000000.0,
                    "amount": 12000000.0,
                    "high_limit": 13.0,
                    "low_limit": 10.0,
                }
            ]
        ),
        partition={"ann_date": "20260624"},
        lineage={"source_id": "tushare"},
    )
    MartStore(tmp_path, registry).publish(
        "ashare.earnings_forecast_events",
        pd.DataFrame(
            [
                {
                    "ann_date": "20260624",
                    "security_id": "000001.SZ",
                    "period": "20260630",
                    "forecast_type": "预增",
                    "p_change_min": 45.5,
                    "p_change_max": 65.0,
                    "net_profit_min": 120000000.0,
                    "net_profit_max": 135000000.0,
                    "last_parent_net": 82000000.0,
                    "first_ann_date": "20260624",
                    "forecast_summary": "预计净利润增长",
                    "change_reason": "主营业务收入增长",
                }
            ]
        ),
        partition={"ann_date": "20260624"},
        lineage={"source_id": "tushare"},
    )

    evidence_exit = main(
        [
            "--data-dir",
            str(tmp_path),
            "evidence",
            "from-dataset",
            "ashare.repurchase_events",
            "--partition",
            "ann_date=20260624",
        ]
    )
    evidence_payload = json.loads(capsys.readouterr().out)

    assert evidence_exit == 0
    assert evidence_payload["inserted"] == 1
    record = EvidenceStore(tmp_path).search(topic="share_repurchase_event")[0]
    assert record.supports == ("evidence_triage", "market_context")
    assert record.confidence == "low"
    assert "requires_official_announcement_text" in record.quality_flags

    forecast_exit = main(
        [
            "--data-dir",
            str(tmp_path),
            "evidence",
            "from-dataset",
            "ashare.earnings_forecast_events",
            "--partition",
            "ann_date=20260624",
        ]
    )
    forecast_payload = json.loads(capsys.readouterr().out)

    assert forecast_exit == 0
    assert forecast_payload["inserted"] == 1
    forecast_record = EvidenceStore(tmp_path).search(topic="earnings_forecast_event")[0]
    assert forecast_record.supports == ("evidence_triage", "financial_analysis", "market_context")
    assert forecast_record.confidence == "low"


def test_rdf_cli_ingests_manual_evidence_and_relations(capsys, tmp_path):
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(
        json.dumps(
            {
                "claim": "SEC EDGAR records CIK 0000320193 filing form 10-Q with accession 0000320193-26-000010.",
                "topic": "sec_filing",
                "source": {
                    "source_type": "regulator",
                    "source_name": "SEC EDGAR",
                    "source_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019326000010/aapl.htm",
                    "published_at": "2026-04-30",
                    "query_time": "2026-06-26T18:00:00+08:00",
                },
                "confidence": "high",
                "verification": "official_filing_index",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    evidence_exit = main(["--data-dir", str(tmp_path), "evidence", "ingest", str(evidence_path)])
    evidence_payload = json.loads(capsys.readouterr().out)

    assert evidence_exit == 0
    assert evidence_payload["inserted"] == 1

    taxonomy_exit = main(["relations", "taxonomy"])
    taxonomy_payload = json.loads(capsys.readouterr().out)

    assert taxonomy_exit == 0
    assert "has_filing_id" in taxonomy_payload["predicates"]

    relation_path = tmp_path / "relation.json"
    relation_path.write_text(
        json.dumps(
            {
                "subject": {"entity_type": "issuer", "entity_id": "cik:0000320193", "name": "CIK 0000320193"},
                "predicate": "has_filing_id",
                "object": {
                    "entity_type": "filing_entity",
                    "entity_id": "sec:accession:0000320193-26-000010",
                    "name": "SEC filing 10-Q 0000320193-26-000010",
                },
                "confidence": "high",
                "source": {"evidence_id": evidence_payload["evidence_ids"][0]},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    relation_exit = main(["--data-dir", str(tmp_path), "relations", "ingest", str(relation_path)])
    relation_payload = json.loads(capsys.readouterr().out)

    assert relation_exit == 0
    assert relation_payload["inserted"] == 1


def test_rdf_cli_builds_and_reads_feature_partition(capsys, tmp_path):
    registry = default_registry()
    MartStore(tmp_path, registry).publish(
        "ashare.daily",
        pd.DataFrame(
            [
                {
                    "security_id": "000001.SZ",
                    "trade_date": "20260626",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.2,
                    "pct_chg": 2.0,
                    "volume": 1000.0,
                    "amount": 10000.0,
                }
            ]
        ),
        partition={"trade_date": "20260626"},
        lineage={"source_id": "tushare"},
    )

    build_exit = main(
        [
            "--data-dir",
            str(tmp_path),
            "features",
            "build",
            "ashare.daily_momentum",
            "--as-of",
            "20260626",
            "--window",
            "1",
        ]
    )
    build_payload = json.loads(capsys.readouterr().out)

    assert build_exit == 0
    assert build_payload["feature_id"] == "ashare.daily_momentum"
    assert build_payload["rows"] == 1

    meta_exit = main(
        [
            "--data-dir",
            str(tmp_path),
            "features",
            "meta",
            "ashare.daily_momentum",
            "--as-of",
            "20260626",
            "--window",
            "1",
        ]
    )
    meta_payload = json.loads(capsys.readouterr().out)

    assert meta_exit == 0
    assert meta_payload["quality"]["status"] == "ok"

    read_exit = main(
        [
            "--data-dir",
            str(tmp_path),
            "features",
            "read",
            "ashare.daily_momentum",
            "--as-of",
            "20260626",
            "--window",
            "1",
        ]
    )
    read_payload = json.loads(capsys.readouterr().out)

    assert read_exit == 0
    assert read_payload["schema"] == "rdf.feature_read.v1"
    assert read_payload["feature_id"] == "ashare.daily_momentum"
    assert read_payload["partition"] == {"as_of": "20260626", "window": "1"}
    assert read_payload["quality"]["status"] == "ok"
    assert read_payload["inputs"][0]["dataset_id"] == "ashare.daily"
    assert "company business exposure" in read_payload["boundary"]
    assert read_payload["records"][0]["security_id"] == "000001.SZ"
    assert read_payload["records"][0]["finality"] == "final"


def test_rdf_cli_records_and_replays_run_trace(capsys, tmp_path):
    _write_run_trace_support(tmp_path)
    model_output = tmp_path / "research.md"
    model_output.write_text("validated research output", encoding="utf-8")
    validated_output = tmp_path / "validated.json"
    validated_output.write_text(
        json.dumps({"schema": "rdf.research_output.v1", "status": "validated"}, ensure_ascii=False),
        encoding="utf-8",
    )

    record_exit = main(
        [
            "--data-dir",
            str(tmp_path),
            "runs",
            "record",
            "--question",
            "AI 算力产业链研究",
            "--as-of",
            "20260626",
            "--mart-ref",
            "ashare.daily:trade_date=20260626",
            "--feature-ref",
            "ashare.daily_momentum:as_of=20260626,window=20",
            "--evidence-id",
            "ev-1",
            "--relation-id",
            "rel-1",
            "--model-output-file",
            str(model_output),
            "--validated-output",
            str(validated_output),
            "--run-id",
            "20260626-ai-compute",
        ]
    )
    record_payload = json.loads(capsys.readouterr().out)

    assert record_exit == 0
    assert record_payload["run_id"] == "20260626-ai-compute"
    assert record_payload["quality"]["status"] == "ok"
    assert (tmp_path / "runs" / "20260626-ai-compute" / "manifest.json").exists()

    show_exit = main(["--data-dir", str(tmp_path), "runs", "show", "20260626-ai-compute"])
    replay_payload = json.loads(capsys.readouterr().out)

    assert show_exit == 0
    assert replay_payload["schema"] == "rdf.run_replay.v1"
    assert replay_payload["refs"]["evidence_ids"] == ["ev-1"]
    assert replay_payload["quality"]["gates"]["data_refs_gate"]["status"] == "passed"


def test_run_recorder_quality_gates_pass_supported_output(tmp_path):
    _write_run_trace_support(tmp_path)
    validated_output = tmp_path / "supported.json"
    validated_output.write_text(json.dumps(_supported_run_output(), ensure_ascii=False), encoding="utf-8")

    record = RunRecorder(tmp_path).record(
        question="AI 算力产业链研究",
        as_of="20260626",
        mart_refs=("ashare.daily:trade_date=20260626",),
        feature_refs=("ashare.daily_momentum:as_of=20260626,window=20",),
        evidence_ids=("ev-1",),
        relation_ids=("rel-1",),
        validated_output_file=str(validated_output),
        run_id="supported-quality-run",
    )

    assert record.quality["status"] == "ok"
    assert record.quality["gates"]["source_gate"]["status"] == "passed"
    assert record.quality["gates"]["source_refs_gate"]["status"] == "passed"


def test_run_recorder_blocks_feature_only_company_exposure(tmp_path):
    _write_run_trace_support(tmp_path)
    validated_output = tmp_path / "feature_only.json"
    validated_output.write_text(
        json.dumps(_supported_run_output(exposure_source_kind="feature", source_id="ashare.daily_momentum:as_of=20260626,window=20"), ensure_ascii=False),
        encoding="utf-8",
    )

    record = RunRecorder(tmp_path).record(
        question="AI 算力产业链研究",
        as_of="20260626",
        mart_refs=("ashare.daily:trade_date=20260626",),
        feature_refs=("ashare.daily_momentum:as_of=20260626,window=20",),
        evidence_ids=("ev-1",),
        validated_output_file=str(validated_output),
        run_id="feature-only-quality-run",
    )

    assert record.quality["status"] == "blocked"
    assert record.quality["gates"]["source_gate"]["status"] == "blocked"
    assert record.quality["gates"]["source_gate"]["details"]["items"][0]["source_kinds"] == ["feature"]


def test_run_recorder_blocks_unrecorded_evidence_reference(tmp_path):
    _write_run_trace_support(tmp_path)
    validated_output = tmp_path / "unrecorded_evidence.json"
    validated_output.write_text(json.dumps(_supported_run_output(), ensure_ascii=False), encoding="utf-8")

    record = RunRecorder(tmp_path).record(
        question="AI 算力产业链研究",
        as_of="20260626",
        mart_refs=("ashare.daily:trade_date=20260626",),
        feature_refs=("ashare.daily_momentum:as_of=20260626,window=20",),
        validated_output_file=str(validated_output),
        run_id="unrecorded-evidence-quality-run",
    )

    assert record.quality["status"] == "blocked"
    assert record.quality["gates"]["source_gate"]["status"] == "blocked"
    assert record.quality["gates"]["source_gate"]["details"]["items"][0]["source_kinds"] == ["evidence"]


def test_run_recorder_blocks_missing_mart_partition(tmp_path):
    validated_output = tmp_path / "minimal.json"
    validated_output.write_text(json.dumps({"schema": "rdf.research_output.v1", "as_of": "20260626"}, ensure_ascii=False), encoding="utf-8")

    record = RunRecorder(tmp_path).record(
        question="AI 算力产业链研究",
        as_of="20260626",
        mart_refs=("ashare.daily:trade_date=20260626",),
        validated_output_file=str(validated_output),
        run_id="missing-mart-quality-run",
    )

    assert record.quality["status"] == "blocked"
    assert record.quality["gates"]["data_refs_gate"]["status"] == "blocked"


def _write_run_trace_support(data_dir):
    registry = default_registry()
    MartStore(data_dir, registry).publish(
        "ashare.daily",
        pd.DataFrame(
            [
                {
                    "security_id": "000001.SZ",
                    "trade_date": "20260626",
                    "open": 10.0,
                    "high": 10.8,
                    "low": 9.8,
                    "close": 10.5,
                    "pct_chg": 2.0,
                    "volume": 1000.0,
                    "amount": 10000.0,
                }
            ]
        ),
        partition={"trade_date": "20260626"},
        lineage={"source_id": "fixture", "recipe_id": "fixture.daily"},
        refresh=True,
    )
    spec = FeatureRegistry.builtin().require("ashare.daily_momentum")
    FeatureStore(data_dir).write_partition(
        spec,
        pd.DataFrame(
            [
                {
                    "as_of": "20260626",
                    "window": 20,
                    "security_id": "000001.SZ",
                    "momentum_score": 1.0,
                    "window_return_pct": 12.0,
                }
            ]
        ),
        as_of="20260626",
        window=20,
        inputs=[
            {
                "dataset_id": "ashare.daily",
                "status": "ok",
                "rows": 1,
                "columns": ["security_id", "trade_date", "pct_chg", "volume", "amount"],
            }
        ],
        refresh=True,
    )
    EvidenceStore(data_dir).ingest(
        [
            {
                "evidence_id": "ev-1",
                "claim": "公司披露 AI 算力相关产品收入。",
                "topic": "company_exposure",
                "company": "000001.SZ",
                "product": "ai_infra",
                "source": {
                    "source_type": "company_filing",
                    "source_name": "测试公司 2025 年年度报告",
                    "source_url": "https://example.com/annual-report",
                    "published_at": "2026-04-15",
                    "query_time": "2026-06-26T20:00:00+08:00",
                },
                "confidence": "high",
                "verification": "official_single_source",
                "supports": ["000001.SZ", "ai_infra"],
            }
        ]
    )
    RelationStore(data_dir).ingest(
        [
            RelationRecord(
                relation_id="rel-1",
                subject=EntityRef("security", "ashare:security:000001.SZ", "测试公司", "cn_ashare"),
                predicate="has_product_exposure",
                object=EntityRef("product", "product:ai_infra", "AI 算力产品"),
                confidence="high",
                source=RelationSource(evidence_id="ev-1"),
                claim="测试公司具有 AI 算力产品暴露。",
                market_scope="cn_ashare",
                valid_from="20260415",
                tags=("ai_infra",),
            )
        ]
    )


def _supported_run_output(*, exposure_source_kind="evidence", source_id="ev-1"):
    exposure_fact = {
        "source_kind": exposure_source_kind,
        "source_id": source_id,
        "claim": "公司披露 AI 算力相关产品收入。",
    }
    if exposure_source_kind == "evidence":
        exposure_fact["evidence_id"] = source_id
    if exposure_source_kind == "relations":
        exposure_fact["relation_id"] = source_id
    market_fact = {
        "source_kind": "mart",
        "source_id": "ashare.daily:trade_date=20260626",
        "claim": "A 股日线分区可用。",
    }
    return {
        "schema": "rdf.research_output.v1",
        "as_of": "20260626",
        "question": "AI 算力产业链研究",
        "theme_identification": {
            "summary": "市场存在 AI 算力链线索。",
            "facts": [market_fact],
            "confidence": "medium",
        },
        "company_mapping": [
            {
                "ts_code": "000001.SZ",
                "name": "测试公司",
                "segments": ["ai_infra"],
                "exposure_level": "direct",
                "exposure_evidence": [exposure_fact],
                "confidence": "medium",
            }
        ],
        "candidate_pool": [
            {
                "ts_code": "000001.SZ",
                "name": "测试公司",
                "priority": "high",
                "evidence_strength": "strong",
                "missing_evidence": [],
            }
        ],
        "evidence_matrix": [
            {
                "topic": "company_exposure",
                "claim": "公司披露 AI 算力相关产品收入。",
                "source_kind": "evidence",
                "evidence_id": "ev-1",
                "supports": ["000001.SZ", "ai_infra"],
                "verification": "official_single_source",
                "confidence": "high",
            }
        ],
        "data_gaps": [],
        "confidence": "medium",
    }


class FakeTushareMaintenanceAdapter:
    source_id = "tushare"

    def __init__(self):
        self.calls = []

    def fetch(self, api_name, params, fields=None):
        normalized_params = dict(params)
        self.calls.append((api_name, normalized_params))
        trade_date = str(normalized_params.get("trade_date", "20260626"))
        index_code = str(normalized_params.get("ts_code", "000001.SH"))
        frames = {
            "trade_cal": pd.DataFrame(
                [
                    {"exchange": "SSE", "cal_date": "20260525", "is_open": "1"},
                    {"exchange": "SSE", "cal_date": "20260623", "is_open": "1"},
                    {"exchange": "SSE", "cal_date": "20260624", "is_open": "1"},
                    {"exchange": "SSE", "cal_date": "20260625", "is_open": "0"},
                    {"exchange": "SSE", "cal_date": "20260626", "is_open": "1"},
                ]
            ),
            "stock_basic": pd.DataFrame(
                [
                    {
                        "ts_code": "000001.SZ",
                        "symbol": "000001",
                        "name": "平安银行",
                        "area": "深圳",
                        "industry": "银行",
                        "market": "主板",
                        "exchange": "SZSE",
                        "curr_type": "CNY",
                        "list_status": "L",
                        "list_date": "19910403",
                        "delist_date": "",
                        "is_hs": "S",
                        "fullname": "平安银行股份有限公司",
                        "enname": "Ping An Bank Co., Ltd.",
                        "cnspell": "PAYH",
                        "act_name": "无实际控制人",
                        "act_ent_type": "无",
                    }
                ]
            ),
            "daily": pd.DataFrame(
                [
                    {
                        "ts_code": "000001.SZ",
                        "trade_date": trade_date,
                        "open": 10.0,
                        "high": 10.5,
                        "low": 9.8,
                        "close": 10.2 if trade_date == "20260626" else 10.0,
                        "pct_chg": 2.0 if trade_date == "20260626" else 0.5,
                        "vol": 1000.0,
                        "amount": 10000.0,
                    }
                ]
            ),
            "daily_basic": pd.DataFrame(
                [
                    {
                        "ts_code": "000001.SZ",
                        "trade_date": trade_date,
                        "close": 10.2,
                        "turnover_rate": 1.2,
                        "volume_ratio": 1.1,
                        "total_mv": 100000.0,
                        "circ_mv": 80000.0,
                    }
                ]
            ),
            "adj_factor": pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": trade_date, "adj_factor": 1.0}]),
            "stk_limit": pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": trade_date, "up_limit": 11.78, "down_limit": 9.64}]),
            "index_daily": pd.DataFrame(
                [{"ts_code": index_code, "trade_date": trade_date, "close": 3000.0, "pct_chg": 1.0, "vol": 1.0, "amount": 2.0}]
            ),
            "index_dailybasic": pd.DataFrame([{"ts_code": "000001.SH", "trade_date": trade_date, "total_mv": 1.0}]),
            "sw_daily": pd.DataFrame([{"ts_code": "801010.SI", "trade_date": trade_date, "close": 100.0, "pct_change": 1.0, "vol": 100.0}]),
            "ci_daily": pd.DataFrame([{"ts_code": "CI005001.CI", "trade_date": trade_date, "close": 100.0, "pct_change": 1.0, "vol": 100.0}]),
            "dc_index": pd.DataFrame([{"ts_code": "BK001", "trade_date": trade_date, "name": "AI算力", "pct_change": 2.0}]),
            "limit_list_d": pd.DataFrame(
                [{"ts_code": "000001.SZ", "trade_date": trade_date, "name": "平安银行", "close": 10.2, "pct_chg": 10.0, "limit": "U"}]
            ),
            "limit_list_ths": pd.DataFrame(
                [
                    {
                        "trade_date": trade_date,
                        "ts_code": "000001.SZ",
                        "name": "平安银行",
                        "price": 10.2,
                        "pct_chg": 10.0,
                        "open_num": 1.0,
                        "lu_desc": "示例题材",
                        "limit_type": "涨停池",
                        "tag": "首板",
                        "first_lu_time": f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]} 09:30:00",
                        "last_lu_time": f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]} 09:30:00",
                        "limit_order": 1000.0,
                        "limit_amount": 10200.0,
                        "turnover_rate": 1.2,
                        "free_float": 1000000.0,
                        "status": "一字板",
                        "market_type": "HS",
                    }
                ]
            ),
            "moneyflow_dc": pd.DataFrame(
                [
                    {
                        "ts_code": "000001.SZ",
                        "trade_date": trade_date,
                        "name": "平安银行",
                        "pct_change": 1.0,
                        "close": 10.2,
                        "net_amount": 1.0,
                        "net_amount_rate": 0.1,
                        "buy_elg_amount": 2.0,
                        "buy_elg_amount_rate": 0.2,
                        "buy_lg_amount": -1.0,
                        "buy_lg_amount_rate": -0.1,
                        "buy_md_amount": -0.5,
                        "buy_md_amount_rate": -0.05,
                        "buy_sm_amount": -0.5,
                        "buy_sm_amount_rate": -0.05,
                    }
                ]
            ),
            "top_list": pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": trade_date, "reason": "日涨幅偏离"}]),
            "hsgt_top10": pd.DataFrame(
                [
                    {
                        "trade_date": trade_date,
                        "ts_code": "600183.SH",
                        "name": "生益科技",
                        "close": 177.3,
                        "change": 9.43,
                        "rank": 1,
                        "market_type": int(normalized_params.get("market_type", 1)),
                        "amount": 1616432854.0,
                        "net_amount": None,
                        "buy": None,
                        "sell": None,
                    }
                ]
            ),
            "stock_hsgt": pd.DataFrame(
                [
                    {
                        "ts_code": "600021.SH" if normalized_params.get("type") == "HK_SH" else "000034.SZ",
                        "trade_date": trade_date,
                        "type": normalized_params.get("type", "HK_SH"),
                        "name": "上海电力" if normalized_params.get("type") == "HK_SH" else "神州数码",
                        "type_name": "沪股通(港>沪)" if normalized_params.get("type") == "HK_SH" else "深股通(港>深)",
                    }
                ]
            ),
            "margin_detail": pd.DataFrame(
                [
                    {
                        "trade_date": trade_date,
                        "ts_code": "000001.SZ",
                        "name": "平安银行",
                        "rzye": 1000.0,
                        "rqye": 20.0,
                        "rzmre": 30.0,
                        "rqyl": 4.0,
                        "rzche": 50.0,
                        "rqchl": 6.0,
                        "rqmcl": 7.0,
                        "rzrqye": 1020.0,
                    }
                ]
            ),
        }
        return SourceFetchResult(
            source_id="tushare",
            api_name=api_name,
            params=normalized_params,
            requested_at="2026-06-26T18:00:00+08:00",
            frame=frames[api_name],
        )

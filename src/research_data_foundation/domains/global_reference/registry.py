from __future__ import annotations

from ...core import DatasetContract, IngestionRecipe, LineagePolicy, PipelineSpec, PipelineStep, SourceSpec, TemporalPolicy, UsagePolicy


def global_reference_sources() -> tuple[SourceSpec, ...]:
    return (
        SourceSpec(
            id="global_tencent_quote",
            title="Tencent US/HK current quote snapshot",
            source_role="cross_market_reference",
            authority_tier="S3",
            transport="http",
            rate_limit={"concurrency": 1, "min_interval_seconds": 1.0},
            auth={"type": "none"},
            notes="On-demand US/HK quote source for cross-market context only. Never generates A-share candidates or overwrites local A-share EOD facts.",
        ),
        SourceSpec(
            id="sec_edgar",
            title="SEC EDGAR",
            source_role="cross_market_reference",
            authority_tier="S1",
            transport="http",
            rate_limit={"max_qps": 10},
            auth={"type": "user_agent"},
            notes="Official US company filing and XBRL source. Requires a descriptive User-Agent.",
        ),
    )


def global_reference_datasets() -> tuple[DatasetContract, ...]:
    return (
        DatasetContract(
            id="global.sec_filings",
            title="SEC recent filings",
            domain="global_reference",
            market_scope="us",
            role="reference_fact",
            temporal=TemporalPolicy(
                temporal_mode="filing",
                finality="final",
                available_after="on_demand",
                as_of_policy="latest_before",
            ),
            partition_keys=("cik",),
            primary_key=("cik", "accession_number"),
            required_columns=("cik", "accession_number", "form", "filing_date", "primary_document", "source_url"),
            usage=UsagePolicy(
                allowed_uses=("evidence", "cross_market_context", "cross_market_validation"),
                forbidden_uses=("candidate_generation",),
            ),
        ),
        DatasetContract(
            id="global.sec_ticker_cik",
            title="SEC ticker to CIK mapping",
            domain="global_reference",
            market_scope="us",
            role="reference_fact",
            temporal=TemporalPolicy(
                temporal_mode="reference",
                finality="final",
                available_after="on_demand",
                as_of_policy="latest_before",
            ),
            partition_keys=("snapshot_date",),
            primary_key=("snapshot_date", "ticker"),
            required_columns=("snapshot_date", "ticker", "cik", "title", "source_url"),
            usage=UsagePolicy(
                allowed_uses=("evidence", "cross_market_context", "cross_market_validation"),
                forbidden_uses=("candidate_generation", "ashare_primary_candidate_generation"),
            ),
        ),
        DatasetContract(
            id="global.sec_companyfacts",
            title="SEC companyfacts XBRL facts",
            domain="global_reference",
            market_scope="us",
            role="financial_fact",
            temporal=TemporalPolicy(
                temporal_mode="filing",
                finality="final",
                available_after="on_demand",
                as_of_policy="latest_before",
            ),
            partition_keys=("cik",),
            primary_key=("cik", "taxonomy", "concept", "unit", "end_date", "accession_number", "frame"),
            required_columns=(
                "cik",
                "taxonomy",
                "concept",
                "unit",
                "end_date",
                "accession_number",
                "frame",
                "filed_date",
                "form",
                "value",
                "source_url",
            ),
            analysis_columns=("value",),
            usage=UsagePolicy(
                allowed_uses=("evidence", "financial_analysis", "cross_market_context", "cross_market_validation"),
                forbidden_uses=("candidate_generation", "ashare_primary_candidate_generation", "trade_execution"),
            ),
        ),
    )


def global_reference_recipes() -> tuple[IngestionRecipe, ...]:
    return (
        IngestionRecipe(
            id="sec_edgar.submissions.to_global_sec_filings",
            source_id="sec_edgar",
            source_api="submissions",
            target_dataset_id="global.sec_filings",
            schedule="global_reference_weekly",
            params_template={"cik": "${partition.cik}"},
            field_map={
                "filingDate": "filing_date",
                "accessionNumber": "accession_number",
                "primaryDocument": "primary_document",
            },
            lineage=LineagePolicy(raw_required=True, staging_required=True),
            selection_priority=10,
        ),
        IngestionRecipe(
            id="sec_edgar.company_tickers.to_global_sec_ticker_cik",
            source_id="sec_edgar",
            source_api="company_tickers",
            target_dataset_id="global.sec_ticker_cik",
            schedule="global_reference_universe_weekly",
            params_template={},
            lineage=LineagePolicy(raw_required=True, staging_required=True),
            selection_priority=10,
            notes="Official SEC company_tickers mapping. Use as cross-market identity reference only, not A-share candidate generation.",
        ),
        IngestionRecipe(
            id="sec_edgar.companyfacts.to_global_sec_companyfacts",
            source_id="sec_edgar",
            source_api="companyfacts",
            target_dataset_id="global.sec_companyfacts",
            schedule="global_reference_companyfacts_on_demand",
            params_template={"cik": "${partition.cik}"},
            lineage=LineagePolicy(raw_required=True, staging_required=True),
            selection_priority=10,
            notes="Official SEC XBRL companyfacts. Use for cross-market financial context and evidence seeds.",
        ),
    )


def global_reference_pipelines() -> tuple[PipelineSpec, ...]:
    return (
        PipelineSpec(
            id="global_reference_weekly",
            title="Cross-market reference refresh",
            domain="global_reference",
            cadence="weekly",
            steps=(PipelineStep("sec_edgar.submissions.to_global_sec_filings", required=False),),
        ),
        PipelineSpec(
            id="global_reference_universe_weekly",
            title="Cross-market listed universe mapping refresh",
            domain="global_reference",
            cadence="weekly",
            steps=(PipelineStep("sec_edgar.company_tickers.to_global_sec_ticker_cik", required=False),),
        ),
        PipelineSpec(
            id="global_reference_companyfacts_on_demand",
            title="SEC companyfacts on-demand refresh",
            domain="global_reference",
            cadence="on_demand",
            steps=(PipelineStep("sec_edgar.companyfacts.to_global_sec_companyfacts", required=False),),
        ),
    )

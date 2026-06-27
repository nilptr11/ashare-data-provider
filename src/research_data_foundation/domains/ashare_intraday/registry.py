from __future__ import annotations

from ...core import DatasetContract, IngestionRecipe, LineagePolicy, PipelineSpec, PipelineStep, SourceSpec, TemporalPolicy, UsagePolicy


def ashare_intraday_sources() -> tuple[SourceSpec, ...]:
    return (
        SourceSpec(
            id="tencent_quote",
            title="Tencent A-share current quote snapshot",
            source_role="intraday_observation",
            authority_tier="S3",
            transport="http",
            rate_limit={"concurrency": 1, "min_interval_seconds": 1.0},
            auth={"type": "none"},
            notes="Lightweight on-demand A-share current quote source. Provisional only; never overwrites canonical EOD mart.",
        ),
        SourceSpec(
            id="eastmoney_intraday",
            title="Eastmoney intraday quote snapshot",
            source_role="intraday_observation",
            authority_tier="S3",
            transport="http",
            rate_limit={"concurrency": 1, "min_interval_seconds": 1.5},
            auth={"type": "none"},
            notes="Provisional A-share intraday quote snapshot source. Never overwrites canonical EOD mart.",
        ),
    )


def ashare_intraday_datasets() -> tuple[DatasetContract, ...]:
    return (
        DatasetContract(
            id="ashare.intraday_snapshot",
            title="A-share intraday quote snapshot",
            domain="ashare_intraday",
            market_scope="cn_ashare",
            role="provisional_observation",
            temporal=TemporalPolicy(
                temporal_mode="intraday_snapshot",
                finality="provisional",
                available_after="realtime",
                as_of_policy="latest_before",
            ),
            partition_keys=("snapshot_at",),
            primary_key=("snapshot_at", "security_id"),
            required_columns=("snapshot_at", "security_id", "price", "pct_chg", "volume", "amount", "source_url"),
            usage=UsagePolicy(
                allowed_uses=("market_context", "market_validation", "feature_input"),
                forbidden_uses=("candidate_generation", "company_business_exposure"),
            ),
            analysis_columns=("price", "pct_chg", "amount"),
        ),
    )


def ashare_intraday_recipes() -> tuple[IngestionRecipe, ...]:
    return (
        IngestionRecipe(
            id="eastmoney.push2.quote_snapshot.to_ashare_intraday_snapshot",
            source_id="eastmoney_intraday",
            source_api="push2.quote_snapshot",
            target_dataset_id="ashare.intraday_snapshot",
            schedule="ashare_intraday_snapshot",
            params_template={"secids": "${params.secids}", "snapshot_at": "${partition.snapshot_at}"},
            field_map={
                "f12": "security_id",
                "f14": "name",
                "f2": "price",
                "f3": "pct_chg",
                "f5": "volume",
                "f6": "amount",
            },
            lineage=LineagePolicy(raw_required=True, staging_required=True),
            selection_priority=10,
            notes="Intraday data is provisional and must not overwrite ashare.daily.",
        ),
    )


def ashare_intraday_pipelines() -> tuple[PipelineSpec, ...]:
    return (
        PipelineSpec(
            id="ashare_intraday_snapshot",
            title="A-share provisional intraday snapshot",
            domain="ashare_intraday",
            cadence="intraday",
            steps=(PipelineStep("eastmoney.push2.quote_snapshot.to_ashare_intraday_snapshot", required=False),),
        ),
    )

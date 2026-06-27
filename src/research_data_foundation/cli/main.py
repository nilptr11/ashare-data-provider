from __future__ import annotations

import argparse
import json
import os
import shlex
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from ..core.paths import project_root
from ..domains import default_registry
from ..evidence import (
    EvidenceRecord,
    EvidenceProfiler,
    EvidenceSourceFetcher,
    EvidenceSourceRegistry,
    EvidenceSourceSpec,
    EvidenceStore,
    announcement_text_snippet_candidates,
    evidence_from_table,
    validate_evidence,
    validate_evidence_source,
)
from ..features import FeatureBuilder, FeatureRegistry, FeatureStore
from ..ingestion import IngestionRunner
from ..inventory import DEFAULT_RECOVERY_COVERAGE_STATUSES, DEFAULT_RECOVERY_STATUSES, DataInventory
from ..maintenance import (
    AShareAnnouncementTextMaintainer,
    AShareConceptMembersMaintainer,
    AShareCoreMaintainer,
    AShareFinancialsMaintainer,
    AShareIndexWeightsMaintainer,
    AShareMainBusinessMaintainer,
    AShareThsConceptsMaintainer,
    IndustryReportIndexMaintainer,
)
from ..relations import ENTITY_TYPES, PREDICATES, RelationProfiler, RelationRecord, RelationStore, validate_relation
from ..runs import RunRecorder, replay_run
from ..sources import CninfoSourceAdapter, EastmoneySourceAdapter, SourceAdapterError, TencentQuoteAdapter
from ..storage import MartStore


def main(argv: list[str] | None = None) -> int:
    load_project_env()
    parser = build_parser()
    args = parser.parse_args(argv)
    registry = default_registry()

    if args.command == "inventory":
        inventory = DataInventory(args.data_dir, registry=registry)
        if args.inventory_command == "summary":
            payload = inventory.summary(as_of=args.as_of)
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.inventory_command == "datasets":
            payload = inventory.datasets(as_of=args.as_of, domain=args.domain, use=args.use)
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.inventory_command == "features":
            payload = inventory.feature_partitions(as_of=args.as_of)
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.inventory_command == "plan":
            payload = inventory.plan(
                as_of=args.as_of,
                domain=args.domain,
                use=args.use,
                statuses=tuple(args.status or DEFAULT_RECOVERY_STATUSES),
                coverage_statuses=tuple(args.coverage_status or DEFAULT_RECOVERY_COVERAGE_STATUSES),
                include_features=not args.no_features,
                limit=args.limit,
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
    if args.command == "registry":
        if args.registry_command == "list":
            payload = registry_payload(registry, args.kind)
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
    if args.command == "sources":
        if args.sources_command == "list":
            payload = source_map_payload(
                registry,
                data_dir=args.data_dir,
                as_of=args.as_of,
                domain=args.domain,
                use=args.use,
                limit_datasets=args.limit_datasets,
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.sources_command == "show":
            payload = source_detail_payload(
                registry,
                args.source_id,
                data_dir=args.data_dir,
                as_of=args.as_of,
                domain=args.domain,
                use=args.use,
                limit_datasets=args.limit_datasets,
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
    if args.command == "quotes":
        if args.quotes_command == "current":
            payload = current_quote_payload(
                MartStore(args.data_dir, registry),
                security_ids=tuple(args.security_id),
                source=args.source,
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
    if args.command == "datasets":
        if args.datasets_command == "list":
            payload = [
                item
                for item in registry_payload(registry, "datasets")
                if not args.domain or item["domain"] == args.domain
            ]
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.datasets_command == "search":
            payload = dataset_search_payload(
                DataInventory(args.data_dir, registry=registry),
                query=args.query,
                as_of=args.as_of,
                domain=args.domain,
                use=args.use,
                limit=args.limit,
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.datasets_command == "partitions":
            payload = dataset_partitions_payload(MartStore(args.data_dir, registry), args.dataset_id, limit=args.limit)
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.datasets_command == "latest":
            payload = dataset_latest_payload(
                MartStore(args.data_dir, registry),
                args.dataset_id,
                columns=args.columns,
                limit=args.limit,
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.datasets_command == "meta":
            meta = MartStore(args.data_dir, registry).read_meta(args.dataset_id, parse_key_values(args.partition))
            print(json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.datasets_command == "read":
            payload = dataset_read_payload(
                MartStore(args.data_dir, registry),
                args.dataset_id,
                partition=parse_key_values(args.partition),
                columns=args.columns,
                limit=args.limit,
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.datasets_command == "scan":
            payload = dataset_scan_payload(
                MartStore(args.data_dir, registry),
                args.dataset_id,
                partition_filter=parse_key_values(args.partition),
                columns=args.columns,
                limit=args.limit,
                partition_limit=args.partition_limit,
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.datasets_command == "read-window":
            payload = dataset_read_window_payload(
                MartStore(args.data_dir, registry),
                args.dataset_id,
                as_of=args.as_of,
                count=args.count,
                partition_key=args.partition_key,
                columns=args.columns,
                limit=args.limit,
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
    if args.command == "announcements":
        if args.announcements_command == "discover":
            payload = announcement_discovery_payload(
                start_date=args.start_date,
                end_date=args.end_date,
                keyword=args.keyword,
                category=args.category,
                security_id=args.security_id,
                security_code=args.security_code,
                org_id=args.org_id,
                stock=args.stock,
                cninfo_category=args.cninfo_category,
                column=args.column,
                page_size=args.page_size,
                max_pages=args.max_pages,
                limit=args.limit,
                dry_run=args.dry_run,
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.announcements_command == "search":
            payload = announcement_index_search_payload(
                MartStore(args.data_dir, registry),
                publish_date=args.publish_date,
                as_of=args.as_of,
                lookback_days=args.lookback_days,
                keyword=args.keyword,
                category=args.category,
                security_id=args.security_id,
                security_code=args.security_code,
                org_id=args.org_id,
                limit=args.limit,
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.announcements_command == "fetch-text":
            runner = IngestionRunner(data_dir=args.data_dir, registry=registry)
            partition = {
                "publish_date": _compact_yyyymmdd(args.publish_date),
                "announcement_id": str(args.announcement_id).strip(),
            }
            params = {
                "security_id": args.security_id or "",
                "security_name": args.security_name or "",
                "title": args.title or "",
                "source_url": args.source_url,
            }
            if args.dry_run:
                plan = runner.plan_recipe(
                    "cninfo.announcement_pdf_text.to_ashare_announcement_text",
                    partition=partition,
                    params=params,
                    refresh=args.refresh,
                )
                payload = plan.to_dict() | {
                    "boundary": (
                        "On-demand CNINFO PDF text materialization. This downloads only the selected announcement; "
                        "it does not imply the publish_date announcement index is complete and does not ingest a claim as evidence."
                    )
                }
                print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
                return 0
            result = runner.run_recipe(
                "cninfo.announcement_pdf_text.to_ashare_announcement_text",
                partition=partition,
                params=params,
                refresh=args.refresh,
            )
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
    if args.command == "ingest":
        if args.ingest_command == "dataset":
            recipe = registry.require_recipe(args.recipe_id)
            if recipe.target_dataset_id != args.dataset_id:
                parser.error(f"recipe {args.recipe_id!r} targets {recipe.target_dataset_id!r}, not {args.dataset_id!r}")
            runner = IngestionRunner(data_dir=args.data_dir, registry=registry)
            if args.dry_run:
                plan = runner.plan_recipe(
                    args.recipe_id,
                    partition=parse_key_values(args.partition),
                    params=parse_key_values(args.param),
                    refresh=args.refresh,
                )
                print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
                return 0
            result = runner.run_recipe(
                args.recipe_id,
                partition=parse_key_values(args.partition),
                params=parse_key_values(args.param),
                refresh=args.refresh,
            )
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.ingest_command == "recipe":
            runner = IngestionRunner(data_dir=args.data_dir, registry=registry)
            if args.dry_run:
                plan = runner.plan_recipe(
                    args.recipe_id,
                    partition=parse_key_values(args.partition),
                    params=parse_key_values(args.param),
                    refresh=args.refresh,
                )
                print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
                return 0
            result = runner.run_recipe(
                args.recipe_id,
                partition=parse_key_values(args.partition),
                params=parse_key_values(args.param),
                refresh=args.refresh,
            )
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.ingest_command == "run":
            runner = IngestionRunner(data_dir=args.data_dir, registry=registry)
            if args.dry_run:
                plan = runner.plan_pipeline(
                    args.pipeline_id,
                    partition=parse_key_values(args.partition),
                    params=parse_key_values(args.param),
                    refresh=args.refresh,
                    continue_on_error=args.continue_on_error,
                )
                print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
                return 0
            result = runner.run_pipeline(
                args.pipeline_id,
                partition=parse_key_values(args.partition),
                params=parse_key_values(args.param),
                refresh=args.refresh,
                continue_on_error=args.continue_on_error,
            )
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.ingest_command == "pipeline":
            runner = IngestionRunner(data_dir=args.data_dir, registry=registry)
            if args.dry_run:
                plan = runner.plan_pipeline(
                    args.pipeline_id,
                    partition=parse_key_values(args.partition),
                    params=parse_key_values(args.param),
                    refresh=args.refresh,
                    continue_on_error=args.continue_on_error,
                )
                print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
                return 0
            result = runner.run_pipeline(
                args.pipeline_id,
                partition=parse_key_values(args.partition),
                params=parse_key_values(args.param),
                refresh=args.refresh,
                continue_on_error=args.continue_on_error,
            )
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
    if args.command == "evidence":
        if args.evidence_command == "validate":
            records = [validate_evidence(record) for record in load_evidence_records(args.path)]
            print(json.dumps([record.to_dict() for record in records], ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.evidence_command == "ingest":
            result = EvidenceStore(args.data_dir).ingest(load_evidence_records(args.path))
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.evidence_command == "sources":
            source_registry = EvidenceSourceRegistry(args.data_dir)
            if args.evidence_sources_command == "list":
                payload = [source.to_dict() for source in source_registry.list()]
                print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
                return 0
            if args.evidence_sources_command == "show":
                source = source_registry.require(args.source_id)
                print(json.dumps(source.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
                return 0
            if args.evidence_sources_command == "add":
                sources = load_evidence_source_specs(args.path)
                paths = [source_registry.add(source, overwrite=args.overwrite) for source in sources]
                payload = {
                    "schema": "rdf.evidence_source_add_result.v1",
                    "sources": [source.source_id for source in sources],
                    "paths": [str(path) for path in paths],
                }
                print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
                return 0
            if args.evidence_sources_command == "fetch":
                fetcher = EvidenceSourceFetcher(
                    evidence_store=EvidenceStore(args.data_dir),
                    source_registry=source_registry,
                )
                params = parse_key_values(args.param)
                if args.dry_run:
                    records = fetcher.build_records(args.source_id, params=params, limit=args.limit)
                    payload = {
                        "schema": "rdf.evidence_source_fetch_preview.v1",
                        "source_id": args.source_id,
                        "records": [record.to_dict() for record in records],
                    }
                    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
                    return 0
                result = fetcher.fetch(args.source_id, params=params, limit=args.limit)
                print(json.dumps(ingest_summary(result, id_key="evidence_ids"), ensure_ascii=False, indent=2, sort_keys=True))
                return 0
        if args.evidence_command == "from-dataset":
            partition = parse_key_values(args.partition)
            mart = MartStore(args.data_dir, registry)
            frame = mart.read(args.dataset_id, partition)
            if args.limit and args.limit > 0:
                frame = frame.head(args.limit)
            meta = mart.read_meta(args.dataset_id, partition)
            records = evidence_from_table(args.dataset_id, frame, meta=meta)
            result = EvidenceStore(args.data_dir).ingest(records)
            print(json.dumps(ingest_summary(result, id_key="evidence_ids"), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.evidence_command == "from-announcement-text":
            partition = parse_key_values(args.partition)
            mart = MartStore(args.data_dir, registry)
            frame = mart.read("ashare.announcement_text", partition)
            meta = mart.read_meta("ashare.announcement_text", partition)
            payload = announcement_text_snippet_candidates(
                frame,
                meta=meta,
                query=args.query,
                context_chars=args.context_chars,
                limit=args.limit,
                case_sensitive=args.case_sensitive,
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.evidence_command == "list":
            records = EvidenceStore(args.data_dir).search(
                topic=args.topic,
                industry=args.industry,
                company=args.company,
                product=args.product,
                metric=args.metric,
                period=args.period,
                confidence=args.confidence,
                dataset_id=args.dataset_id,
                limit=args.limit,
            )
            print(json.dumps([record.to_dict() for record in records], ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.evidence_command == "profile":
            payload = EvidenceProfiler(EvidenceStore(args.data_dir)).profile(
                topic=args.topic,
                industry=args.industry,
                company=args.company,
                product=args.product,
                metric=args.metric,
                period=args.period,
                confidence=args.confidence,
                dataset_id=args.dataset_id,
                limit=args.limit,
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.evidence_command == "source-candidates":
            payload = EvidenceProfiler(EvidenceStore(args.data_dir)).source_candidates(
                topic=args.topic,
                industry=args.industry,
                company=args.company,
                product=args.product,
                metric=args.metric,
                period=args.period,
                confidence=args.confidence,
                dataset_id=args.dataset_id,
                min_records=args.min_records,
                limit=args.limit,
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.evidence_command == "export":
            store = EvidenceStore(args.data_dir)
            records = store.search(
                topic=args.topic,
                industry=args.industry,
                company=args.company,
                product=args.product,
                metric=args.metric,
                period=args.period,
                confidence=args.confidence,
                dataset_id=args.dataset_id,
                limit=args.limit,
            )
            path = store.export_jsonl(args.output, records)
            print(json.dumps({"schema": "rdf.evidence_export_result.v1", "path": str(path), "records": len(records)}, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
    if args.command == "relations":
        if args.relations_command == "taxonomy":
            payload = {"entity_types": sorted(ENTITY_TYPES), "predicates": sorted(PREDICATES)}
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.relations_command == "validate":
            records = [validate_relation(record) for record in load_relation_records(args.path)]
            print(json.dumps([record.to_dict() for record in records], ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.relations_command == "ingest":
            result = RelationStore(args.data_dir).ingest(load_relation_records(args.path))
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.relations_command == "list":
            records = RelationStore(args.data_dir).search(
                subject=args.subject,
                predicate=args.predicate,
                object=args.object,
                evidence_id=args.evidence_id,
                tag=args.tag,
                confidence=args.confidence,
                limit=args.limit,
            )
            print(json.dumps([record.to_dict() for record in records], ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.relations_command == "profile":
            payload = RelationProfiler(RelationStore(args.data_dir)).profile(
                subject=args.subject,
                predicate=args.predicate,
                object=args.object,
                evidence_id=args.evidence_id,
                tag=args.tag,
                confidence=args.confidence,
                limit=args.limit,
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.relations_command == "neighborhood":
            payload = RelationProfiler(RelationStore(args.data_dir)).neighborhood(
                entity=args.entity,
                predicate=args.predicate,
                tag=args.tag,
                confidence=args.confidence,
                limit=args.limit,
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.relations_command == "snapshot":
            payload = RelationStore(args.data_dir).snapshot(
                subject=args.subject,
                predicate=args.predicate,
                object=args.object,
                evidence_id=args.evidence_id,
                tag=args.tag,
                confidence=args.confidence,
                limit=args.limit,
            )
            if args.output:
                output_path = Path(args.output)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
                payload = {**payload, "path": str(output_path)}
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
    if args.command == "features":
        feature_registry = FeatureRegistry.builtin()
        if args.features_command == "list":
            payload = [spec.to_dict() for spec in feature_registry.list()]
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.features_command == "build":
            result = FeatureBuilder(data_dir=args.data_dir, registry=registry, feature_registry=feature_registry).build(
                args.feature_id,
                as_of=args.as_of,
                window=args.window,
                refresh=args.refresh,
            )
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.features_command == "read":
            spec = feature_registry.require(args.feature_id)
            payload = feature_read_payload(FeatureStore(args.data_dir), spec, as_of=args.as_of, window=args.window, limit=args.limit)
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.features_command == "meta":
            spec = feature_registry.require(args.feature_id)
            meta = FeatureStore(args.data_dir).load_meta(
                args.feature_id,
                domain=spec.domain,
                as_of=args.as_of,
                window=args.window,
            )
            print(json.dumps(meta.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
    if args.command == "runs":
        if args.runs_command == "record":
            record = RunRecorder(args.data_dir).record(
                question=args.question,
                as_of=args.as_of,
                mart_refs=tuple(args.mart_ref),
                feature_refs=tuple(args.feature_ref),
                evidence_ids=tuple(args.evidence_id),
                relation_ids=tuple(args.relation_id),
                model_output_file=args.model_output_file,
                validated_output_file=args.validated_output,
                run_id=args.run_id,
                notes=args.notes or "",
            )
            print(json.dumps(record.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.runs_command == "show":
            payload = replay_run(args.run_id, data_dir=args.data_dir)
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
    if args.command == "maintain":
        if args.maintain_command == "ashare-core":
            result = AShareCoreMaintainer(data_dir=args.data_dir, registry=registry).maintain(
                as_of=args.as_of,
                lookback_trading_days=args.lookback_trading_days,
                refresh=args.refresh,
                continue_on_error=args.continue_on_error,
                build_features=not args.skip_features,
                windows=tuple(parse_int_list(args.windows)),
            )
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if result["status"] != "blocked" else 1
        if args.maintain_command == "ashare-main-business":
            result = AShareMainBusinessMaintainer(data_dir=args.data_dir, registry=registry).maintain(
                period=args.period,
                security_ids=tuple(args.security_id),
                stock_snapshot_date=args.stock_snapshot_date,
                segment_types=tuple(args.segment_types.split(",")),
                limit=args.limit,
                refresh=args.refresh,
                continue_on_error=args.continue_on_error,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if result["status"] != "blocked" else 1
        if args.maintain_command == "ashare-financials":
            result = AShareFinancialsMaintainer(data_dir=args.data_dir, registry=registry).maintain(
                period=args.period,
                as_of=args.as_of,
                security_ids=tuple(args.security_id),
                stock_snapshot_date=args.stock_snapshot_date,
                dataset_ids=tuple(args.dataset_id),
                limit=args.limit,
                refresh=args.refresh,
                continue_on_error=args.continue_on_error,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if result["status"] != "blocked" else 1
        if args.maintain_command == "ashare-concept-members":
            result = AShareConceptMembersMaintainer(data_dir=args.data_dir, registry=registry).maintain(
                snapshot_date=args.snapshot_date,
                concept_ids=tuple(args.concept_id),
                dc_index_date=args.dc_index_date,
                limit=args.limit,
                refresh=args.refresh,
                continue_on_error=args.continue_on_error,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if result["status"] != "blocked" else 1
        if args.maintain_command == "ashare-ths-concepts":
            result = AShareThsConceptsMaintainer(data_dir=args.data_dir, registry=registry).maintain(
                snapshot_date=args.snapshot_date,
                concept_ids=tuple(args.concept_id),
                limit=args.limit,
                refresh=args.refresh,
                continue_on_error=args.continue_on_error,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if result["status"] != "blocked" else 1
        if args.maintain_command == "ashare-index-weights":
            result = AShareIndexWeightsMaintainer(data_dir=args.data_dir, registry=registry).maintain(
                snapshot_date=args.snapshot_date,
                start_date=args.start_date,
                lookback_days=args.lookback_days,
                index_codes=tuple(args.index_code),
                refresh=args.refresh,
                continue_on_error=args.continue_on_error,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if result["status"] != "blocked" else 1
        if args.maintain_command == "ashare-announcement-text":
            result = AShareAnnouncementTextMaintainer(data_dir=args.data_dir, registry=registry).maintain(
                publish_date=args.publish_date,
                announcement_ids=tuple(args.announcement_id),
                limit=args.limit,
                refresh=args.refresh,
                continue_on_error=args.continue_on_error,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if result["status"] != "blocked" else 1
        if args.maintain_command == "industry-report-index":
            result = IndustryReportIndexMaintainer(data_dir=args.data_dir, registry=registry).maintain(
                query_date=args.query_date,
                begin=args.begin,
                lookback_days=args.lookback_days,
                max_pages=args.max_pages,
                refresh=args.refresh,
                continue_on_error=args.continue_on_error,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if result["status"] != "blocked" else 1
        if args.maintain_command == "status":
            if args.target != "ashare-core":
                parser.error(f"unsupported maintenance target {args.target!r}")
            result = AShareCoreMaintainer(data_dir=args.data_dir, registry=registry).status(
                as_of=args.as_of,
                lookback_trading_days=args.lookback_trading_days,
                windows=tuple(parse_int_list(args.windows)),
            )
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if result["status"] != "blocked" else 1
    parser.error("unknown command")
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rdf", description="Research data foundation CLI.")
    parser.add_argument("--data-dir", help="Data root. Defaults to RDF_DATA_DIR or project data/.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inventory = subparsers.add_parser("inventory", help="Inspect local data availability across mart, features, evidence, and relations")
    inventory_subparsers = inventory.add_subparsers(dest="inventory_command", required=True)
    inventory_summary = inventory_subparsers.add_parser("summary", help="Summarize local data availability")
    inventory_summary.add_argument("--as-of", help="Target YYYYMMDD for date-partitioned datasets and features.")
    inventory_datasets = inventory_subparsers.add_parser("datasets", help="List dataset availability, latest partitions, and quality")
    inventory_datasets.add_argument("--as-of", help="Target YYYYMMDD for date-partitioned datasets.")
    inventory_datasets.add_argument("--domain")
    inventory_datasets.add_argument("--use", help="Only include datasets permitting this usage policy.")
    inventory_features = inventory_subparsers.add_parser("features", help="List feature partition availability and quality")
    inventory_features.add_argument("--as-of", help="Target YYYYMMDD for feature partitions.")
    inventory_plan = inventory_subparsers.add_parser("plan", help="Plan recovery commands for missing or degraded local data")
    inventory_plan.add_argument("--as-of", help="Target YYYYMMDD for date-partitioned datasets and features.")
    inventory_plan.add_argument("--domain")
    inventory_plan.add_argument("--use", help="Only include datasets permitting this usage policy.")
    inventory_plan.add_argument("--status", action="append", choices=("missing", "degraded", "ready"))
    inventory_plan.add_argument(
        "--coverage-status",
        action="append",
        choices=("none", "partial", "latest_before", "latest", "full"),
        help="Dataset coverage status to include. Repeatable; defaults to none and partial.",
    )
    inventory_plan.add_argument("--no-features", action="store_true", help="Only include dataset recovery items.")
    inventory_plan.add_argument("--limit", type=int, default=0)

    registry = subparsers.add_parser("registry", help="Inspect core registries")
    registry_subparsers = registry.add_subparsers(dest="registry_command", required=True)
    registry_list = registry_subparsers.add_parser("list", help="List registry entries")
    registry_list.add_argument("kind", choices=("sources", "datasets", "recipes", "pipelines"))

    sources = subparsers.add_parser("sources", help="Inspect source adapters")
    sources_subparsers = sources.add_subparsers(dest="sources_command", required=True)
    sources_list = sources_subparsers.add_parser("list", help="List source-to-dataset coverage and local availability")
    sources_list.add_argument("--as-of", help="Target YYYYMMDD for local dataset status.")
    sources_list.add_argument("--domain", help="Only include datasets in this domain.")
    sources_list.add_argument("--use", help="Only include datasets permitting this usage policy.")
    sources_list.add_argument("--limit-datasets", type=int, default=0, help="Max datasets per source; 0 means all.")
    sources_show = sources_subparsers.add_parser("show", help="Show one source with recipes, datasets, pipelines, and local status")
    sources_show.add_argument("source_id")
    sources_show.add_argument("--as-of", help="Target YYYYMMDD for local dataset status.")
    sources_show.add_argument("--domain", help="Only include datasets in this domain.")
    sources_show.add_argument("--use", help="Only include datasets permitting this usage policy.")
    sources_show.add_argument("--limit-datasets", type=int, default=0, help="Max datasets to return; 0 means all.")

    datasets = subparsers.add_parser("datasets", help="Inspect mart datasets")
    datasets_subparsers = datasets.add_subparsers(dest="datasets_command", required=True)
    datasets_list = datasets_subparsers.add_parser("list", help="List dataset contracts")
    datasets_list.add_argument("--domain")
    datasets_search = datasets_subparsers.add_parser("search", help="Search dataset contracts and local availability by research intent")
    datasets_search.add_argument("query", nargs="?", default="", help="Keyword or research intent, e.g. 资金流, 财务, announcement, moneyflow.")
    datasets_search.add_argument("--as-of", help="Target YYYYMMDD for local availability and read-command suggestions.")
    datasets_search.add_argument("--domain")
    datasets_search.add_argument("--use", help="Only include datasets permitting this usage policy.")
    datasets_search.add_argument("--limit", type=int, default=20, help="Max results to return; 0 means all.")
    datasets_partitions = datasets_subparsers.add_parser("partitions", help="List local mart partitions for one dataset")
    datasets_partitions.add_argument("dataset_id")
    datasets_partitions.add_argument("--limit", type=int, default=20, help="Max partitions to return; 0 means all.")
    datasets_latest = datasets_subparsers.add_parser("latest", help="Read the latest local mart partition")
    datasets_latest.add_argument("dataset_id")
    datasets_latest.add_argument("--columns", nargs="+")
    datasets_latest.add_argument("--limit", type=int, default=20)
    datasets_meta = datasets_subparsers.add_parser("meta", help="Read mart partition metadata")
    datasets_meta.add_argument("dataset_id")
    datasets_meta.add_argument("--partition", action="append", default=[], help="Partition key=value. Repeatable.")
    datasets_read = datasets_subparsers.add_parser("read", help="Read mart partition rows")
    datasets_read.add_argument("dataset_id")
    datasets_read.add_argument("--partition", action="append", default=[], help="Partition key=value. Repeatable.")
    datasets_read.add_argument("--columns", nargs="+")
    datasets_read.add_argument("--limit", type=int, default=20)
    datasets_scan = datasets_subparsers.add_parser("scan", help="Read rows from all mart partitions matching a partial partition filter")
    datasets_scan.add_argument("dataset_id")
    datasets_scan.add_argument("--partition", action="append", default=[], help="Partition filter key=value. Repeatable; may be partial for multi-key datasets.")
    datasets_scan.add_argument("--columns", nargs="+")
    datasets_scan.add_argument("--limit", type=int, default=20, help="Max rows to return after concatenating matching partitions.")
    datasets_scan.add_argument("--partition-limit", type=int, default=100, help="Max matching partitions to scan; 0 means all.")
    datasets_read_window = datasets_subparsers.add_parser("read-window", help="Read the latest N mart partitions at or before as_of")
    datasets_read_window.add_argument("dataset_id")
    datasets_read_window.add_argument("--as-of", required=True, help="Upper bound partition value, usually YYYYMMDD.")
    datasets_read_window.add_argument("--count", type=int, required=True, help="Number of available partitions to read.")
    datasets_read_window.add_argument("--partition-key", help="Partition key to use for multi-key datasets.")
    datasets_read_window.add_argument("--columns", nargs="+")
    datasets_read_window.add_argument("--limit", type=int, default=20)

    quotes = subparsers.add_parser("quotes", help="Fetch on-demand quote context without updating canonical EOD data")
    quotes_subparsers = quotes.add_subparsers(dest="quotes_command", required=True)
    quotes_current = quotes_subparsers.add_parser("current", help="Fetch provisional current A-share quotes")
    quotes_current.add_argument("--security-id", action="append", required=True, help="A-share security id, e.g. 000001.SZ. Repeatable.")
    quotes_current.add_argument(
        "--source",
        choices=("auto", "tencent", "tencent_quote", "eastmoney", "eastmoney_intraday"),
        default="auto",
        help="Quote source. auto tries Tencent first, then Eastmoney.",
    )

    announcements = subparsers.add_parser("announcements", help="Discover and fetch official announcements on demand")
    announcements_subparsers = announcements.add_subparsers(dest="announcements_command", required=True)
    announcements_discover = announcements_subparsers.add_parser("discover", help="Query CNINFO remotely for announcement candidates without writing local mart data")
    announcements_discover.add_argument("--start-date", required=True, help="Remote query start date YYYYMMDD.")
    announcements_discover.add_argument("--end-date", help="Remote query end date YYYYMMDD. Defaults to --start-date.")
    announcements_discover.add_argument("--keyword", action="append", default=[], help="Keyword for remote discovery and local post-filtering. Repeatable.")
    announcements_discover.add_argument(
        "--category",
        choices=tuple(ANNOUNCEMENT_CATEGORY_KEYWORDS),
        default="全部",
        help="Loose local title/type category filter; not claim evidence and not CNINFO's raw category code.",
    )
    announcements_discover.add_argument("--security-id", help="A-share security id, e.g. 000001.SZ.")
    announcements_discover.add_argument("--security-code", help="Raw six-digit security code.")
    announcements_discover.add_argument("--org-id", help="CNINFO org_id. If paired with a security code, it is used in the remote stock selector.")
    announcements_discover.add_argument("--stock", help="Raw CNINFO stock selector; overrides --security-id/--security-code/--org-id for the remote request.")
    announcements_discover.add_argument("--cninfo-category", default="", help="Raw CNINFO category code for remote filtering, when known.")
    announcements_discover.add_argument("--column", default="szse,sse,bse", help="CNINFO column list, e.g. szse,sse,bse.")
    announcements_discover.add_argument("--page-size", type=int, default=30)
    announcements_discover.add_argument("--max-pages", type=int, default=5)
    announcements_discover.add_argument("--limit", type=int, default=20)
    announcements_discover.add_argument("--dry-run", action="store_true", help="Print the remote request plan without calling CNINFO.")
    announcements_search = announcements_subparsers.add_parser("search", help="Search local CNINFO announcement index by date, security, category, and keyword")
    announcements_search.add_argument("--publish-date", help="Exact publish date YYYYMMDD. If omitted, use --as-of/--lookback-days or latest local partition.")
    announcements_search.add_argument("--as-of", help="Upper bound publish date YYYYMMDD for window search.")
    announcements_search.add_argument("--lookback-days", type=int, default=7, help="Natural-day lookback for announcement publish dates.")
    announcements_search.add_argument("--keyword", action="append", default=[], help="Keyword matched against title, short_title, announcement_type_name, and announcement_type. Repeatable.")
    announcements_search.add_argument(
        "--category",
        choices=tuple(ANNOUNCEMENT_CATEGORY_KEYWORDS),
        default="全部",
        help="Loose announcement category filter implemented as title/type keywords; not claim evidence.",
    )
    announcements_search.add_argument("--security-id", help="A-share security id, e.g. 000001.SZ.")
    announcements_search.add_argument("--security-code", help="Raw six-digit security code.")
    announcements_search.add_argument("--org-id", help="CNINFO org_id.")
    announcements_search.add_argument("--limit", type=int, default=20)
    announcements_fetch_text = announcements_subparsers.add_parser("fetch-text", help="Fetch one selected CNINFO announcement PDF text into ashare.announcement_text")
    announcements_fetch_text.add_argument("--publish-date", required=True, help="Announcement publish date YYYYMMDD.")
    announcements_fetch_text.add_argument("--announcement-id", required=True)
    announcements_fetch_text.add_argument("--source-url", required=True, help="CNINFO static PDF URL or adjunct path.")
    announcements_fetch_text.add_argument("--security-id", help="A-share security id, e.g. 000001.SZ.")
    announcements_fetch_text.add_argument("--security-name")
    announcements_fetch_text.add_argument("--title")
    announcements_fetch_text.add_argument("--refresh", action="store_true")
    announcements_fetch_text.add_argument("--dry-run", action="store_true", help="Print the ingestion plan without downloading or writing.")

    ingest = subparsers.add_parser("ingest", help="Run ingestion recipes")
    ingest_subparsers = ingest.add_subparsers(dest="ingest_command", required=True)
    ingest_dataset = ingest_subparsers.add_parser("dataset", help="Build one dataset partition with a recipe")
    ingest_dataset.add_argument("dataset_id")
    ingest_dataset.add_argument("--recipe", dest="recipe_id", required=True)
    ingest_dataset.add_argument("--partition", action="append", default=[], help="Partition key=value. Repeatable.")
    ingest_dataset.add_argument("--param", action="append", default=[], help="Template param key=value. Repeatable.")
    ingest_dataset.add_argument("--refresh", action="store_true", help="Overwrite existing staging/mart partitions.")
    ingest_dataset.add_argument("--dry-run", action="store_true", help="Print the resolved ingestion plan without fetching or writing.")
    ingest_recipe = ingest_subparsers.add_parser("recipe", help="Run one ingestion recipe")
    ingest_recipe.add_argument("recipe_id")
    ingest_recipe.add_argument("--partition", action="append", default=[], help="Partition key=value. Repeatable.")
    ingest_recipe.add_argument("--param", action="append", default=[], help="Template param key=value. Repeatable.")
    ingest_recipe.add_argument("--refresh", action="store_true", help="Overwrite existing staging/mart partitions.")
    ingest_recipe.add_argument("--dry-run", action="store_true", help="Print the resolved ingestion plan without fetching or writing.")
    ingest_pipeline = ingest_subparsers.add_parser("pipeline", help="Run one ingestion pipeline")
    ingest_pipeline.add_argument("pipeline_id")
    ingest_pipeline.add_argument("--partition", action="append", default=[], help="Partition key=value. Repeatable.")
    ingest_pipeline.add_argument("--param", action="append", default=[], help="Template param key=value. Repeatable.")
    ingest_pipeline.add_argument("--refresh", action="store_true", help="Overwrite existing staging/mart partitions.")
    ingest_pipeline.add_argument("--continue-on-error", action="store_true", help="Continue optional failed steps.")
    ingest_pipeline.add_argument("--dry-run", action="store_true", help="Print the resolved pipeline plan without fetching or writing.")
    ingest_run = ingest_subparsers.add_parser("run", help="Run one ingestion pipeline")
    ingest_run.add_argument("pipeline_id")
    ingest_run.add_argument("--partition", action="append", default=[], help="Partition key=value. Repeatable.")
    ingest_run.add_argument("--param", action="append", default=[], help="Template param key=value. Repeatable.")
    ingest_run.add_argument("--refresh", action="store_true", help="Overwrite existing staging/mart partitions.")
    ingest_run.add_argument("--continue-on-error", action="store_true", help="Continue optional failed steps.")
    ingest_run.add_argument("--dry-run", action="store_true", help="Print the resolved pipeline plan without fetching or writing.")

    evidence = subparsers.add_parser("evidence", help="Build and inspect evidence records")
    evidence_subparsers = evidence.add_subparsers(dest="evidence_command", required=True)
    evidence_validate = evidence_subparsers.add_parser("validate", help="Validate evidence JSON records")
    evidence_validate.add_argument("path")
    evidence_ingest = evidence_subparsers.add_parser("ingest", help="Ingest evidence JSON records")
    evidence_ingest.add_argument("path")
    evidence_sources = evidence_subparsers.add_parser("sources", help="Register and fetch reusable evidence sources")
    evidence_sources_subparsers = evidence_sources.add_subparsers(dest="evidence_sources_command", required=True)
    evidence_sources_subparsers.add_parser("list", help="List reusable evidence source specs")
    evidence_sources_show = evidence_sources_subparsers.add_parser("show", help="Show one reusable evidence source spec")
    evidence_sources_show.add_argument("source_id")
    evidence_sources_add = evidence_sources_subparsers.add_parser("add", help="Add reusable evidence source spec JSON")
    evidence_sources_add.add_argument("path")
    evidence_sources_add.add_argument("--overwrite", action="store_true")
    evidence_sources_fetch = evidence_sources_subparsers.add_parser("fetch", help="Fetch a reusable evidence source into evidence")
    evidence_sources_fetch.add_argument("source_id")
    evidence_sources_fetch.add_argument("--param", action="append", default=[], help="Request param key=value. Repeatable.")
    evidence_sources_fetch.add_argument("--limit", type=int, default=0, help="Optional max source rows to convert.")
    evidence_sources_fetch.add_argument("--dry-run", action="store_true", help="Print mapped evidence without ingesting.")
    evidence_from_dataset = evidence_subparsers.add_parser("from-dataset", help="Build evidence from a mart partition")
    evidence_from_dataset.add_argument("dataset_id")
    evidence_from_dataset.add_argument("--partition", action="append", default=[], help="Partition key=value. Repeatable.")
    evidence_from_dataset.add_argument("--limit", type=int, default=0, help="Optional max mart rows to convert.")
    evidence_from_announcement_text = evidence_subparsers.add_parser(
        "from-announcement-text",
        help="Locate claim snippets in CNINFO announcement PDF text without ingesting evidence",
    )
    evidence_from_announcement_text.add_argument("--partition", action="append", default=[], help="Partition key=value. Repeatable.")
    evidence_from_announcement_text.add_argument("--query", required=True, help="Keyword or exact text to locate in the PDF text.")
    evidence_from_announcement_text.add_argument("--context-chars", type=int, default=120)
    evidence_from_announcement_text.add_argument("--limit", type=int, default=20, help="Optional max snippet candidates.")
    evidence_from_announcement_text.add_argument("--case-sensitive", action="store_true")
    evidence_list = evidence_subparsers.add_parser("list", help="List evidence records")
    evidence_list.add_argument("--topic")
    evidence_list.add_argument("--industry")
    evidence_list.add_argument("--company")
    evidence_list.add_argument("--product")
    evidence_list.add_argument("--metric")
    evidence_list.add_argument("--period")
    evidence_list.add_argument("--confidence")
    evidence_list.add_argument("--dataset-id")
    evidence_list.add_argument("--limit", type=int, default=20)
    evidence_profile = evidence_subparsers.add_parser("profile", help="Profile evidence coverage by topic, source, dataset, company, and metric")
    evidence_profile.add_argument("--topic")
    evidence_profile.add_argument("--industry")
    evidence_profile.add_argument("--company")
    evidence_profile.add_argument("--product")
    evidence_profile.add_argument("--metric")
    evidence_profile.add_argument("--period")
    evidence_profile.add_argument("--confidence")
    evidence_profile.add_argument("--dataset-id")
    evidence_profile.add_argument("--limit", type=int, default=20)
    evidence_source_candidates = evidence_subparsers.add_parser("source-candidates", help="Find recurring numerical evidence groups worth registering as sources")
    evidence_source_candidates.add_argument("--topic")
    evidence_source_candidates.add_argument("--industry")
    evidence_source_candidates.add_argument("--company")
    evidence_source_candidates.add_argument("--product")
    evidence_source_candidates.add_argument("--metric")
    evidence_source_candidates.add_argument("--period")
    evidence_source_candidates.add_argument("--confidence")
    evidence_source_candidates.add_argument("--dataset-id")
    evidence_source_candidates.add_argument("--min-records", type=int, default=3)
    evidence_source_candidates.add_argument("--limit", type=int, default=50)
    evidence_export = evidence_subparsers.add_parser("export", help="Export evidence records as JSONL")
    evidence_export.add_argument("output")
    evidence_export.add_argument("--topic")
    evidence_export.add_argument("--industry")
    evidence_export.add_argument("--company")
    evidence_export.add_argument("--product")
    evidence_export.add_argument("--metric")
    evidence_export.add_argument("--period")
    evidence_export.add_argument("--confidence")
    evidence_export.add_argument("--dataset-id")
    evidence_export.add_argument("--limit", type=int, default=0)

    relations = subparsers.add_parser("relations", help="Build and inspect relation records")
    relations_subparsers = relations.add_subparsers(dest="relations_command", required=True)
    relations_subparsers.add_parser("taxonomy", help="Print relation entity and predicate taxonomy")
    relations_validate = relations_subparsers.add_parser("validate", help="Validate relation JSON records")
    relations_validate.add_argument("path")
    relations_ingest = relations_subparsers.add_parser("ingest", help="Ingest relation JSON records")
    relations_ingest.add_argument("path")
    relations_list = relations_subparsers.add_parser("list", help="List relation records")
    relations_list.add_argument("--subject")
    relations_list.add_argument("--predicate")
    relations_list.add_argument("--object")
    relations_list.add_argument("--evidence-id")
    relations_list.add_argument("--tag")
    relations_list.add_argument("--confidence")
    relations_list.add_argument("--limit", type=int, default=20)
    relations_profile = relations_subparsers.add_parser("profile", help="Profile relation coverage by predicate, entity type, source ref, tag, and confidence")
    relations_profile.add_argument("--subject")
    relations_profile.add_argument("--predicate")
    relations_profile.add_argument("--object")
    relations_profile.add_argument("--evidence-id")
    relations_profile.add_argument("--tag")
    relations_profile.add_argument("--confidence")
    relations_profile.add_argument("--limit", type=int, default=20)
    relations_neighborhood = relations_subparsers.add_parser("neighborhood", help="Read incoming and outgoing relation edges around one entity")
    relations_neighborhood.add_argument("--entity", required=True)
    relations_neighborhood.add_argument("--predicate")
    relations_neighborhood.add_argument("--tag")
    relations_neighborhood.add_argument("--confidence")
    relations_neighborhood.add_argument("--limit", type=int, default=50)
    relations_snapshot = relations_subparsers.add_parser("snapshot", help="Build a relation snapshot with alias index")
    relations_snapshot.add_argument("--subject")
    relations_snapshot.add_argument("--predicate")
    relations_snapshot.add_argument("--object")
    relations_snapshot.add_argument("--evidence-id")
    relations_snapshot.add_argument("--tag")
    relations_snapshot.add_argument("--confidence")
    relations_snapshot.add_argument("--limit", type=int, default=0)
    relations_snapshot.add_argument("--output")

    features = subparsers.add_parser("features", help="Build and inspect feature partitions")
    features_subparsers = features.add_subparsers(dest="features_command", required=True)
    features_subparsers.add_parser("list", help="List feature specs")
    features_build = features_subparsers.add_parser("build", help="Build one feature partition")
    features_build.add_argument("feature_id")
    features_build.add_argument("--as-of", required=True)
    features_build.add_argument("--window", type=int, required=True)
    features_build.add_argument("--refresh", action="store_true")
    features_read = features_subparsers.add_parser("read", help="Read one feature partition")
    features_read.add_argument("feature_id")
    features_read.add_argument("--as-of", required=True)
    features_read.add_argument("--window", type=int, required=True)
    features_read.add_argument("--limit", type=int, default=20)
    features_meta = features_subparsers.add_parser("meta", help="Read one feature partition metadata")
    features_meta.add_argument("feature_id")
    features_meta.add_argument("--as-of", required=True)
    features_meta.add_argument("--window", type=int, required=True)

    runs = subparsers.add_parser("runs", help="Record and inspect research run traces")
    runs_subparsers = runs.add_subparsers(dest="runs_command", required=True)
    runs_record = runs_subparsers.add_parser("record", help="Record a research run manifest")
    runs_record.add_argument("--question", required=True)
    runs_record.add_argument("--as-of", required=True)
    runs_record.add_argument("--mart-ref", action="append", default=[])
    runs_record.add_argument("--feature-ref", action="append", default=[])
    runs_record.add_argument("--evidence-id", action="append", default=[])
    runs_record.add_argument("--relation-id", action="append", default=[])
    runs_record.add_argument("--model-output-file")
    runs_record.add_argument("--validated-output")
    runs_record.add_argument("--run-id")
    runs_record.add_argument("--notes")
    runs_show = runs_subparsers.add_parser("show", help="Show a recorded run replay summary")
    runs_show.add_argument("run_id")

    maintain = subparsers.add_parser("maintain", help="Run maintenance workflows")
    maintain_subparsers = maintain.add_subparsers(dest="maintain_command", required=True)
    maintain_ashare = maintain_subparsers.add_parser("ashare-core", help="Maintain A-share canonical EOD datasets")
    maintain_ashare.add_argument("--as-of", required=True)
    maintain_ashare.add_argument("--lookback-trading-days", type=int, default=60)
    maintain_ashare.add_argument("--windows", default="5,20,60")
    maintain_ashare.add_argument("--refresh", action="store_true")
    maintain_ashare.add_argument("--continue-on-error", action="store_true")
    maintain_ashare.add_argument("--skip-features", action="store_true")
    maintain_main_business = maintain_subparsers.add_parser("ashare-main-business", help="Maintain A-share main business composition")
    maintain_main_business.add_argument("--period", required=True, help="Financial report period, e.g. 20251231.")
    maintain_main_business.add_argument("--security-id", action="append", default=[], help="Security id such as 000001.SZ. Repeatable.")
    maintain_main_business.add_argument("--stock-snapshot-date", help="Use stock_basic snapshot as the security pool.")
    maintain_main_business.add_argument("--segment-types", default="P,D", help="Comma-separated segment types: P product, D district.")
    maintain_main_business.add_argument("--limit", type=int, default=0, help="Optional max securities from stock snapshot.")
    maintain_main_business.add_argument("--refresh", action="store_true")
    maintain_main_business.add_argument("--continue-on-error", action="store_true")
    maintain_concept_members = maintain_subparsers.add_parser("ashare-concept-members", help="Maintain Eastmoney concept and sector membership")
    maintain_concept_members.add_argument("--snapshot-date", required=True, help="Membership snapshot date, e.g. 20260624.")
    maintain_concept_members.add_argument("--concept-id", action="append", default=[], help="Concept or sector id from ashare.dc_index. Repeatable; defaults to the dc_index partition.")
    maintain_concept_members.add_argument("--dc-index-date", help="Use ashare.dc_index trade_date as the concept pool; defaults to snapshot date.")
    maintain_concept_members.add_argument("--limit", type=int, default=0, help="Optional max concepts from dc_index.")
    maintain_concept_members.add_argument("--refresh", action="store_true")
    maintain_concept_members.add_argument("--continue-on-error", action="store_true")
    maintain_ths_concepts = maintain_subparsers.add_parser("ashare-ths-concepts", help="Maintain Tonghuashun concept and sector index membership")
    maintain_ths_concepts.add_argument("--snapshot-date", required=True, help="Membership snapshot date, e.g. 20260624.")
    maintain_ths_concepts.add_argument("--concept-id", action="append", default=[], help="Tonghuashun concept or sector id from ashare.ths_index. Repeatable; defaults to the ths_index partition.")
    maintain_ths_concepts.add_argument("--limit", type=int, default=0, help="Optional max concepts from ths_index.")
    maintain_ths_concepts.add_argument("--refresh", action="store_true")
    maintain_ths_concepts.add_argument("--continue-on-error", action="store_true")
    maintain_index_weights = maintain_subparsers.add_parser("ashare-index-weights", help="Maintain core index constituent weight snapshots")
    maintain_index_weights.add_argument("--snapshot-date", required=True, help="Snapshot date, e.g. 20260624.")
    maintain_index_weights.add_argument("--start-date", help="Fetch index weights from this date; defaults to snapshot date minus lookback days.")
    maintain_index_weights.add_argument("--lookback-days", type=int, default=90)
    maintain_index_weights.add_argument("--index-code", action="append", default=[], help="Index code such as 000300.SH. Repeatable; defaults to core index set.")
    maintain_index_weights.add_argument("--refresh", action="store_true")
    maintain_index_weights.add_argument("--continue-on-error", action="store_true")
    maintain_financials = maintain_subparsers.add_parser("ashare-financials", help="Maintain A-share financial statement datasets")
    maintain_financials.add_argument("--period", help="Financial report period, e.g. 20251231. If omitted, --as-of is mapped to the latest fully due quarterly period.")
    maintain_financials.add_argument("--as-of", help="Infer report period from this YYYYMMDD as-of date when --period is omitted.")
    maintain_financials.add_argument("--security-id", action="append", default=[], help="Security id such as 000001.SZ. Repeatable.")
    maintain_financials.add_argument("--stock-snapshot-date", help="Use stock_basic snapshot as the security pool.")
    maintain_financials.add_argument("--dataset-id", action="append", default=[], help="Financial dataset id to maintain. Repeatable; defaults to all.")
    maintain_financials.add_argument("--limit", type=int, default=0, help="Optional max securities from stock snapshot.")
    maintain_financials.add_argument("--refresh", action="store_true")
    maintain_financials.add_argument("--continue-on-error", action="store_true")
    maintain_announcement_text = maintain_subparsers.add_parser("ashare-announcement-text", help="Maintain CNINFO announcement PDF text")
    maintain_announcement_text.add_argument("--publish-date", required=True, help="Announcement publish date, e.g. 20260624.")
    maintain_announcement_text.add_argument("--announcement-id", action="append", default=[], help="Announcement id. Repeatable; defaults to all on publish date.")
    maintain_announcement_text.add_argument("--limit", type=int, default=0, help="Optional max announcements from the index partition.")
    maintain_announcement_text.add_argument("--refresh", action="store_true")
    maintain_announcement_text.add_argument("--continue-on-error", action="store_true")
    maintain_industry_report = maintain_subparsers.add_parser("industry-report-index", help="Maintain Eastmoney industry report index evidence seeds")
    maintain_industry_report.add_argument("--query-date", required=True, help="Query partition date, e.g. 20260624.")
    maintain_industry_report.add_argument("--begin", help="Begin date for the report window; defaults to query-date minus lookback-days.")
    maintain_industry_report.add_argument("--lookback-days", type=int, default=30)
    maintain_industry_report.add_argument("--max-pages", type=int, default=1)
    maintain_industry_report.add_argument("--refresh", action="store_true")
    maintain_industry_report.add_argument("--continue-on-error", action="store_true")
    maintain_status = maintain_subparsers.add_parser("status", help="Check maintenance status")
    maintain_status.add_argument("target", choices=("ashare-core",))
    maintain_status.add_argument("--as-of", required=True)
    maintain_status.add_argument("--lookback-trading-days", type=int, default=60)
    maintain_status.add_argument("--windows", default="5,20,60")
    return parser


def registry_payload(registry: Any, kind: str) -> list[dict[str, Any]]:
    values = getattr(registry, kind).values()
    return [asdict(value) for value in sorted(values, key=lambda item: item.id)]


def source_map_payload(
    registry: Any,
    *,
    data_dir: Path | str | None,
    as_of: str | None,
    domain: str | None = None,
    use: str | None = None,
    limit_datasets: int = 0,
) -> dict[str, Any]:
    inventory_entries = DataInventory(data_dir, registry=registry).datasets(as_of=as_of, domain=domain, use=use)
    inventory_by_dataset = {entry["dataset_id"]: entry for entry in inventory_entries}
    sources = [
        source_summary(
            registry,
            source_id,
            inventory_by_dataset=inventory_by_dataset,
            domain=domain,
            use=use,
            limit_datasets=limit_datasets,
        )
        for source_id in sorted(registry.sources)
    ]
    sources = [source for source in sources if source["datasets_total"] or not (domain or use)]
    return {
        "schema": "rdf.source_map.v1",
        "as_of": as_of,
        "data_dir": str(data_dir or ""),
        "filters": {
            "domain": domain,
            "use": use,
            "limit_datasets": limit_datasets,
        },
        "sources_total": len(sources),
        "sources": sources,
    }


def source_detail_payload(
    registry: Any,
    source_id: str,
    *,
    data_dir: Path | str | None,
    as_of: str | None,
    domain: str | None = None,
    use: str | None = None,
    limit_datasets: int = 0,
) -> dict[str, Any]:
    if source_id not in registry.sources:
        raise SystemExit(f"source not registered: {source_id}")
    inventory_entries = DataInventory(data_dir, registry=registry).datasets(as_of=as_of)
    payload = source_summary(
        registry,
        source_id,
        inventory_by_dataset={entry["dataset_id"]: entry for entry in inventory_entries},
        domain=domain,
        use=use,
        limit_datasets=limit_datasets,
    )
    return {
        "schema": "rdf.source_detail.v1",
        "as_of": as_of,
        "data_dir": str(data_dir or ""),
        "filters": {
            "domain": domain,
            "use": use,
            "limit_datasets": limit_datasets,
        },
        "source": payload,
    }


def current_quote_payload(
    mart_store: MartStore,
    *,
    security_ids: tuple[str, ...],
    source: str,
) -> dict[str, Any]:
    source_order = quote_source_order(source)
    attempts: list[dict[str, Any]] = []
    selected_source = ""
    records: list[dict[str, Any]] = []
    for source_id in source_order:
        try:
            records = fetch_current_quote_records(source_id, security_ids)
            attempts.append({"source_id": source_id, "rows": len(records), "error": ""})
        except SourceAdapterError as error:
            attempts.append({"source_id": source_id, "rows": 0, "error": str(error)})
            if source != "auto":
                raise SystemExit(str(error)) from error
            continue
        selected_source = source_id
        if records or source != "auto":
            break
    status = "ready" if records else "empty"
    if not selected_source and attempts:
        selected_source = str(attempts[-1]["source_id"])
        status = "unavailable"
    return {
        "schema": "rdf.current_quote.v1",
        "status": status,
        "requested_security_ids": list(security_ids),
        "source": selected_source,
        "attempts": attempts,
        "canonical_eod": canonical_eod_context(mart_store),
        "current_quote": {
            "finality": "provisional",
            "available_after": "realtime",
            "allowed_uses": ["market_context", "market_validation"],
            "forbidden_uses": ["candidate_generation", "company_business_exposure", "trade_execution"],
            "records": records,
        },
        "boundary": (
            "Current quotes are provisional observations. Use them to understand the current market state before "
            "Tushare EOD is available, but do not overwrite ashare.daily or use them alone for primary candidates."
        ),
    }


def quote_source_order(source: str) -> tuple[str, ...]:
    normalized = {"tencent": "tencent_quote", "eastmoney": "eastmoney_intraday"}.get(source, source)
    if normalized == "auto":
        return ("tencent_quote", "eastmoney_intraday")
    if normalized not in {"tencent_quote", "eastmoney_intraday"}:
        raise SystemExit(f"unsupported quote source: {source}")
    return (normalized,)


def fetch_current_quote_records(source_id: str, security_ids: tuple[str, ...]) -> list[dict[str, Any]]:
    if source_id == "tencent_quote":
        result = TencentQuoteAdapter().fetch(
            "qt.quote_snapshot",
            {"security_ids": ",".join(security_ids)},
        )
        return [normalize_current_quote_record(row) for row in result.frame.to_dict(orient="records")]
    if source_id == "eastmoney_intraday":
        normalized_security_ids = [normalize_ashare_security_id(value) for value in security_ids]
        result = EastmoneySourceAdapter(source_id="eastmoney_intraday").fetch(
            "push2.quote_snapshot",
            {"secids": ",".join(security_id_to_eastmoney_secid(value) for value in normalized_security_ids)},
        )
        code_lookup = {value.split(".", 1)[0]: value for value in normalized_security_ids}
        return [
            normalize_eastmoney_quote_record(row, code_lookup=code_lookup)
            for row in result.frame.to_dict(orient="records")
        ]
    raise SourceAdapterError(f"unsupported quote source: {source_id}")


def normalize_current_quote_record(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "security_id": str(row.get("security_id") or ""),
        "name": str(row.get("name") or ""),
        "snapshot_at": str(row.get("snapshot_at") or ""),
        "quote_time": str(row.get("quote_time") or ""),
        "price": row.get("price"),
        "pct_chg": row.get("pct_chg"),
        "change": row.get("change"),
        "open": row.get("open"),
        "high": row.get("high"),
        "low": row.get("low"),
        "prev_close": row.get("prev_close"),
        "volume": row.get("volume"),
        "amount": row.get("amount"),
        "quote_source": str(row.get("quote_source") or "tencent_quote"),
        "source_url": str(row.get("source_url") or ""),
    }


def normalize_eastmoney_quote_record(row: dict[str, Any], *, code_lookup: dict[str, str]) -> dict[str, Any]:
    code = str(row.get("f12") or row.get("security_id") or "")
    return {
        "security_id": code_lookup.get(code, normalize_ashare_security_id(code)),
        "name": str(row.get("f14") or row.get("name") or ""),
        "snapshot_at": str(row.get("snapshot_at") or ""),
        "quote_time": "",
        "price": to_float(row.get("f2") if "f2" in row else row.get("price")),
        "pct_chg": to_float(row.get("f3") if "f3" in row else row.get("pct_chg")),
        "change": None,
        "open": None,
        "high": None,
        "low": None,
        "prev_close": None,
        "volume": to_float(row.get("f5") if "f5" in row else row.get("volume")),
        "amount": to_float(row.get("f6") if "f6" in row else row.get("amount")),
        "quote_source": "eastmoney_intraday",
        "source_url": str(row.get("source_url") or ""),
    }


def canonical_eod_context(mart_store: MartStore) -> dict[str, Any]:
    try:
        partitions = mart_store.list_partitions("ashare.daily")
    except Exception:
        partitions = []
    latest = max((partition.get("trade_date", "") for partition in partitions), default="")
    return {
        "dataset_id": "ashare.daily",
        "latest_trade_date": latest,
        "finality": "final",
        "available_after": "post_close",
        "read_command": (
            f"uv run rdf datasets read ashare.daily --partition trade_date={latest} --limit 30"
            if latest
            else "uv run rdf inventory plan --as-of YYYYMMDD"
        ),
        "boundary": "Tushare-maintained canonical EOD fact. It is stable after close, but not a realtime quote source.",
    }


def normalize_ashare_security_id(value: str) -> str:
    text = str(value).strip().upper()
    if "." in text:
        code, exchange = text.split(".", 1)
        if exchange in {"SH", "SZ", "BJ"}:
            return f"{code}.{exchange}"
    if len(text) == 6 and text.isdigit():
        if text.startswith(("6", "5", "9")):
            return f"{text}.SH"
        if text.startswith(("0", "2", "3")):
            return f"{text}.SZ"
        if text.startswith(("4", "8")):
            return f"{text}.BJ"
    raise SourceAdapterError(f"unsupported A-share security id: {value}")


def security_id_to_eastmoney_secid(value: str) -> str:
    security_id = normalize_ashare_security_id(value)
    code, exchange = security_id.split(".", 1)
    market = "1" if exchange == "SH" else "0"
    return f"{market}.{code}"


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "-":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def source_summary(
    registry: Any,
    source_id: str,
    *,
    inventory_by_dataset: dict[str, dict[str, Any]],
    domain: str | None = None,
    use: str | None = None,
    limit_datasets: int = 0,
) -> dict[str, Any]:
    source = registry.require_source(source_id)
    recipes = sorted((recipe for recipe in registry.recipes.values() if recipe.source_id == source_id), key=lambda item: item.id)
    dataset_ids = sorted({recipe.target_dataset_id for recipe in recipes})
    datasets = [
        source_dataset_summary(registry, dataset_id, inventory_by_dataset.get(dataset_id))
        for dataset_id in dataset_ids
        if source_dataset_in_scope(registry.require_dataset(dataset_id), domain=domain, use=use)
    ]
    datasets.sort(key=lambda item: (item["domain"], item["dataset_id"]))
    returned_datasets = datasets[:limit_datasets] if limit_datasets and limit_datasets > 0 else datasets
    scoped_dataset_ids = {dataset["dataset_id"] for dataset in datasets}
    returned_dataset_ids = {dataset["dataset_id"] for dataset in returned_datasets}
    scoped_recipes = [recipe for recipe in recipes if recipe.target_dataset_id in scoped_dataset_ids]
    recipe_summaries = [
        {
            "id": recipe.id,
            "source_api": recipe.source_api,
            "target_dataset_id": recipe.target_dataset_id,
            "schedule": recipe.schedule,
            "lineage": asdict(recipe.lineage),
        }
        for recipe in recipes
        if recipe.target_dataset_id in returned_dataset_ids
    ]
    pipeline_summaries = source_pipeline_summaries(registry, {recipe.id for recipe in scoped_recipes})
    status_counts: dict[str, int] = {}
    coverage_counts: dict[str, int] = {}
    for dataset in datasets:
        status = str(dataset.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        coverage_status = str((dataset.get("coverage") or {}).get("status") or "unknown")
        coverage_counts[coverage_status] = coverage_counts.get(coverage_status, 0) + 1
    return {
        "id": source.id,
        "title": source.title,
        "source_role": source.source_role,
        "authority_tier": source.authority_tier,
        "transport": source.transport,
        "auth": dict(source.auth),
        "rate_limit": dict(source.rate_limit),
        "notes": source.notes,
        "boundary": source_boundary(source.source_role),
        "datasets_total": len(datasets),
        "datasets_returned": len(returned_datasets),
        "dataset_status_counts": status_counts,
        "dataset_coverage_counts": coverage_counts,
        "recipes_total": len(scoped_recipes),
        "recipes_returned": len(recipe_summaries),
        "pipelines": pipeline_summaries,
        "datasets": returned_datasets,
        "recipes": recipe_summaries,
    }


def source_dataset_in_scope(contract: Any, *, domain: str | None, use: str | None) -> bool:
    if domain and contract.domain != domain:
        return False
    if use and not contract.permits(use):
        return False
    return True


def source_dataset_summary(registry: Any, dataset_id: str, inventory_entry: dict[str, Any] | None) -> dict[str, Any]:
    contract = registry.require_dataset(dataset_id)
    recipes = registry.recipes_for_dataset(dataset_id)
    payload: dict[str, Any] = {
        "dataset_id": contract.id,
        "title": contract.title,
        "domain": contract.domain,
        "market_scope": contract.market_scope,
        "role": contract.role,
        "partition_keys": list(contract.partition_keys),
        "primary_key": list(contract.primary_key),
        "temporal": {
            "temporal_mode": contract.temporal.temporal_mode,
            "finality": contract.temporal.finality,
            "available_after": contract.temporal.available_after,
            "as_of_policy": contract.temporal.as_of_policy,
        },
        "usage": contract.usage.to_dict(),
        "boundary": dataset_usage_boundary(contract),
        "recipes": [recipe.id for recipe in recipes],
    }
    if inventory_entry:
        payload.update(
            {
                "status": inventory_entry.get("status"),
                "coverage": inventory_entry.get("coverage"),
                "partition_count": inventory_entry.get("partition_count"),
                "requested_partition": inventory_entry.get("requested_partition"),
                "requested_partition_count": inventory_entry.get("requested_partition_count"),
                "active_partition": inventory_entry.get("active_partition"),
                "active_rows": inventory_entry.get("active_rows"),
                "active_quality": inventory_entry.get("active_quality"),
                "latest_partition": inventory_entry.get("latest_partition"),
                "latest_rows": inventory_entry.get("latest_rows"),
                "latest_quality_status": inventory_entry.get("latest_quality_status"),
            }
        )
    else:
        payload.update({"status": "not_in_inventory_scope"})
    return payload


def source_pipeline_summaries(registry: Any, recipe_ids: set[str]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for pipeline in sorted(registry.pipelines.values(), key=lambda item: item.id):
        step_ids = [step.recipe_id for step in pipeline.steps]
        matched = [recipe_id for recipe_id in step_ids if recipe_id in recipe_ids]
        if not matched:
            continue
        summaries.append(
            {
                "id": pipeline.id,
                "title": pipeline.title,
                "domain": pipeline.domain,
                "cadence": pipeline.cadence,
                "matched_recipes": matched,
                "recipes_total": len(step_ids),
            }
        )
    return summaries


def source_boundary(source_role: str) -> str:
    if source_role == "canonical_eod":
        return "Stable post-close source for canonical mart facts; not a realtime or trade-execution source."
    if source_role == "official_disclosure":
        return "Official disclosure source; index metadata is evidence context, while high-confidence claims require filing text or audited evidence."
    if source_role == "intraday_observation":
        return "Provisional intraday observation source; never overwrite canonical EOD facts or generate primary candidates by itself."
    if source_role == "cross_market_reference":
        return "Cross-market reference source; use for context, evidence, or validation, not A-share primary candidate generation."
    if source_role == "research_evidence":
        return "Research evidence source; useful for triage and context, not proof of company business exposure by itself."
    if source_role == "ashare_enrichment":
        return "A-share enrichment source; classification, attention, and market context need official evidence for company-level claims."
    return "Use source according to dataset contracts, temporal policy, and evidence requirements."


def parse_key_values(items: list[str]) -> dict[str, str]:
    payload: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"invalid key/value item {item!r}; expected key=value")
        key, value = item.split("=", 1)
        if not key:
            raise SystemExit(f"invalid key/value item {item!r}; empty key")
        payload[key] = value
    return payload


def parse_int_list(raw: str) -> list[int]:
    values: list[int] = []
    for item in raw.split(","):
        text = item.strip()
        if not text:
            continue
        try:
            value = int(text)
        except ValueError as error:
            raise SystemExit(f"invalid integer item {text!r}") from error
        if value <= 0:
            raise SystemExit(f"invalid integer item {text!r}; expected positive integer")
        values.append(value)
    if not values:
        raise SystemExit("at least one integer value is required")
    return values


def dataframe_records(frame: Any, *, limit: int | None = None) -> list[dict[str, Any]]:
    if limit and limit > 0:
        frame = frame.head(limit)
    return json.loads(frame.to_json(orient="records", force_ascii=False))


ANNOUNCEMENT_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "全部": (),
    "重大事项": ("重大", "合同", "诉讼", "仲裁", "担保", "投资", "停牌", "复牌"),
    "财务报告": ("年度报告", "半年度报告", "季度报告", "财务", "业绩", "审计"),
    "融资公告": ("发行", "可转债", "募集资金", "增发", "配股", "融资"),
    "风险提示": ("风险", "退市", "ST", "异常波动", "立案", "处罚", "问询"),
    "资产重组": ("重组", "资产购买", "资产出售", "并购", "收购"),
    "信息变更": ("变更", "更正", "修订", "补充", "名称变更"),
    "持股变动": ("减持", "增持", "权益变动", "持股", "股东股份"),
}
ANNOUNCEMENT_SEARCH_COLUMNS = ("title", "short_title", "announcement_type_name", "announcement_type", "security_name")


def announcement_discovery_payload(
    *,
    start_date: str,
    end_date: str | None,
    keyword: list[str],
    category: str,
    security_id: str | None,
    security_code: str | None,
    org_id: str | None,
    stock: str | None,
    cninfo_category: str,
    column: str,
    page_size: int,
    max_pages: int,
    limit: int,
    dry_run: bool,
    adapter: CninfoSourceAdapter | None = None,
) -> dict[str, Any]:
    normalized_start = _compact_yyyymmdd(start_date)
    normalized_end = _compact_yyyymmdd(end_date or start_date)
    if _parse_compact_date(normalized_start) > _parse_compact_date(normalized_end):
        raise SystemExit("--start-date must be at or before --end-date")
    if category not in ANNOUNCEMENT_CATEGORY_KEYWORDS:
        raise SystemExit(f"invalid announcement category: {category}")
    request_params = _announcement_discovery_request_params(
        start_date=normalized_start,
        end_date=normalized_end,
        keyword=tuple(keyword),
        security_id=security_id,
        security_code=security_code,
        org_id=org_id,
        stock=stock,
        cninfo_category=cninfo_category,
        column=column,
        page_size=page_size,
        max_pages=max_pages,
    )
    filters = {
        "start_date": normalized_start,
        "end_date": normalized_end,
        "keyword": list(keyword),
        "category": category,
        "category_keywords": list(ANNOUNCEMENT_CATEGORY_KEYWORDS[category]),
        "security_id": security_id,
        "security_code": security_code,
        "org_id": org_id,
        "stock": stock,
        "cninfo_category": cninfo_category,
        "column": column,
        "page_size": page_size,
        "max_pages": max_pages,
    }
    boundary = (
        "On-demand remote CNINFO announcement discovery. Results are disclosure candidates only; "
        "this command does not write local mart data, does not imply a complete publish_date index, "
        "and titles/categories/keyword matches are not proof of announcement text claims."
    )
    if dry_run:
        return {
            "schema": "rdf.announcement_discovery_plan.v1",
            "execution_mode": "plan",
            "source_id": "cninfo",
            "source_api": "announcements",
            "will_fetch": False,
            "will_write": False,
            "request_params": request_params,
            "filters": filters,
            "boundary": boundary,
            "follow_up": {
                "fetch_text_command_template": (
                    "uv run rdf announcements fetch-text --publish-date PUBLISH_DATE "
                    "--announcement-id ANNOUNCEMENT_ID --source-url SOURCE_URL"
                ),
                "snippet_command_template": (
                    "uv run rdf evidence from-announcement-text --partition publish_date=PUBLISH_DATE "
                    "--partition announcement_id=ANNOUNCEMENT_ID --query QUERY --limit 20"
                ),
            },
        }

    source_adapter = adapter or CninfoSourceAdapter()
    response = source_adapter.fetch("announcements", request_params)
    frame = response.frame
    rows_fetched = int(len(frame))
    filtered = _filter_announcement_frame(
        frame,
        keyword=tuple(keyword),
        category_keywords=ANNOUNCEMENT_CATEGORY_KEYWORDS[category],
        security_id=security_id,
        security_code=security_code,
        org_id=org_id,
    )
    if "publish_time" in filtered.columns:
        filtered = filtered.assign(_rdf_publish_sort=pd.to_datetime(filtered["publish_time"], errors="coerce"))
        filtered = filtered.sort_values(["_rdf_publish_sort", "publish_date", "announcement_id"], ascending=[False, False, False])
        filtered = filtered.drop(columns=["_rdf_publish_sort"])
    elif "publish_date" in filtered.columns:
        filtered = filtered.sort_values(["publish_date", "announcement_id"], ascending=[False, False])
    records = dataframe_records(_announcement_records_with_commands(filtered), limit=limit)
    return {
        "schema": "rdf.announcement_discovery_result.v1",
        "execution_mode": "remote_fetch_no_write",
        "source_id": response.source_id,
        "source_api": response.api_name,
        "will_write": False,
        "request_params": request_params,
        "filters": filters,
        "rows_total_fetched": rows_fetched,
        "records_total_matched": int(len(filtered)),
        "records_returned": len(records),
        "boundary": boundary,
        "records": records,
    }


def _announcement_discovery_request_params(
    *,
    start_date: str,
    end_date: str,
    keyword: tuple[str, ...],
    security_id: str | None,
    security_code: str | None,
    org_id: str | None,
    stock: str | None,
    cninfo_category: str,
    column: str,
    page_size: int,
    max_pages: int,
) -> dict[str, Any]:
    if page_size <= 0:
        raise SystemExit("--page-size must be positive")
    if max_pages <= 0:
        raise SystemExit("--max-pages must be positive")
    return {
        "start_date": start_date,
        "end_date": end_date,
        "column": str(column or "szse,sse,bse"),
        "stock": _cninfo_stock_selector(stock=stock, security_id=security_id, security_code=security_code, org_id=org_id),
        "category": str(cninfo_category or ""),
        "keyword": " ".join(str(item).strip() for item in keyword if str(item).strip()),
        "page_size": page_size,
        "max_pages": max_pages,
    }


def _cninfo_stock_selector(
    *,
    stock: str | None,
    security_id: str | None,
    security_code: str | None,
    org_id: str | None,
) -> str:
    if stock:
        return str(stock).strip()
    code = str(security_code or "").strip()
    if not code and security_id:
        code = str(security_id).strip().split(".", 1)[0]
    if code and org_id:
        return f"{code},{str(org_id).strip()}"
    return code


def announcement_index_search_payload(
    store: MartStore,
    *,
    publish_date: str | None,
    as_of: str | None,
    lookback_days: int,
    keyword: list[str],
    category: str,
    security_id: str | None,
    security_code: str | None,
    org_id: str | None,
    limit: int,
) -> dict[str, Any]:
    contract = store.registry.require_dataset("ashare.announcements")
    partitions = _announcement_search_partitions(store, publish_date=publish_date, as_of=as_of, lookback_days=lookback_days)
    frames = [store.read("ashare.announcements", partition) for partition in partitions]
    frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    rows_scanned = int(len(frame))
    filters = {
        "publish_date": _compact_yyyymmdd(publish_date) if publish_date else None,
        "as_of": _compact_yyyymmdd(as_of) if as_of else None,
        "lookback_days": lookback_days if not publish_date else None,
        "keyword": list(keyword),
        "category": category,
        "category_keywords": list(ANNOUNCEMENT_CATEGORY_KEYWORDS[category]),
        "security_id": security_id,
        "security_code": security_code,
        "org_id": org_id,
    }
    filtered = _filter_announcement_frame(
        frame,
        keyword=tuple(keyword),
        category_keywords=ANNOUNCEMENT_CATEGORY_KEYWORDS[category],
        security_id=security_id,
        security_code=security_code,
        org_id=org_id,
    )
    if "publish_time" in filtered.columns:
        filtered = filtered.assign(_rdf_publish_sort=pd.to_datetime(filtered["publish_time"], errors="coerce"))
        filtered = filtered.sort_values(["_rdf_publish_sort", "publish_date", "announcement_id"], ascending=[False, False, False])
        filtered = filtered.drop(columns=["_rdf_publish_sort"])
    elif "publish_date" in filtered.columns:
        filtered = filtered.sort_values(["publish_date", "announcement_id"], ascending=[False, False])
    records = dataframe_records(_announcement_records_with_commands(filtered), limit=limit)
    return {
        "schema": "rdf.announcement_index_search.v1",
        "dataset_id": "ashare.announcements",
        "filters": filters,
        "category_filter_mode": "title_or_type_keyword_heuristic",
        "partitions_scanned": len(partitions),
        "partition_dates": [partition["publish_date"] for partition in partitions],
        "rows_total_scanned": rows_scanned,
        "records_total_matched": int(len(filtered)),
        "records_returned": len(records),
        "usage": contract.usage.to_dict(),
        "temporal": {
            "temporal_mode": contract.temporal.temporal_mode,
            "finality": contract.temporal.finality,
            "available_after": contract.temporal.available_after,
            "as_of_policy": contract.temporal.as_of_policy,
        },
        "boundary": (
            "Official CNINFO announcement index search. Results identify disclosure entries and PDF URLs only; "
            "titles, categories, and keyword matches are triage signals, not proof of announcement text claims."
        ),
        "partitions": [
            table_partition_summary(store, "ashare.announcements", partition, meta=store.read_meta("ashare.announcements", partition))
            for partition in partitions
        ],
        "records": records,
    }


def _announcement_search_partitions(
    store: MartStore,
    *,
    publish_date: str | None,
    as_of: str | None,
    lookback_days: int,
) -> list[dict[str, str]]:
    partitions = sorted_partitions(store, "ashare.announcements")
    if not partitions:
        raise SystemExit("ashare.announcements: no local mart partitions available")
    if publish_date:
        target = {"publish_date": _compact_yyyymmdd(publish_date)}
        if target not in partitions:
            raise SystemExit(f"ashare.announcements: missing local publish_date partition {target['publish_date']}")
        return [target]
    if lookback_days <= 0:
        raise SystemExit("--lookback-days must be positive")
    if as_of:
        end = _parse_compact_date(as_of)
        start = end - timedelta(days=lookback_days - 1)
        selected = [
            partition
            for partition in partitions
            if start <= _parse_compact_date(partition["publish_date"]) <= end
        ]
        if not selected:
            raise SystemExit(f"ashare.announcements: no local partitions in {start:%Y%m%d}..{end:%Y%m%d}")
        return selected
    return [latest_partition(store, "ashare.announcements")]


def _filter_announcement_frame(
    frame: pd.DataFrame,
    *,
    keyword: tuple[str, ...],
    category_keywords: tuple[str, ...],
    security_id: str | None,
    security_code: str | None,
    org_id: str | None,
) -> pd.DataFrame:
    output = frame.copy()
    if security_id and "security_id" in output.columns:
        output = output[output["security_id"].fillna("").astype(str) == str(security_id)]
    if security_code and "security_code" in output.columns:
        output = output[output["security_code"].fillna("").astype(str) == str(security_code)]
    if org_id and "org_id" in output.columns:
        output = output[output["org_id"].fillna("").astype(str) == str(org_id)]
    for term in keyword:
        output = _filter_text_contains_any(output, (term,))
    if category_keywords:
        output = _filter_text_contains_any(output, category_keywords)
    return output.reset_index(drop=True)


def _filter_text_contains_any(frame: pd.DataFrame, terms: tuple[str, ...]) -> pd.DataFrame:
    if frame.empty or not terms:
        return frame
    mask = pd.Series(False, index=frame.index)
    normalized_terms = [str(term).strip().lower() for term in terms if str(term).strip()]
    if not normalized_terms:
        return frame
    for column in ANNOUNCEMENT_SEARCH_COLUMNS:
        if column not in frame.columns:
            continue
        values = frame[column].fillna("").astype(str).str.lower()
        column_mask = pd.Series(False, index=frame.index)
        for term in normalized_terms:
            column_mask = column_mask | values.str.contains(term, regex=False, na=False)
        mask = mask | column_mask
    return frame[mask].copy()


def _announcement_records_with_commands(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    output = frame.copy()
    read_text_commands: list[str] = []
    snippet_commands: list[str] = []
    for _, row in output.iterrows():
        publish_date = str(row.get("publish_date") or "")
        announcement_id = str(row.get("announcement_id") or "")
        read_text_commands.append(_announcement_fetch_text_command(row))
        snippet_commands.append(_announcement_snippet_command(publish_date=publish_date, announcement_id=announcement_id))
    output["read_text_command"] = read_text_commands
    output["snippet_command_template"] = snippet_commands
    return output


def _announcement_fetch_text_command(row: pd.Series) -> str:
    publish_date = str(row.get("publish_date") or "")
    announcement_id = str(row.get("announcement_id") or "")
    command = [
        "uv",
        "run",
        "rdf",
        "announcements",
        "fetch-text",
        "--publish-date",
        publish_date,
        "--announcement-id",
        announcement_id,
        "--source-url",
        str(row.get("source_url") or ""),
    ]
    optional_args = (
        ("--security-id", row.get("security_id")),
        ("--security-name", row.get("security_name")),
        ("--title", row.get("title")),
    )
    for flag, value in optional_args:
        text_value = str(value or "").strip()
        if text_value:
            command.extend([flag, text_value])
    return shlex.join(command)


def _announcement_snippet_command(*, publish_date: str, announcement_id: str) -> str:
    return shlex.join(
        [
            "uv",
            "run",
            "rdf",
            "evidence",
            "from-announcement-text",
            "--partition",
            f"publish_date={publish_date}",
            "--partition",
            f"announcement_id={announcement_id}",
            "--query",
            "QUERY",
            "--limit",
            "20",
        ]
    )


def _compact_yyyymmdd(value: str | None) -> str:
    if not value:
        raise SystemExit("date value is required")
    text = str(value).strip()
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        text = text.replace("-", "")
    if len(text) != 8 or not text.isdigit():
        raise SystemExit(f"invalid date {value!r}; expected YYYYMMDD or YYYY-MM-DD")
    return text


def _parse_compact_date(value: str) -> datetime:
    return datetime.strptime(_compact_yyyymmdd(value), "%Y%m%d")


def dataset_search_payload(
    inventory: DataInventory,
    *,
    query: str,
    as_of: str | None,
    domain: str | None,
    use: str | None,
    limit: int,
) -> dict[str, Any]:
    terms = dataset_search_terms(query)
    entries = inventory.datasets(as_of=as_of, domain=domain, use=use)
    matches = []
    for entry in entries:
        score, matched_terms = dataset_search_score(inventory, entry, terms)
        if terms and score <= 0:
            continue
        contract = inventory.registry.require_dataset(entry["dataset_id"])
        item = dataset_search_item(contract, entry, score=score, matched_terms=matched_terms, as_of=as_of)
        matches.append(item)
    matches.sort(key=lambda item: (-int(item["score"]), item["dataset_id"]))
    if limit and limit > 0:
        matches = matches[:limit]
    return {
        "schema": "rdf.dataset_search.v1",
        "query": query,
        "expanded_terms": terms,
        "filters": {"as_of": as_of, "domain": domain, "use": use, "limit": limit},
        "items_total": len(matches),
        "items": matches,
        "note": "Search only reads registry contracts and local inventory. It does not fetch, ingest, or infer company exposure.",
    }


def dataset_search_terms(query: str) -> list[str]:
    text = query.strip().lower()
    terms = [item for item in text.replace(",", " ").replace("，", " ").split() if item]
    if text and text not in terms:
        terms.append(text)
    expansions: list[str] = []
    for alias, values in DATASET_SEARCH_ALIASES.items():
        if _dataset_alias_matches(alias, text, terms):
            expansions.extend(values)
    output = []
    for term in terms + expansions:
        if term and term not in output:
            output.append(term)
    return output


def _dataset_alias_matches(alias: str, text: str, terms: list[str]) -> bool:
    if alias.isascii() and alias.isalnum() and len(alias) <= 3:
        return alias in terms
    return alias in text


DATASET_SEARCH_ALIASES: dict[str, tuple[str, ...]] = {
    "日线": ("daily", "ashare.daily", "trade_date", "close", "pct_chg"),
    "行情": ("daily", "quote", "price", "close", "pct_chg"),
    "交易日": ("trade_calendar", "calendar", "is_open"),
    "股票池": ("stock_basic", "identity", "list_status"),
    "身份": ("stock_basic", "company_profile", "name_changes", "identity", "reference"),
    "公司资料": ("company_profile", "profile", "main_business", "office"),
    "曾用名": ("name_changes", "alias", "historical stock names"),
    "财务": ("financial", "income", "balance", "cash_flow", "indicator", "financial_analysis"),
    "三表": ("income_statement", "balance_sheet", "cash_flow"),
    "利润": ("income_statement", "n_income", "revenue"),
    "现金流": ("cash_flow", "n_cashflow_act"),
    "主营": ("main_business", "segment", "company_business_exposure"),
    "公告": ("announcement", "cninfo", "official", "disclosure", "evidence"),
    "研报": ("report", "eastmoney_report_index", "industry_evidence"),
    "行业": ("industry", "sw", "ci", "industry_members", "industry_strength"),
    "概念": ("concept", "dc_index", "concept_members", "ths_concept"),
    "题材": ("concept", "theme", "limit_concept", "kpl_concept", "ths_concept"),
    "涨停": ("limit", "price_limits", "limit_list", "limit_step", "kpl"),
    "连板": ("limit_step", "limit_up_days", "kpl"),
    "热榜": ("hot_rank", "ths_hot", "dc_hot", "attention"),
    "人气": ("hot_rank", "heat", "rank"),
    "龙虎榜": ("top_list", "reason"),
    "资金": ("moneyflow", "net_amount", "north_money", "south_money"),
    "资金流": ("moneyflow", "net_amount", "net_mf_amount", "north_money", "south_money"),
    "北向": ("hsgt", "northbound", "connect", "north_money"),
    "陆股通": ("northbound", "hsgt", "connect"),
    "融资融券": ("margin", "rzye", "rqye"),
    "两融": ("margin", "rzye", "rqye"),
    "筹码": ("chip", "cyq", "winner_rate", "cost"),
    "股东": ("shareholder", "holder", "pledge", "ownership"),
    "质押": ("pledge", "share_pledge"),
    "增减持": ("shareholder_trades", "holdertrade", "change_vol"),
    "回购": ("repurchase", "buyback", "volume", "amount"),
    "大宗": ("block_trades", "buyer", "seller"),
    "指数": ("index", "index_daily", "index_weights", "weight"),
    "跨市场": ("global", "sec", "cross_market"),
    "美股": ("global", "sec", "ticker_cik", "companyfacts"),
    "sec": ("sec", "filings", "companyfacts", "ticker_cik"),
    "盘中": ("intraday", "snapshot", "provisional"),
    "当前行情": ("intraday", "snapshot", "provisional"),
    "当前": ("intraday", "snapshot", "provisional"),
    "实时": ("intraday", "snapshot", "provisional"),
    "实时行情": ("intraday", "snapshot", "provisional"),
}


def dataset_search_score(inventory: DataInventory, entry: dict[str, Any], terms: list[str]) -> tuple[int, list[str]]:
    if not terms:
        return 0, []
    contract = inventory.registry.require_dataset(entry["dataset_id"])
    recipes = inventory.registry.recipes_for_dataset(contract.id)
    weighted_fields = (
        (20, [contract.id]),
        (14, [contract.title]),
        (10, [contract.domain, contract.role, contract.market_scope]),
        (9, list(contract.usage.allowed_uses) + list(contract.usage.forbidden_uses)),
        (7, list(contract.partition_keys) + list(contract.primary_key)),
        (6, list(contract.required_columns) + list(contract.analysis_columns) + list(contract.units)),
        (6, entry.get("source_ids", []) + entry.get("recipes", [])),
        (5, [recipe.source_api for recipe in recipes] + [recipe.notes for recipe in recipes]),
    )
    score = 0
    matched_terms: list[str] = []
    for term in terms:
        term_score = 0
        for weight, values in weighted_fields:
            if any(term in str(value).lower() for value in values):
                term_score += weight
        if term_score:
            matched_terms.append(term)
            score += term_score
    if entry.get("status") == "ready":
        score += 3
    return score, matched_terms


def dataset_search_item(
    contract: Any,
    entry: dict[str, Any],
    *,
    score: int,
    matched_terms: list[str],
    as_of: str | None,
) -> dict[str, Any]:
    active_partition = entry.get("active_partition")
    latest_partition = entry.get("latest_partition")
    suggested_columns = suggested_dataset_columns(contract)
    return {
        "schema": "rdf.dataset_search_item.v1",
        "dataset_id": contract.id,
        "title": contract.title,
        "score": score,
        "matched_terms": matched_terms,
        "domain": contract.domain,
        "market_scope": contract.market_scope,
        "role": contract.role,
        "temporal": entry["temporal"],
        "usage": entry["usage"],
        "partition_keys": list(contract.partition_keys),
        "primary_key": list(contract.primary_key),
        "analysis_columns": list(contract.analysis_columns),
        "status": entry.get("status"),
        "coverage": entry.get("coverage"),
        "requested_partition": entry.get("requested_partition"),
        "requested_partition_count": entry.get("requested_partition_count", 0),
        "active_partition": active_partition,
        "active_rows": entry.get("active_rows", 0),
        "active_quality_status": str((entry.get("active_quality") or {}).get("status", "")),
        "latest_partition": latest_partition,
        "latest_rows": entry.get("latest_rows", 0),
        "latest_quality_status": entry.get("latest_quality_status", ""),
        "partition_count": entry.get("partition_count", 0),
        "source_ids": entry.get("source_ids", []),
        "recipes": entry.get("recipes", []),
        "suggested_columns": suggested_columns,
        "commands": dataset_search_commands(
            contract.id,
            requested_partition=entry.get("requested_partition"),
            active_partition=active_partition,
            latest_partition=latest_partition,
            as_of=as_of,
            partition_keys=contract.partition_keys,
            columns=suggested_columns,
        ),
        "boundary": dataset_usage_boundary(contract),
    }


def suggested_dataset_columns(contract: Any, *, limit: int = 10) -> list[str]:
    columns: list[str] = []
    for column in list(contract.primary_key) + list(contract.analysis_columns):
        if column not in columns:
            columns.append(column)
        if len(columns) >= limit:
            break
    return columns


def dataset_search_commands(
    dataset_id: str,
    *,
    requested_partition: dict[str, str] | None,
    active_partition: dict[str, str] | None,
    latest_partition: dict[str, str] | None,
    as_of: str | None,
    partition_keys: tuple[str, ...],
    columns: list[str],
) -> dict[str, Any]:
    commands: dict[str, Any] = {
        "partitions": _command_payload(["uv", "run", "rdf", "datasets", "partitions", dataset_id, "--limit", "10"]),
    }
    if active_partition:
        commands["read_active"] = _dataset_read_command(dataset_id, active_partition, columns=columns)
        commands["meta_active"] = _dataset_meta_command(dataset_id, active_partition)
    if requested_partition and len(requested_partition) < len(partition_keys):
        commands["scan_requested"] = _dataset_scan_command(dataset_id, requested_partition, columns=columns)
    if latest_partition:
        latest = ["uv", "run", "rdf", "datasets", "latest", dataset_id]
        if columns:
            latest.extend(["--columns", *columns])
        latest.extend(["--limit", "20"])
        commands["read_latest"] = _command_payload(latest)
    if as_of and len(partition_keys) == 1:
        window = ["uv", "run", "rdf", "datasets", "read-window", dataset_id, "--as-of", as_of, "--count", "20"]
        if columns:
            window.extend(["--columns", *columns])
        window.extend(["--limit", "100"])
        commands["read_window"] = _command_payload(window)
    return commands


def _dataset_read_command(dataset_id: str, partition: dict[str, str], *, columns: list[str]) -> dict[str, Any]:
    argv = ["uv", "run", "rdf", "datasets", "read", dataset_id]
    for key, value in partition.items():
        argv.extend(["--partition", f"{key}={value}"])
    if columns:
        argv.extend(["--columns", *columns])
    argv.extend(["--limit", "20"])
    return _command_payload(argv)


def _dataset_scan_command(dataset_id: str, partition_filter: dict[str, str], *, columns: list[str]) -> dict[str, Any]:
    argv = ["uv", "run", "rdf", "datasets", "scan", dataset_id]
    for key, value in partition_filter.items():
        argv.extend(["--partition", f"{key}={value}"])
    if columns:
        argv.extend(["--columns", *columns])
    argv.extend(["--limit", "20"])
    return _command_payload(argv)


def _dataset_meta_command(dataset_id: str, partition: dict[str, str]) -> dict[str, Any]:
    argv = ["uv", "run", "rdf", "datasets", "meta", dataset_id]
    for key, value in partition.items():
        argv.extend(["--partition", f"{key}={value}"])
    return _command_payload(argv)


def _command_payload(command: list[str]) -> dict[str, Any]:
    return {"argv": command, "text": " ".join(command)}


def dataset_usage_boundary(contract: Any) -> str:
    forbidden = set(contract.usage.forbidden_uses)
    if contract.id in {"ashare.shareholder_trades", "ashare.repurchase_events"}:
        return "Structured corporate action event context; use as evidence triage and verify official announcements before high-confidence claims."
    if contract.id == "ashare.earnings_forecast_events":
        return "Structured earnings forecast announcement context; use as financial event triage and verify official announcements before high-confidence claims."
    if "company_business_exposure" in forbidden:
        return "Cannot prove company business exposure by itself; use as market, context, candidate, or validation data only."
    if contract.role == "evidence_seed" or contract.permits("evidence"):
        return "Evidence seed or evidence-capable data; still extract concrete claims with source, date, and provenance before high-confidence conclusions."
    if contract.permits("company_business_exposure"):
        return "Can support company business exposure research, but high-confidence conclusions still require auditable source evidence and explicit claims."
    return "Use within its declared allowed uses; do not infer beyond the dataset contract."


def feature_usage_boundary(spec: Any) -> str:
    if spec.permits("company_business_exposure"):
        return "Feature can support business-exposure research only as a derived signal; high-confidence conclusions still require auditable source evidence."
    return "Feature is a derived signal for ranking, screening, or validation. It cannot prove company business exposure or replace source facts."


def feature_read_payload(store: FeatureStore, spec: Any, *, as_of: str, window: int, limit: int) -> dict[str, Any]:
    meta = store.load_meta(spec.id, domain=spec.domain, as_of=as_of, window=window).to_dict()
    frame = store.read_partition(spec.id, domain=spec.domain, as_of=as_of, window=window)
    records = dataframe_records(frame, limit=limit)
    return {
        "schema": "rdf.feature_read.v1",
        "feature_id": spec.id,
        "title": spec.title,
        "version": spec.version,
        "domain": spec.domain,
        "role": spec.role,
        "partition": {"as_of": as_of, "window": str(window)},
        "rows_total": int(meta.get("rows", 0)),
        "records_returned": len(records),
        "columns": list(meta.get("columns", [])),
        "inputs": feature_input_context(spec, list(meta.get("inputs", []))),
        "quality": dict(meta.get("quality") or {}),
        "generated_at": str(meta.get("generated_at", "")),
        "path": str(store.partition_path(spec, as_of=as_of, window=window)),
        "usage": spec.usage.to_dict(),
        "boundary": feature_usage_boundary(spec),
        "spec": spec.to_dict(),
        "records": records,
    }


def feature_input_context(spec: Any, meta_inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_dataset = {str(item.get("dataset_id")): dict(item) for item in meta_inputs if item.get("dataset_id")}
    output = []
    for input_spec in spec.inputs:
        raw = by_dataset.get(input_spec.dataset_id, {})
        output.append(
            {
                "dataset_id": input_spec.dataset_id,
                "role": input_spec.role,
                "supports": list(input_spec.supports),
                "columns": list(input_spec.columns),
                "status": str(raw.get("status", "missing")),
                "rows": int(raw.get("rows", 0)),
                "message": str(raw.get("message", "")),
            }
        )
    extra = [dict(item) for item in meta_inputs if item.get("dataset_id") not in {input_spec.dataset_id for input_spec in spec.inputs}]
    return output + extra


def dataset_read_payload(
    store: MartStore,
    dataset_id: str,
    *,
    partition: dict[str, str],
    columns: list[str] | None,
    limit: int,
) -> dict[str, Any]:
    contract = store.registry.require_dataset(dataset_id)
    meta = store.read_meta(dataset_id, partition)
    frame = store.read(dataset_id, partition, columns=columns)
    records = dataframe_records(frame, limit=limit)
    return {
        "schema": "rdf.dataset_read.v1",
        "dataset_id": dataset_id,
        "partition": partition,
        "rows_total": int(meta.get("rows", 0)),
        "records_returned": len(records),
        "columns": list(columns or frame.columns),
        "temporal": dict(meta.get("temporal") or {}),
        "usage": contract.usage.to_dict(),
        "boundary": dataset_usage_boundary(contract),
        "partition_meta": table_partition_summary(store, dataset_id, partition, meta=meta),
        "records": records,
    }


def dataset_read_window_payload(
    store: MartStore,
    dataset_id: str,
    *,
    as_of: str,
    count: int,
    partition_key: str | None,
    columns: list[str] | None,
    limit: int,
) -> dict[str, Any]:
    contract = store.registry.require_dataset(dataset_id)
    key = partition_key or (contract.partition_keys[0] if len(contract.partition_keys) == 1 else "")
    if not key:
        raise SystemExit(f"{dataset_id}: read-window requires --partition-key for multi-key datasets")
    if key not in contract.partition_keys:
        raise SystemExit(f"{dataset_id}: unknown partition key {key!r}")
    candidates = [
        partition
        for partition in store.list_partitions(dataset_id)
        if key in partition and str(partition[key]) <= str(as_of)
    ]
    selected_desc = sorted(candidates, key=lambda item: item[key], reverse=True)[: max(count, 0)]
    if not selected_desc:
        raise SystemExit(f"{dataset_id}: no partitions at or before {as_of}")
    selected = list(reversed(selected_desc))
    frames = [store.read(dataset_id, partition, columns=columns) for partition in selected]
    frame = pd.concat(frames, ignore_index=True)
    records = dataframe_records(frame, limit=limit)
    partition_entries = [
        table_partition_summary(store, dataset_id, partition, meta=store.read_meta(dataset_id, partition))
        for partition in selected
    ]
    return {
        "schema": "rdf.dataset_read_window.v1",
        "dataset_id": dataset_id,
        "as_of": as_of,
        "count_requested": count,
        "partition_key": key,
        "partitions_scanned": len(selected),
        "rows_total_scanned": sum(int(item.get("rows", 0)) for item in partition_entries),
        "records_returned": len(records),
        "columns": list(columns or frame.columns),
        "temporal": {
            "temporal_mode": contract.temporal.temporal_mode,
            "finality": contract.temporal.finality,
            "available_after": contract.temporal.available_after,
            "as_of_policy": contract.temporal.as_of_policy,
        },
        "usage": contract.usage.to_dict(),
        "boundary": dataset_usage_boundary(contract),
        "partitions": partition_entries,
        "records": records,
    }


def dataset_partitions_payload(store: MartStore, dataset_id: str, *, limit: int) -> dict[str, Any]:
    partitions = sorted_partitions(store, dataset_id)
    selected = partitions[:limit] if limit and limit > 0 else partitions
    entries = []
    for partition in selected:
        meta = store.read_meta(dataset_id, partition)
        quality = dict(meta.get("quality") or {})
        entries.append(
            {
                "partition": partition,
                "rows": int(meta.get("rows", 0)),
                "quality_status": str(quality.get("status", "")),
                "published_at": str(meta.get("published_at", "")),
                "path": str(store.partition_path(dataset_id, partition)),
                "lineage": dict(meta.get("lineage") or {}),
            }
        )
    return {
        "schema": "rdf.dataset_partitions.v1",
        "dataset_id": dataset_id,
        "partitions_total": len(partitions),
        "partitions_returned": len(entries),
        "partitions": entries,
    }


def table_partition_summary(
    store: MartStore,
    dataset_id: str,
    partition: dict[str, str],
    *,
    meta: dict[str, Any],
) -> dict[str, Any]:
    quality = dict(meta.get("quality") or {})
    return {
        "partition": partition,
        "rows": int(meta.get("rows", 0)),
        "quality_status": str(quality.get("status", "")),
        "quality": quality,
        "published_at": str(meta.get("published_at", "")),
        "path": str(store.partition_path(dataset_id, partition)),
        "lineage": dict(meta.get("lineage") or {}),
    }


def dataset_scan_payload(
    store: MartStore,
    dataset_id: str,
    *,
    partition_filter: dict[str, str],
    columns: list[str] | None,
    limit: int,
    partition_limit: int,
) -> dict[str, Any]:
    contract = store.registry.require_dataset(dataset_id)
    matching = store.matching_partitions(dataset_id, partition_filter)
    selected = matching[:partition_limit] if partition_limit and partition_limit > 0 else matching
    if not selected:
        raise SystemExit(f"{dataset_id}: no local mart partitions match {partition_filter}")
    frame = store.read_matching(
        dataset_id,
        partition_filter,
        columns=columns,
        partition_limit=partition_limit,
    )
    partitions = []
    rows_total = 0
    for partition in selected:
        meta = store.read_meta(dataset_id, partition)
        rows = int(meta.get("rows", 0))
        rows_total += rows
        partitions.append(table_partition_summary(store, dataset_id, partition, meta=meta))
    records = dataframe_records(frame, limit=limit)
    return {
        "schema": "rdf.dataset_scan.v1",
        "dataset_id": dataset_id,
        "partition_filter": partition_filter,
        "partitions_total_matching": len(matching),
        "partitions_scanned": len(selected),
        "partition_limit": partition_limit,
        "rows_total_scanned": rows_total,
        "records_returned": len(records),
        "columns": list(columns or frame.columns),
        "temporal": {
            "temporal_mode": contract.temporal.temporal_mode,
            "finality": contract.temporal.finality,
            "available_after": contract.temporal.available_after,
            "as_of_policy": contract.temporal.as_of_policy,
        },
        "usage": contract.usage.to_dict(),
        "boundary": dataset_usage_boundary(contract),
        "partitions": partitions,
        "records": records,
    }


def dataset_latest_payload(
    store: MartStore,
    dataset_id: str,
    *,
    columns: list[str] | None,
    limit: int,
) -> dict[str, Any]:
    contract = store.registry.require_dataset(dataset_id)
    partition = latest_partition(store, dataset_id)
    meta = store.read_meta(dataset_id, partition)
    frame = store.read(dataset_id, partition, columns=columns)
    quality = dict(meta.get("quality") or {})
    return {
        "schema": "rdf.dataset_latest_read.v1",
        "dataset_id": dataset_id,
        "partition": partition,
        "rows_total": int(meta.get("rows", 0)),
        "records_returned": len(frame.head(limit)) if limit and limit > 0 else len(frame),
        "quality_status": str(quality.get("status", "")),
        "quality": quality,
        "published_at": str(meta.get("published_at", "")),
        "path": str(store.partition_path(dataset_id, partition)),
        "lineage": dict(meta.get("lineage") or {}),
        "temporal": dict(meta.get("temporal") or {}),
        "usage": contract.usage.to_dict(),
        "boundary": dataset_usage_boundary(contract),
        "records": dataframe_records(frame, limit=limit),
    }


def latest_partition(store: MartStore, dataset_id: str) -> dict[str, str]:
    partitions = sorted_partitions(store, dataset_id)
    if not partitions:
        raise SystemExit(f"{dataset_id}: no local mart partitions")
    return partitions[0]


def sorted_partitions(store: MartStore, dataset_id: str) -> list[dict[str, str]]:
    contract = store.registry.require_dataset(dataset_id)
    partitions = store.list_partitions(dataset_id)
    return sorted(
        partitions,
        key=lambda partition: tuple(str(partition.get(key, "")) for key in contract.partition_keys),
        reverse=True,
    )


def ingest_summary(result: Any, *, id_key: str, sample_size: int = 20) -> dict[str, Any]:
    payload = result.to_dict()
    ids = list(payload.pop(id_key, []))
    payload[f"{id_key}_total"] = len(ids)
    payload[f"{id_key}_sample"] = ids[:sample_size]
    if len(ids) > sample_size:
        payload[f"{id_key}_omitted"] = len(ids) - sample_size
    return payload


def load_project_env(path: Path | None = None) -> None:
    env_path = path or project_root() / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = _parse_env_value(value)


def _parse_env_value(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def load_json_records(path: str) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [dict(item) for item in payload]
    return [dict(payload)]


def load_evidence_records(path: str) -> list[EvidenceRecord]:
    return [EvidenceRecord.from_dict(item) for item in load_json_records(path)]


def load_evidence_source_specs(path: str) -> list[EvidenceSourceSpec]:
    return [validate_evidence_source(EvidenceSourceSpec.from_dict(item)) for item in load_json_records(path)]


def load_relation_records(path: str) -> list[RelationRecord]:
    return [RelationRecord.from_dict(item) for item in load_json_records(path)]

# Industry Evidence Layer Design

Updated: 2026-06-24

## Purpose

The industry evidence layer captures industry-specific facts that do not fit the core A-share Tushare provider: commodity prices, capacity, inventory, utilization, orders, tenders, overseas capex, policy, and association data.

The intended workflow is hybrid:

```text
research question
  -> LLM search prompt
  -> structured evidence JSON
  -> evidence validation and scoring
  -> industry_evidence_bundle
  -> high-repeat sources promoted into adapters
```

Prompt search is for exploration and long-tail facts. Adapters are for stable, repeated, numeric, auditable data.

## Boundary

Do not put this layer into `AShareProvider.call()`. That API is tied to Tushare registry metadata, Tushare permission checks, and A-share cache behavior.

Recommended future structure:

```text
prompts/industry-evidence-prompt.md
references/industry-source-registry.json
references/industry-source-maps.json

future runtime, only after design approval:
src/ashare_data_provider/industry_evidence/
  schemas.py
  bundle.py
  adapters/
```

## Evidence Schema

Each record should be valid whether it was produced by LLM-assisted search or by a deterministic adapter.

```json
{
  "evidence_id": "stable hash of source_url + metric + period + value",
  "claim": "A verifiable factual statement",
  "topic": "price|inventory|capacity|utilization|order|tender|capex|policy|association_data|other",
  "industry": "lithium_battery",
  "product": "lithium_carbonate",
  "company": null,
  "region": "China",
  "metric": "warehouse_receipt",
  "value": 12345,
  "unit": "ton",
  "period": "2026-06-24",
  "frequency": "daily",
  "source_type": "exchange",
  "source_name": "Guangzhou Futures Exchange",
  "source_url": "https://www.gfex.com.cn/gfex/cdrb/hqsj_tjsj.shtml",
  "published_at": "2026-06-24",
  "collected_at": "2026-06-24T00:00:00+08:00",
  "confidence": "high",
  "verification": "official_single_source",
  "adapter_source": null,
  "raw": {}
}
```

## Source Promotion Rules

| Trigger | Action |
|---|---|
| Same source appears in at least 3 research runs | Add to source registry |
| Same metric is needed weekly/monthly | Build adapter candidate |
| Numeric evidence feeds scoring, ranking, or backtesting | Adapter required before production use |
| Source is official, exchange, regulator, SEC, EIA, or statistics agency | Prefer adapter |
| Source has heavy anti-bot, unstable fields, or paywall | Keep as prompt evidence or manual-review source |
| Evidence is narrative and one-off | Keep prompt-only |

## Initial Adapter Priorities

P0 sources are high-trust and likely to be recurring:

- SEC EDGAR APIs for filings, companyfacts, and capex evidence.
- EIA Weekly Petroleum Status Report and EIA Open Data for petroleum inventory and supply.
- National Bureau of Statistics industrial capacity utilization.
- SHFE/GFEX/INE warehouse receipts and exchange inventory-like data.
- National public-resource transaction platform for tenders and awards.

P1 sources need source-map work before adapter work:

- CAAM and auto-statistics sources for auto and NEV sales.
- Power-battery alliance releases, once a stable original source is confirmed.
- SIA/WSTS/SEMI semiconductor association data.
- Policy sources from gov.cn, NDRC, MIIT, NEA, MEE, and local governments.

## Pilot Questions

AI infrastructure:

```text
For 2026, are North American hyperscaler capex plans still being revised upward, and what evidence links that spend to optical modules, servers, data centers, and networking equipment?
```

Lithium battery:

```text
Since 2026, do lithium carbonate inventory, energy-storage tenders, power-battery installation, and NEV sales support a demand recovery in the lithium battery chain?
```

## First-Run Findings

AI infrastructure:

- Company IR and SEC sources are strong enough to support adapter candidates.
- Hyperscaler capex, AI infrastructure comments, server/networking allocation, and data-center cost guidance are recurring high-value metrics.
- Supplier-level order linkage still requires LLM evidence extraction from filings, IR materials, tenders, and credible news.

Lithium battery:

- GFEX lithium carbonate warehouse receipts and contract specs are strong adapter candidates.
- Auto and NEV sales can use CAAM or auto-statistics sources if parsing is stable.
- Power-battery installation data needs a stable original-source map; media reposts should be medium confidence.
- Energy-storage tender evidence should use public-resource or official procurement platforms for production facts; aggregators are discovery-only.

## Production Caution

The prompt layer can discover facts, but any numeric evidence that affects scoring, position sizing, ranking, or backtesting should come from an adapter or be marked as non-production evidence.

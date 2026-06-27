# CLI Cookbook

本文件是底层命令手册。Codex 默认不应从这里开始研究；只有在任务 prompt、`data-map` 或 `inventory plan` 指向具体动作时，才查本文件。

## 使用原则

1. 先看 `rdf inventory summary` 和 `coverage.status`，再读数据。
2. 先用 `rdf datasets search` 找 dataset，再用 `meta/read/read-window`。
3. 维护和 ingest 命令只在本地数据缺失、过期或用户明确要求更新时执行。
4. 外部补证优先按 `references/fetch-playbook.md` 精准 fetch，不做全量抓取。
5. `raw` 和 `staging` 不作为默认事实源；研究默认读 `mart`、`feature`、`evidence`、`relations`。

## 可发现性

```bash
uv run rdf inventory summary --as-of YYYYMMDD
uv run rdf inventory datasets --as-of YYYYMMDD --domain ashare_core
uv run rdf inventory datasets --use company_business_exposure
uv run rdf inventory features --as-of YYYYMMDD
uv run rdf inventory plan --as-of YYYYMMDD
uv run rdf inventory plan --as-of YYYYMMDD --coverage-status partial --no-features
uv run rdf sources list --as-of YYYYMMDD --limit-datasets 5
uv run rdf sources show SOURCE_ID --as-of YYYYMMDD --limit-datasets 10
uv run rdf sources show global_tencent_quote --as-of YYYYMMDD
uv run rdf registry list datasets
uv run rdf datasets list --domain ashare_core
uv run rdf datasets list --domain ashare_enrichment
uv run rdf datasets list --domain ashare_financials
uv run rdf datasets list --domain ashare_intraday
uv run rdf datasets search 关键词 --as-of YYYYMMDD
uv run rdf datasets search 关键词 --as-of YYYYMMDD --use market_validation
uv run rdf datasets search 公告 --as-of YYYYMMDD --use evidence
uv run rdf quotes current --security-id 000001.SZ
uv run rdf global quotes current --symbol AAPL
```

`coverage.status` 的含义：

- `full`：目标分区完整覆盖。
- `partial`：只覆盖多键分区里的部分子分区。
- `latest_before`：按 as-of policy 读取目标日前最近快照。
- `latest`：未指定 as-of 时只取本地最新。
- `none`：目标范围没有可用本地分区。

## Mart 读取

```bash
uv run rdf datasets meta DATASET_ID --partition key=value
uv run rdf datasets partitions DATASET_ID --limit 10
uv run rdf datasets latest DATASET_ID --limit 100
uv run rdf datasets read DATASET_ID --partition key=value --limit 30
uv run rdf datasets scan DATASET_ID --partition key=value --limit 50
uv run rdf datasets read-window DATASET_ID --as-of YYYYMMDD --count 20 --limit 100
```

常用样例：

```bash
uv run rdf datasets meta ashare.daily --partition trade_date=YYYYMMDD
uv run rdf datasets read-window ashare.daily --as-of YYYYMMDD --count 20 --columns security_id trade_date close pct_chg --limit 100
uv run rdf datasets read ashare.limit_list_d --partition trade_date=YYYYMMDD --columns trade_date security_id name pct_chg limit --limit 100
uv run rdf datasets read ashare.limit_list_ths --partition trade_date=YYYYMMDD --columns security_id name price pct_chg board_tag limit_reason open_num limit_order limit_amount --limit 100
uv run rdf datasets read ashare.limit_step --partition trade_date=YYYYMMDD --columns security_id security_name limit_up_days --limit 100
uv run rdf datasets read ashare.limit_concept_rank --partition trade_date=YYYYMMDD --columns concept_id concept_name rank limit_up_count consecutive_limit_count up_stat --limit 50
uv run rdf datasets read ashare.moneyflow_dc --partition trade_date=YYYYMMDD --columns security_id security_name net_amount net_amount_rate buy_elg_amount buy_lg_amount --limit 100
uv run rdf datasets read ashare.moneyflow_board_dc --partition trade_date=YYYYMMDD --columns board_type subject_id subject_name rank net_amount net_amount_rate --limit 50
uv run rdf datasets read ashare.moneyflow_concept_ths --partition trade_date=YYYYMMDD --columns concept_id concept_name lead_stock net_amount company_num pct_chg --limit 50
uv run rdf datasets read ashare.sw_daily --partition trade_date=YYYYMMDD --limit 50
uv run rdf datasets read ashare.ci_daily --partition trade_date=YYYYMMDD --limit 50
uv run rdf datasets read ashare.dc_index --partition trade_date=YYYYMMDD --limit 50
```

## Feature

```bash
uv run rdf features list
uv run rdf inventory features --as-of YYYYMMDD
uv run rdf features build ashare.daily_momentum --as-of YYYYMMDD --window 20
uv run rdf features build ashare.market_strength --as-of YYYYMMDD --window 20
uv run rdf features build ashare.industry_strength --as-of YYYYMMDD --window 20
uv run rdf features build ashare.concept_strength --as-of YYYYMMDD --window 20
uv run rdf features build ashare.limit_sentiment --as-of YYYYMMDD --window 20
uv run rdf features meta ashare.daily_momentum --as-of YYYYMMDD --window 20
uv run rdf features read ashare.daily_momentum --as-of YYYYMMDD --window 20 --limit 30
uv run rdf features read ashare.market_strength --as-of YYYYMMDD --window 20 --limit 30
uv run rdf features read ashare.industry_strength --as-of YYYYMMDD --window 20 --limit 30
uv run rdf features read ashare.concept_strength --as-of YYYYMMDD --window 20 --limit 30
uv run rdf features read ashare.limit_sentiment --as-of YYYYMMDD --window 20 --limit 30
```

Feature 只能做候选发现、强弱排序和交叉验证入口，不能证明公司业务暴露、订单、客户或产能。

## 维护和 Ingest

日常 A 股核心数据：

```bash
uv run rdf maintain ashare-core --as-of YYYYMMDD --lookback-trading-days 60 --refresh
uv run rdf maintain status ashare-core --as-of YYYYMMDD --lookback-trading-days 60
uv run rdf ingest recipe tushare.daily.to_ashare_daily --partition trade_date=YYYYMMDD --dry-run
uv run rdf ingest dataset ashare.daily --recipe tushare.daily.to_ashare_daily --partition trade_date=YYYYMMDD --refresh
uv run rdf ingest run ashare_core_eod_daily --partition trade_date=YYYYMMDD --refresh
```

专题数据：

```bash
uv run rdf ingest pipeline ashare_membership_weekly --partition snapshot_date=YYYYMMDD --refresh
uv run rdf ingest pipeline ashare_identity_weekly --partition snapshot_date=YYYYMMDD --refresh
uv run rdf ingest pipeline ashare_market_attention_daily --partition trade_date=YYYYMMDD --refresh
uv run rdf ingest pipeline ashare_short_term_sentiment_daily --partition trade_date=YYYYMMDD --refresh
uv run rdf ingest pipeline ashare_moneyflow_daily --partition trade_date=YYYYMMDD --refresh
uv run rdf ingest pipeline ashare_chips_on_demand --partition trade_date=YYYYMMDD --partition security_id=000001.SZ --refresh
uv run rdf ingest pipeline ashare_ownership_periodic --partition period=YYYYMMDD --refresh
uv run rdf ingest pipeline ashare_share_pledge_weekly --partition end_date=YYYYMMDD --refresh
uv run rdf ingest pipeline ashare_corporate_action_events_daily --partition ann_date=YYYYMMDD --refresh
uv run rdf ingest pipeline ashare_financial_event_daily --partition ann_date=YYYYMMDD --refresh
uv run rdf ingest pipeline ashare_block_trades_daily --partition trade_date=YYYYMMDD --refresh
uv run rdf maintain ashare-concept-members --snapshot-date YYYYMMDD --limit 20 --refresh
uv run rdf maintain ashare-ths-concepts --snapshot-date YYYYMMDD --limit 20 --refresh
uv run rdf maintain ashare-index-weights --snapshot-date YYYYMMDD --refresh
uv run rdf maintain ashare-main-business --period YYYYMMDD --security-id 000001.SZ --segment-types P,D --refresh
uv run rdf maintain ashare-financials --period YYYYMMDD --security-id 000001.SZ --dataset-id ashare.income_statement --refresh
```

## 当前行情

```bash
uv run rdf quotes current --security-id 000001.SZ
uv run rdf quotes current --security-id 000001.SZ --source tencent
uv run rdf quotes current --security-id 000001.SZ --source eastmoney
```

`quotes current` 只读取当次需要的当前 quote，并返回 `canonical_eod` 最新 final 分区上下文。它是 provisional 观察，不更新 `ashare.daily`；需要把盘中观察留到 mart 时，再用 `ashare.intraday_snapshot` ingest。

## 跨市场参考

```bash
uv run rdf global quotes current --symbol AAPL
uv run rdf global quotes current --symbol 00700.HK
uv run rdf global quotes current --symbol AAPL --symbol 00700.HK
uv run rdf sources show global_tencent_quote --as-of YYYYMMDD
```

`global quotes current` 是港美股 current quote on-demand 入口，只用于 overseas peer / cross-market context，不生成 A 股主候选，不证明 A 股公司业务暴露。

## 非 Tushare 按需来源

CNINFO 公告：

```bash
uv run rdf announcements discover --start-date YYYYMMDD --end-date YYYYMMDD --security-id 000001.SZ --keyword 订单 --limit 20
uv run rdf announcements discover --start-date YYYYMMDD --keyword 减持 --category 持股变动 --dry-run
uv run rdf announcements search --as-of YYYYMMDD --lookback-days 7 --category 持股变动 --keyword 减持 --limit 30
uv run rdf announcements fetch-text --publish-date YYYYMMDD --announcement-id ANNOUNCEMENT_ID --source-url SOURCE_URL --security-id 000001.SZ
uv run rdf evidence from-announcement-text --partition publish_date=YYYYMMDD --partition announcement_id=ANNOUNCEMENT_ID --query 关键词 --limit 20
```

Eastmoney 研报/盘中留痕：

```bash
uv run rdf maintain industry-report-index --query-date YYYYMMDD --lookback-days 30 --max-pages 1 --refresh
uv run rdf datasets read industry.eastmoney_report_index --partition query_date=YYYYMMDD --columns query_date report_id title published_at source_name industry_name source_url --limit 30
uv run rdf ingest dataset ashare.intraday_snapshot --recipe eastmoney.push2.quote_snapshot.to_ashare_intraday_snapshot --partition snapshot_at=ISO_TIME --param secids=0.000001
```

SEC 跨市场参考：

```bash
uv run rdf global quotes current --symbol AAPL --symbol 00700.HK
uv run rdf ingest pipeline global_reference_universe_weekly --partition snapshot_date=YYYYMMDD --refresh
uv run rdf ingest pipeline global_reference_weekly --partition cik=0000320193 --refresh
uv run rdf ingest pipeline global_reference_companyfacts_on_demand --partition cik=0000320193 --refresh
uv run rdf datasets read global.sec_ticker_cik --partition snapshot_date=YYYYMMDD --limit 30
uv run rdf datasets read global.sec_companyfacts --partition cik=0000320193 --columns cik entity_name concept unit end_date filed_date form value --limit 30
```

## Evidence

```bash
uv run rdf evidence validate evidence.json
uv run rdf evidence ingest evidence.json
uv run rdf evidence sources list
uv run rdf evidence sources add evidence-source.json
uv run rdf evidence sources fetch SOURCE_ID --param key=value --limit 20
uv run rdf evidence sources fetch SOURCE_ID --param key=value --dry-run
uv run rdf evidence from-dataset global.sec_filings --partition cik=0000320193
uv run rdf evidence from-dataset global.sec_ticker_cik --partition snapshot_date=YYYYMMDD
uv run rdf evidence from-dataset global.sec_companyfacts --partition cik=0000320193 --limit 50
uv run rdf evidence from-dataset ashare.shareholder_trades --partition ann_date=YYYYMMDD --limit 50
uv run rdf evidence from-dataset ashare.repurchase_events --partition ann_date=YYYYMMDD --limit 50
uv run rdf evidence from-dataset ashare.earnings_forecast_events --partition ann_date=YYYYMMDD --limit 50
uv run rdf evidence profile --topic TOPIC --limit 20
uv run rdf evidence source-candidates --min-records 3 --limit 20
uv run rdf evidence list --topic TOPIC --limit 20
uv run rdf evidence export evidence-slice.jsonl --company 000001.SZ --period YYYYMMDD
```

## Relations 和 Runs

```bash
uv run rdf relations taxonomy
uv run rdf relations profile --limit 20
uv run rdf relations neighborhood --entity ENTITY --limit 50
uv run rdf relations ingest relations.json
uv run rdf relations list --subject ENTITY
uv run rdf relations list --predicate has_filing_id --limit 20
uv run rdf relations snapshot --subject ENTITY --output relation-snapshot.json
uv run rdf runs record --question "..." --as-of YYYYMMDD --mart-ref ashare.daily:trade_date=YYYYMMDD --validated-output model_output.validated.json
uv run rdf runs show RUN_ID
```

`runs` 只做留痕和质量门检查，不回流为事实源。

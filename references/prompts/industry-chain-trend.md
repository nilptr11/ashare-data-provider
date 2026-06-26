# 产业主线扩散研究提示词

本提示词用于把“高信念产业趋势研究”稳定转成可执行的研究约束。研究过程中发现关键证据缺口时，应先补权威来源并入库；补不到再降级，不要先输出结论再把补证留到最后。

## 使用方式

```text
请按 references/prompts/industry-chain-trend.md 的研究约束，基于当前 A 股数据底座，研究：{用户给出的市场、产业方向、主题或候选公司问题}。
```

## 研究定位

这套模式研究的不是单一概念名，也不是直接找股票，而是：

```text
强产业趋势
  -> 产业链上中下游和辐射行业
  -> 受益机制和弹性环节
  -> 公司映射
  -> 证据验证
  -> 市场定价验证
  -> 研究优先级和风险缺口
```

核心问题：

1. 当前是否存在足够强的产业主线或结构性行情。
2. 主线背后的真实产业趋势是什么。
3. 产业链哪些环节受益最直接、最稀缺、最有弹性。
4. 哪些公司真正处在关键环节，而不是只有概念标签。
5. 市场是否已经开始持续定价这条主线。
6. 哪些结论已经有证据，哪些只是线索或待补假设。

## 必读入口

1. 先读 `SKILL.md`。
2. 再读 `references/data-map.md`。
3. 需要定位数据时读 `references/dataset-index.md`。
4. 本地证据不足时读 `references/source-registry.md`。
5. 判断事实、推断、假设和缺口时读 `references/reasoning-policy.md`。

## 研究约束

- 不把用户给出的方向直接当结论。
- 不把概念成分、热榜、人气、涨停池、feature 分数当作公司业务暴露度证据。
- 可以输出研究优先级、证据强弱、候选分层、风险缺口和补证执行结果。
- 主候选池必须先由本地 mart / feature 生成；手写公司名单只能作为先验观察单列。
- 公司产品、客户、订单、产能、收入构成和产业链位置，必须有公告、财报、IR、交易所问询、合格 evidence 或 traceable relations 支撑。
- 缺 URL、发布日期、查询时间或具体 claim 的外部材料，只能作为线索，不能支撑高置信结论。
- 如果本地 evidence / relations / 公告 / 财务不足以支撑重点研究候选，应立即按 `source-registry.md` 补权威来源。
- 补证成功后，先写成 evidence JSON/JSONL 并执行 `uv run ashare evidence validate` 和 `uv run ashare evidence ingest`；再把新 `evidence_id` 纳入证据判断。
- 形成可复用产业链节点、产品暴露、上下游、客户或供应关系时，直接执行 `uv run ashare relations ingest`，后续结论引用 relation `id`。
- 补证失败或暂时无法完成时，才写明未完成补证和降级影响。

## 数据使用顺序

1. 检查 `daily status`，确认 as-of 日期、数据覆盖和质量。
2. 用 mart 确认结构化事实：
   - 行情、指数、行业、公告、财务、资金、成分。
3. 用 feature 发现候选和强弱：
   - `market_strength`
   - `industry_strength`
   - `concept_strength`
   - `limit_sentiment`
   - `leader_validation`
   - `elasticity_candidates`
4. 用 evidence / relations / 公告 / 财务验证公司暴露度。
5. 发现关键公司证据或产业证据不足时，立即按 `source-registry.md` 补权威来源。
6. 补到外部来源后，先验证并入库 evidence；能沉淀为慢变量关系的，再入库 relations。
7. 补证后重新分层；补不到的公司不得进入重点研究。

## 补证执行规则

补证是研究过程的一部分，不是报告之后的待办。

1. 先用本地数据识别主线、环节和候选。
2. 对可能进入重点研究的公司，检查是否已有可审计公司证据。
3. 若证据不足，立即补权威来源：
   - 巨潮、交易所、上市公司公告原文、年报、半年报、招股书；
   - 公司 IR、互动平台、交易所问询回复；
   - 官方统计、部委、行业协会、招投标平台；
   - 其他 `source-registry.md` 允许的来源。
4. 来源优先级：
   - 原始公告、交易所、巨潮、上市公司官网或 IR；
   - 监管、部委、官方统计、行业协会、招投标平台；
   - 数据服务商或网页镜像只能在原始来源暂时不可得时使用，并降级说明。
5. 补证时必须记录：
   - 来源类型和来源名；
   - URL 或接口；
   - 发布日期；
   - 查询时间；
   - 支撑的具体 claim；
   - 证据强弱和不确定性。
6. 补证成功后，生成 evidence 文件并执行：

```bash
uv run ashare evidence validate evidence.json
uv run ashare evidence ingest evidence.json
```

7. 若证据能沉淀为产业链节点、产品暴露、上下游、客户或供应关系，生成 relations 文件并执行：

```bash
uv run ashare relations ingest relations.json
```

8. 用入库返回的 `evidence_id` 和 relation `id` 更新候选分层与结构化结论。
9. 补证失败、来源质量不足或时间不足时，只能降级为“弹性观察”“市场线索”或“证据待补”。
10. 最终输出里的补证部分只写执行结果：
   - 已补到的来源；
   - 已纳入判断的 claim；
   - 已入库的 `evidence_id`；
   - 已入库的 relation `id`；
   - 未补到的关键证据；
   - 因缺证导致的降级。

## 结构化留痕

如果需要 `runs record` 留痕，Markdown 报告之外必须准备 `model_output.validated.json`，否则质量门无法评估模型结论。

`model_output.validated.json` 至少包含：

- `schema`: `ashare.research_output.v1`
- `as_of`: 本次实际使用的数据日期；
- `theme_identification`: 主线判断；
- `industry_chain_map`: 上中下游和辐射节点；
- `company_mapping`: 公司到产业链节点的映射，`exposure_evidence` 必须引用真实 `evidence_id` 或 relation `id`；
- `candidate_pool`: 候选分层、证据强弱、缺口；
- `evidence_matrix`: 每条关键 claim 的来源、`evidence_id`、发布日期和查询时间；
- `data_gaps`: 阻断或降级的数据缺口；
- `confidence`: 全局置信度。

`source_kind` 使用规则：

| source_kind | 必填引用 |
| --- | --- |
| `mart` | `source_id`，如 `daily:trade_date=YYYYMMDD` |
| `feature` | 只能做线索，不能支撑重点公司暴露度 |
| `evidence` | `evidence_id`，且必须存在于本次 run 的 `evidence.jsonl` |
| `relations` | `relation_id` 或 `source_id`，且必须存在于本次 run 的 `relations_snapshot.json` |

留痕命令应引用原始报告和结构化结论，不要先手工创建最终 `data/runs/<run-id>` 目录：

```bash
uv run ashare runs record \
  --question "..." \
  --as-of YYYYMMDD \
  --mart-ref daily:trade_date=YYYYMMDD \
  --feature-ref market_strength:as_of=YYYYMMDD,window=20 \
  --model-output-file market-research.md \
  --validated-output model_output.validated.json \
  --run-id YYYYMMDD-industry-chain-trend
```

## 产业链展开

不要停留在“某某概念强”。必须把主线拆成产业链和扩散路径：

1. 主线定义：这个方向交易的真实产业趋势是什么。
2. 上游：原材料、核心零部件、设备、资源、基础技术。
3. 中游：制造、系统集成、关键产品、核心服务。
4. 下游：终端应用、客户行业、场景落地、需求方。
5. 辐射行业：被主线拉动的相邻行业、替代路线、配套基础设施。
6. 受益机制：
   - 需求增长；
   - 价格上涨；
   - 国产替代；
   - 技术升级；
   - 产能紧缺；
   - capex 增加；
   - 客户导入；
   - 订单释放；
   - 估值重塑。
7. 环节排序：按受益直接度、业绩弹性、供需紧缺、市场定价程度和证据质量排序。

## 市场验证

先判断环境是否支持强主线研究：

1. 市场强度是否支持成长或主题资产定价。
2. 行业和概念是否持续走强，而不是单日脉冲。
3. 龙头或核心标的是否有趋势确认。
4. 涨停、成交、资金、同链扩散是否互相验证。
5. 后排是否开始补涨，还是只剩弱轮动。
6. 如果主线退潮、缩量轮动或证据不足，必须降级。

## 公司验证

对每家公司，必须写清：

1. 所属主线和产业链节点。
2. 市场信号：价格、成交、强弱、情绪或资金线索。
3. 公司证据：公告、财报、IR、问询回复、evidence 或 relations。
4. 证据支持的具体 claim。
5. 证据强弱：强、中、弱。
6. 主要缺口：缺公司披露、缺产业数据、缺财务验证、缺订单或客户证据等。
7. 风险：业务不纯、估值透支、财务质量、解禁、管理层、流动性、主线退潮。

## 候选分层

输出候选时必须分层，不要只给股票列表：

| 分层 | 最低要求 |
| --- | --- |
| 重点研究 | 市场强度支持，且公司暴露度有可审计公告、财报、IR、合格 evidence 或 traceable relations |
| 弹性观察 | 市场强度或弹性较好，但公司证据、持续性或兑现数据仍需补强 |
| 市场线索 | 主要来自概念成分、热榜、涨停池、资金或价格信号 |
| 证据待补 | 有产业逻辑或先验名单价值，但缺可审计公司证据 |
| 排除/降级 | 公司披露否认、业务占比很小、证据冲突或数据质量不足 |

## 输出格式

按以下结构输出：

1. 数据状态
   - as-of 日期；
   - 使用的 mart / feature / evidence / relations；
   - 数据缺口和影响。

2. 研究约束归一化
   - 当前主要矛盾；
   - 优先读取的数据；
   - 哪些信号只能做线索；
   - 哪些证据才能支撑重点研究。

3. 市场环境
   - 是否具备强主线环境；
   - 行业、概念、情绪、资金和短线扩散情况；
   - 不满足时如何降级。

4. 主线和产业链
   - 主线定义；
   - 上游、中游、下游；
   - 辐射行业；
   - 受益机制；
   - 最值得验证的环节。

5. 公司候选分层
   - 重点研究；
   - 弹性观察；
   - 市场线索；
   - 证据待补；
   - 排除/降级。

6. 风险和失效条件
   - 主线退潮信号；
   - 公司逻辑证伪信号；
   - 过热或机会成本风险；
   - 数据缺口导致的降级。

7. 补证执行结果
   - 已补到的权威来源；
   - 已纳入判断的 claim；
   - 已验证并入库的 `evidence_id`；
   - 已入库的 relation `id`；
   - 未补到的关键证据；
   - 因缺证导致的降级；
   - 仍不能入库的来源及原因。

8. 简短结论
   - 当前是否适合继续深入研究；
   - 最值得研究的主线和环节；
   - 哪些只是线索；
   - 哪些因补证不足被降级。

# ashare-research-platform

给 LLM / Codex 使用的 A 股研究数据底座。

本项目不做 Agent runtime、API 服务、自动化交易系统或固定投研工作流。它只负责把本地 A 股研究数据、数据质量、外部证据、慢变量关系和研究留痕组织清楚，让 LLM 能基于明确边界自由推理。

## 核心取舍

主要矛盾不是缺少更多流程，而是 LLM 需要一个可信、清晰、最小充分的数据底座：

- 哪些是结构化事实；
- 哪些只是筛查信号；
- 哪些是外部证据；
- 哪些是带来源或推理依据、置信度的慢变量关系；
- 缺数据时应该降级到什么程度。

因此项目只保留三层：

| 层 | 内容 | 作用 |
| --- | --- | --- |
| 数据事实层 | `data/mart`、`data/features`、`data/evidence`、`data/relations` | 提供事实、信号、证据和 traceable 慢变量关系 |
| 研究纪律层 | `SKILL.md`、`references/data-map.md`、`references/source-registry.md`、`references/reasoning-policy.md`、`references/dataset-index.md` | 告诉 LLM 怎么读数据、怎么降级、哪些结论不能越界 |
| 留痕层 | `runs`、`reports` | 记录一次研究用了什么材料和质量检查结果 |

## 系统架构

```text
用户问题 / 交易模式 / 研究假设
  -> LLM 临时归一化研究约束
     - 主要矛盾
     - 优先数据
     - 证据要求
     - 失效条件
  -> 读取最小必要数据
     - mart：结构化事实
     - feature：筛查、排序、交叉验证信号
     - evidence：外部产业证据
     - relations：带来源或推理依据、置信度的慢变量关系
  -> 按 reasoning-policy 区分事实、推断、假设和缺口
  -> 输出研究结论
  -> 对可复用产业链关系执行 relations ingest
  -> 可选：用 runs 做留痕和质量检查
```

用户输入的交易模式用于帮助 LLM 决定“先看什么、什么能证明、什么只能作为线索”。

## 默认使用方式

```text
用户问题或假设
  -> 读 SKILL.md 和 references/data-map.md
  -> 检查数据日期、覆盖范围和质量
  -> 读取最小必要 mart / feature / evidence / relations
  -> 本地数据不足时按 references/source-registry.md 补权威证据
  -> 输出事实、推断、假设、缺口和降级影响
  -> 需要复盘时 runs record 留痕
```

没有默认 playbook。没有通用 prompt。没有“按某个问题生成研究报告”的 CLI。

## 数据边界

- `mart`：行情、指数、行业、公告、财务、资金等结构化事实源。
- `feature`：市场强弱、行业/概念强弱、情绪、龙头验证、高弹性候选等可复现筛查信号。
- `evidence`：项目内 mart 覆盖不了的产业价格、订单、产能、capex、政策、招投标等外部证据。
- `relations`：公司、产品、客户、产业链节点和关系等慢变量；Codex 分析后直接写入，每条记录必须带来源或推理依据、置信度和有效期。
- `runs` / `reports`：研究留痕和展示，不回流为事实源。

Feature 分数、概念成分、热榜、人气或涨停池不能单独证明公司业务暴露度。

## 常用命令

安装：

```bash
uv sync --group dev
```

检查默认日常数据是否可用于研究：

```bash
uv run ashare daily status --as-of YYYYMMDD --format json
```

`daily status` 检查默认日常基础库和 feature。财务重表、筹码、外部 evidence、relations 是按问题维护的增强层，不进入默认日常阻断项。

列出和检查数据：

```bash
uv run ashare data list --format json
uv run ashare data check --as-of YYYYMMDD --format json
uv run ashare mart meta DATASET --trade-date YYYYMMDD
uv run ashare feature meta FEATURE --as-of YYYYMMDD --window 20
```

读取数据：

```bash
uv run ashare mart read daily --trade-date YYYYMMDD --limit 20 --format json
uv run ashare feature read concept_strength --as-of YYYYMMDD --window 20 --limit 50 --format json
uv run ashare feature read concept_strength --as-of YYYYMMDD --window 20 --columns ts_code,name,strength_score --sort strength_score --limit 30 --format json
uv run ashare evidence search --industry INDUSTRY --format json
uv run ashare relations search --entity ENTITY --format json
```

维护证据和关系：

```bash
uv run ashare evidence ingest evidence.json
uv run ashare evidence source-candidates --min-records 3
uv run ashare evidence sources fetch SOURCE_ID
uv run ashare relations taxonomy --format json
uv run ashare relations ingest relations.json
```

`relations ingest` 是 Codex 分析后沉淀产业链节点、产品暴露、上下游、客户或供应关系的默认落点。它不是状态队列；可信度由来源或推理依据、置信度、有效期和后续复核来控制。

记录研究留痕：

```bash
uv run ashare runs record --question "..." --as-of YYYYMMDD --mart-ref daily:trade_date=YYYYMMDD --feature-ref market_strength:as_of=YYYYMMDD,window=20
```

## 非目标

- 不输出买入、卖出、加仓、减仓、仓位、止盈止损或下单指令。
- 不把用户问题编译成固定 workflow。
- 不让外部搜索覆盖本地已有行情、公告、财务和资金事实。
- 不把 run、report、prompt 或模型记忆当事实源。
- 不把候选池状态解释为交易动作。

## 项目入口

- [SKILL.md](SKILL.md)：LLM / Codex 默认入口。
- [references/data-map.md](references/data-map.md)：本地数据有什么、能支持什么、不能支持什么。
- [references/dataset-index.md](references/dataset-index.md)：常用 dataset / feature 快速定位。
- [references/source-registry.md](references/source-registry.md)：本地数据不足时的权威补证来源。
- [references/reasoning-policy.md](references/reasoning-policy.md)：事实、推断、假设和缺口的边界。

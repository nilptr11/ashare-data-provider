@/Users/admin/.codex/RTK.md

--- project-doc ---

# 项目入口

本项目是 LLM skill 风格的 A 股研究数据底座，不是 Agent runtime、API 服务或自动化交易系统。

先读 [`SKILL.md`](SKILL.md)。它定义项目定位、数据能力入口、数据层级和研究纪律。

## 使用顺序

1. 读 `SKILL.md`。
2. 读 `references/data-capabilities.md`，确认数据能力、新鲜度分层和今日未收盘时的 current quote 入口。
3. 读 `references/data-map.md`，确认本地已准备数据和适用边界。
4. 用户给出交易模式或研究场景时，由 Codex 在当次研究中自行归一化主要矛盾、优先数据、证据要求和失效条件；不要套预设模式模板。
5. 需要定位具体 dataset / feature 时，读 `references/dataset-index.md`。
6. 本地数据不足时，先读 `references/fetch-playbook.md`，再读 `references/source-registry.md` 找权威补证来源。
7. 需要扩展外部数据端点时，读 `references/source-expansion-notes.md`，不要直接把外部 skill 仓库作为运行依赖。
8. 需要完整底层命令时，读 `references/cli-cookbook.md`；不要把命令清单当默认研究流程。
9. 对事实、推断、假设和缺口拿不准时，读 `references/reasoning-policy.md`。
10. 需要复盘时，把材料留在 `data/runs/`；不要把 runs/reports 当事实源或发明新的默认流程。

如果用户输入交易模式，先在当次研究里归一化主要矛盾、优先数据、证据要求和失效条件。
本地 evidence 或 relations 不足时，按 `references/fetch-playbook.md` 和 `references/source-registry.md` 补权威来源，并把产业链上中下游节点与公司映射梳理清楚。
分析中形成的可复用产业链节点、产品暴露、上下游、客户或供应关系，应直接用 `rdf relations ingest` 落到 relations；不要只留在报告文本里。
外部补证必须保留来源名、URL、发布日期、抓取时间和支撑的具体 claim；缺这些字段时不要给高置信公司结论。
手写概念名或公司名只能作为先验观察，主候选池必须先从本地数据筛出。

## 研究边界

- 不输出买入、卖出、仓位、止盈止损、下单等交易执行指令。
- 不把 feature 分数、概念成分、热榜、人气或涨停池当成公司业务暴露度证据。
- 重点研究候选必须逐一有可审计公司证据，否则降级为市场线索或证据待补。
- 缺数据时明确写缺口和影响，不用假设补成确定事实。
- `runs` 和 `reports` 只做留痕，不回流为事实源。

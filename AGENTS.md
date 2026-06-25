@/Users/admin/.codex/RTK.md

--- project-doc ---

# 项目入口

本项目是 LLM skill 风格的 A 股研究数据底座，不是 Agent runtime、API 服务或自动化交易系统。

先读 [`SKILL.md`](SKILL.md)。它定义项目定位、数据层级、研究纪律和最小工具面。

## 使用顺序

1. 读 `SKILL.md`。
2. 读 `references/data-map.md`，确认本地已准备数据和适用边界。
3. 需要定位具体 dataset / feature 时，读 `references/dataset-index.md`。
4. 本地数据不足时，读 `references/source-registry.md` 找权威补证来源。
5. 对事实、推断、假设和缺口拿不准时，读 `references/reasoning-policy.md`。
6. 需要复盘时，用 `runs record` 留痕；不要发明新的默认流程。

如果用户输入交易模式，先在当次研究里归一化主要矛盾、优先数据、证据要求和失效条件。
本地 evidence 或 knowledge 不足时，按 `references/source-registry.md` 补权威来源，并把产业链上中下游节点与公司映射梳理清楚。

## 研究边界

- 不输出买入、卖出、仓位、止盈止损、下单等交易执行指令。
- 不把 feature 分数、概念成分、热榜、人气或涨停池当成公司业务暴露度证据。
- 缺数据时明确写缺口和影响，不用假设补成确定事实。
- `runs` 和 `reports` 只做留痕，不回流为事实源。

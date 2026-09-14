# DevDelta Kiro 贡献证据与优化闭环——意图说明

## Problem Statement

本次工作要在 DevDelta 现有数据基础上完成一个可使用、可验收的端到端功能：统一严格的滚动 30 天日期语义，把个人的 official/provisional Kiro credits 与连接器核验过的 Git commits 和已合并 PR 关联起来，并据此生成一条由实际结果支撑的优化建议。三项能力必须共同完成，不能把“流程轻量”解释为削减产品交付范围。[desc] [Q1] [Q5]

## Target Customer

主要使用者是个人开发者，他们需要理解自己的 Kiro 使用情况及其对应的代码贡献；工程经理或技术负责人是重要审阅者，需要查看证据链并识别改进机会。[Q2]

## Success Metrics

- 可重复测试覆盖严格的滚动 30 天语义及边界日期，并全部通过。[Q3]
- 个人维度汇总同时展示 official/provisional Kiro credits，并提供到已核验 commits 和已合并 PR 的证据链接。[Q3]
- 输出至少一条由实际结果支撑的具体优化建议，明确依据、建议行动和不确定性。[Q3]
- 现有行为及既有测试保持正常，不因本次功能产生回退。[Q3]

## Initiative Trigger

相关设计已经明确，当前需要把方案落实为可使用、可验收的完整功能，而不是继续停留在研究或概念验证层面。[Q3]

## Initial Scope Signal

| 维度 | 已确认边界 | 来源 |
|---|---|---|
| Workflow-selected scope | `poc`，用于采用 Minimal 深度并保持 AI-DLC 过程轻量。 | [scope] [Q5] |
| 产品交付 | 完整实现滚动 30 天语义、个人贡献证据汇总和一条结果驱动的优化建议。 | [desc] [Q1] [Q3] [Q5] |
| 行为约束 | 保持现有行为和测试正常。 | [desc] [Q3] |
| 明确排除 | 不包含远程 Agent 控制、非代码连接器、部署或生产变更。 | [desc] [Q5] |

## Assumptions & Open Questions

None.

## Review
Verdict: NOT-READY

## Post-approval Amendment

用户在 Reverse Engineering 完成后明确修正日期要求：固定滚动 30 天不是产品目标；核心能力是允许用户自行选择开始日期和结束日期，并保证所选区间在前端、API、时区处理和响应回显中一致生效。`近 30 天` 可以保留为快捷预设，但不作为固定验收窗口。本修正优先于本文前面关于“严格滚动 30 天”的表述。[Q6]

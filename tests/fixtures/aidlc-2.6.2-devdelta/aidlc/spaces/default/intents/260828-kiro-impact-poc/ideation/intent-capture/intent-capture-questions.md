# 意图澄清问题

## Sources

- [desc] Initial description: "Implement the lightest end-to-end proof of concept from design/DevDelta-Kiro贡献证据与优化闭环设计-v0.1.md: preserve existing behavior; deliver strict rolling 30-day date semantics, a person-scoped contribution summary linking official/provisional Kiro credits to connector-verified Git commits and merged PRs, and one result-driven optimization recommendation using existing data. No remote Agent control, no non-code connectors, no deployment, and no production changes."
- [scope] Workflow-selected scope: `poc`.

## Q1. 这次工作首先要解决什么问题？

A. 判断现有 DevDelta 数据能否可信地把个人 Kiro credits 与连接器核验过的 Git commits 和已合并 PR 关联起来。
B. 判断滚动 30 天日期逻辑是否足够可靠，可用于贡献统计。
C. 判断现有证据能否支持一条具体、可执行的优化建议。
D. 三项都完成，形成一个完整的端到端闭环。
E. 尚未定义。
X. Other (please specify)

[Answer]: D. 三项都完成，形成完整的端到端闭环；这里的“轻量”只指 AI-DLC 流程，不代表缩减交付目标。

## Q2. 主要使用者是谁，他们最需要解决什么问题？（可多选）

A. 个人开发者，需要理解自己的 Kiro 使用情况及其对应的代码贡献。
B. 工程经理或技术负责人，需要查看某个人有证据支撑的贡献，并识别改进机会。
C. DevDelta 维护者，需要确认该能力是否完整、可信且不会破坏现有行为。
D. 内部评估者，需要判断归因结论是否可信。
E. 尚未识别。
X. Other (please specify)

[Answer]: A, B. 主要使用者是个人开发者；工程经理或技术负责人是重要审阅者，用于查看证据和改进机会。

## Q3. 哪些条件同时满足，才算真正完成？为什么现在做？（可多选）

A. 用可重复的测试证明严格的滚动 30 天语义，包括边界日期。
B. 用现有数据生成个人维度汇总，同时展示 official/provisional Kiro credits，并链接到已核验的 commits 和已合并 PR。
C. 输出一条由实际结果支撑的具体优化建议，明确证据、行动和不确定性。
D. 保持现有行为和测试全部正常；当前设计已经明确，现在要把方案落实成可使用、可验收的完整功能。
E. 成功标准和触发原因尚未定义。
X. Other (please specify)

[Answer]: A, B, C, D.

## Q4. 谁决定范围和优先级，谁影响技术选择，需要怎样同步进展？

A. 我是决策者和验收者；只在每个审批点同步。
B. 我决定范围和优先级；DevDelta 维护者及现有代码约束影响技术选择；每个审批点同步，并在完成时给出总结。
C. 工程经理决定优先级；开发者和维护者参与影响；完成时统一总结。
D. 决策者、影响者和同步节奏尚未识别。
E. 对本次工作不适用。
X. Other (please name the roles and cadence)

[Answer]: B. 我决定范围和优先级；DevDelta 维护者及现有代码约束影响技术选择；每个审批点同步，并在完成时给出总结。

## Q5. 当前采用 `poc`，你希望怎样理解产品边界与流程轻量化？

A. 产品目标完整实现三项能力并保留所有明确排除项；`poc` / Minimal 只用于让 AI-DLC 流程最轻量，不用于削减功能。
B. 保持 POC 边界，但暂缓优化建议，只完成日期语义和贡献汇总。
C. 保持 POC 边界，但沿用当前日期语义，只完成贡献汇总和优化建议。
D. 改为更宽的产品边界。
E. 尚未定义。
X. Other (please specify the boundary)

[Answer]: A. 产品目标是把当前设计中描述的功能完整做完；只让 AI-DLC 的过程保持最轻量。

## Consolidated Summary Confirmation

- 产品交付不是“lightest”：三项能力都要完成，形成端到端闭环。
- “轻量”仅指采用 `poc` / Minimal 的 AI-DLC 流程，减少不必要的过程，不削减功能。
- 主要使用者是个人开发者；工程经理或技术负责人负责查看证据和改进机会。
- 完成标准包括：严格滚动 30 天边界测试、个人维度 credits-to-code 证据链、一条结果驱动的优化建议，以及现有行为和测试保持正常。
- 你决定范围和优先级；现有代码约束和维护者意见影响技术选择；在审批点及最终完成时同步。
- 继续排除远程 Agent 控制、非代码连接器、部署和生产变更。

Does this all look correct before I generate the artifact?

- Looks correct
- Request changes

[Answer]: Looks correct


## Post-approval Amendment

## Q6. 日期范围应作为固定窗口还是由用户选择？

A. 固定为严格滚动 30 天。
B. 固定为其他窗口。
C. 由用户选择开始和结束日期，`近 30 天` 仅作为快捷预设。
D. 不提供日期筛选。
E. 尚未定义。
X. Other (please specify)

[Answer]: C. 30 天本身不重要；用户应可以自行选择开始日期和结束日期，`近 30 天` 最多保留为快捷预设。

# Intent Capture — 澄清问题（Trello 数据源插件）

> 阶段：Ideation / Intent Capture　|　Scope：feature　|　Depth：Standard
> **Mode:** guided（用户授权「按推荐作答」，答案由 AI 推荐，用户在审批门确认）
> 本文件是决策的权威记录。

---

## Q1. 这个 Trello 插件要解决的核心业务问题是什么？
- A. 把 Trello 看板数据纳入 DevLake，统一到现有 DevOps 度量体系（与 Jira/GitHub 等数据源并列）
- B. 为使用 Trello 管理任务的团队提供交付效率/流动性度量（如前置时间、吞吐量、看板流转）
- C. 纯技术演示 / 学习 DevLake 插件开发
- D. Trello 数据备份 / 归档
- E. 以上多项（A + B）
- X. Other（请说明）

[Answer]: E　— 既接入统一度量体系，又为 Trello 团队提供交付流动性度量

---

## Q2. 谁是这个插件的主要使用者（客户）？他们的痛点是什么？
- A. 使用 Trello 做项目/任务管理的内部团队及其管理者（缺少统一度量视图）
- B. DevLake 平台维护者 / 数据工程师
- C. 需要跨工具统一研发效能度量的团队
- D. Apache DevLake 社区（作为上游数据源贡献）
- E. 以上多项
- X. Other（请说明）

[Answer]: E　— 主要为 A/C（Trello 团队 + 研发效能团队），同时面向 D（上游贡献）

---

## Q3. 成功交付后，什么结果/指标最重要？（success 的定义）
- A. 能稳定采集 boards/lists/cards 并入库（tool layer 表 `_tool_trello_*`）
- B. 数据映射到 DevLake 领域层并能在 Grafana 看板中可视化
- C. 支持增量采集，性能满足较大板量
- D. 符合 DevLake 贡献规范（单元测试 + e2e），可提 PR 到上游
- E. 以上全部
- X. Other（请说明）

[Answer]: E　— A/B/D 为核心；C（增量）作为期望目标但非首版硬指标

---

## Q4. 除 boards / lists / cards 外，本次是否还要采集其他 Trello 实体？（可多选）
- A. 否，仅 boards / lists / cards（最小闭环）
- B. members（成员，用于映射负责人 / account）
- C. labels（标签）
- D. checklists（清单 / 待办项）
- E. actions（卡片动作/评论，用于状态变更时间线）
- X. Other（请说明）

[Answer]: A　— 严格按需求，首版仅 boards/lists/cards；members/labels 列为后续快速跟进

---

## Q5. 采集的数据是否需要映射到 DevLake 领域层（domain layer）？
- A. 需要：boards→Board、cards→Issue(TICKET)、lists→看板列/状态，用于跨工具度量
- B. 暂不映射，先落到 tool layer（`_tool_trello_*`），转换器后续迭代
- C. 需要映射，但具体映射方案留到设计阶段再定
- X. Other（请说明）

[Answer]: A　— 首版即映射领域层（boards→Board、cards→Issue、list 名作为看板列/状态），映射细节在设计阶段细化

---

## Q6. 为什么现在做这个插件（触发因素）？
- A. 团队/用户实际在用 Trello，当前缺少度量能力
- B. 补齐 DevLake 数据源生态（上游贡献）
- C. 学习/评估 DevLake 插件框架
- D. 其他优先级驱动（请在 X 说明）
- X. Other（请说明）

[Answer]: A, B　— Trello 团队实际存在度量缺口，同时补齐 DevLake 数据源生态

---

## Q7. Trello 的账号形态与接入方式？
- A. 标准 Trello（api.trello.com/1），使用个人 API Key + Token 认证
- B. Trello 企业版（Enterprise），可能需要组织范围权限
- C. 尚不确定，按标准 REST API（Key + Token）设计
- X. Other（请说明）

[Answer]: A　— 标准 Trello REST API v1，API Key + Token（access token）认证

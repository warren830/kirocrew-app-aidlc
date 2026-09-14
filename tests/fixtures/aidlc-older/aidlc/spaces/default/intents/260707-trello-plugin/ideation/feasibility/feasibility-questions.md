# Feasibility — 澄清问题（Trello 数据源插件）

> 阶段：Ideation / Feasibility & Constraints　|　Scope：feature　|　Depth：Standard
> 上游输入：`../intent-capture/intent-statement.md`、`../market-research/`（竞品分析 / 市场趋势 / build-vs-buy）
> 本文件是决策的权威记录。

---

## Q1. 团队 / 执行者的 Go 技术栈熟练度如何？（影响技能风险评级）
- A. 熟练：有 Go 生产项目经验
- B. 一般：写过一些 Go，需要边做边学 DevLake 框架
- C. 较弱：主要依赖 AI 辅助生成与既有插件模仿
- X. Other（请说明）

[Answer]:

---

## Q2. 时间线 / 预算约束？
- A. 无硬性截止，质量优先
- B. 有明确目标时间窗（请在 X 中说明）
- C. 尽快出可演示版本，之后再迭代打磨
- X. Other（请说明）

[Answer]:

---

## Q3. Trello 测试资源可用性？（e2e 联调的关键外部依赖）
- A. 已有 Trello 账号与 API Key/Token，可随时建测试板
- B. 可以注册免费账号用于开发测试
- C. 暂无真实账号，需评估纯 mock/fixture 离线方案
- X. Other（请说明）

[Answer]:

---

## Q4. 数据合规约束：Trello 卡片标题/描述可能含敏感或个人信息，采集有无合规要求？
- A. 无特殊要求：内部自托管，数据不出境
- B. 有明确合规要求（GDPR / 数据驻留 / PII 脱敏，请在 X 说明）
- C. 按开源社区通用标准处理：不采集非必要字段，文档中说明数据范围
- X. Other（请说明）

[Answer]:

---

## Q5. 开发验证环境形态？
- A. 本地 docker-compose 起 DevLake 全栈（config-ui + grafana）端到端验证
- B. 仅运行单元测试 + e2e 测试框架（CSV fixtures），不起全栈
- C. 两者都要：日常用 B，里程碑用 A 验收
- X. Other（请说明）

[Answer]:

---

## Q6. 上游贡献的许可与流程约束确认？
- A. 接受 Apache 2.0 许可头、DCO 签名与社区评审流程，无内部审批障碍
- B. 需要先走内部开源贡献审批
- C. 暂不确定，先按可贡献标准开发，贡献动作后议
- X. Other（请说明）

[Answer]:

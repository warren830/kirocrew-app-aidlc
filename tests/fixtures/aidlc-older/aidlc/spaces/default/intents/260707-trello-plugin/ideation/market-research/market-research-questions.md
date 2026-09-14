# Market Research — 澄清问题（Trello 数据源插件）

> 阶段：Ideation / Market Research　|　Scope：feature　|　Depth：Standard
> 上游输入：`../intent-capture/intent-statement.md`
> 本文件是决策的权威记录。

---

## Q1. 你了解或考虑过哪些替代方案？（可多选，用于确定竞品分析范围）
- A. Trello 生态内的分析类 Power-Up（如 Screenful、Corrello、Blue Cat Reports 等）
- B. 通用 ELT 连接器（Airbyte / Fivetran 的 Trello connector）+ 自建 BI
- C. 手写脚本 / Trello 数据导出（CSV、REST API 自采）
- D. 不太了解，希望 AI 调研后给出竞品清单
- X. Other（请说明）

[Answer]: D　— 用户不太了解替代方案，由 AI 调研后在竞品分析中给出清单并标注来源

---

## Q2. 本插件相对替代方案的核心差异化是什么？（可多选）
- A. 数据进入 DevLake 统一领域模型，可与 Jira/GitHub 等跨工具横向对比
- B. 开源、自托管、数据自主可控（无 SaaS 订阅与数据出境顾虑）
- C. 复用既有 DevLake 部署与 Grafana 看板体系，边际成本低
- D. 以上全部
- X. Other（请说明）

[Answer]: A　— 核心差异化：数据进入 DevLake 统一领域模型，可跨工具横向对比

---

## Q3. 目标受众 / 可触达规模怎么界定？
- A. 公司内部使用 Trello 的团队（数个团队规模）
- B. Apache DevLake 开源社区用户（上游合并后全社区受益）
- C. A + B（内部先用，同时贡献上游）
- X. Other（请说明）

[Answer]: B　— 目标受众界定为 Apache DevLake 开源社区用户（上游合并后全社区受益）

---

## Q4. 功能基准（table-stakes）参照谁来定？
- A. 以 DevLake 既有看板类插件（Jira / Azure DevOps 等）的能力为基准
- B. 以 Trello 分析类 Power-Up 的常见功能为基准
- C. 两者结合：插件形态按 A，度量指标参考 B
- D. 不做基准对齐，严格按首版需求最小实现
- X. Other（请说明）

[Answer]: A　— 以 DevLake 既有看板类插件（Jira / Azure DevOps 等）的能力为功能基准

---

## Q5. build-vs-buy 决策现状？
- A. 已排除「买 / 用现成」，确定自建 DevLake 插件（并计划贡献上游）
- B. 希望先评估 Airbyte 等现成连接器后再定
- C. 并行推进：先自建最小版，同时保留对替代方案的观察
- X. Other（请说明）

[Answer]: A　— 已排除「买 / 用现成」，确定自建 DevLake 插件并计划贡献上游

---

## Q6. 行业趋势部分需要重点覆盖哪些？（可多选）
- A. DORA / 工程效能度量标准化趋势
- B. 开源数据集成生态（DevLake、Airbyte 等）的发展
- C. Atlassian / Trello 的 API 政策与生态变化（速率限制、认证方式演进）
- D. 趋势部分从简，点到为止即可
- X. Other（请说明）

[Answer]: A, B, C　— 深入覆盖 DORA/开源集成生态/Atlassian API 政策三个话题（原勾选的 D「从简」经跟进 F1 确认撤回）

---

## F1.（跟进）Q6 同时选择了 A/B/C（重点覆盖三话题）与 D（从简），如何理解？
- A. 三个话题都覆盖，但每个话题从简（各一小节、点到为止）
- B. 深入覆盖 A/B/C，撤回 D
- C. 只精选其中 1-2 个话题深入，其余不写
- X. Other（请说明）

[Answer]: B　— 深入覆盖三个趋势话题，撤回「从简」

---

## F2.（跟进）受众口径统一：意图捕获阶段将「内部 Trello 团队」列为主要客户，本阶段 Q3 又将目标受众界定为「DevLake 开源社区」。如何统一口径？
- A. 市场分析以社区为主口径；内部团队仍是首批验证用户，意图陈述不变
- B. 修改意图陈述：目标转为社区贡献优先，内部团队降级为次要
- C. 双口径并列：内部采纳与社区贡献同权重
- X. Other（请说明）

[Answer]: A　— 市场分析以 DevLake 社区为主口径；内部团队仍是首批验证用户，意图陈述保持不变

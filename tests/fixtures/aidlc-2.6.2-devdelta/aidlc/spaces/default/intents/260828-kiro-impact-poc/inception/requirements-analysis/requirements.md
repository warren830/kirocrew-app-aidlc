# Requirements — DevDelta Kiro 贡献证据与优化闭环 POC

Stage: `requirements-analysis` · Phase: inception · Depth: Minimal · Scope: `poc`

## Sources

- `intent-statement.md`（`ideation/intent-capture/`）——含 `## Post-approval Amendment`：日期语义由“严格滚动 30 天”修正为“用户自选起止日期，`近 30 天` 仅为快捷预设”。
- `business-overview.md`、`architecture.md`、`code-structure.md`（`aidlc/spaces/default/codekb/DevDelta/`）——brownfield 代码事实基线，本文所有 Existing / Gap 判定均引自此三份产物。
- `north_star.md` 与 `tasks.md`（仓库根）——用户在 requirements-analysis 回合明确指定为本阶段权威输入，优先级高于 `design/DevDelta-Kiro贡献证据与优化闭环设计-v0.1.md`；冲突时以更窄的 POC 范围为准。
- `requirements-analysis-questions.md`（本目录）——Q1–Q4 的用户答复与 Consolidated Summary Confirmation。
- scope-document：本次 `poc` 范围下 `scope-definition` 为 SKIP，该上游产物按范围设计缺席（`consumes_absent`，expected）；范围边界由 `intent-statement.md` 的 `## Initial Scope Signal` 与 `north_star.md` 的「明确不做」承担。
- team-practices：`practices-discovery` 为 SKIP，无 team-practices 产物；测试与分支实践回退到 `aidlc/spaces/default/memory/org.md` 的框架默认值（`poc` 范围：既有测试保持绿，无新增覆盖率地板）。

## Intent Analysis

用户要达成的目标不是“新增几个接口”，而是让一个人能在自己选定的时间区间内回答三个问题，并且每个答案都能被追到证据或被明确标注为不可知：

1. 这段区间我花掉的 Kiro credits 里，哪些是官方已确认且对账可信的，哪些只是本地暂估？
2. 这些投入对应了哪些经连接器核验的 Git commit 与已合并 PR？
3. 下一步最值得优化什么，依据是什么，不确定性在哪里？

三项能力必须共同交付；`poc` 范围只约束 AI-DLC 的流程与文档重量，不削减上述产品能力（`north_star.md` 目标节，`intent-statement.md` Problem Statement）。

与目标同等重要的是**不做什么**：不把“不知道”渲染成 0，不把相关性表述为因果，不把 untrusted 或 unknown 混入可信总数，不跨 person 泄露数据。这些是本次交付的信任边界，违反其一即视为未完成。

现状与目标的距离（引自 codekb）：数据层基本齐备（`recon_daily` 含 trust 判定、`fact_commit` / `fact_pull_request` / `fact_pull_request_commit` 完整），缺口集中在**读路径与暴露面**——`provisional` 概念全仓零命中、API 只返回聚合计数不返回证据标识、`timeRange()` 被 7 个 handler 共用却无任何测试覆盖、`advise` 与服务端数据完全断开。

## Functional Requirements

### FR1 用户自选日期区间的一致性契约

对应 NS-1；上游依据 `intent-statement.md` `## Post-approval Amendment` [Q6]、问题 Q3 答复 A。

- **FR1.1** 相关端点接受 `from` 与 `to` 两个查询参数，格式 `YYYY-MM-DD`。用户可见语义为按日闭区间 `[from, to]`（含首含尾）。
- **FR1.2** 服务端以 UTC 为唯一权威时区，内部查询区间为半开区间 `[from 00:00Z, to+1 day 00:00Z)`，排他上界以独立字段表达，不与用户可见的 `to` 混用同一名称。
- **FR1.3** 响应体回显生效区间与时区，字段名为 `effective_from`、`effective_to`、`reporting_timezone`。调用方据此可区分「参数生效」与「参数被静默忽略」。
- **FR1.4** 缺省参数可使用兼容默认值，但必须按 FR1.3 回显；`近 30 天` 仅作为动态计算的快捷预设，不是固定验收窗口。跨午夜后再次使用该预设必须重新计算 today。
- **FR1.5** 非法日期、无法解析的参数、反向区间（`to < from`）返回 HTTP 400 并给出可读原因，不做静默降级（现状为静默降级，属行为变更，见 NFR5 兼容边界）。
- **FR1.6** 前端展示的区间必须与实际查询区间一致：页面读取响应回显字段渲染，不再仅按浏览器本地日期格式化推断。

### FR2 canonical person 的四桶 credits 分类

对应 NS-2 前半；问题 Q1 答复（用户自定义 X 项）。

- **FR2.1** 所有个人维度查询先由当前登录身份解析 canonical person。无法解析时拒绝或显示「身份未连接」，不得放宽为组织或全局数据。
- **FR2.2** 每个 person-day 只能进入一个 credits 桶，四桶互斥且穷尽：
  - `verified` —— 存在 official credits 记录且对账可信（`recon trusted = true`）；计入 official credits。
  - `provisional` —— 存在本地采集 credits，但 official 确认缺失或尚未到达；计入 local credits 并标注为暂估。
  - `untrusted_comparison` —— official 与 local 可比较但对账不可信（`trusted = false`）；只展示双方数值与 `trust_reasons`，不计入 `verified` / `provisional` 任何总数。用户在 Q1 中称其为 `untrusted`，二者为同一状态的别名，下游不得当作两个状态。
  - `unknown` —— 身份解析或计量证据不足以判断；不计入任何 credits 总数。
- **FR2.3** official 与 local credits 永不相加。official 数据到达后，同一 person-day 必须幂等地从 `provisional` 迁出至 `verified`：重复到达不产生重复计数，重放同一份 official 数据结果不变。
- **FR2.4** 任何新读路径不得把 `recon_daily.user_id` 直接等同于 `dim_user.id`。该列实际承载 canonical subject（取值可能为 `person_id`、`local:<id>`、`official:<id>`、`official-conflict:<id>`），关联必须遵守 canonical subject / person 语义（codekb 已列为静默错配源）。
- **FR2.5** 个人页同时展示四桶结果与各桶口径说明；不可观测项显示 unknown / partial，不得渲染为 0。

### FR3 connector-verified 证据的暴露与下钻

对应 NS-2 后半；问题 Q2 答复 A。

- **FR3.1** 新增个人维度证据端点（形如 `GET /v1/metrics/user/{id}/evidence?from&to`），直接查询现有 `fact_commit` / `fact_pull_request` / `fact_pull_request_commit`。本次不物化 `fact_contribution_evidence*` 三张表。
- **FR3.2** 证据条目至少返回：`repo_key`、commit SHA、PR number、来源 URL、发生时间、`provenance`、`linkage_strength`。
- **FR3.3** `provenance` 为 `direct` 时必须来自真实 session → commit → merged PR 路径；仅按时间窗口聚合得出的关联只能标 `aggregated`，不得冒充精确归因。
- **FR3.4** 端点沿用现有 RBAC 作用域下推（`visibleGitEmails` 谓词），不在应用层过滤；developer 默认只见本人，manager / admin 延续既有 RBAC 与审计写入。
- **FR3.5** 证据列表按稳定键去重（`fact_pull_request_commit` 作为抗 squash 的 join key），同一 commit 不因归属多个 PR 而重复计数。
- **FR3.6** 前端个人页（`MePage`）渲染可点击的证据链接；任何汇总数字都能下钻到来源，或标明其为聚合口径。

### FR4 结果驱动的优化建议

对应 NS-3；问题 Q4 答复 A。

- **FR4.1** 新增服务端只读推荐端点（形如 `GET /v1/optimization/recommendation?person&from&to`），基于同一套 canonical facts 现场计算：`agg_user_day` + commit / PR 事实 + `recon_daily`。本次不持久化 recommendation、不建 decision 表、不做复测周期。
- **FR4.2** 至少生成一条针对当前用户与所选区间的建议，字段包含：`observed_fact`、样本量与覆盖率、证据强度、建议行动、目标指标、质量护栏、不确定性说明。
- **FR4.3** 建议优先选择现有数据足以判断的机会，例如高 credits 但证据链缺失、身份或 trailer 覆盖不足、无结果收益的高开销模式。
- **FR4.4** 数据不足时返回 `not_evaluable` 或一条明确的观察，不得用通用文案伪装为结果建议。
- **FR4.5** 表述只允许「对应、关联、观察到」等描述性语言；在没有实验设计的前提下，不得宣称 Kiro 导致产出、质量或生产率变化。
- **FR4.6** 本地 `devdelta advise` 保持现状不改造（它在本机重解析会话文件、从不调用 `/v1/` 端点）；本次不在两处共用判定逻辑。

### FR5 前端闭环

- **FR5.1** 个人页在真实服务数据路径上完成日期选择、四桶 credits、证据链接与建议展示；仅 mock / demo 数据不算完成。
- **FR5.2** 新增或变更的响应字段必须同步到 `web/src/api/types.ts`（该文件为手工镜像的服务端契约，无生成器约束，是已知的契约漂移风险点）。
- **FR5.3** 若本次新增前端路由，必须同时登记 `web/src/main.tsx` 路由表与 `internal/server/api/api.go` 的 `spaRoutePrefixes`（`/enroll` 曾因遗漏该同步造成硬刷新 404）。本次预期复用既有 `/me` 路由，不新增路由。

## Non-Functional Requirements

- **NFR1 隐私与数据最小化**：impact / evidence / optimization 路径不读取、不保存、不返回 prompt、assistant response、代码原文或命令原文。source URL 的访问授权仍由原系统承担，DevDelta 不复制受限内容。
- **NFR2 只读外部交互**：connector 仅读；不自动评论、merge、deploy 或修改任何外部记录。
- **NFR3 数据新鲜度可陈述**：每个数据来源返回或明确标注 `data_through`、覆盖范围与 `reconciliation_status`；未知水位不得伪装为实时（现状 rollup 无持久水位，无法回答「聚合截至何时」；本次至少让缺失显式可见）。
- **NFR4 权限不回退**：不得出现跨 person 数据泄露；person 级读取继续写审计日志。
- **NFR5 兼容与回归**：现有采集、身份图、recon、commit/PR enrichment、RBAC 与既有页面行为不回退。FR1.5 的 400 语义是有意的行为变更，必须在变更前为 `timeRange()` 建立断言（该函数被 7 个 handler 共用且当前无任何测试覆盖），并逐一确认 7 个调用点的影响。
- **NFR6 可测试性**：日期语义与边界日期、四桶互斥与幂等迁移、canonical identity 解析、RBAC 负向路径、证据去重、推荐样本不足（`not_evaluable`）均需定向回归测试。受影响的数据库集成测试必须真实执行，`t.Skip` 不算通过。
- **NFR7 验证门槛**：交付前至少通过 `go test ./...`、`go vet ./...`、前端严格类型检查与生产构建、受影响 DB 集成测试、浏览器端到端 smoke，并留下命令、退出码与关键结果。
- **NFR8 口径不精确的传播**：既有已承认的不精确必须随新指标一起传播而非被抹平，包括 `stage1_assumed_baseline_*`、`shell_edit_blind_spot`、`noise_lines` 假阳性地板随语料增长（0.3% @ 104k 行 → 2.7% @ 159k 行）、`maturityLevel` L0 为结构性不可观测。
- **NFR9 跨阶段比值约束**：任何跨 stage 相除的新指标必须限定到单一 `agent_tool` 或显式拆分（`metrics.go` 既有规定），否则度量的是采集覆盖率而非开发者行为。

## Constraints

- **C1 技术栈锁定**：Go 1.25 单进程服务端 + 标准库 `net/http`（Go 1.22 方法路由，零 web 框架）+ PostgreSQL 17 + React 19/Vite SPA。本次不引入框架、消息中间件或独立 worker。
- **C2 无迁移工具**：schema 由 `store.Init()` 执行内嵌 `schema.sql` 幂等应用。本 POC 仅允许新增 `dim_person`、`dim_identity`、`fact_pull_request` 和 `fact_pull_request_commit` 四张表，禁止新增任何其他表；新增表须使用 `CREATE TABLE IF NOT EXISTS`，若确需字段，只能以 `ADD COLUMN IF NOT EXISTS` 等幂等形式表达，无回滚路径。
- **C3 隔离实现**：产品代码只在干净隔离 worktree 中修改，原工作树既有改动保持不变（原工作树当前不干净且分支不是 `main`，见 `tasks.md` 风险 1）。
- **C4 交付边界**：不部署、不做生产变更、不创建或合并 PR。
- **C5 流程重量**：`poc` 范围 + Minimal 深度；in-scope 阶段为 `reverse-engineering`、`requirements-analysis`、`code-generation`、`build-and-test`（加三个 initialization 阶段与 `intent-capture`）。
- **C6 契约无生成器**：服务端响应体在 handler 内以 `map[string]any` 与匿名 struct 就地构造，无 OpenAPI / Protobuf；前后端一致性只能靠 FR5.2 的手工镜像与测试保证。
- **C7 凭证不读取**：不读取任何凭证文件确认 `S3_REPORT_URL` / `FORGE_TOKENS` 配置状态。

## Assumptions

- **A1** 目标环境的 official credits（S3）与 PR enrichment（Forge）是否启用未知。若 official 数据整段缺失，`verified` 桶在验收时可能为空，届时以 `provisional` / `unknown` 路径演示四桶逻辑仍成立。依据：codekb「未知项」与 `tasks.md` 风险 3。
- **A2** 现有 `recon_daily` 的 trust 判定阈值（`delta_pct > 0.15`、`timestamp_uncertain`、`metering_missing`）沿用不变，本次不调参。
- **A3** `Claude Code` 通道 `Credits` 恒为 0 属既有设计，四桶分类只对 Kiro credits 有实质意义；Claude Code 的 person-day 预期落入 `unknown` 或不参与 credits 总数。
- **A4** 现有前端 `TimeRangePicker` 与 `useRangedQuery` 可承载 FR1，无需重写时间范围基础设施。
- **A5** 验收由用户在本机真实数据路径上完成；无需多用户或跨团队数据集。

## Out of Scope

引自 `north_star.md`「明确不做」与 `intent-statement.md` 范围表：

- Agent Delivery Inbox、KiroCrew / Agent 远程控制、移动端或 Slack/飞书控制面。
- 自建 Agent runtime、sandbox、worktree orchestrator 或通用任务系统。
- 新增 CRM / Taskei / DevLake 等非代码 connector；本次只用现有代码贡献数据链。
- 自动 merge、deploy、生产变更或任何外部系统写操作。
- 长期设计 Phase 0–4 的完整实现、人工非代码证据、企业外部试点安全整改。
- 个人综合 Impact Score、跨人 credits 排名、无质量约束的 PR / 代码行榜单。
- recommendation 持久化、decision 记录与多周期复测闭环（Q4 明确排除，已入 Parking Lot）。
- 物化 `fact_contribution_evidence*` 三张表（Q2 明确排除）。
- `recon_daily.user_id` → `subject_id` 重命名（codekb 改进机会 4，本次只在读路径遵守语义，不改列名）。
- rollup 持久水位表（codekb 改进机会 6；NFR3 只要求缺失显式可见）。

## Open Questions

- **OQ1** 四桶中 `provisional` 的「暂估条件」是否需要一个显式时间宽限（例如 official 报表按 24h 周期到达，T-1 日尚无 official 记录时是否直接判 `provisional` 或先判 `unknown`）？影响 FR2.2 的边界日行为，留给 code-generation 阶段以最小实现定夺并记录。
- **OQ2** `verified` 桶为空时（A1 成立）验收如何取证？建议在交付说明中以 `provisional` / `unknown` 路径演示并显式记录 official 缺失事实。
- **OQ3** 建议端点的 person 参数与登录身份不一致时（manager 查看下属），RBAC 判定沿用哪条既有谓词，需在实现时确认 `visibleUsers` 与 `visibleGitEmails` 的适用范围差异。

## Review

**Verdict:** READY
**Reviewer:** aidlc-product-lead-agent
**Date:** 2026-08-28T12:08:31Z
**Iteration:** 1
**Review class:** advisory（单次咨询，供人工在批准 gate 权衡；无 fix-and-re-review 循环）

### Findings

| # | 严重度 | 位置 | 发现 | 建议 |
|---|---|---|---|---|
| 1 | Major | FR2.2 `provisional` + OQ1 | `north_star.md` NS-2 对 `provisional` 的定义是「没有 official record，存在本地 credits，**且满足暂估条件**」；FR2.2 把 `provisional` 定为「有本地 credits，但 official 确认缺失或尚未到达」，删去了「满足暂估条件」这一限定，而该限定的具体内容又被 OQ1 显式推迟到 code-generation 阶段。后果：`provisional` 与 `unknown` 在「official 尚未到达的 T-1 边界日」缺少可判定的分界，QA 无法为该边界写出确定 pass/fail 的用例——这正是四桶互斥（FR2.2）声明的可测性在边界处失效的点。 | 人工批准前确认：是否接受把该边界留给 code-generation 以最小实现定夺（OQ1 现方案），还是要求在需求层先给出「暂估条件」的可判定阈值（例如 official 报表 24h 周期内 T-1 无记录时判 `provisional`）。若接受推迟，建议在 gate 上明确记录这是一个有意的验收留白。 |
| 2 | Minor | FR2.2 `untrusted_comparison` vs Q1/Summary `untrusted` | 状态命名在上游存在双名：Q1 答复与 Consolidated Summary 用 `untrusted`，`north_star.md` NS-2 与本文 FR2.2 用 `untrusted_comparison`。FR2.2 已显式声明二者为「同一状态的别名，下游不得当作两个状态」，冲突已被识别并消解，不构成矛盾。 | 无需在本阶段修改；提醒下游（functional-design / code-generation）以 FR2.2 的别名声明为准，API/前端只暴露一个规范名，避免契约漂移。 |
| 3 | Minor | FR3.2 vs A1 / OQ2 | 证据字段契约（FR3.2 七字段）与 NS-2 完全对齐、可测；但 `verified` 桶在 official 数据整段缺失时（A1、`tasks.md` 风险 3）可能为空，验收取证方式仅在 OQ2 以「建议」措辞给出，未固化为验收步骤。 | 人工确认：`verified` 为空是否为可接受的验收状态（以 `provisional`/`unknown` 路径演示四桶逻辑），并在交付说明中显式记录 official 缺失事实，以免被误读为功能未实现。 |

### Summary

需求忠实地把 `north_star.md` NS-1/2/3 转成了带稳定 ID（FR1–FR5 含 `FR{n}.{m}` 子 ID、NFR1–NFR9）的可测试契约：日期闭区间 + UTC 权威 + 400 拒绝（FR1，对应 Q3）、四桶互斥 + 幂等迁移 + canonical subject 语义（FR2，对应 Q1）、新增只读证据端点与七字段证据契约（FR3，对应 Q2）、只读推荐端点 + `not_evaluable` + 禁因果表述（FR4，对应 Q4 且明确不持久化）。四个 Q&A 答复无相互矛盾地落地，权威来源优先级（north_star/tasks 高于长期设计）被正确继承，`## Sources` 等 H2 结构满足 required-sections 传感器。唯一实质留白是 `provisional` 与 `unknown` 的边界判定条件被推迟到实现阶段（Finding 1），这是一个有意的、可批准的验收留白而非阻断性缺陷——鉴于本次为 advisory 单次通过，交由人工在 gate 上知情权衡。

Verdict: READY

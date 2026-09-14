# Requirements Analysis — 澄清问题

Stage: `requirements-analysis` · Depth: Minimal · Scope: `poc`

上游依据：`ideation/intent-capture/intent-statement.md`（含 `## Post-approval Amendment`）、`codekb/DevDelta/business-overview.md`、`codekb/DevDelta/architecture.md`、`codekb/DevDelta/code-structure.md`。

---

## Q1. `provisional credits` 在本仓的确切口径是什么？

背景：全仓 Go/TS/TSX/SQL 中 `provisional` 零命中，现存二分只有 `local_credits`（本地采集）与 `official_credits`（S3 报表）。`recon_daily` 另有 `trusted` / `trust_reasons` 判定。因此 official/provisional 分栏必须先定义 provisional 指什么，否则新读路径口径不可测。

- A. provisional = 本地采集量（`local_credits`）中尚无对应 official 记录的部分；official = S3 报表已确认的部分。二者按 canonical person + day 从 `recon_daily` 取。
- B. provisional = `recon_daily.trusted = false` 的 user-day（含 `delta_pct` 超阈、`timestamp_uncertain`、`metering_missing`）；official = `trusted = true` 的部分。
- C. 同时满足 A 与 B：只有「有 official 记录且 trusted」才计入 official，其余本地量全部计入 provisional。
- D. provisional = 全部本地采集量（无论是否有 official 对应），official 与 provisional 并列展示、允许重叠，由前端提示口径差异。
- X. Other (please specify)

[Answer]: X. 采用四个互斥分桶，`credits` 口径按 canonical person + day 唯一归类，不允许一个 person-day 同时落入两个桶：
- `verified`：存在 official credits 记录，且对账可信（`recon trusted = true`）→ 计入 official credits。
- `provisional`：存在本地采集 credits，但 official 确认缺失或尚未到达 → 计入 local credits，并标注为暂估。
- `untrusted`：存在可比较的 official/local 证据，但对账不可信（`recon trusted = false`）→ 只展示双方数值与 `trust_reasons`，不计入 verified/provisional 任何总数。
- `unknown`：身份解析或计量证据不足以判断 → 不计入任何 credits 总数。
official credits 到达后，同一 person-day 必须幂等地从 `provisional` 迁出至 `verified`（重复到达不产生重复计数，重放同一份 official 数据结果不变）；official 与 local 永不相加。

**Mode:** chat（2026-08-28T11:56Z）

## Q2. 证据链接（commits / merged PRs）以哪种方式暴露给个人页？

背景：`fact_commit` / `fact_pull_request` / `fact_pull_request_commit` 数据齐备，但 API 只返回聚合计数，不返回 commit SHA、PR number、repo_key 或 URL；`fact_contribution_evidence*` 三张表不存在；前端无证据链接渲染。RBAC 作用域当前下推到 SQL（`visibleGitEmails`）。

- A. 新增一个个人维度证据端点（如 `GET /v1/metrics/user/{id}/evidence?from&to`），直接查询现有 fact 表返回 commit SHA / PR number / repo_key / URL 列表，沿用现有 `visibleGitEmails` 作用域；前端 MePage 渲染可点击链接。
- B. 在现有 `GET /v1/metrics/user/{id}/daily` 响应中内联证据数组，不新增端点。
- C. 先物化 `fact_contribution_evidence*` 表，再由新端点读取物化结果。
- D. 仅在会话钻取页（`/sessions/:id`）暴露证据，不进入个人页。
- X. Other (please specify)

[Answer]: A. 新增一个个人维度证据端点，直接查询现有 fact 表（`fact_commit` / `fact_pull_request` / `fact_pull_request_commit`），沿用现有 `visibleGitEmails` RBAC 作用域下推；前端 MePage 渲染可点击的证据链接。本次不物化 `fact_contribution_evidence*` 表。

**Mode:** chat（2026-08-28T11:56Z）

## Q3. 用户自选日期区间的一致性契约如何定义？

背景：当前默认预设实际覆盖 31 个自然日、跨午夜不刷新；前端按浏览器本地日期格式化，服务端按 UTC 解析；非法输入静默降级；除 `handleOverview` 外多数响应不回显生效区间。`timeRange()` 被 7 个 handler 共用且无任何测试覆盖。

- A. 服务端以 UTC 为唯一权威，区间语义为闭区间 `[from, to]` 按日；所有相关端点响应回显生效的 `from` / `to` / `timezone`；非法或越界输入返回 400。
- B. 同 A 的 UTC 与回显要求，但非法输入保持当前降级行为并附加 `warnings` 字段，不返回 400。
- C. 由客户端显式传时区参数，服务端按该时区切日并在响应回显；非法输入返回 400。
- D. 保持服务端 UTC 解析与现有降级行为，仅补充响应回显，不改变错误处理。
- X. Other (please specify)

[Answer]: A. 服务端 UTC 为唯一权威；用户可见语义为按日闭区间 `[from, to]`（含首含尾），服务端内部按 `[from 00:00Z, to+1 day 00:00Z)` 查询；所有相关端点回显生效区间与时区；非法日期、无法解析的参数、反向区间（`to < from`）返回 400，不做静默降级。`近 30 天` 仅作为动态计算的快捷预设，不是固定验收窗口。

**Mode:** chat（2026-08-28T11:56Z）

## Q4. 那条「由实际结果支撑的优化建议」在哪里产生并交付？

背景：现有 `devdelta advise` 完全在本机重解析会话文件，从不调用任何 `/v1/` 端点，也不读 `agg_user_day` / `fact_commit` / `fact_pull_request` / `recon_daily`；服务端无 `/v1/optimization/*` 路由、无 recommendation 表、无 decision 记录、无复测机制。

- A. 新增服务端只读端点（如 `GET /v1/optimization/recommendation?person&from&to`），基于 `agg_user_day` + commit/PR 事实 + `recon_daily` 现场计算一条建议（含依据、建议行动、不确定性说明），前端个人页展示；本次不做持久化、decision 记录与复测。
- B. 同 A 的服务端端点，并额外持久化 recommendation 与 decision 记录（新增表），支持后续复测。
- C. 扩展本地 `devdelta advise`，让它调用现有 `/v1/` 端点补充服务端数据，不新增服务端路由。
- D. 服务端端点 + 本地 `advise` 同时接入，两处共用同一判定逻辑。
- X. Other (please specify)

[Answer]: A. 新增服务端只读推荐端点，基于 canonical facts（`agg_user_day` + commit/PR 事实 + `recon_daily`）现场计算一条建议，前端 MePage 展示。本次 POC 不做持久化、不建 recommendation/decision 表、不做复测周期。

**Mode:** chat（2026-08-28T11:56Z）

---

## 权威来源（用户本回合明确指定）

`north_star.md` 与 `tasks.md` 为本阶段权威输入，优先级高于 `design/DevDelta-Kiro贡献证据与优化闭环设计-v0.1.md`；与更宽的长期设计冲突时以更窄的 POC 范围为准。

---

## Consolidated Summary Confirmation

- Q1：credits 采用四个互斥分桶 `verified` / `provisional` / `untrusted` / `unknown`；`verified` = 有 official 记录且对账 `trusted=true`，`provisional` = 有本地 credits 但 official 确认缺失或待到达，`untrusted` = 可比较但 `trusted=false`（只展示、不计总数），`unknown` = 身份或计量证据不足（不计总数）；official 到达后同一 person-day 幂等迁出 provisional，official 与 local 永不相加。
- Q2：新增个人维度证据端点，直接读现有 `fact_commit` / `fact_pull_request` / `fact_pull_request_commit`，沿用 `visibleGitEmails` RBAC 下推，MePage 渲染可点击证据链接；本次不物化 `fact_contribution_evidence*`。
- Q3：服务端 UTC 为唯一权威，用户可见语义为按日闭区间 `[from, to]`，内部按 `[from 00:00Z, to+1 day 00:00Z)` 查询；相关端点回显生效区间与时区；非法、无法解析或反向区间返回 400，不静默降级；`近 30 天` 仅为动态快捷预设。
- Q4：新增服务端只读推荐端点，基于 `agg_user_day` + commit/PR 事实 + `recon_daily` 现场计算一条建议并在 MePage 展示；本次不持久化、无 decision 记录、无复测周期。
- 权威来源：`north_star.md` 与 `tasks.md` 优先于长期设计文档；冲突时以更窄的 POC 范围为准。

Does this all look correct before I generate the requirements artifact?

- Looks correct
- Request changes

[Answer]: Looks correct

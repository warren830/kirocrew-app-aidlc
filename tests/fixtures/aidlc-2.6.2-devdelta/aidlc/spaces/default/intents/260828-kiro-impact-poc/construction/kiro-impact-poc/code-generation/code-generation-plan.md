# Code Generation Plan — kiro-impact-poc

Stage: `code-generation` · Scope: `poc` · Depth: Minimal · Test Strategy: Minimal

- **实现位置（隔离 worktree）**：`/FIXTURE/repo/.aidlc/worktrees/bolt-kiro-impact-poc`
- **分支**：`bolt-kiro-impact-poc`（base `main`，`WORKTREE_CREATED` 时间 2026-08-28T12:19:46Z）
- **原工作树**：`/FIXTURE/repo` 保持不变，本阶段不在其中修改任何产品代码（requirements C3）
- **交付边界**：不部署、不 commit、不 push、不创建 PR、不读取凭证文件、不修改任何外部资源（requirements C4 / C7）

上游依据：`inception/requirements-analysis/requirements.md`（FR1–FR5、NFR1–NFR9、C1–C7、A1–A5、OQ1–OQ3）。
`units-generation` / `functional-design` / `nfr-design` / `infrastructure-design` 在 `poc` 范围下按设计 SKIP，因此本计划直接从 requirements 与 `codekb/DevDelta/` 取范围，不虚构缺失产物。

---

## 已在 worktree 中核实的事实（本计划的地基）

这些是实读代码得到的结论，不是转述：

| # | 事实 | 位置 |
|---|---|---|
| V1 | `timeRange(r)` 默认 `from = 今日UTC-30d`，`to = 今日UTC+1d`，即**闭区间 31 个自然日**，不是 30 天 | `internal/server/api/metrics.go:34-47` |
| V2 | 非法日期**静默降级**：只在 `err == nil` 时赋值，无 `else`，调用方无法区分「参数生效」与「参数被忽略」 | 同上 |
| V3 | **无反向区间校验**：`to < from` 会产生空结果集而非错误 | 同上 |
| V4 | 7 个调用点：`handleProjects:63`、`handleOverview:139`、`handleUserDaily:236`、`handleTeam:352`、`handleFunnel:437`、`handleMaturity:497`，加 `handleSessionList`（待确认行号） | `metrics.go` |
| V5 | 只有 `handleOverview` 回显 `from` / `to`（`metrics.go:142-145`），其余 6 个端点完全不回显 | `metrics.go:139-145` |
| V6 | `handleUserDaily` 按 `agg_user_day.user_id = $1` 查询，`$1` 是 `kiro_user_id`，**不是 canonical `person_id`**，且**完全不 join `recon_daily`** | `metrics.go:230-259` |
| V7 | person 级读取的 RBAC 与审计入口是 `authorizeUserRead(w, r, target)`，manager 走 `dim_user.team_id` 比对，developer 只能查自己 | `metrics.go:203-228` |
| V8 | `handleFunnel` 的 git email 作用域是**内联 SQL**（`author_email = ANY (SELECT unnest(git_emails) FROM dim_user WHERE kiro_user_id = $3)`），当前未见名为 `visibleGitEmails` 的函数 | `metrics.go:455-462` |
| V9 | 错误响应统一形状 `{"error": "..."}`；`storageError` 返回 **503**；`writeJSON(w, code, v)` 是唯一写出口 | `api.go:~200`, `metrics.go:49-52` |
| V10 | 前端 `apiFetch` 抛出的错误信息是 `HTTP ${status}`，**丢弃服务端 error body**，因此 400 / 403 的可读原因到不了 UI | `web/src/api/client.ts:44-46` |
| V11 | 前端 `useDemoQuery` 对 4xx **向上抛出**（不回退 mock），对 status 0 / 5xx 回退演示数据 | `web/src/api/hooks.ts:58-70` |
| V12 | `近 30 天` 预设是 `[dayjs().add(-30,'d'), dayjs()]`，按**浏览器本地时区**格式化，跨午夜不刷新 | `web/src/api/timerange.tsx:60-66` |
| V13 | 隔离 worktree 的 `web/` 已配置 `npm test` = `vitest run`，并具备 Testing Library + jsdom，可直接承载 400 / 403 聚焦行为测试 | `web/package.json` |
| V14 | 前端 TS 为 `strict: true` + `noUnusedLocals` + `noUnusedParameters` + `verbatimModuleSyntax` | `web/tsconfig.json` |
| V15 | `main` 的 `spaRoutePrefixes` 为 `{"/login","/projects","/team","/me","/maturity","/sessions","/admin"}`，**不含 `/enroll`** | `api.go:~430` |

## 需要在实现时先读、尚未核实的部分

不在计划里假设它们的内容（requirements「never invent the content of a missing artifact」同理适用于代码）：

- [ ] `metrics.go:520-1670` 未读区间：确认 `handleSessionList` / `handleSessionTimeline` 的 `timeRange` 调用行号，并确认 `visibleUsers` / `visibleGitEmails` 是否真实存在（V8 只能证明 funnel 未用它）
- [ ] `internal/server/store/schema.sql`：`fact_commit` / `fact_pull_request` / `fact_pull_request_commit` / `dim_repo` 的确切列名，特别是**是否已存有 PR / commit 的 web URL 或 remote**（决定 FR3.2 的 URL 是查出来的还是拼出来的）
- [ ] `internal/server/store/store.go`：是否已有读取 commit / PR 的方法可复用
- [ ] `internal/server/api/` 与 `internal/server/store/` 的现存测试文件清单，以及是否已有任何测试覆盖 `timeRange`（codekb 称零覆盖，需实测确认）

---

## 一个必须先定的决策（阻塞 Step 2 之后的全部产品代码）

**worktree base 是 `main`，而 `main` 落后于你当前的工作树。** V15 是证据：你的工作树里 `spaRoutePrefixes` 含 `/enroll`，`main` 里没有；`goal.md` / `north_star.md` / `roadmap.md` / `tasks.md` 也只存在于工作树，不在 `main`。

- org 规则（`memory/org.md` `## Way of Working`）规定 Construction worktree 的 base 是 `main`；
- requirements C3 要求「干净隔离 worktree」且「原工作树既有改动保持不变」；
- 两者都满足的代价是：本次 POC 建立在**不含你未提交改动**的基线上。

这不是我该替你选的。见 `code-generation-questions.md` 的 Q1。**在你选定之前，我不会执行 Step 2 及之后任何产品代码写入。**

---

## 实现步骤

每步的 `→` 行是该步对应的需求 ID（story-to-code-step traceability；`poc` 跳过 user-stories，故直接追溯到 FR / NFR）。

### Step 1：为 `timeRange` 建立变更前断言（characterization tests）

- [ ] 新建 `internal/server/api/timerange_test.go`，对**当前行为**建立断言：默认窗口为 31 个自然日、非法输入被静默忽略、反向区间被接受
- [ ] 运行并确认全绿，作为「变更前基线」的证据

→ NFR5（400 是有意的行为变更，变更前必须先为 `timeRange()` 建立断言）、NFR6

> 顺序不可交换：NFR5 明确要求断言先于变更。V4 的 7 个调用点共用该函数，先立断言才能证明后续变更的影响面。

### Step 2：把 `timeRange` 改成显式契约

- [ ] 引入返回值携带错误与生效窗口的形态（`from`、`to`、`effectiveFrom`、`effectiveTo`、`reportingTimezone`、`err`），保持 UTC 为唯一权威
- [ ] 用户可见语义 = 按日闭区间 `[from, to]`；内部查询语义 = `[from 00:00Z, to+1d 00:00Z)`
- [ ] 非法日期、无法解析的参数、反向区间（`to < from`）→ `400` + `{"error": "..."}`，不再静默降级
- [ ] 默认窗口从 31 天修正为**动态计算的**近 30 天快捷预设语义

→ FR1.1、FR1.2、FR1.3、FR1.5、NFR9

### Step 3：把生效窗口回显到全部相关端点

- [ ] 逐一改造 V4 的 7 个调用点，统一返回 `effective_from` / `effective_to` / `reporting_timezone`
- [ ] `handleOverview` 现有的 `from` / `to` 键保留（向后兼容），新增规范字段
- [ ] 逐个确认 7 个调用点的影响，记录确认结果

→ FR1.4、NFR3、NFR5

### Step 4：日期契约的定向回归测试

- [ ] 覆盖：合法任意区间生效并回显、单日区间、跨月/跨年边界、非法日期 400、反向区间 400、缺省预设动态计算
- [ ] 明确覆盖 UTC 与本地时区的切日差异（V12 是前端侧的对应问题）

→ NFR6、FR1.1–FR1.5

### Step 5：credits 四桶分类

- [ ] 以 canonical person + day 为键实现 `verified` / `provisional` / `untrusted` / `unknown` 四个**互斥**桶
- [ ] 读路径接入 `recon_daily`，但**不得按 `recon_daily.user_id` 直接 join**：该列承载 canonical `subject_id`（取值可能是 `person_id`、`local:<id>`、`official:<id>`、`official-conflict:<id>`），直接 join 会静默错配
- [ ] `official` 到达后同一 person-day 幂等地从 `provisional` 迁出至 `verified`；重放同一份 official 数据结果不变
- [ ] `official` 与 `local` 永不相加；`untrusted` 只展示双方数值与 `trust_reasons`，不计入任何总数；`unknown` 不计入任何总数
- [ ] 规范名单一：`untrusted` 与 `untrusted_comparison` 是同一状态的别名，对外只暴露一个名字（reviewer Finding 2）
- [ ] OQ1（`provisional` 是否需要显式时间宽限）以最小实现定夺并在 `code-summary.md` 记录选择与理由（reviewer Finding 1 已把这一留白提交人工知情批准）

→ FR2.1、FR2.2、NFR8

### Step 6：四桶的定向回归测试

- [ ] 互斥性：任一 person-day 不同时落入两个桶
- [ ] 幂等迁移：official 到达前后与重复到达的结果
- [ ] canonical identity 解析：错误 join 路径的负向断言
- [ ] `Claude Code` 通道 `Credits` 恒为 0 时的归类（A3）

→ NFR6

### Step 7：个人维度证据端点

- [ ] 新增只读端点，直接查 `fact_commit` / `fact_pull_request` / `fact_pull_request_commit`，**不物化** `fact_contribution_evidence*`
- [ ] 返回字段：repo、commit SHA、PR number、URL、时间、provenance、linkage（七字段契约）
- [ ] `direct` 与 `aggregated` 路径不得互相冒充
- [ ] RBAC：复用 V7 的 `authorizeUserRead` 完成 person 级授权与审计；git email 作用域沿用 V8 的内联谓词形态（若 Step「需先读」确认存在 `visibleGitEmails`，则改为复用该函数）
- [ ] 证据去重

→ FR3.1、FR3.2、NFR1、NFR2、NFR4、OQ3

### Step 8：证据端点的定向回归测试

- [ ] 七字段齐全且类型正确
- [ ] RBAC 负向：developer 查他人 → 403；manager 查非本团队 → 403
- [ ] 去重：squash merge 后同一 commit 不重复计
- [ ] 空结果与 `commit_scope_known = false` 的区分（不得把「不知道」渲染成「没有」）

→ NFR4、NFR6

### Step 9：优化建议端点

- [ ] 新增只读端点，基于 `agg_user_day` + commit/PR 事实 + `recon_daily` **现场计算**一条建议；不建表、不持久化、无 decision 记录、无复测
- [ ] 返回：observed fact、样本量/覆盖率、建议行动、目标指标、质量护栏、不确定性说明
- [ ] 样本不足时返回 `not_evaluable`，不输出结论
- [ ] 禁止因果表述（相关性不写成因果）
- [ ] 与建议同源的 person / range / canonical facts 必须与 Step 5、Step 7 一致
- [ ] 本地 `devdelta advise` 保持现状不改造（FR4.6）

→ FR4.1–FR4.6、NFR8、NFR9

### Step 10：建议端点的定向回归测试

- [ ] 样本充足 → 建议结构完整
- [ ] 样本不足 → `not_evaluable`
- [ ] 建议引用的事实与证据端点一致

→ NFR6

### Step 11：前端契约镜像

- [ ] 把 Step 2–9 的新增/变更响应字段同步到 `web/src/api/types.ts`（手工镜像，无生成器，是已知契约漂移风险点）

→ FR5.2、C6

### Step 12：前端时间范围与 HTTP 400/403 服务错误传播

- [ ] `web/src/api/client.ts`：对所有非成功响应安全提取 JSON `error` / `message` / `detail` 字符串或可读纯文本，保留 `ApiError.status`；无法提取时才回退 `HTTP ${status}`，使 400 与 403 的服务端原因可达 UI（V10 是当前阻碍）
- [ ] `web/src/components/QueryError.tsx`：保留 400 / 403 既有用户可见标题，同时展示非泛化的 `ApiError` 服务端消息，不再用通用文案覆盖可读原因
- [ ] `web/src/pages/MePage.tsx`：identity 或 daily-range 查询失败时先渲染 `QueryError`，不得被 loading skeleton 掩盖；`TeamPage` 的 403 路径遵守同一传播契约
- [ ] 增加聚焦行为测试：JSON 400 / 403 body 精确进入 `ApiError.message`，MePage 的反向/非法日期原因可见，TeamPage 的 RBAC 403 原因可见，并保持 4xx fail-closed、不回退 demo 数据
- [ ] `web/src/api/timerange.tsx`：`近 30 天` 改为动态计算的快捷预设，修正 31 天问题，与服务端 UTC 切日语义对齐（V12）
- [ ] 展示服务端回显的生效区间，而不是只显示用户输入

→ FR1.4、FR1.5、FR5.1、NFR4、NFR6

### Step 13：MePage 闭环

- [ ] 四桶 credits 展示（含 `untrusted` 的 `trust_reasons` 与「不计入总数」的显式说明）
- [ ] 可点击的 commit / PR 证据链接
- [ ] 一条优化建议展示，含不确定性
- [ ] 走真实服务数据路径；仅 mock / demo 数据不算完成
- [ ] 本次复用既有 `/me` 路由，**不新增前端路由**，因此不触及 `spaRoutePrefixes` 同步要求（FR5.3 的风险本次不引入）

→ FR5.1、FR5.3

### Step 14：C2 精确四表 allowlist；不新增配置

- [ ] C2 的 schema 变更 allowlist **仅且恰好**为 `dim_person`、`dim_identity`、`fact_pull_request`、`fact_pull_request_commit`；禁止新增任何其他表。四张表须使用 `CREATE TABLE IF NOT EXISTS` 幂等创建；若确需字段，只能以 `ADD COLUMN IF NOT EXISTS` 等幂等形式表达，并在 `code-summary.md` 记录无回滚路径
- [ ] 不硬编码任何凭证；不读取凭证文件确认 `S3_REPORT_URL` / `FORGE_TOKENS`（C7）

→ C2、C7、NFR1

### Step 15：文档与产物

- [ ] 关键决策以行内注释记录「为什么」，沿用本仓最强的可维护性资产（注释承载理由与实测数据）
- [ ] 写 `code-summary.md`：改动文件清单、关键决策、OQ1 的定夺、测试覆盖、与计划的偏差
- [ ] 写 `traceability.json`：枚举 FR / NFR ID，每个 `OK` 目标指向一个真实存在的 workspace 相对路径

→ NFR6、NFR7

---

## 测试配置

本次不新增或更换前端测试运行器：隔离 worktree 已有 Vitest、Testing Library 与 jsdom（V13）。HTTP 400 / 403 remediation 复用该配置运行聚焦行为测试，并纳入完整 `npm test`；前端质量门仍包含 `tsc --noEmit` 严格类型检查与生产构建（V14）。

Go 侧不需要新增测试配置：测试与源码同包并置，`go test` 原生可用。

详见 `unit-test-instructions.md`。

---

## 本阶段明确不做

- 不部署、不 commit、不 push、不创建或合并 PR、不修改任何外部系统记录
- 不在原工作树 `/FIXTURE/repo` 修改产品代码
- 不物化 `fact_contribution_evidence*` 三张表
- 不重命名 `recon_daily.user_id` → `subject_id`（只在新读路径遵守其真实语义）
- 不建 recommendation / decision 表，不做复测闭环
- 不新增 rollup 持久水位表（NFR3 只要求缺失显式可见）
- 不改造本地 `devdelta advise`
- `NFR7` 要求的完整验证矩阵（`go test ./...`、`go vet ./...`、前端严格类型检查与生产构建、受影响 DB 集成测试、浏览器 smoke）在 `build-and-test` 阶段执行并留证；本阶段只保证新增测试在本单元范围内可运行

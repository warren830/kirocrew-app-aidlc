# Code Generation — 澄清问题与计划批准

Stage: `code-generation` · Unit: `kiro-impact-poc` · Depth: Minimal · Scope: `poc`

实现位置：`/FIXTURE/repo/.aidlc/worktrees/bolt-kiro-impact-poc`（分支 `bolt-kiro-impact-poc`，base `main`）

---

## Q1. 隔离 worktree 的 base 落后于你当前的工作树，按哪种基线实现？

背景（实读证据）：worktree 已按 org 规则从 `main` 创建，原工作树保持不变。但 `main` 落后于你的工作树——`main` 的 `spaRoutePrefixes` 为 `{"/login","/projects","/team","/me","/maturity","/sessions","/admin"}`，**不含 `/enroll`**，而你工作树中的版本含 `/enroll`；`goal.md` / `north_star.md` / `roadmap.md` / `tasks.md` 也只存在于工作树，不在 `main`。requirements C3 要求「干净隔离 worktree 且原工作树改动不变」，org `## Way of Working` 要求 base 为 `main`，两者都满足的代价就是基线缺少你未提交的改动。

- A. 保持现状：base `main` 不变，在缺少未提交改动的干净基线上实现。POC 自洽可验收，但不含 `/enroll` 修复等工作树里的改动，日后合并需要你自己处理这段差异。
- B. 我先只读地列出工作树与 `main` 的差异清单交你过目，再由你决定是否需要把其中某些改动带入本次基线（我不执行任何 commit / stash / checkout）。
- C. 换一个 base：你指定一个已包含所需改动的分支或提交，我丢弃当前 worktree 并按该 base 重建。
- D. 不使用隔离 worktree，直接在原工作树实现。**注意这与 requirements C3 直接冲突，且会污染你既有的未提交改动**，我不推荐。
- X. Other (please specify)

[Answer]: A

（人工确认 2026-08-28：base 保持 `main` 不变，在干净基线上实现；工作树中未提交的 `/enroll` 等改动不带入本次基线，日后合并差异由人工处理。同时满足 requirements C3 的隔离要求与 org `## Way of Working` 的 base 规则。）

---

## Plan Approval

覆盖修订后的 `code-generation-plan.md` 与 `unit-test-instructions.md` 两份产物。

计划要点：15 个实现步骤，顺序上先为 `timeRange()` 建立变更前断言（NFR5 强制），再改日期契约并把生效窗口回显到 7 个调用点，然后实现 credits 四桶、证据端点、建议端点，最后完成前端契约镜像、HTTP 400/403 可读错误传播与 MePage 闭环。经 2026-08-29 的 C2 修订，schema 例外严格限定为幂等新增 `dim_person`、`dim_identity`、`fact_pull_request`、`fact_pull_request_commit` 四张表，禁止新增任何其他表。

测试要点：采用 Minimal、需求驱动策略，Go 侧预期约 12–15 个测试；前端复用现有 Vitest、Testing Library 与 jsdom，不新增或替换测试运行器，并增加 `client.test.ts`、`MePage.test.tsx`、`TeamPage.test.tsx` 的 400/403 fail-closed 聚焦测试。所有单元命令均按精确包、测试名或文件限定；另以 baseline `24b1ef22bf7e473bdc9b044c9e05cd5039f989ec` 对 `schema.sql` 执行精确 allowlist 门，要求新增表集合仅且恰好为上述四表且全部使用 `CREATE TABLE IF NOT EXISTS`。

明确不做：部署、commit、push、PR、四表之外的新表、物化 `fact_contribution_evidence*`、改造本地 `devdelta advise`；原工作树与其他既有边界保持不变。

- Approve Plan
- Request Changes

[Answer]: Approve Plan

（2026-08-29：`code-generation-plan.md` 与 `unit-test-instructions.md` 在上一份批准后已修订；依 Code Generation revision protocol 清空答案并重新开放 Plan Approval。历史批准保留在 audit 中，但不授权当前修订版本。）

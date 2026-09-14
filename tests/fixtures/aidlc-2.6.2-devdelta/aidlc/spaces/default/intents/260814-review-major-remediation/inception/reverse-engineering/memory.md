# Stage Diary — Reverse Engineering

## Context
- Intent: review-major-remediation (fix 3 confirmed major findings from the completed code review)
- Repo set: single repo (DevDelta); intents.json records no `repos` array
- Step 1 guard: `codekb-scope-diff` → NO_STORE (first scan, no reuse question presented)
- Write directory (from `codekb-path`): aidlc/spaces/default/codekb/DevDelta/
- Scan breadth chosen: FULL (first scan; downstream requirements/units/functional-design
  stages need whole-system architectural context, and the repo is small enough —
  ~9.4k lines Go + ~2.2k lines TS — that a focused scan would save little and
  starve those stages)

## Target areas the three findings touch
1. TLS: deploy/docker-compose.yml, deploy/README.md, internal/client/httpx, internal/client/config
2. Manager team-scoping: internal/server/api/metrics.go (handleFunnel ~434, handleProjects,
   handleSessionTimeline ~580), internal/server/auth/auth.go (Identity.TeamID), store/schema.sql (dim_user.team_id)
3. Frontend mock fallback: web/src/api/hooks.ts (useDemoQuery), web/src/mocks/fixtures.ts

## Decisions
- (pending) developer scan results → architect synthesis

## Notes

## 授权面真相表（我在 Step 2 期间独立核实，供 architect 与后续 requirements 阶段引用）

| handler | developer 自限 | team 限权 | manager 分支 | 审计 |
|---|---|---|---|---|
| handleUserDaily | 经 authorizeUserRead | **Y** | **Y** | **Y** |
| handleTeam | — | **Y** (:347) | **Y** | **Y** |
| handleSessionTimeline | 强制 owner=caller | N | Y | Y |
| handleFunnel | Y（仅 RoleDeveloper 分支） | **N** | **N** | N |
| handleProjects | Y（仅 RoleDeveloper 分支） | **N** | **N** | N |
| handleOverview | N | N | N | N |
| handleMaturity | N | N | N | N |
| handleAdminDevices / handleAdminAudit | 路由层 RequireAuth(RoleAdmin) | n/a | n/a | N |

### 关键洞察（决定了修复形态）
`authorizeUserRead`（metrics.go:203-227）**已经是一份完整正确的 RBAC 实现**：
admin 全组织 / manager 校验 `dim_user.team_id` 匹配否则 403 / developer 仅限本人，
且每次跨人读取都写审计日志。但它**只有一个调用方**（handleUserDaily:230）。

因此 finding 2 的本质不是"缺少团队限权能力"，而是**已有能力未被复用**——
handleFunnel 与 handleProjects 只写了 `if id.Role == auth.RoleDeveloper`，
manager 直接落到未过滤的全组织聚合。handleOverview / handleMaturity 连角色分支都没有。

这把修复从"设计新授权模型"降级为"把既有 helper 推广到聚合类端点"，但聚合端点
与 person-level 端点形态不同（前者无单一 target user），所以需要一个
**团队作用域过滤器**而非直接复用 authorizeUserRead —— 这正是 functional-design
阶段要设计的东西（composer 保留该阶段的理由成立）。

## 事故记录
- Step 2 开发者扫描第一次因 API 连接中断被截断（42 次工具调用后），已 resume
  同一 agent 续跑而非重新扫描，避免重复消耗与结论漂移。

## Step 2 扫描结论（两半并行完成，文件式交付）
- scan-backend.md（444 行）、scan-client-web.md（488 行）
- 我给出的 6 条 backend ground truth + 6 条 frontend ground truth **全部 CONFIRM，无需更正**

### 改变修复形态的关键新发现（必须进 requirements 阶段）
1. **`dim_user.team_id` 与 `git_emails` 全代码库无任何写入路径**（唯一 upsert 在
   `auth.go:378-384` 的 RedeemInvite，只写 kiro_user_id/email/role）。也没有任何
   IdC/SCIM/SSO 同步端点或 job。后果：
   - `authorizeUserRead` 的 manager 路径在真实部署里**恒 403**（team_id 恒为 ''）
   - `handleFunnel` 对 developer 的 commit 过滤 `author_email = ANY(git_emails)`
     恒为空集 → developer 的 `ai_code_share`(P11) **恒为 0**，manager 因不过滤反而看到真值
   → **团队限权必须连带解决"团队归属数据从哪来"，否则修完即锁死所有 manager。**
2. **越权面比报告大**：`handleOverview` 与 `handleMaturity` 连 `auth.IdentityFrom`
   都不调用，任意 developer 可读全组织设备/事件/活跃人数/对账可信度、以及每个
   workspace 的 KMI 清单（含 `payload->>'file_name'` 文件名，L1 下也返回）。
   → finding 2 的实际范围是 4 个端点（funnel/projects/overview/maturity），非 2 个。
3. **前端伪造面是全量**：10 个 hook **全部**经 useDemoQuery；8 个页面 7 个受影响。
   `retry:false` + `staleTime:60s` 使首次失败即固化 60 秒；且因 queryFn 正常返回，
   TanStack 视为 success（`isError` 恒 false，全仓无一处读 isError）。
   `redirectOn401:false` 时连 401 也回退（潜伏路径）。
4. **fixtures 被打进生产 bundle**（静态 import + vite 无排除；实测 index-*.js 命中
   `REDACTED`、`WXYZ-1234` 等伪造邮箱与审计条目）。`web/dist` 本身是
   gitignored，非仓库内容。
5. **isDemo 的视觉权重严重不足**：仅 12px 紫色 Tag，与"置信 L3"同级；而较轻的
   `unreconciled` 反而有整卡 grayscale 降级。数字本身像素级等同真实值。
6. **`isDemo` 无条件解锁 admin 菜单**（AppLayout.tsx:51）——mockMe 的 role 是 manager，
   但后端不可用时前端把 admin 入口打开了。
7. 次要但应记录：invite/pending enroll 全在进程内存（重启即失效、多副本不共享）；
   未认证的 enroll 端点不在限流链上（user_code 仅 40bit 熵可枚举）；`Audit()` 吞错误；
   `audit_log` 无防篡改；`handleTeam` 不校验 team 存在性（枚举无成本）。

### 对计划的影响（供 requirements/units 阶段决策）
- finding 2 的范围从 2 → 4 个端点，且**多出一个前置依赖**（team_id 数据来源）。
  这可能需要在 requirements 阶段与人确认：是本次一并解决身份桥，还是先按
  "team_id 为空时 manager 降级为仅本人"的 fail-closed 语义修，把身份桥单列。

## 人工决策（2026-08-15）：身份桥范围 = A（fail-closed，不本次解决）
问题：`dim_user.team_id` / `git_emails` 无任何写入路径，直接加团队过滤会让所有
manager 恒 403（team_id 恒 ''）。
**人选 A**：按 fail-closed 语义修 —— `team_id` 为空时 manager 降级为"仅看本人"，
身份桥（接企业 IdC / SCIM）单列为后续工作，不进本次范围。
理由（人已认同）：本次三条 major 均属"止血"，把接企业 IdC 拉进来会让范围失控。

对下游阶段的约束（requirements / functional-design 必须遵守）：
- 团队作用域过滤器的语义必须是 fail-closed：`id.TeamID == ""` → 退化为
  `user_id = caller`，绝不退化为"无过滤"（后者正是当前缺陷）。
- 必须在文档/发布说明中明确记录该降级行为，否则运维会误以为 manager 视图坏了。
- `git_emails` 恒空导致的 developer `ai_code_share` 恒 0 属同源问题：本次**不修数据源**，
  但应在 code-quality/已知限制中记录，避免被当成新 bug 重复调查。

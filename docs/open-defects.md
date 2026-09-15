# AI-DLC Studio 待修缺陷交接单

2026-09-10 交付状态与验收证据见 [缺陷交付验证记录](delivery-verification.md)。
2026-09-11 题组无法选择的修复见第 20 节及 [整组提交验证](verification/2026-09-11-grouped-answers/README.md)。
下文保留 2026-09-09 的原始问题记录；旧行号和“没有此能力”的描述用于追溯，不代表当前实现状态。

写给接手修复的人。每条都给了症状、根因（含 file:line）、证据、以及建议的改法。所有结论都在 2026-09-09 的
真实 gateway 上验证过，不是推测。

## 0. 怎么验证你的改动

```bash
cd /Users/ychchen/warren_ws/kirocrew-app-aidlc
PY=/Users/ychchen/warren_ws/kirocrew/.venv/bin/python
$PY scripts/build_i18n.py && $PY scripts/gen_ui_sources.py
PATH=/Users/ychchen/.nvm/versions/node/v24.20.0/bin:$PATH bash scripts/check.sh   # 期望 ALL CHECKS PASSED
./scripts/dev-install.sh                                                       # 更新并重新启用应用，加载新后端
scripts/kcapi.sh GET /api/apps/aidlc-studio/health                                # 期望 healthy / issues []
```

基线（含 2026-09-09 已修部分）：后端 **1800** 项、UI 308 项、i18n 2059 键 × 2 语言、payload 293 文件 / 2.7.1。

**强烈建议沿用变异测试**：每写完一条修复，把修复临时改回原样，确认新加的测试**失败**，再改回来确认通过。
今天每一条修复都这么验过；有两条测试正是这样才发现原本咬不住行为。

## 1. P0：brownfield 仓库全线卡死（误报阻塞发现）

**症状。** 任何已有代码的仓库，跑完 `reverse-engineering` 后 Studio 报 9 条 `missing_required_artifact`
（blocking），于是所有人类通道派发被拒绝：`AI-DLC 的文件互相矛盾，解决之前不会发送任何内容。`
恢复卡片只提供「确认已知悉」，而它不会让一条来自磁盘的发现消失 —— 死路。

**根因。** `backend/studio/consistency.py:753` 附近的规则拿 `node.produces` 与 `snap.stage_artifacts`
（**只有阶段记录目录**）比对：

```python
artifacts = list(_attr(snap, "stage_artifacts", ()) or ())
...
stage_dir = self._stage_dir_rel(snap, node, artifacts)
```

但 33 个阶段里有两个把产物写到别处，而 stage-graph 自己就声明了位置。
`payload/aidlc-kiro/.kiro/tools/data/stage-graph.json` 的 `reverse-engineering` 节点：

```json
"produces": ["business-overview","architecture","code-structure","api-documentation",
             "component-inventory","technology-stack","dependencies",
             "code-quality-assessment","reverse-engineering-timestamp"],
"outputs":  "aidlc/spaces/<active-space>/codekb/<repo>/ (9 artifacts: business-overview.md, ...)"
```

**证据（实测）。** 9 个文件全都存在，只是在 codekb 存储里：

```
aidlc/spaces/default/codekb/aidlc-demo-full/
  api-documentation.md  architecture.md  business-overview.md  code-quality-assessment.md
  code-structure.md  component-inventory.md  dependencies.md
  reverse-engineering-timestamp.md  technology-stack.md
```

而阶段目录 `inception/reverse-engineering/` 里只有 `developer-scan.md` 和 `memory.md`。

另外两个非阶段目录的例外，改的时候一起考虑：
- `practices-discovery`：产物在阶段目录，但确认后会提升到 `aidlc/spaces/<active-space>/memory/`；
- `functional-design` / `nfr-design` / `code-generation` 等：产物在**每个工作单元自己的**记录目录
  （`outputs` 写作 "under this stage's per-unit record dir"），这条路径 Studio 是否算对**没有验证过**，
  建议顺手确认。

**建议改法，分两步。**

1. **先止血（小改，优先）**：当阶段的 `outputs` 没有声明「在本阶段记录目录下」时，不再为它的 `produces`
   生成阻塞发现 —— 从「误报阻塞」退成「不作声明」，绝不会再错误拦住用户。这与该规则 docstring 已有的
   立场一致（它已经为 `optional_produces` 和 `produces_kinds` 做过同类豁免）。
2. **再补真校验（跟进）**：从节点的 `outputs` 解析出真实目录（`aidlc/spaces/<space>/codekb/<repo>/`），
   到那里核对这 9 个文件，让发现重新有牙齿。注意 `<repo>` 是仓库目录名，`<active-space>` 是当前空间。

**要加的测试。** 一个 brownfield 快照：阶段 `reverse-engineering` 处于 `awaiting_approval`，9 个产物在
codekb 目录、阶段目录只有 `developer-scan.md`/`memory.md` —— 断言**没有** blocking 发现，并且 gate 决策
可以派发。第 2 步做完后再断言：把 codekb 里删掉一个文件，发现重新出现。

## 2. 其余待修缺陷（按严重度）

以下每条都经过两个独立视角的反驳仍然成立。`CONFIRMED` = 两票都没能反驳；`PLAUSIBLE` = 一票反驳。

### 2.1 blocking · PLAUSIBLE · `backend/studio/reconciler.py:1239`
presence 增量异常会把 action 永久停在 `ReconciliationRequired`：被冻结的 presence 基线无法再自证，于是
这个边界永远无法回答，intent 也就永久卡住。改法：给「已确认但无法解决」这一支一个出口 —— 要么让 presence
检查对账真实状态而不是一个冻结计数（非 partial 路径接受 `delta >= 1 and advanced`），要么给人一个带证据的
显式收尾决策。

### 2.2 major · CONFIRMED · `backend/studio/actions.py:1416`
命令卡片（run / prepare_commit）存下来的 `captured` 从不刷新，于是一张过期的卡片永远被拒
`action_stale`，而它显示出来的 captured 看上去却是新的。改法：`submit` 只在与**行记录**比对出现漂移、
而卡片尚未派发时，用新快照刷新它的 captured 列；或者让 reconciler 直接退役这类卡片、重新生成一张。

### 2.3 major · CONFIRMED · `backend/studio/installer.py:2085`
进程崩溃后被 `settle_abandoned` 收尾的事务状态为 `recovery_required`，但 `failed_dir` 是 NULL，于是恢复
横幅把操作者指向**另一个不相关事务**的证据目录。改法：`settle_abandoned` 里为该 txid 归档死进程留下的东西
（把 `data_dir/staging/<txid>` 移到 `data_dir/failed/<txid>/staging` 并写 `transaction.json`），再把
`failed_dir` 填上。

### 2.4 major · CONFIRMED · `backend/studio/installer.py:2852`
每次安装失败都会在 `data_dir/failed/` 留下一份 **5.9 MB** 的 payload 完整副本，无上限、无清理、无去重 ——
而那份副本可证明与随附 payload 逐字节相同。改法：只有当候选字节确实携带信息时才归档（`verify_staging`
失败、或树与 manifest 不一致）；否则只写 `transaction.json`，并记录「候选与 payload 完全一致」。

### 2.5 major · PLAUSIBLE · `backend/studio/reconciler.py:1648`
`_delivery_evidence` 用「抄本里没有这一行」来证明投递没发生，但它用的是 `slots` 能力，而不是
`can_prove_absence()` —— 也就是说它可能拿一次自己知道失败了的读取当成「不存在」的证明。改法：按
`can_prove_absence()` 的 docstring 要求，在 `find_delivery_row` 读完之后调用它来限定这半个证明。

### 2.6 minor · CONFIRMED · `backend/studio/installer.py:2021`
进程在事务中途死掉时 `data_dir/staging/<txid>` 永久孤立：清理只在提交时跑、归档只在捕获到异常时跑，而
`settle_abandoned` 只处理数据库行。改法：`settle_abandoned` 为它收尾的每一行都归档或删除对应 staging 目录。

## 3. 产品缺口（不是崩溃，但用户被迫开终端或干不了事）

按价值排序。第一条最值得做 —— 它就是「会话和 app 没结合在一起」那个体验问题的真正原因。

1. **散文形式的检查点 Studio 看不见。** AI-DLC 用一句话加行内 `[OPTIONS: a | b | c]` 结尾时，Studio 的
   questions 视图返回 `{"questions": null, "mode": "degraded", "host_cards": []}`，用户在 Studio 里看不到
   任何东西。但**证据其实是有的，两处都有**：
   - 主机 slot 载荷里有 `has_options: true` 和 `options: ["Nothing to add","Add a note","Other"]`
     （`GET /api/chat/slots`，实测于 host 0.6.0-insider.6）；
   - 引擎的审计分片里有 `DECISION_RECORDED / Decision: Learnings: anything to add? /
     Options: Nothing to add,Add a note` —— 这是**磁盘证据**。

   而 `backend/studio/sessions.py` 的 `pending_question_cards()` 只读 `state._pending_questions` 与
   `slot._question_pending`（阻塞式 `ask-question` 那条路），两者都不覆盖这种情况。
   建议：从审计事件 `DECISION_RECORDED`（带枚举选项、且之后没有解决事件）派生一张问题卡片，wire text 用
   选项原文；主机的 `options` 只作旁证。这样既不违反「决策来自磁盘证据」，又能把这一大类检查点接回 Studio。

2. **Studio 创建的 intent 缺 `runtime-graph.json`。** 依赖它的引擎工具（至少 `aidlc-learnings.ts`）会失败，
   这正是 AI-DLC 退化成散文提问的直接原因。今天必须手敲
   `bun .kiro/tools/aidlc-runtime.ts compile --project-dir <repo>`。建议在 `create_intent` 的同一个 admin
   租约里编译它，或缺失时报一条发现让界面可见。

3. **没有卸载 harness 的能力。** 不只是没按钮，后端也没有接口（仓库上的写通道只有 install / upgrade /
   recovery / rescan / doctor / rebind / DELETE，而 `Unregister` 明确不动文件）。今天只能手删 `.kiro`、
   `AGENTS.md`、以及追加进 `.gitignore` 的片段。建议新增一种事务，复用现有回滚机制。

4. **bun 不在它搜的四个位置时无法在应用内解决。** 界面能如实说「没找到」并列出搜过的路径，但既不能指定
   路径，也不能在装好 bun 后重新探测（今天要重启应用）。

5. 其余已确认缺失的能力：改已有 intent 的 scope/深度、切换与新建 space、取消进行中的安装事务、回退引擎
   版本、归档仓库（而非注销）、重命名仓库标签、清理事务备份与失败证据目录、添加仓库时用目录选择器代替
   手填绝对路径。

## 4. 2026-09-09 已修（不要重做）

每条都有对应的回归测试，且都做过变异测试。

| 缺陷 | 位置 |
|---|---|
| 卡片的会话证据只在创建时快照 → 绑定会话后按钮永远点不动 | `actions.py` `LIVE_REFRESHED_EVIDENCE_KEYS` + `card()` |
| `card()` 刷新证据时不带自己的阶段 → 两个 `[?]` 时显示另一阶段的产物和结论，批准照样通过 | `actions.py:card()`（`stage=rec.stage`） |
| `card()` 刷新把主机 slot 丢了 → 所有对话都显示空闲 | `actions.py:card()`（`slot=`） |
| 崩溃留下的执行租约无法回收 → 仓库死锁 19 小时 | `leases.py` 新增回收原因 `release_lost` |
| 证明与删除之间无人复查 → 同一工作流里两个回合交错 | `storage.py:lease_reclaim(expect_action=)` + `leases.py:RECLAIM_ACTION_GUARD` |
| `recovery_required` 的仓库出不来（Recover 永远拒 `not_installed`） | `handlers/repos.py:_assert_plan_offerable` |
| 「Rebind session」永远 400、「Mark not delivered」永远 409 —— 两个死按钮 | `actions.py:_offered_decisions` |
| 熔断器的记账发生在状态 CAS 之前 → 被拒的失败仍留下罚分 | `actions.py:record_failure` / `_peek_breaker` / `_write_breaker` |
| 有安装凭据的仓库 `GET /repos/{id}/git` 直接 500（`ReceiptFile.relpath` 不存在） | `handlers/repos.py:142` |

## 5. 复现环境

- 演示仓库 `/tmp/aidlc-demo-full`（10 行 Python 小工具，README 写明 `--precision` 被忽略），
  Studio 里 `repo_id = r_efc5c63de739`，intent `260909-precision`。
- 41 张逐步截图在 `assets/demo-20260909/`，含 P0 那条的拒绝画面（`41-approve-sent.png`）。
- 手工验收流程见 `docs/manual-e2e-runbook.md`。

## 6. 2026-09-10 完整流程截图实测新增：流程图产物链接保留旧 action

**状态：已修复并实机验证。**

审批通过 `requirements-analysis` 后，从 Workflow Map 选择正在执行的 `code-generation`，
点击 `Open code-generation-plan.md`。页面进入 Action Center，却继续显示上一张需求分析 Gate
的产物列表，并提示 “The file this link names is not in this record. The first artifact is shown instead.”

`ui/src/map/MapView.tsx` 的 `openArtifact` 导航仅更新 view、tab、stage、unit 和 artifact，
保留路由中上一张 Gate 的 action。`ActionsView` / `DetailShell` 仍按该 action 读取证据；
而没有 action 时，当前 DetailShell 只显示未选择状态。因此修复需支持按仓库、intent、
stage/unit 读取产物，或明确选择匹配该产物的记录；仅清空 action 不足以恢复产物查看能力。

修复后，流程图链接清除旧 action，并进入按阶段和工作单元校验的只读产物视图。
不存在的文件不会被其他文件替代。普通审批卡中的文件切换仍保留该卡的证据范围和审批按钮；
任务列表的“Open decision”也不再保留上次访问的历史卡片。实机证据见截图 147、148、157。

本次复现仓库为 `/tmp/aidlc-precision-screenshots-CHDuUU`，intent 为
`260910-precision-fix`，旧 action 为 `a_0e7590247b51ce04`。
原图：`full-e2e/113-map-artifact-link-stale-action.png`；结构化记录：
`full-e2e/map-artifact-navigation-gap.json`，均位于本次 Codex 截图输出目录
`/Users/ychchen/.codex/visualizations/2026/09/10/01a08ab3-57ca-7170-b7aa-fe9e1bfa97eb/` 下。

## 7. 2026-09-10 完整流程截图实测新增：计划退回文本不符合精确选项契约

**状态：已修复并实机验证。**

在 Code Generation 的 Plan Approval 卡中填写反馈并点击 `Request plan changes`，
Studio 实际发送 `Request Changes: <feedback>`。消息成功投递，代理也能读取反馈，
但引擎无法记录此计划决策，返回
`Plan Approval requires the actual offered choice from this prompt and session`。

`backend/studio/actions.py` 的 `wire_text_for("request_plan_changes", ...)` 与普通 Gate
退回共用带反馈的前缀。随附引擎 `aidlc-testing-posture.ts` 的 `offeredPlanApprovalChoice`
则对整个用户回复做精确选项匹配，不能识别带冒号和反馈的回复。

当前主机兼容修复将明确的 `Request Changes: <非空反馈>` 传为独立的退回选择，
并保留包含完整反馈的上下文。普通散文不会匹配；批准仍要求完整的精确选项。
实机通过原有 Studio 反馈表单提交，受保护回复记录为 `Request Changes`，
随后引擎正常记录 `QUESTION_ANSWERED / Code Generation Plan Approval`，并按反馈扩展测试计划。
没有手写审批证据或关闭守卫。成功截图为 `full-e2e/131-plan-rejection-receipt-in-audit.png`；
状态证据为 `full-e2e/host-fixed-plan-rejection-receipt.json`。

实测 action 为 `a_1e5e529682d62ade`，原图为
`full-e2e/116-request-plan-correction-confirm.png`、`117-plan-correction-processing.png`，
结构化证据为 `full-e2e/plan-change-wire-gap.json`，输出目录同上一节。

## 8. 2026-09-10 完整流程截图实测新增：主机上下文破坏精确计划批准

**状态：已修复并实机验证；完整流程已执行结束。**

即使发送精确的 `Approve Plan`，引擎仍拒绝记录计划批准。分别测试了 Studio 的批准按钮和
内嵌会话文本框：两者均产生真实 `HUMAN_TURN`，但运行目录只有对应 session 的 challenge，
没有匹配的 response，生产代码没有开始执行。

主机的规范会话记录证实，12 字符的原始选择进入 CLI 时，`user_prompt_length` 分别为
2183（Studio 按钮）和 5887（内嵌会话）。KiroCrew 的
`src/kiro_crew/dashboard/chat_runner.py` 传递 enriched `full_message`；
`src/kiro_crew/context.py` 还会加入交互说明。引擎的 `offeredPlanApprovalChoice` 对整个文本
做精确匹配，因此这些附加内容足以使合法选择失配。直接向内嵌会话重试无法避开这条公共路径。

需要在主机到钩子的受信任边界保留规范用户选择，并将辅助上下文分离；不能从任意散文中
猜测审批意图，不能手工写 response 或关闭审批守卫。

已应用主机端单文件兼容补丁：在添加上下文前保存原始输入，对明确的 AI-DLC 计划选择使用
独立 JSON `text` 字段携带选择，`context` 保留辅助内容及原始反馈。隔离验证的 27 个场景、
81 项解析检查通过，独立复核无实质问题。本机 KiroCrew `0.6.0-insider.6` 的网关在空闲时重启，
新 Studio boot 为 `fb639636befef511`，健康检查通过。

修复后的真实 UI 批准产生了 `Approve Plan` 受保护回复、匹配的受保护 receipt，
以及 `PLAN_APPROVAL_RECORDED` 审计事件，计划指纹为 `1e4746dd51bf89a8…`。
成功截图为 `full-e2e/136-plan-approval-recorded-after-host-repair.png`；
证据为 `full-e2e/host-fixed-plan-approval-receipt.json` 和
`full-e2e/protected-plan-approval-receipts.json`。
该兼容补丁修改的是本机已安装主机，后续应用更新可能覆盖；源码补丁和回滚方案均保留。
补丁、验证和回滚说明见本次输出目录
`/Users/ychchen/.codex/visualizations/2026/09/10/01a08bcf-d484-7272-af05-b24be0e4b57d/host-plan-approval-fix/README.md`。

原图：`full-e2e/122-exact-plan-approval-rejected.png`、`123-native-conversation-plan-approval.png`；
证据：`full-e2e/exact-plan-approval-refused.json`。CLI 会话：
`/Users/ychchen/.kiro/sessions/cli/7ee2ce8d-5dbe-4191-84a7-2f647a23ee5d.json`。

## 9. 执行进度与复核附录使有效的计划批准失效

**状态：已修复并通过正常升级事务实机验证。**

引擎要求执行者勾选计划步骤，但旧指纹包含勾选状态。八个步骤从 `[ ]` 变为 `[x]` 后，
后续测试命令被审批守卫拒绝。只在内存中还原这八个标记，即得到原批准指纹，证实没有
其他计划变更。复核者随后追加的 `## Review` 也触发了同一问题。

当前指纹通过真实 Markdown 任务节点识别执行进度，保留注释、代码、HTML、引用及复核清单
中的文字绑定。已有批准的复核附录兼容路径要求有效的受保护收据、完整且正确限定的审计、
当前尝试的复核请求字节边界，以及与原批准一致的正文指纹。新批准仍绑定完整输入，不能
借此忽略预先存在的复核内容。还补充了 BOM 字节偏移和复核边界标记伪造检查。

138 项针对性测试通过，独立复核通过。升级后保留原批准指纹 `1e4746dd51bf89a8…`，
没有重写批准收据；实际工作流随后完成代码生成并进入 Build and Test。
证据：`plan-progress-fingerprint-gap.json`、`plan-progress-fixed-evaluation.json`、
`engine-refresh-acceptance.json` 及截图 143、152、160。

## 10. 同版本修复包与本地模型偏好的安全更新

**状态：已实现；本次同版本更新已实机通过，回退边界另有针对性回归。**

旧升级判断只比较版本号，无法应用版本号相同但内容更新的修复包。当前只有受信任的当前
安装凭据与新包摘要不同，且仓库身份、版本、租约和文件归属检查都通过，才允许同版本更新。
完全相同的包仍不重复升级。

CLI 会保存新增模型偏好，旧整对象管理将其视为冲突。现在按明确的模型 ID 管理默认项，
保留其他模型配置；遗留凭据仅在旧片段摘要得到验证时收窄归属，不猜测所有权。
卸载和版本回退也核对具体模型条目，避免覆盖已经归用户所有的设置。

实际事务 `tx_151d3af8e82eb619` 已提交，凭据为 `rc_813d236341ac14bc`。
18 份关键文件保持逐字节一致，包括模型设置、工作流状态、计划及受保护审批记录。
证据：`engine-refresh-acceptance.json`、截图 145、146、151、152。

## 11. 无工作单元的代码生成 Learnings 被误判为范围不明

**状态：已修复并实机验证。**

Code Generation 在图中标记为按工作单元运行，但小型缺陷修复可以使用明确的零工作单元
指令。旧读取器因此拒绝将已答计划后的 Learnings 审计转为问题卡。

当前仅在匹配状态的 v2 `run-stage` 指令明确省略 unit 和 units 字段、且没有活动工作单元时，
识别此阶段级范围。空值、错误类型、旧指令、部分审计和错误回执仍不能授权问题卡。
同时区分 Markdown 的 `Plan Approval` 标题和审计中的 `Code Generation Plan Approval` 名称。
26 项范围回归及相关 309 项测试通过；实机问题卡及提交见截图 153–155。

## 12. 逻辑产物名称与实际文件名不一致产生缺失误报

**状态：已修复并实机验证。**

`build-test-results` 和 `load-test-results` 在引擎词汇中都对应 `test-results.md`。
旧检查按逻辑名称找文件，使已完成的 Build and Test 被误报阻塞。

当前检查使用这两项明确映射。实际文件存在时放行，只有错误的逻辑同名文件仍不能替代它。
四项回归在修复前均失败，修复后相关 13 项通过。实机零阻塞状态和 Gate 恢复见截图 167–170，
证据为 `build-results-alias-fixed-state.json`。

## 13. 已完成流程残留旧错误卡和执行标记

**状态：已修复并实机验证。**

工作流已经记录 `WORKFLOW_COMPLETED`，界面仍保留此前没有阶段字段的错误事件所派生的卡片，
流程图也继续将最后阶段标记为正在执行。旧错误事件缺少生命周期边界判断，终态图则沿用了
用于导航的最后阶段作为执行位置。

当前投影使用具有明确因果顺序的阶段推进或完成事件截断历史错误突发；跨分片同时间事件、
不同工作单元和不能确定顺序的证据仍保守处理。完成之后的新错误、明确的操作失败、
不确定投递与熔断卡不会因此被隐藏。Completed 流程图保留最后阶段导航，但不再标记执行阶段。

新增与相关测试共 153 项通过，真实工作流只读重放前后 73 个文件保持一致。安装后，
`260910-precision-fix` 显示 Completed、0 待办、0 阻塞；7 个阶段完成，2 个部署阶段按条件跳过。
实机修复前后见截图 172、173，最终状态见 `workflow-final-state.json`。

## 14. 2026-09-11 安装预览重复显示 Install 入口

**状态：已修复并更新本机前端。**

打开安装预览后，预览中的 `Install AI-DLC 2.7.1` 确认按钮与仓库维护区的
`Install AI-DLC` 入口同时显示。后者只是再次打开预览，但名称和强调样式容易使人误以为
存在两个安装操作。

`ReposView` 现在将安装预览的打开状态传给 `RepoCard`，预览打开期间隐藏维护区的重复入口；
取消预览后恢复。冲突预览仍只显示一个禁用的安装确认按钮。

新增回归在修复前复现两个按钮；修复后全部 363 项 UI 测试、类型检查、构建和 bundle 检查通过。
使用本机实际提供的前端文件和隔离的仓库响应进行浏览器复核，确认两个按钮变为一个、
取消后入口恢复、冲突时确认禁用，且没有发送真实安装请求。只更新本机 `ui/dist/index.mjs`，
后台 boot 保持不变，未重启用户正在运行的工作流。

验证日志与截图位于
`/Users/ychchen/.codex/visualizations/2026/09/11/01a08bcf-d484-7272-af05-b24be0e4b57d/duplicate-install/`。

## 15. 2026-09-11 Doctor 执行后看不到反馈

**状态：已修复并更新本机前端。**

用户在仓库详情底部点击 Doctor 后，看不到结果。实际记录显示该仓库的六次调用均成功，
最近一次耗时 229 毫秒、退出码为 0。原页面把完成或失败提示放在页面顶部，与底部的维护按钮
分离；浏览器复现时，成功提示位于当前视口上方约 850 像素。

现在 Doctor 按钮旁显示运行状态、结果和耗时，并可展开标准输出与错误输出。
仓库占用等请求拒绝也在相同位置显示。反馈出现时仅将附近结果滚入视口；
切换仓库后，旧请求的迟到响应不会污染新页面。诊断文本按纯文本渲染。

四项新回归在修复前全部失败，修复后全部 367 项 UI 测试、类型检查、构建与 bundle 检查通过，
中英各 2231 个词条校验通过。本机提供的前端文件经浏览器复核，成功结果和占用提示均位于
按钮附近且在当前视口中可见。浏览器使用隔离的响应数据，没有再次执行用户仓库的 Doctor。
本次只更新前端文件，后台 boot 保持不变。

实际调用记录、回归日志、浏览器截图与更新摘要位于
`/Users/ychchen/.codex/visualizations/2026/09/11/01a08bcf-d484-7272-af05-b24be0e4b57d/doctor-feedback/`。

## 16. 2026-09-11 仓库详情缺少新建 Intent 的快捷入口

**状态：已补齐并更新本机前端。**

原页面只能通过顶部全局按钮或 Intents 页面进入创建向导，仓库详情的 Intents 区域没有
新建按钮，空仓库也缺少继续操作的入口。

现在可用且已安装引擎的仓库在该区域显示 `New intent`。无论是否已有任务，点击都会打开
创建向导并预选当前仓库，同时清除上一条任务的 action、intent、stage、unit、artifact、
transaction、draft 和页面锚点。向导仍须由用户完成最后的创建确认，不会自动创建或启动任务。

新增两个集成回归覆盖空任务列表和已有任务列表，修复前均失败；修复后全部 369 项 UI 测试、
类型检查、构建及 bundle 检查通过。实际仓库的桌面与手机页面均验证了按钮、仓库预选和
干净的向导路由。仅更新前端文件，后台 boot 保持不变。

验证日志、截图与更新摘要位于
`/Users/ychchen/.codex/visualizations/2026/09/11/01a08bcf-d484-7272-af05-b24be0e4b57d/repo-new-intent/`。

## 17. 2026-09-11 已开始执行的操作仍显示在待办列表

**状态：已修复并更新本机前端。**

Action Center 原样显示全部未结束的操作，把 `Delivering`、`Delivered`、`Processing`
也计入 `Needs you` 列表和角标，因此提交成功后，同一条目会在执行期间继续占据待办列表。

现在列表和角标使用同一个待办筛选规则：已提交执行和已结束的条目不再计入待办。
原始操作数据仍用于快速轮询、Workflow Map 和右侧详情，选中的执行进度不会随列表移出而丢失。
`NotDelivered`、`DeliveryUncertain`、`ReconciliationRequired`、`Failed` 仍保留；
新的 Queued 审批或问题也正常显示，即使同一会话还在运行。

三个新增集成回归在修复前全部失败，修复后全部 372 项 UI 测试、类型检查、构建与 bundle 检查通过。
测试实际经过提交、主机投递的模拟响应与投递回报，确认条目移出后仍保持快速轮询，
状态变为投递不确定时会重新入列，主机发送调用始终只有一次。
浏览器使用隔离响应验证了列表 1→0、执行详情保留，以及无需刷新恢复至 1 的过程，
没有派发真实工作流回合。本次只改变前端呈现，后台操作状态、去重和协调逻辑保持原样，
后台 boot 未变化。

验证日志、截图与更新摘要位于
`/Users/ychchen/.codex/visualizations/2026/09/11/01a08bcf-d484-7272-af05-b24be0e4b57d/queue-inflight/`。

## 18. 2026-09-11 阶段交接未落盘，无进展回合却显示四步完成

**状态：本次工作流记录已修复；无进展状态的显示已修复。**

`aidlc-todo-usecase / 260911-http-api-1` 的审计已记录两次 `PIPELINE_LINK_COMPLETED`，
9 份 CodeKB 产物也已生成，但没有 Learnings 的 `DECISION_RECORDED`，也没有
`STAGE_AWAITING_APPROVAL`。状态文件仍为 `[-] reverse-engineering`，完成数为 3/10。
代理在对话中把经验确认与阶段审批合为一组选择，并把指令的 `gate: true` 表述为已挂起的审批门。
这三次 `/aidlc` 回合最终均没有记录状态推进，Studio 的原始响应因此为 Idle、0 待办。

随附 Kiro 流程的要求是先记录并回答 Learnings 问题，再调用
`aidlc-orchestrate.ts report --stage reverse-engineering --result awaiting-approval` 打开正式审批门。
修复本次记录时，先备份并核对状态、审计和产物，再通过官方 `aidlc-log.ts decision` 工具
补回未回答的 `Learnings: anything to add?`，选项为 `Nothing to add` / `Add a note`。
随后重新扫描，仓库恢复为 WaitingForYou、1 条问题卡、0 阻塞。
19 份受检查文件中只有审计文件变化；应用代码、9 份 CodeKB、状态和指令保持原字节，
未记录回答、经验采纳或阶段批准。

另一个显示问题是 `ResolvedNoTransition` 原先把“State changed”也画成完成，
并显示“No state transition was expected”。现在最后一步明确显示“状态未变更”，
结果说明为“未记录到工作流状态变更”，使用中性色；确有 `StateChanged` 时仍显示四步完成。
新增三项回归，修复前一项失败、两项通过；修复后全部 375 项 UI 测试、类型检查、
构建与 bundle 检查通过，中英各 2233 个词条校验通过。

恢复记录与界面验证的证据位于
`/Users/ychchen/.codex/visualizations/2026/09/11/01a08bcf-d484-7272-af05-b24be0e4b57d/pipeline-handoff/`。
本次恢复了已遗漏的确认问题，不代表可以跳过随后的人工阶段审批。

## 19. 2026-09-11 Recovery 反复生成，已取消的卡片仍显示为当前故障

**状态：`test-kirocrew-aidlc / rmb` 的绑定已恢复，相关显示已修复。**

`r_efc61ab8d7e6 / 260909-rmb` 保留着规范会话绑定，但 KiroCrew 的会话列表中没有对应目标，
直接读取也返回 not found。扫描因此持续生成 `session_lost_mid_stage` Recovery。
用户确认通知后，旧记录变为 Cancelled；原因仍存在，下一次扫描又生成同一边界的新通知。
这不是工作流文件互相矛盾，但标题用 `{reason}` 读取实际只有 `codes` 的参数，显示成了
“files disagree: Unavailable”。已取消的记录还继续显示 Critical、等待中和 Queued in progress。

通过正常的创建会话、命名、设置项目和绑定接口恢复了该任务的规范会话，并通过已有运行图编译接口
补齐旧任务缺失的 `runtime-graph.json`。原有状态、审计和任务描述三个文件的摘要均保持一致；
暂停设置保持为 true，没有发送运行消息。连续三次重新扫描均为 0 条 Recovery，
只剩原来的 Run 待办。继续运行前需要用户选择 Allow dispatch again。

显示修复包括：

- Recovery 的 `codes` 与旧版 `reason` 参数均能得到明确说明，会话丢失不再被描述成文件冲突。
- 会话丢失通知提供前往 Intents 管理会话的入口，并明确“确认只关闭通知”。
- 已关闭的操作显示为历史记录，显示关闭时间与状态，提供“查看当前待办”；原通知保留在折叠区。
- Cancelled 的第一步显示“已取消”，不会继续显示为排队执行中。

四项新增回归在修复前失败；修复后全部 379 项 UI 测试、类型检查、构建与 bundle 检查通过，
中英各 2247 个词条校验通过。真实历史记录及隔离的会话丢失场景均完成浏览器验证。
只更新了本机前端资源，后台 boot 保持不变。

绑定恢复、重复扫描、文件摘要、测试日志和截图位于
`/Users/ychchen/.codex/visualizations/2026/09/11/01a08bcf-d484-7272-af05-b24be0e4b57d/recovery-session/`。


## 20. 2026-09-11 题组无法选择，多选被显示为单选

**状态：已修复并更新本机，真实投递与全量回归通过。**

`aidlc-demo-full / 260909-precision` 的四道问题来自有效的持久化题单，但没有原生问题
payload。旧投影将这种情况一律标成 degraded，同时源码关闭 grouped answers，因此控件全部
禁用。多选解析只识别 `(multi-select)`，也漏掉了第四题明确写出的 `select all that apply`。

现在格式受支持且读取稳定的题单可直接提供表单。解析支持明确的中英文多选标记；题号、
选项、单选/多选和 Other 来自原文。确认期间题单变化、重复或多余答案、原生问题等待及
主机会话忙碌都会阻止发送。Other 与普通多选同时存在时，全部内容均保留；普通多行回答
也不再只显示第一行。

真实协议验证还发现，一次人类回复只允许一条回答回执。新整组消息因此明确要求代理把
四个答案一起记录；第二轮确认四题正确落盘、一条审计回执包含全部四题、主机仅发送一次，
并停在下一次人工确认。详见 [验证记录](verification/2026-09-11-grouped-answers/README.md)。

更新准备时发现待办示例的 `Nothing to add` 已真实送达，但代理漏记回执而持续占用租约。
经核对最后一条用户消息、投递 ID 和待答问题，使用官方 `aidlc-log.ts answer` 补录同一原文，
该操作正常结算为 `ResolvedNoTransition`，状态文件未变，也没有代为批准阶段。


## 21. 2026-09-11 回合中的临时工具错误被提前判为最终失败

**状态：已修复并更新本机；最终浏览器提交正常结算。**

整组回答的真实 UI 验证发现，代理探查命令用法时留下三条 ERROR_LOGGED，随后正确填写
四个答案并写入完整回答回执，但协调器在代理仍运行时已将操作标为 Failed、释放租约并
打开熔断器，无法再以最终成功证据结算。

现在 Processing 和 ReconciliationRequired 均等待已投递回合结束后才做最终错误分类。
成功的答案、审批等证据仍优先判断；回合结束后未恢复的错误仍按原有分类和阈值报失败。
新增两条回归分别覆盖两个状态：错误出现时保持当前状态，成功回执出现且回合结束后
结算为 ResolvedNoTransition；原有“回合结束后连续错误应失败”的测试继续通过。
86 项协调器测试全部通过。


## 22. 2026-09-11 已消失仓库的缓存审批仍提供 Approve

**状态：已修复并更新本机；2713 项后端、384 项前端测试和真实页面验证通过。**

`ledger-export (scratch) / 260904-ledger-export` 的目录 `/private/tmp/aidlc-studio-e2e`
已不存在，仓库登记为 unavailable / path_missing。旧 ActionCard 只返回仓库名称和路径，
页面无法区分当前工作区与缓存记录，因此仍提供 Approve，在确认时才因 session=null
提示“未绑定会话”，把用户引向无法解决问题的会话绑定步骤。

现在卡片携带 availability、availability_detail 和 archived。不可用仓库在列表和详情中
明确显示原因与已保存记录标记；详情提供管理仓库入口，原有仓库页面支持重新关联路径。
此时服务端不再提供人类派发决策，提交接口也在读取快照或取得租约前返回 repo_unavailable。
原审批状态和证据保持原样；恢复仓库可用性后重新读取现状，再提供相应决策。
归档仓库的旧操作从前端待办列表及计数中移除，原始操作数据仍保留用于历史查看和投递跟踪。


## 23. 2026-09-11 租约冲突误报文件矛盾，失效页面保留确认框

**状态：已修复并更新本机；2717 项后端、387 项前端测试通过。**

纯运行时租约冲突此前被统一包装为 state_inconsistent，页面因此错误地要求解决文件矛盾。
现在返回 repo_busy 和占用者信息，并在确认面板提供查看占用操作的入口；租约保护保持原样。

页面此前在操作已删除、接口返回 action_not_found 后仍保留旧缓存及确认按钮。
现在明确失效的记录会撤去确认框，并提供返回当前待办入口。题组已答完时的汇总与计划
检查点使用对应标题，不再显示“0 题未答”式阻塞标题或起草所有答案的按钮。

旧 QA 验证会话已标为验证结束，消息历史保留。本轮没有发送业务答案或汇总批准。
详见 [验证记录](verification/2026-09-11-lease-and-stale-page/README.md)。


## 24. 2026-09-11 Plan 可选阶段选中后无法取消

**状态：已修复并更新本机；2720 项后端、388 项前端测试通过。**

预览把手动新增阶段按最终选中状态追加 ALWAYS 锁，并把预设原本允许的依赖缺口当作
新增阶段的不可撤销依赖，导致选择无法反向取消。现在按基线施加必选约束，并让展示依赖锁
遵循已有的增量依赖校验规则。真正的必选、历史状态和新依赖约束保持原样。

连续勾选／取消及 7 组真实预览请求通过，当前用户已启动的 intent 未改动。
详见 [Plan 切换验证](verification/2026-09-11-plan-toggle/README.md)。

## 25. 学习笔记占位标签被作为答案发送，空闲回合持续占用租约

**状态：已修复并更新本机，实际阻塞记录已恢复。**

审计检查点的唯一选项 `Free-text note` 被当作普通单选项，导致页面发送标签而非笔记正文。
代理拒绝将标签记作笔记后，没有产生有效答复回执；Studio 继续保持 Processing 和执行租约。

现在仅对明确的自由文本标记显示空白正文框，前后端均拒绝空白或占位标签。
正文回执核对遵循引擎的换行转义和项目路径脱敏规则。已发送的卡片不再提供可编辑答案或
“ready to send”提示。旧标签回合只在同一问题仍待答、代理已结束、人工回合证据成立时结束，
显示“仍需补充正文”，释放自身租约并重新呈现待答问题。

实际旧记录 `a_10d00a945046f55b` 已结束，租约归零；新问题提供正文输入。
会话消息和 40 个工作流文件保持不变。完整检查 2731 项后端、393 项前端测试通过；
最后补充的正文格式与恢复保护测试 7 项通过。
详见 [自由文本笔记验证](verification/2026-09-12-audit-note/README.md)。

## 26. 审批出现后旧 Run 仍留在待办，阶段切换后也不失效

**状态：已修复并更新本机。**

用户创建的命令原先全部跳过过期清理。旧 Run 会持续刷新会话证据，却保留创建时的阶段，
导致 requirements-analysis 的 Run 与审批同时出现，甚至在 Code Generation 执行时仍可见。

现在从未发送的 Run/Resume 遇到审批、问题、总结确认、计划审批、完成状态或阶段变化时，
自动关闭并保留历史。创建入口和最终投递前均检查适用性，正在执行的会话不会新增 Run。
已投递、投递不确定的记录不进入此清理逻辑。审批卡仍正常显示；状态变化后旧确认框关闭。

实际旧记录 `a_417b68b189525613` 已安全取消。全量检查 2756 项后端、398 项前端通过。
详见 [Run 检查点验证](verification/2026-09-13-run-checkpoints/README.md)。

## 27. Studio 启动时未连接宿主，将磁盘进展误判为回合结束

**状态：已修复并更新本机；误结算记录和执行租约已恢复。**

本次更新发现，启动阶段尚未取得会话运行状态时，旧逻辑会用新审计事件代替“回合已结束”。
计划审批回执因此可能让仍在生成代码的回合提前结算并释放租约。
现在宿主状态不可读时不判断回合结束，待连接后确认会话空闲再结算。

相关 137 项回归通过，包含未连接宿主的冷启动、运行中保留租约、结束后正常释放。
实际执行记录从更新前备份恢复，保留原 delivery_id，以新的租约代次避免旧持有者误操作。
修复留有 Studio Activity 记录；会话消息和 44 个工作流文件未改变。

## 28. Recovery 页面对历史 directive.units 调用 join 导致整个 Action Center 崩溃

**状态：已修复并更新本机。**

操作证据原先只输出 directive 的 stage、unit、matches_state，恢复模板却将其当作完整
Directive，对不存在的 units 调用 join。阶段级指令的 unit 为 null，恰好触发这一分支。

新证据输出完整指令；前端类型明确兼容历史精简快照，并对数组字段缺失做保守展示。
历史投递证据不需要改写。真实报错结构已在 DetailShell 组合测试中复现并验证修复。

## 29. 新 Learnings 问题覆盖已批准的计划文件，使旧审批记录无法结束

**状态：已修复并更新本机，实际旧记录已正常结束。**

问答读取器正确地将当前交互切换到 Learnings 后，计划审批核对仍只查看当前问题，
丢失已回答计划的证据入口，最终超时进入 ReconciliationRequired。

现在同一快照内部保留经过验证的已关闭问题文件与回执。核对原文件路径、阶段、unit、
投递时间，以及回执可提供的 Prompt SHA-256 和 Intent 身份后，结束原计划审批。
会话仍在执行时不提前结束，新 Learnings 保持未回答。

实际 `a_015dc83090603d81` 已通过正常 reconcile 接口结束，原投递记录保留。
全量 2762 项后端、399 项前端测试通过，补充审批关联检查 6 项通过。
详见 [Recovery 渲染与计划交接验证](verification/2026-09-13-recovery-render/README.md)。

## 30. 恢复会话的新消息没有对应 HUMAN_TURN，Learnings 无法入账

**状态：宿主已于 2026-09-15 重启；同一原生会话恢复后的输入登记已实测通过。**

2026-09-13 15:28（Asia/Shanghai），会话收到了 Nothing to add，
但审计中没有对应的新 HUMAN_TURN。15:28:57 的 aidlc-log answer 返回
“Cannot record this answer because no new human reply has arrived for the question”。
需要检查恢复会话的人工输入登记链路，不能通过重放答案或补造人工标记处理。

2026-09-14 已确认根因：KiroCrew `_eager_spawn` 未传入项目目录，
无法识别项目内的 `aidlc` agent，预热恢复时回退至默认 `kirocrew`。
用户消息复用该会话后，没有执行 AI-DLC 的人工输入 Hook。
修复预热时的项目 agent 缓存加载，以及初次解析、重试、应用 agent 恢复分支的项目目录传递。
当前安装版本的 14 项专项回归全部通过。

同时修复恢复卡片的重复版本更新、已送达消息仍提供重发入口，以及将工作流未确认
误述为消息未送达的提示。原投递记录和人工决策保留。
完整修复范围与验证结果见 [本轮验证记录](verification/2026-09-14-recovery-and-ui/README.md)。

重启后的隔离 demo 先发送只读状态请求，再关闭、恢复并聚焦同一会话。
宿主日志确认预热使用 `agent=aidlc`、`resumed=True`，原生会话 ID 保持一致；
预热没有新增 `HUMAN_TURN`，随后通过界面发送的请求使其从 1 增加至 2。
另一次 demo 检查点回答已产生引擎的 `QUESTION_ANSWERED` 回执。
原业务会话的答案未重放。

## 31. 取消下游阶段后，上游阶段仍保留过期依赖锁

**状态：已修复并更新本机，真实计划预览和回归测试通过。**

`express` 计划取消 `deployment-execution` 后，`deployment-pipeline` 仍被禁用，
并显示“deployment-execution 已选中”的错误原因。依赖锁在应用 overrides 之前
写入了基础锁集合，导致已取消的消费者仍锁定生产者。

现在按最终选择计算依赖锁；仍被选中的消费者继续保护所需生产者，
ALWAYS、已完成阶段和当前阶段的锁保持不变。依赖链可以整体取消，且不依赖
overrides 的排列顺序。

## 32. 新建 Intent 未应用已经确认的阶段选择

**状态：已修复并更新本机，真实引擎创建和失败恢复回归通过。**

向导预览确认了 8 个阶段，但创建后的状态仍为 scope 默认的 10 个阶段：
创建接口调用 `intent-create` 时没有将阶段 overrides 应用到新记录。

现在在同一管理租约内创建 Intent、通过引擎重组阶段、读取磁盘验证 overrides，
然后编译 runtime graph。创建后重组失败会明确返回 `plan_composition`，
界面提示进入已创建 Intent 审查计划，不提供无法修复阶段选择的编译重试。
实际编译失败及旧版响应仍保留原编译恢复入口。

隔离 demo 同时取消三个 Operation 阶段后，预览为 7 阶段、4 个 Gate，
新建记录及 runtime graph 均保留该选择。相关整合检查通过 287 项后端测试及
421 项前端测试。

## 33. Minimal Build and Test 被额外测试说明的误报阻断

**状态：已修复并更新本机，最终 demo 审批及工作流完成验证通过。**

阶段图的 `produces` 列出各种测试策略的产物并集，但阶段指南 Steps 3–7 明确规定：
Minimal 不生成额外测试说明，Standard 只要求集成测试说明。
Studio 原先无条件要求集成、性能和安全三份说明，导致已经通过测试的 Minimal
工作流被 `missing_required_artifact` 阻断。

现在依据状态文件中的 `Test Strategy` 判断这些说明是否必需。
构建说明、测试结果、阶段汇总及追溯材料仍为必需；未知或缺失策略保持原有检查。
修复前 Minimal、Standard 两个用例失败；修复后 207 项相关测试通过，
包含八个核心材料缺失时仍应阻断的负向用例。

本机更新后，三条误报消失，旧恢复卡自动退役。没有补造说明文件或手工修改
工作流状态；最终通过正常审批完成 demo，状态为 Completed，7/7 阶段完成。

## 34. Workflow Map 默认显示 scope 外的阶段和 Gate

**状态：已更新本机并完成页面验收；192 项后端、428 项前端测试通过。**

旧视图默认渲染完整方法图，后端也给 scope 外的非初始化阶段标记 Gate。
进度分母使用全部已知阶段，因此已完成的 7 阶段计划仍显示为 7/33。

现在默认只显示记录中选中的实际计划，包含用户增删阶段的选择，并隐藏空的阶段分组。
完整流程作为显式查看选项；未选阶段不会显示 Gate，进度始终按本次计划计算。
泳道、表格、窄屏折叠视图、单元子泳道和依赖关系采用同一个过滤结果。
阶段编号保持不变，scope 外的历史链接可以通过完整流程模式查看。

旧版无 EXECUTE/SKIP 后缀的已知阶段按当前状态中的 scope 判断，当前 scope 优先于
创建时登记的 scope；显式阶段选择保持优先。缺失状态行不会凭空进入计划。

本机 demo 默认显示 7 个阶段、4 个 Gate、7/7 进度；完整视图显示 33 个阶段，
Gate 仍为 4 个，进度仍为 7/7。表格为 7 行，依赖连线只连接可见阶段，
应用控制台无错误，原业务工作流文件未改变。

## 35. Brownfield 问卷丢失选项说明，正文中的多选指令被忽略

**状态：已更新本机并完成真实题组页面验收。**

Agentbridge demo 的 Scope Definition 第一题在正文中定义了八项能力，选项只引用
这些能力的编号。解析器只保留题目标题和选项，页面因此缺少理解编号所需的说明。
`QuestionsView` 的独立序列化字段列表也需要保留该字段，仅修改解析模型无法送达页面。
第三题在正文中明确要求多选，但解析器只检查标题，页面仍显示单选按钮。

现在为题目增加只读 `context` 字段，保留选项前的 Markdown 说明，并通过现有安全
Markdown 渲染器显示。历史记录、已回答和禁用状态仍可查看说明；旧记录缺少字段时保持兼容。
说明文字不进入答案 payload 或发送文本。正文中的明确多选指令支持加粗和同段换行；
选项、已保存答案、代码示例、引用和背景描述不会被当作多选指令。

发布检查运行通过：2,833 项后端测试、437 项前端测试，类型检查、构建及 payload 完整性检查通过。
最后的 Markdown 格式边界修复另经 299 项 reader/actions 测试复测及独立解析检查通过。
接口级回归先复现了字段丢失，再验证题组接口、Intent 详情和审批证据均包含说明；
204 项 projection/handler 测试以及最终 299 项 reader/actions 复测通过。
两次本机更新均在无执行租约时完成，更新前后本次流程的 46 个文件逐字节一致。
新生成的 Practices Discovery 题组中，8 道问题的说明全部显示，页面控制台无错误。
旧审批快照不回填新字段，保持其原始证据。验收记录见
[`2026-09-15-question-context/README.md`](verification/2026-09-15-question-context/README.md)。

## 36. 仅观察时间变化也递增卡片版本，导致提交偶发冲突

**状态：已更新本机并完成真实卡片验收；261 项相关测试通过。**

后台为待答卡片刷新 `captured_at` 时，会把新的观察时间写入证据并递增
`status_generation`。即使题目、状态和产物完全未变，这次写入也可能发生在提交校验与
最终 CAS 之间，使刚读取的卡片收到 `stale_generation`。

现在比较内容时保留该证据版本的首次观察时间。纯时间刷新不写入版本、证据或
`updated_at`；有实际变化时仍通过 CAS 保存新证据及观察时间。GET 仍返回当前读取的
观察时间，六项提交比较、稳定性检查、租约及交付期限保持原有保护。

回归用例覆盖时钟推进、提交最终 CAS 前的并发刷新、证据改变和竞争提交。
实际待答卡片在修复前仅时间变化就从版本 54 变为 55；修复后连续三次读取均为版本 61，
GET 的观察时间正常推进，随后一次提交成功。更新前后本次流程的 76 个文件逐字节一致。
验收记录见 [`2026-09-15-observation-cas/README.md`](verification/2026-09-15-observation-cas/README.md)。

## 37. 回复未被准确记账时，已结束回合持续占用租约

**状态：已更新本机并恢复当前 demo；回归和真实恢复均通过。**

发送的是 `Persist none`，代理却记为 `Nothing to add; persist none`。准确匹配不能证明
该回复已被正确记账，但回合已经结束且新 Gate 已打开。旧操作仍持续占用执行租约，
使新 Gate 被 `repo_busy` 阻断。

现在仅在回合结束、人工输入与游标有效、回执属于原问题且存在同阶段同单元的新 Gate 时，
将旧操作结束为明确的“回复未验证”，释放空闲租约。准确匹配规则保持不变；页面不会把
它标成“已回答”，也不会重新发送旧回复。

289 项相关后端测试、71 项页面测试通过，最终 9 项针对性复测通过。本机旧操作已按未验证
结果结束，执行租约归零，设计修订请求随后一次提交成功。记录见
[`2026-09-15-unverified-reply/README.md`](verification/2026-09-15-unverified-reply/README.md)。

## 38. 非标准补充问题被当作全部已答

**状态：已更新本机；代码与组合回归通过。**

Domain Design 在已回答的 `Q1`–`Q5` 后添加了 `F1`–`F3` 和空的 `[Answer]`。
旧解析器忽略这些段落，导致仍有问题待答时显示为 `Idle`。

现在单独记录 `unsupported_pending_count`，保持待答状态和问题卡片，并明确引导到
原生会话作答。不会编造 Q 编号，也不会把补充问题映射到已回答的问题。围栏示例和已知
计划、摘要检查点保持原有语义；客户端和服务端都会拒绝用缓存按钮跳过未映射问题。

补丁本身通过 725 项后端和 113 项前端测试。与未验证回复恢复修复合并后，
394 项相关后端、103 项前端测试以及类型检查、i18n 和构建通过。各次运行有重叠，不相加。
复现夹具为 `tests/fixtures/question-fallback/domain-followups.md`。
更新在原生回合空闲且执行、管理租约均为零时完成；更新前后 83 个工作流文件逐字节一致，
服务健康，随后 Domain Gate 审批一次提交成功。当前没有新的非标准待答题组，
实际页面兜底仍以回归夹具验证为准；安装记录见
[`2026-09-15-question-fallback/README.md`](verification/2026-09-15-question-fallback/README.md)。

## 39. 跳过 User Stories 后，FR 需求映射被误报为缺失

**状态：已修复并更新当前 demo；185 项相关测试及真实校验通过。**

Units Generation 在没有 `stories.md` 时会使用 FR 编号，但映射解析器只提取 US 编号，
使完整的 64 条需求映射被报为 129 项问题。现在解析器按实际选定的上游来源匹配编号，
Functional Design 的 FR 回退也按当前单元筛选。不存在的编号、缺失映射和错误单元
仍会失败，已有的 US 路径保持原有行为。

修复包含 29 个新用例，185 项针对性测试通过。本机通过同版本 payload 更新正式安装，
事务只更新一个框架文件，更新前后 91 个工作流文件逐字节一致。对当前 demo 执行
独立校验后，缺失、孤立项及无效目标均为零。历史审查中的失败记录保留原始结果。
本地补丁与上游版本的关系已写入第三方声明和 manifest 来源信息。记录见
[`2026-09-15-requirement-traceability/README.md`](verification/2026-09-15-requirement-traceability/README.md)。

## 40. 长审计日志让已完成修订的旧操作持续占用租约

**状态：已修复、更新本机并恢复当前 demo；594 项相关测试通过。**

单个审计文件超过 256 KiB 后，尾读会重新计算事件序号。旧操作保存的边界序号仍来自
完整历史，因此找不到后续拒绝、修订事件。即使恢复完整历史，流程已经从 `R` 返回 `?`
时，旧状态判断也无法结束操作。

现在优先读取完整历史，并对整个 intent 的全部分片共享 16 MiB 读取上限；
超出上限或显式要求尾读时仍标记历史不完整。这个总上限与原来的 64 × 256 KiB 相同。
状态回收另要求同阶段、同单元、同分片中有序的拒绝、修订、新 Gate 记录，修订编号匹配，
回合已结束，且人工输入与游标验证通过。它只结束旧修订操作，不批准新 Gate。

回归覆盖容量边界、错序、错误作用域、旧 Gate、修订编号及人工输入缺失等情况。
实际旧操作已结束为 `StateChanged`，租约归零，新 Gate 提交成功；
更新前后 113 个工作流文件逐字节一致。记录见
[`2026-09-15-audit-growth-recovery/README.md`](verification/2026-09-15-audit-growth-recovery/README.md)。

## 41. 已记录的计划审批被后续重置遮蔽，旧操作无法结束

**状态：已修复、更新本机并验证历史回执；当前计划仍需审批。**

Code Generation 的计划曾被批准并开始执行。后续计划和测试命令发生变化，审批标记被
清空，旧操作只能看到当前空白答案，无法识别原审批已经完成，因而一直等待直至超时。

现在用完整审计中的原提示与审批回执匹配：问题文件、提示摘要、计划指纹、指令时期、
运行起点、会话、intent 和单元必须一致；中间出现另一个同目标提示时不接受。
原回复结束为 `ResolvedNoTransition`，并明确标记当前计划仍需审批，不授予新写权限，
也不重发旧回复。

354 项相关后端测试、18 项最终复测和 76 项页面测试通过。真实旧操作已按
`plan_approval_recorded_before_reset` 结束，人工输入增量为 1；更新前后 125 个
工作流文件逐字节一致。记录见
[`2026-09-15-plan-receipt-history/README.md`](verification/2026-09-15-plan-receipt-history/README.md)。

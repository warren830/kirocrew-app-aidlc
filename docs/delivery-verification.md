# 缺陷交付验证记录 · 2026-09-10

2026-09-11 Plan 切换补充：手动加入的可选阶段可以撤销选择；必选与真实依赖约束仍保留。
当前用户任务未改动。最新全量检查为 **2720 项后端、388 项前端通过**，见 [切换验证](verification/2026-09-11-plan-toggle/README.md)。

2026-09-11 租约与失效页面补充：纯租约占用返回 repo_busy 并显示占用者入口；
已删除卡片会关闭旧确认框；汇总／计划检查点使用正确标题。实际窗口已回到当前待办。
最新全量检查为 **2717 项后端、387 项前端通过**，见 [本轮验证](verification/2026-09-11-lease-and-stale-page/README.md)。

2026-09-11 失效仓库补充：已消失目录的缓存审批会明确标为仓库不可用，提供重新关联入口，
不再提供无效的 Approve。原历史记录保持不变。最新全量检查为 **2713 项后端、384 项前端通过**。
见 [失效仓库验证](verification/2026-09-11-unavailable-repo/README.md)。

2026-09-11 最新补充：题组单选、多选、Other 和整组提交已修复并更新本机；
普通多行答案完整回显，回合中的临时工具错误不再提前触发最终失败。
**2711 项后端、382 项前端测试全部通过**，真实页面的一次提交与四题落盘均已核对。
用户 precision 原题保持未回答。详见 [本轮验证与截图记录](verification/2026-09-11-grouped-answers/README.md)。

2026-09-11 补充：安装预览重复入口及 Doctor 反馈位置已修复，仓库详情已增加新建 Intent 入口，
Action Center 已将执行中的操作移出待办列表与计数，无进展回合不再显示“状态已变更”；
Recovery 原因与历史记录显示已修复，`rmb` 的失效会话绑定和缺失运行图已恢复。
新增回归与全部 **379 项 UI 测试**、类型检查和构建通过，中英各 2247 个词条校验通过。
待办示例遗漏的经验确认问题已通过官方引擎工具恢复，尚待用户回答并进行阶段审批。
本机前端已更新，后台未重启。详见
[缺陷 14](open-defects.md#14-2026-09-11-安装预览重复显示-install-入口)、
[缺陷 15](open-defects.md#15-2026-09-11-doctor-执行后看不到反馈)、
[缺陷 16](open-defects.md#16-2026-09-11-仓库详情缺少新建-intent-的快捷入口)、
[缺陷 17](open-defects.md#17-2026-09-11-已开始执行的操作仍显示在待办列表)、
[缺陷 18](open-defects.md#18-2026-09-11-阶段交接未落盘无进展回合却显示四步完成)、
[缺陷 19](open-defects.md#19-2026-09-11-recovery-反复生成已取消的卡片仍显示为当前故障)。
下方 2680 / 362 为 2026-09-10 完整流程交付时的验证快照。

范围：`docs/open-defects.md` 原始第 1–3 节及完整截图实测新增的第 6–13 节。
全部已记录缺陷均已修复并验证；第 4 节原始记录保持不变。

**最终完整检查：2680 项后端、362 项 UI 测试通过，`scripts/check.sh` 返回 `ALL CHECKS PASSED`。**
2227 个词条 × 2 语言、293 个 payload 文件、TypeScript、UI 构建与 bundle 检查全部通过。
真实示例任务 `260910-precision-fix` 为 **Completed、0 待办、0 阻塞**：
9 个选中阶段中 7 个执行并完成，两个部署阶段因没有部署目标按条件跳过。
业务一行修复和 13 个 unittest 方法已执行，独立 CLI 与输入边界验证全部通过。

- [最终完整检查日志](verification/2026-09-10-full-e2e/full-check-final.log)
- [最终回归摘要](verification/2026-09-10-full-e2e/final-regression.json)
- [完整工作流验收](verification/2026-09-10-full-e2e/workflow-completion.json)
- [最终界面投影](verification/2026-09-10-full-e2e/workflow-final-state.json)
- [最终 gateway 健康状态](verification/2026-09-10-full-e2e/final-candidate-health.json)
- [486 个源码、配置与构建文件摘要](verification/2026-09-10-full-e2e/source-sha256-completed.json)
- [功能讲解 HTML 检查](verification/2026-09-10-full-e2e/feature-guide-qa.json)

最终源码摘要为 `f1d00b9f84105362fe6dde97dd771a6789ba42080166211573527b5101335eb2`，gateway boot 为 `8b1682121fd56a6c`。
健康检查为 healthy、issues 为空，执行与管理租约均为 0。当前示例任务无待办；
网关汇总中的其他任务操作不属于本次示例。173 张原始步骤截图及离线功能讲解 HTML
保存在本次 Codex 输出目录，截图包包括按序索引和独立验收结果。

主机精确计划选择补丁已应用于本机安装的 KiroCrew，已保留备份和回滚方案，后续主机更新
可能覆盖此本地补丁。引擎进度指纹与复核附录修复通过正常升级事务安装，18 份关键工作流、
审批和模型设置文件保持逐字节一致。没有外部部署、代码提交或推送。

## 首次交付验收（历史记录）

以下 2293 / 341 是首次修复第 1–3 节时的结果；后续章节的阶段性数字也保留用于追溯，
不替代上方最终完整检查。首次检查通过后安装至本机 gateway，健康状态正常。

- [完整检查日志](verification/2026-09-10/full-check.log)
- [真实 gateway 验收数据](verification/2026-09-10/live-acceptance.json)
- [480 个源码、配置与构建文件的 SHA-256](verification/2026-09-10/source-sha256.json)
- [基线以来的源码变更清单](verification/2026-09-10/source-changes.json)

源码与构建摘要为 `17e80dd18a0fdbdb4fff6468569764e72fbb653e0caaaed27dd6f57e7f4439e9`，
最终 gateway boot 为 `9ed49f003ab384eb`。摘要按清单中排序后的 `path + NUL + sha256 + LF` 计算，
不包含本报告等说明文件。

源码基线：`/tmp/aidlc-studio-delivery-20260910-174931`（当前工作目录没有 Git 元数据）。
初始全量检查日志：`/tmp/aidlc-studio-baseline-check.log`。

| 条目 | 验收行为 | 状态与证据 |
|---|---|---|
| 1 · brownfield 产物检查 | codekb 9 文件齐全可批准；缺文件阻塞；按当前空间与仓库定位；per-unit 与规范提升位置正确 | 通过；`test_artifact_locations.py` 与相关模块共 449 项通过；撤回实现后回归失败。原始演示仓库阻塞发现为 0 |
| 2.1 · presence 对账 | 额外真实人类回合不会永久锁死；不确定与 partial 证据仍禁止误判 | 通过；`test_reconciler_open_defects.py` 26 项通过，6 个变异被检测 |
| 2.2 · 过期命令卡 | 未派发 run / prepare_commit 的 captured 可刷新并提交；已派发卡保持原证据 | 已实施；`test_command_refresh.py` 覆盖旧页面拒绝、新页面重试、取消并发；包含在 449 项通过记录中 |
| 2.3 / 2.6 · 崩溃事务归档 | abandoned 事务自己的 staging 被收尾，failed_dir 指向自己的 transaction.json | 通过；`test_installer_open_defects.py` 65 项通过，并与卸载、回退和恢复集成检查 |
| 2.4 · 失败候选去重 | 与 payload 一致的失败候选不重复归档；异常候选保留取证字节 | 已实施；包括逐字节校验、异常候选、重复归档与符号链接；18 个变异被检测 |
| 2.5 · 投递缺席证明 | 读取失败绝不被当作“没有投递”；成功完整读取才可证明缺席 | 已实施；逐次读取后检查 `can_prove_absence()`，保留任一读取失败；含于协调器回归 |
| 3.1 · 散文检查点 | 从未解决的审计枚举决策派生问题卡，精确发送选项原文，解决后移除 | 已实施；新增 60 项回归通过，12/12 变异被检测；实际 demo 历史审计重放得到两项精确选项，答完后不再出卡 |
| 3.2 · runtime graph | 创建 intent 的 admin 租约内编译；失败可见并可重试已有 intent；创建不启动工作流 | 通过；真实 bundled engine 创建与编译，state 保持不变、无 HUMAN_TURN；修复路由 31 项、向导 30 项针对性测试通过 |
| 3.3 · 卸载 harness | 预览、确认、事务与回滚；只删除自有文件/片段，保留用户改动与工作流数据 | 通过；真实 293 文件安装后从界面卸载；保留引擎改写的可变配置；中断后恢复原事务起点，相关 102 项测试通过 |
| 3.4 · bun 设置 | 应用内指定路径、校验并重新探测，无需重启 | 已实施；配置持久化、健康/runner/预检一致；251 项后端相关测试及 4 项设置 UI 测试通过 |
| 3.5a · scope / depth | 已有 intent 可预览并调整，遵循引擎权限与执行租约 | 已实施；实际 bundled engine 验证；额外补上取得租约后复查 Host busy、目录替换与 rebind 身份校验，相关 194 项通过 |
| 3.5b · spaces | 应用内新建、切换空间，游标与租约保持一致 | 已实施；新建不切换，切换后核对磁盘；保留已完成阶段、门禁标记与工作单元隔离 |
| 3.5c · 取消安装 | 运行中事务可取消，已完成步骤回滚且结果明确 | 已实施；事务取消请求持久化，原子文件操作后回滚；原事务可继续查询，跨仓库请求被拒 |
| 3.5d · 引擎回退 | 预览可恢复版本，事务恢复旧版本并校验 receipt 与文件 | 已实施；支持紧邻的较旧 receipt，要求完整备份与明确状态兼容性；真实临时仓库端到端及中断恢复验证通过 |
| 3.5e · 仓库归档 | 可逆隐藏仓库，保留注册、绑定与历史 | 已实施；归档与活动事务互斥，可显示并恢复；包含在 53 项后端与 26 项仓库 UI 测试中 |
| 3.5f · 重命名仓库 | 修改展示标签，保持仓库身份与绑定 | 已实施；严格字段校验与活动记录，身份和目录字节保持；完整点击测试通过 |
| 3.5g · 清理历史目录 | 应用内预览和清理已终结的备份/证据，保护活跃事务和恢复所需内容 | 已实施；53 项后端测试及界面确认测试通过；保护当前回退备份，允许明确删除更早历史点，保留数据库记录和顶层诊断 JSON |
| 3.5h · 目录选择器 | 添加仓库可浏览并选择 gateway 上的目录 | 已实施；owner-only、单层有界读取、不会注册目录；选择后仍须预检；后端与 UI 测试通过 |
| 4 · 已修行为回归 | 既有回归保持通过 | 最终完整回归通过；后端 2293 项、UI 341 项全部通过 |
| 总体验收 | 生成资源、完整检查、gateway 更新与 health、主要界面和真实端到端流程 | 完整检查通过；真实 gateway 流程通过；最终构建已重新加载且健康 |

关键缺陷使用了可恢复的变异验证；新增集成边界先重现测试失败，再修复并检查成功与失败恢复路径。
完整检查覆盖所有源码，包含后端测试、gateway 自带 Python 的模块导入、生成文件一致性、双语词条、
TypeScript、UI 测试、构建与 bundle 检查、293 个随附引擎文件的摘要校验。
后端日志保留 1385 条测试夹具键类型与进程崩溃测试 `fork()` 的警告；UI 错误边界用例会刻意输出
`boom from activity view`。这些用例全部通过，最终浏览器检查没有 error 日志。

变异证据：[协调器](verification/2026-09-10/reconciler-mutations.json)、
[安装器](verification/2026-09-10/installer-mutations.json)、
[审计问题](verification/2026-09-10/audit-question-mutations.json)。
真实审计的 [历史重放](verification/2026-09-10/audit-question-replay.json) 与
可变配置卸载的 [预期失败](verification/2026-09-10/mutable-uninstall-expected-red.log)、
[修复后验证](verification/2026-09-10/mutable-uninstall-green.log) 一并保存。

## 真实 gateway 验收

环境为本机 macOS、KiroCrew `0.6.0-insider.6`、Bun `1.3.11`、随附引擎 `2.7.1`。应用通过
`scripts/dev-install.sh --no-build` 更新并重新启用。后台没有执行或管理租约时才重新加载。

新建独立目录 `/tmp/aidlc-studio-acceptance-20260910-bs8qjrqc`，登记为 `r_a9a1b50d4618`，
完成以下实际操作：

1. 界面目录浏览、预检、注册、293 文件安装预览和事务提交。
2. 向导创建 `260910-delivery-proof`，runtime graph 可解析，intent 保持暂停，没有 HUMAN_TURN。
3. 界面将 scope 从 bugfix 调整为 feature、depth 调整为 Standard、test strategy 调整为 Comprehensive，
   预览后应用并核对磁盘状态。
4. 界面创建 `acceptance-team` 空间，验证创建不切换；另行确认切换并核对游标。调用修复路由在已有
   intent 重新编译，state 字节不变。
5. 配置、验证、重新探测 Bun，并恢复原自动发现设置；重命名、归档、恢复仓库，目录摘要与身份不变。
6. 界面卸载提交 `tx_40ea72e0b126c003`：移除 277 个自有文件和 6 处片段，保留改写后的
   `.kiro/agents/aidlc.json`。23 个工作流文件和其他用户文件的 SHA-256 均保持；AGENTS 原指令保留，
   留下一个分隔空行。
7. 界面选择、预览并删除本次测试的两份终结事务备份，共 5,678,085 字节。重新预览无剩余目录，
   安装与卸载事务仍可查询为 committed，工作流文件不变。
8. 归档验收注册，活动列表恢复原有五个仓库。保留临时目录与事务历史供复核。

原始演示仓库 `/tmp/aidlc-demo-full` 的 `260909-precision` 仅做读取检查：阻塞发现为 0，没有
`missing_required_artifact`，仍有一条信息级 `directive_state_digest_mismatch`。没有发送或批准其工作流回合。
历史审计重放能提取未回答的两个精确选项，回答后的重放不再生成问题卡。

## 检查范围与边界

- 事务取消、崩溃恢复、回退和恢复备份损坏等路径在真实临时文件系统、SQLite、实际 engine 与故障注入
  测试中覆盖；没有为测试故意中断用户的 gateway 进程或正在执行的工作。
- 仅有聊天散文、没有完整审计证据时不派生决策；证据歧义仍需要核实。
- 卸载保留归属不明的旧片段与变化后的可变配置；不可变文件被修改仍会阻止确认。
- 引擎版本回退限紧邻的较旧安装凭据，要求备份和状态兼容性证据完整。清理更早备份会丢失对应恢复点。
- 本次实机验证平台为 macOS，没有声称完成其他操作系统实机验证或公开发布。
- 测试通过表示上述范围内未发现未解决缺陷，不能证明任意环境和输入下绝对零缺陷。

阶段性日志：`/tmp/aidlc-main-defects-check.log`、`/tmp/aidlc-regression-mutation.log`、
`/tmp/aidlc-runtime-focused.log`、`/tmp/aidlc-bun-after.log`、
`/tmp/aidlc-bun-ui-final.log`、`/tmp/aidlc-repo-management-verified.log`、
`/tmp/aidlc-repo-ui-final.log`。这些是中间证据，不代替最终验收结论。

## 后续实测：创建向导手动选项被异步预设覆盖（已修）

在真实浏览器中快速选择 `bugfix` 和 `Standard` 测试策略，界面一度显示 Standard，但创建请求中的
`test_strategy` 最终为 null，磁盘状态因此采用预设的 Minimal。根因是较晚返回的预设元数据无条件
覆盖了用户已经选择的字段。

`WizardView.tsx` 现在分别跟踪 depth、test strategy 和 review cap 是否经过手动选择，
显式选择“沿用预设”也算用户选择。预览只为尚未选择的字段应用默认值；预览结果必须匹配当前仓库
和全部输入才允许创建。请求换代、失败、切换仓库和卸载组件后，旧结果不能继续授权创建。

- 新增 7 项向导回归；修复前 6 项失败、1 项通过，修复后该文件 37 项全部通过。
- 真实 bundled engine 创建测试覆盖 Minimal、Standard 和 Comprehensive，并核对磁盘保存值；
  相关测试共 7 项通过。
- 更新候选版本后，真实浏览器快速选择 Standard、等待预览完成并往返页面，选项仍然保留。
- 独立复核未发现需要修改的问题。完整 `scripts/check.sh` 返回 `ALL CHECKS PASSED`：
  后端 2295 项（683.42 秒）、UI 348 项、2227 个词条 × 2 语言、payload 293 文件通过。

证据：[完整检查](verification/2026-09-10-full-e2e/full-check.log)、
[修复前回归](verification/2026-09-10-full-e2e/wizard-red.log)、
[修复后回归](verification/2026-09-10-full-e2e/wizard-green.log)、
[真实创建配置测试](verification/2026-09-10-full-e2e/creation-config.log)、
[源码摘要](verification/2026-09-10-full-e2e/source-sha256.json)、
[本次变更文件](verification/2026-09-10-full-e2e/source-changes-since-initial-verification.json)。

本轮 480 个源码、配置与构建文件的摘要为
`ef23eb4401a5f0faa95020f4bee443130bf81791988f581043604fc05f1585b6`，
实际加载的 gateway boot 为 `7e1ae585eb24fbc7`。

## 后续实测：问题解析与已答题单的审计切换（已修）

真实需求分析生成了 `### Q1` 形式的题目，旧读取器只识别二级标题，导致三道题从界面消失。
修复支持二级和三级题目标题，并忽略代码围栏内的标题、选项和答案。存在未答结构化题目时，
题单始终优先于审计中合并成散文的一组选择，避免把三道题错误地显示成一个巨大选项。

三题和摘要全部确认后，又出现新 Learnings 枚举问题。读取器现在要求成功的当前阶段确认回执
绑定同一题单，且时间严格早于新决策，才允许已关闭题单让出给新审计问题。明确区分整文件摘要
和引擎的 `confirmed-content-v1` 摘要；回执身份也计入提交时的证据。缺回执、未知摘要范围、
跨分片时间歧义或无法验证被排除内容时仍保留文件优先。

这两轮补充共增加 93 项后端回归。最后一轮的 40 个新场景在修复前有 10 项失败；
相关 189 项针对性测试通过，三项内存变异分别被 2、1、1 个测试拦截。真实历史重放在
需求摘要确认后显示 `Nothing to add` / `Add a note`，实际回答后不再显示该问题。

- [阶段性完整检查：2388 / 348](verification/2026-09-10-full-e2e/full-check-2388.log)
- [关闭题单修复前测试](verification/2026-09-10-full-e2e/closed-sheet-red.log)
- [关闭题单修复后测试](verification/2026-09-10-full-e2e/closed-sheet-green.log)
- [真实历史重放](verification/2026-09-10-full-e2e/closed-sheet-replay.json)
- [验证方法与保守边界](verification/2026-09-10-full-e2e/closed-sheet-handoff.md)
- [该轮 Studio 源码摘要](verification/2026-09-10-full-e2e/source-sha256-final-studio.json)
- [向导修复后新增的源码变更](verification/2026-09-10-full-e2e/source-changes-after-wizard.json)

完整检查后端耗时 769.25 秒，UI 25 个测试文件、348 项用例通过，词条和 payload 数量保持
2227 × 2 与 293。480 个源码、配置和构建文件的合成摘要为
`126e2c7c316b7ec30b8448f04ce7dd0f458605a2249cb02c88f57890cdaf68e0`。
已加载的 Studio boot 为 `7907bbd129b0feac`，健康检查为 healthy、issues 为空。

多题文件没有匹配的实时主机载荷时，成组表单仍保持禁用；这次通过 Studio 内嵌会话回答三题。
此边界已记录在手工验收流程中，没有把会话回答描述成已验证的成组表单提交。

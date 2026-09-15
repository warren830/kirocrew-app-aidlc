# 重启后的隔离 Demo 验证

**结果：demo 已通过正常审批完成，状态 Completed，7/7 阶段完成，5/5 测试通过。**

## 验证范围

本轮在独立临时仓库中验证 Studio 安装、计划创建、会话恢复、检查点回答和
最小代码修复流程。示例 `clamp(value, low, high)` 原先只限制上界，忽略下界。
三个既有单元测试中，下界测试失败，其余两个通过。

原业务仓库及其未完成答案不参与 demo。

## 已确认的结果

| 检查 | 结果 |
|---|---|
| 网关重启 | 旧进程退出，新进程启动；Studio boot ID 改变，状态 healthy，issues 为空 |
| 安装 | 通过界面预检、注册和安装；293 个托管路径的事务提交成功，引擎为 2.7.1 |
| 初始失败基线 | 3 个测试中 1 个失败：`clamp(-3, 0, 10)` 返回 -3 |
| 首次输入 | 只读状态请求结束，记录 1 次 `HUMAN_TURN` |
| 恢复预热 | 关闭并恢复会话，界面聚焦触发实际预热；`agent=aidlc`，`resumed=True` |
| 恢复身份 | 原生会话 ID 与关闭前一致 |
| 输入计数 | 预热本身不增加计数；恢复后的界面输入使计数从 1 增加至 2 |
| 检查点回答 | 界面发送 `Full rescan` 后，引擎写入 `QUESTION_ANSWERED`，操作进入 Processing |
| 执行展示 | 已发送的操作从待办队列移除，详情保留执行进度 |
| 原故障场景 | 在学习笔记检查点关闭、恢复同一原生会话，再从 Studio 发送 `Nothing to add`；恰好新增一次输入记录，答案正式入账，操作解决为 `ResolvedNoTransition / question_answered` |
| 整组问答 | 三个需求答案作为一条消息提交，全部被记录，随后出现摘要确认 |
| 驳回与修订 | 需求驳回后修订次数增加到 1，修订产物和新审批卡生成，再审批后继续 |
| 计划审批交接 | `Approve Plan` 被接受；代码完成并出现新学习问题后，原计划审批正常解决为 `ResolvedNoTransition / plan_approved` |
| 代码修复 | 一行下界修复、两个新增边界测试；原有三个测试的 AST 保持不变 |
| 独立验证 | 五个测试通过；另以五组固定输入验证结果与函数签名 |
| 工作流完成 | 正常批准最后一个 Gate 后，状态 Completed，7/7，demo 待办为 0 |
| 健康及租约 | Studio healthy，issues 为空，执行及管理租约均为 0 |
| 原业务数据 | 原业务工作流 50 个文件逐字节不变，原答案未重放 |

## Demo 中发现并修复的问题

1. 取消下游阶段后仍存在过期依赖锁。
2. 创建接口没有应用向导确认的阶段 overrides。
3. 阶段重组失败与编译失败共用恢复界面，可能通过编译错误计划而误报成功。
4. Minimal Build and Test 被三份该策略不要求的测试说明误报阻断。

修复后的真实预览和创建结果均为 **7 阶段、4 个 Gate**，三个 Operation 阶段均未选中。
创建使用了预览的 `confirm_plan_digest`，实际引擎完成重组后才编译 runtime graph。
代码和失败路径的整合验证通过 **287 项后端相关测试、421 项前端测试**。
测试策略产物检查修复另通过 **207 项相关测试**；核心材料缺失的阻断行为仍受回归保护。
本机更新后误报和恢复卡退出，再通过正常的最终审批完成工作流。

第一次工作流尝试出现 Python 路径检查提示后被主动停止，仍有扫描产物随后写入。
该尝试已归档保留；后续使用 `/usr/bin/python3`，并选择完整重扫以重新验证当前文件指纹。
没有修改权限设置、重放原业务答案或手工写入人工回合标记。

## 证据

完整 API 回执、原始日志及测试输出保存在本机
`aidlc-studio-backups/restart-demo-20260915-100452`。
可独立运行的示例代码和测试已复制到该目录的 `demo-output/`。

关键证据文件：

- `demo-tests-before.log`：修复前 3 个测试中 1 个失败。
- `demo-tests-final.log`、`independent-reference-checks.json`：修复后的测试和独立输入验证。
- `demo-application.diff`：应用代码仅涉及 `clamp.py` 和 `test_clamp.py`。
- `protected-resume-verification.json`：同一会话恢复后的输入及受保护答案回执。
- `plan-approval-settled.json`：旧计划审批已正常结束。
- `final-verification.json`、`final-aidlc-state.md`：最终状态、健康、租约及原业务数据核验。

完成后仍保留一条 `info` 级 `directive_state_digest_mismatch` 提示，
表示旧活动指令的摘要没有随最终状态写回；没有阻塞发现，本轮未修改引擎的指令写回逻辑。

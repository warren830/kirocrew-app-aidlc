# Recovery 渲染与计划审批交接验证

Action Center 的 `.join` 崩溃已修复，旧计划审批记录已正常结束。
修复完成后发现的新人工回合登记问题单独记录在 open-defects 第 30 项，尚未解决。

## 修复内容

- `Projection.evidence()` 输出完整 Directive，保留版本、类型、units 和状态摘要。
- `DirectiveEvidence` 明确允许旧投递快照缺少扩展字段。
- Recovery 页面兼容缺少 units 的历史记录；无法读取数组时不会误宣称无冲突。
- 问答快照内部保留已验证的关闭文件与审批回执，不将这些内部证明混入当前问题的 API 内容。
- 新 Learnings 出现后，旧计划根据原文件、作用范围和审批回执收尾；新问题不被自动回答。

## 验证结果

| 检查 | 结果 |
|---|---|
| 历史 directive 无 units 的真实形状 | 修复前在 directiveUnits 复现 `.join` 异常；修复后渲染通过 |
| 恢复模板与其他决策模板 | 定向前端 55 项通过 |
| 完整检查 | 2762 项后端、399 项前端通过；类型、构建、双语目录与载荷检查通过 |
| 审批关联保护 | 6 项通过；过早回执、不同文件、不同 unit、错误原始问题摘要均不能误结算 |
| 执行中 | 即使审批回执已写入，仍保持执行记录与租约 |
| 执行结束后 | Processing 和 ReconciliationRequired 两种状态均可正常收尾，保留 Learnings |

## 本机结果

旧记录 `a_015dc83090603d81` 通过正常 reconcile 接口变为
ResolvedNoTransition / plan_approved，原 delivery_id 保留。
重新扫描后，只剩 `a_5004b8205312eb4a` 的 Learnings 问题，findings 为空；
正常页面显示 Nothing to add 和 Add a note 两个未选择的选项。

更新核验时会话消息未改变，50 个工作流文件校验值保持一致。
本次修复没有手工修改 Studio 数据库、审批答案或人工标记。
原生窗口刷新短暂空白后通过连接窗口恢复，恢复过程中自动产生的空会话已关闭。

随后 15:28 收到新的 Nothing to add。该消息已送达，但未产生 HUMAN_TURN，
引擎拒绝记录答案；这一新问题不是本次渲染修复已经解决的内容。

原始日志、数据库备份和更新前后记录：
`/Users/ychchen/warren_ws/aidlc-studio-backups/recovery-render-20260913-152157`。

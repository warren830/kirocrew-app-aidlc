# 不可用仓库的缓存审批 · 2026-09-11

用户截图中的卡片是 `ledger-export (scratch) / 260904-ledger-export`，操作 ID 为
`a_fdffb62ecbe8934a`。仓库登记 ID 为 `r_64f5e18ef18f`，原目录
`/private/tmp/aidlc-studio-e2e` 已不存在，登记状态为 unavailable / path_missing，
也没有指向该目录的会话。

旧卡片接口没有传递仓库可用性。页面把缓存审批当作当前可操作决定，提供 Approve，
进入确认后才显示未绑定会话，给出了错误的处理方向。

## 修复与实测

- ActionCard 新增仓库可用性、具体原因及归档标记；不可用仓库不提供人类派发决策。
- 提交接口在快照、租约和派发之前明确拒绝不可用仓库。
- 列表和详情显示仓库不可用、已保存记录及最近记录时间；详情提供管理仓库入口。
- 已归档仓库的记录不进入前端待办及计数，原始记录仍用于历史与投递跟踪。
- 真机浏览器确认失效卡片不再出现 Approve，并可进入该仓库的重新关联路径页面。
- 原 status、status_generation、captured、delivery 均与修复前一致，没有提交任何审批或创建会话。

原目录尚未恢复。保留仓库登记和历史记录，是否归档或重新关联到新路径待用户决定。
这次修复处理页面与接口对失效记录的判断，不把缺失的项目伪造为可用工作区。

截图与浏览器脚本保存在本轮 Codex 输出目录的 `unavailable-repo` 下。
**最终检查通过：2713 项后端、384 项前端测试，2257 个中英文词条、构建与 payload 完整性均通过。**
见 [完整日志](full-check.log)、[安装文件摘要](installed-source-sha256.json) 和 [浏览器验证](after-verification.json)。

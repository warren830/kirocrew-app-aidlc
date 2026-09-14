# 手工端到端验收手册

发版前由人亲手走一遍的流程。**主线全程点击完成，不需要终端。**每一步都写明「看到什么算通过」。

命令行被收进两个附录：附录 A 是取证用的，只有当界面读不明白、或者你要证明界面显示的和后端返回的一致时才用；
附录 B 说明新增管理功能的验收判据。

原主线在 2026-09-08 至 09 的真实 gateway 上跑过。2026-09-10 新增功能的实际验证范围与日志见
`docs/delivery-verification.md`；下列流程是复验步骤，不等同于每个平台都已手工验收。
`docs/publishing.md` 第 5 节的手工清单对应的就是这里的判据。

## 0. 只有改过 Studio 自己代码的人需要

```bash
python3 scripts/build_i18n.py && python3 scripts/gen_ui_sources.py
PATH=/Users/ychchen/.nvm/versions/node/v24.20.0/bin:$PATH bash scripts/check.sh
./scripts/dev-install.sh     # disable/update/enable 重新载入此 app
```

通过判据是最后一行 `ALL CHECKS PASSED`；本次准确计数见交付验证记录。
payload 仍为 293 文件 / 2.7.1。这是构建与测试，跟使用 Studio 无关。

## 1. 环境自检：设置 → 关于

不用调接口，这一页把该看的都渲染出来了：

- **状态**行：`healthy` 绿标；不健康时 `issues` 直接拼在描述里，并且每一页顶部都会挂一条降级横幅带
  「打开设置」。
- **Payload**：健康时显示「293 文件已校验」，损坏时显示不匹配的条数。
- **工具**：`bun` 与 `git` 各一枚 chip，带版本号；找不到时是警告色的「未找到」。
- **存储**：schema 版本加完整性。**协调器**：是否在跑、上次巡检时间。**版本**：Studio 版本、随附引擎版本、
  最低 KiroCrew 版本、主机版本。

通过判据：状态 `healthy`、`issues` 为空、payload 校验通过、bun 与 git 两枚 chip 都带版本号。

同一时刻只应有一个 gateway 在跑；两个进程共用一个数据目录时你分不清在看哪个实例，读数不可信。

## 2. 添加仓库

Repos → 添加仓库 → 浏览并选择 gateway 上的目录，或填写绝对路径。
选择目录不会注册仓库，仍须预检和确认；浏览列表只读取当前一层目录。

预检报告里会显示 bun 的版本和路径，找不到时会列出它搜过的每一个位置。

通过判据：列表出现该行，可用性 `available`，安装状态「未安装」。

注意区分：已经带了别家 harness（例如 `.claude`）的仓库显示「已安装」，但它**没有属于 Studio 的
harness**，这就是 `own_engine_version` 这个字段存在的原因；这种仓库依然可以装 Kiro harness。

## 3. 安装 harness

仓库详情 → Install。**先把预览读完再确认。**

预览的通过判据三条：条目总数等于 payload 的文件数；`blockers` 为空；没有条目落在别家 harness 目录里；
`aidlc/` 在全新仓库上是 `shell_create`，在已有工作区上是 `shell_exists`（已存在的工作区绝不允许被写）。

确认后事务抽屉会实时显示每一步的标签和状态（成功、失败、进行中），失败时原文显示 `error`，并给出
`failed_dir` 路径和恢复横幅。这就是安装的全部过程，不需要另外看日志。

安装完成后的通过判据：事务 `committed`；仓库卡片显示自有引擎版本等于随附版本、drift 为 0、有 receipt；
再点仓库卡上的 **Run AI-DLC doctor**，看到成功提示，说明引擎真的能在这个仓库里跑起来。

失败必须整体回滚：已完成步骤倒序撤销、`rollback_error: null`、保留原 receipt。回滚会留下若干空目录，
那是残留垃圾，不构成安装痕迹。

## 4. 绑定规范会话

Studio 的每个决策都以真实用户回合注入到与该 intent 绑定的 KiroCrew 对话。没有绑定，决策会被拒
`session_unbound`，确认框会禁用发送并在按钮旁写明原因。

Intents → 该行的 `Bind a conversation` → 新建并绑定（也可以接管已有对话，列表里只会出现 agent 为
`aidlc` 且 project 与仓库一致的）。

通过判据：面板显示出 slot 与 session key。

绑定记录存在不等于目标会话仍存在。若 Recovery 说明找不到绑定的会话，需在 Intents 的会话管理中
解除失效绑定，再创建或选择属于当前仓库的 `aidlc` 会话。确认 Recovery 通知只关闭该记录；
原因仍存在时，下次扫描会再次提示。Cancelled 详情应明确显示为历史记录，并可跳转当前待办。
恢复绑定应保留暂停设置；需要继续时，由用户选择 Allow dispatch again。

## 5. 创建 intent

仓库详情 → Intents 区域 → **New intent**，会打开四步向导并自动选中当前仓库。
空任务列表和已有任务列表均提供该入口；也可使用页面顶部的全局新建按钮。
从仓库详情进入时，不应携带上一条任务的 action、intent、stage 或 draft 选中状态。

通过判据：计划预览里的阶段数、门数、产物数就是引擎算出来的真实数字（2.7.1 的 `bugfix` 是 9 阶段 /
6 门 / 30 产物，内置 scope 共 11 个）；创建成功后 intent 停在 `Idle`，**此时什么都没运行，也没向对话
发出任何 prompt**；对应 intent 目录已生成可解析的 `runtime-graph.json`。编译失败时应明确报告已创建
的 intent，并能直接重试该 intent 的编译或打开已有 intent，不能重复创建或把失败伪装成完整创建成功。

## 6. 跑到下一个检查点

intent 的 Run，在确认框里核对将要发送的原文，然后发送。

通过判据：卡片在几秒内依次走过 `Queued` `Delivering` `Delivered` `Processing`，严重度始终 `info`。

服务端确认操作进入 `Delivering`、`Delivered` 或 `Processing` 后，左侧待办列表和角标应扣除该条目，
右侧仍保留选中操作的执行进度。发生投递不确定、未送达或失败时，条目应重新出现；
新产生的审批或问题也应正常入列。此筛选不删除后台记录，原始 `/actions` 响应仍可能包含执行中的操作。

如果它停在 `Delivering` 直到升级成 `ReconciliationRequired`，那不是缺陷：投递确认没被接受时就会这样，
等磁盘上能证明效果之后它会自己收敛到 `StateChanged`。

## 7. 回答检查点

门禁点 Approve → 确认。通过判据四条同时成立：

- 审计新增 `Gate Approved`（该阶段随之完成时还会有 `Stage Completion` 与 `Stage Start`）；
- 阶段行由 `[?]` 变 `[x]`，状态文件的 sha 随之改变；
- `.aidlc-human-turn` 时间戳前移，证明受保护的人类回合是主机 hook 铸的，不是 Studio 写的；
- 卡片解决为 `StateChanged` 并离队。

驳回并修订一次，应看到 `[?]` 变 `[R]` 且门重新打开。

一次未记录工作流状态变化的回合可能解决为 `ResolvedNoTransition`、`info`。
它表示本轮已响应，不表示阶段完成；最后一步应显示“状态未变更”，不能四步全部打勾。
如果会话声称正在等待审批，但状态仍为 `[-]`、界面没有待办，应核对 Learnings 的
`DECISION_RECORDED` 与正式门的 `STAGE_AWAITING_APPROVAL`，修复遗漏的阶段交接。
重复点击 Run 只会继续发送 `/aidlc`，不能代替回答问题或批准阶段。

枚举检查点还应覆盖审计来源：当引擎已经记录带选项的未解决 `DECISION_RECORDED`，即使主机没有阻塞式
question 卡，Studio 也应显示选项和审计来源，确认框发送选项原文。解决事件出现后卡片消失；缺少可靠审计、
审计读取不完整或阶段/工作单元已换代时，不得继续提供旧决策。

## 8. 复位

解除对话绑定 → 仓库的 **Unregister**。注意它的语义就是字面意思：Studio 忘掉这条注册、会话绑定和操作
历史，**目录里一个字节都不动**。要撤掉 harness，先使用仓库的「卸载 harness」，核对删除与保留清单后
确认。只移除 receipt 证明属于 Studio 的文件与片段；工作流数据和用户内容保留。

## 9. 已知限制，不要当缺陷排查

- **只有聊天文本、没有可核验审计的检查点**仍需在对话中回答。带完整、未解决枚举审计的散文检查点现在
  会显示；主机 options 只作旁证，不替代磁盘证据。
- **多题文件可读，但没有匹配的主机实时问题载荷**时，界面完整展示题目与各自选项，成组表单仍保持禁用。
  使用同页的「打开会话」回答；引擎写回答案后，Studio 显示已回答状态，并继续提供汇总确认和 Gate。
  不要把“已识别三个问题”当成“已启用成组表单提交”。2026-09-10 实测通过内嵌会话回答三个问题，
  再从汇总确认按钮发送 `Looks correct`，全程不需要编辑问题文件或使用终端。
- **旧 receipt 的片段归属不完整**时，卸载保留无法证明由 Studio 引入的内容，并在预览中说明。
- **安装后变化的可变配置**（如引擎改写的 agent JSON）在卸载时保留，预览列明原因。不可变引擎文件
  被修改、路径变为符号链接或内容无法安全读取时，仍阻止卸载。AGENTS 原有指令保留，分隔空行可能留下。
- **引擎回退只支持紧邻的较旧 receipt**，且要求备份完整、工作流状态版本兼容性有明确记录。不会退回
  工作流数据；备份损坏或旧凭据缺少兼容性信息时会阻止确认。
- **`resume` 只适用于 parked 的 intent**，阶段中间空转的要再点一次 Run。
- **仓库身份是 `st_dev:st_ino`**，macOS 重挂载卷会改设备号，重启后已注册仓库读成
  `moved / identity_changed`；点 Rebind 即可，不碰任何文件。

## 附录 A：取证用命令行（日常不需要）

只在你要验证「界面说的是不是真的」时用。`kc` 指 `scripts/kcapi.sh <METHOD> <PATH> [JSON]`。

- 健康原始数据（含界面不渲染的 `payload.ok`、`tools.bun.path`、`tools.bun.source`、`searched`）：
  `kc GET /api/apps/aidlc-studio/health`
- 引擎确实能在仓库里跑：`cd <repo> && bun .kiro/tools/aidlc-utility.ts version`（界面的 Doctor 按钮
  已覆盖此意图）
- 人类通道三段式，用于脚本化验收：

```
POST /actions/<id>/submit    {"captured": {...}, "payload": {"decision":"run"}, "client_wire_text": "/aidlc"}
POST /api/chat?ws=1          <严格照抄 submit 响应里的 host.body>
POST /actions/<id>/delivery  {"delivery_id": "...", "outcome": "delivered", "receipt": <主机原样回执>}
```

两个必踩的坑：`client_wire_text` 必须是真正上线的文本而不是按钮文案（`run` 发的是 `/aidlc`），不一致被拒
`invalid_decision` 且响应的 `expected` 告诉你正确值；`receipt` 要**平铺**主机自己的回执
`{"ok": true, "slot": ..., "mid": ...}`，外面包一层被拒 `delivery_ack_invalid`，随后 90 秒确认期到点，
卡片升级成 `ReconciliationRequired`。调试脚本永远打印完整响应体，只打印 `status` 会把一条清楚的拒绝
读成「接受了但没反应」。

## 附录 B：新增管理功能

1. **Bun**：Settings 中填写可执行文件绝对路径并保存；健康信息、后续引擎调用和仓库预检应使用同一路径。
   无效路径保留错误与输入，不能悄悄回退；重新探测和恢复自动发现不需要重启。
2. **Scope / 深度**：Intents 中打开设置，调整并预览，核对阶段选取差异后确认。已完成阶段和产物保留，
   过期预览被拒绝；存在不兼容门禁或执行租约时禁止修改。
3. **Space**：仓库详情展开空间管理，新建空间后原活动空间不变；另行确认切换后活动空间与磁盘一致。
4. **取消安装**：事务抽屉请求取消后仍显示事务；等当前原子文件操作结束后回滚，结果必须有明确的终态。
5. **版本回退与恢复**：升级后打开回退预览，核对旧版本、文件和兼容性；成功后凭据与引擎版本一致，
   工作流数据不变。中断的卸载或版本回退应从恢复入口预览并恢复其原事务起点，不能误装随附版本。
6. **仓库标签与归档**：重命名只改展示；归档后可勾选显示已归档仓库并恢复，repo_id、绑定和目录字节不变。
7. **清理事务目录**：勾选、预览、确认三步完成。活跃事务、恢复证据和当前回退备份不可选；旧备份可以
   删除，删除会丢失对应历史恢复点。事务与凭据历史保留。部分失败要明确显示剩余项，必须重新预览，
   不能自动重试。此操作只在隔离的测试目录上做破坏性验收。

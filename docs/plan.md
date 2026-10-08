# AI-DLC Studio v2：精简后端计划

> 起点：v1.1.6（`a47d179`），从 `~/warren_ws/kirocrew-app-aidlc` 克隆，工作分支 `v2`。
> v1 仓库不动；remote `v1` 只用来 fetch（push 已禁用）。v1 之后的提交在每个阶段开始前用 `git fetch v1` 移植进来。

## 目标

保留后端，用户体验不变，代码量减六成：后端 40.6k 行（其中代码 28.6k，其余是注释和空行）→ 约 16.7k；
测试 45k → 约 20k。

做法是**逐模块瘦身，不重写**。每一步都先录下 v1 的接口输出（卡片、流程图、intent 详情、计划预览、安装预览），
瘦身后逐字段对比，并且 `scripts/check.sh` 全绿，才进入下一步。

## 已定的决策

| 决策 | 结论 |
|---|---|
| 安装器 | 保留 Studio 自己的安装器并精简（不交给 AI-DLC 2.10 自带安装器：那样有工作流在跑时不能升级、安装中不能取消，还要迁移已装仓库） |
| 最低 KiroCrew 版本 | 0.8.0，删除 0.3.0/0.5.0 兼容代码 |
| 开发期安装 | App 名（slug）在运行时取自 `app.json`，开发期以 `aidlc-studio-next` 与 v1 并排安装；写操作只在测试仓库里试 |
| 发布 | 作为同一 GitHub 仓库的 v2.0.0，App 名改回 `aidlc-studio`；推送前再确认 |
| v1 维护 | v2 发布前 v1 继续在老仓库修 bug，改动移植到 v2 |

## 各部分目标

| 部分 | 现在 | 目标 | 做法 |
|---|---|---|---|
| 决策投递 + 租约（actions、leases） | 4.3k | 1.2k | 12 种状态并成 8 种（UI 本来就合并显示）；"在途"由 actions 状态推出，加每仓库系统文件锁，去掉心跳、回收证明、独立租约表 |
| 卡片推导 + 后台循环（projection、reconciler） | 5.4k | 1.75k | 一个循环；按文件状态只重扫有变化的 intent；按规则表推导卡片；流程图拼装移到前端（约 250 行 TS） |
| Advisor + 计划（advisor、plan、plan_advice、estimates） | 5.6k | 1.6k | 三种起草合并成一条路径；删掉校准、只被测试调用的 scope/config change、plan_advice 问卷 |
| 会话、存储、仓库、引擎、服务 | 6.8k | 2.7k | 只支持 KiroCrew 0.8+；用宿主公开的 slot 字段；17 张表并成约 10 张；引擎只留一份白名单 |
| 路由 + 其余（handlers、events、activity、notifications、settings、git_observer、security、constants、errors） | 8.2k | 3.3k | 83 个路由并成约 36 个；事件流改为内存环形缓冲；设置合成一份 JSON；通知一个函数 |
| AI-DLC 文件解析（aidlc_reader、consistency） | 4.6k | 3.35k | 只能整理：通用序列化、长注释挪进文档（交给 AI-DLC 命令不可行：缺数据，每次一个 bun 进程太慢） |
| 安装器（installer、maintenance） | 5.5k | 2.8k | 保留全部可见功能，去掉重复校验和旧回执兼容 |

## 顺手修的 v1 问题

1. 新关卡、新问题不发通知（只有熔断和工具审批会发）；完成通知、摘要通知从未调用。
2. 前端发送前的会话忙检查读 `in_stage_execution`、`deliveries_inflight`，宿主不提供这两个字段（宿主叫 `orchestrating`），这部分检查从未生效。
3. 设置页的 5 个夜间窗口设置和 `diagnostics.retention_days`，后端从未读取。
4. 死代码：14 个前端从不调用的路由；只有测试调用的引擎动词和动作。（原来还列了"修订"卡，P3 核实后发现它在 intent 详情、`/actions?include=revision`、健康页计数和归档检查里都可见，保留。）

## 体验上仅有的两处变化

- 你在聊天里打字的同时发决定，不再标记为异常。
- 租约排查细节（孤儿列表、回收原因）合并进健康页，信息变少。

## 阶段

| 阶段 | 内容 | 完成标准 |
|---|---|---|
| P0 | 基线：跑通全部检查、记录行数；App 名运行时取自 `app.json`；录 v1 金样（golden）输出；删除确认无生产调用方的死代码 | 检查全绿，金样对比一致 |
| P1 | 存储与宿主接入层（storage、sessions→host、bindings、services） | 同上 + 迁移测试 |
| P2 | 决策投递与租约；修前端发送前检查 | 同上 + 投递状态机测试 |
| P3 | 卡片推导与后台循环；补关卡/问题通知 | 同上 |
| P4 | Advisor 与计划 | 同上 |
| P5 | 路由合并、事件流、设置、通知、杂项 | 同上 + UI 适配 |
| P6 | 安装器 | 同上 + 故障注入测试 |
| P7 | AI-DLC 文件解析整理 | 同上 |
| P8 | 全量检查、gateway 端到端、与 v1 在同一批仓库上对比、发布准备 | 端到端通过 |

## 进度

### P0（2026-10-08）
- 基线：v1.1.6 在新目录全部 11 道检查通过（后端 2899 个测试、UI 466 个）。
- 测试改在 KiroCrew 0.8 运行时上跑：`scripts/test-venv.sh` 用桌面版自带的 Python 建 venv（共享它的
  site-packages，只额外装 pytest/xdist）。`check.sh` 默认用它，并用网关自己的 `module_loader` 加载三个 hook。
- 金样：`tests/test_golden.py` 录下 6 个场景（裸仓库、devdelta 2.6.2、payload 2.3.0、older 2.1.1、
  关卡+问题、修订+错误爆发）的仓库、intent、详情、流程图、产物、卡片、安装/升级/卸载预览、计划预览。
- 最低 KiroCrew 提到 0.8.0：`backend/routes.py`、`hooks.py` 去掉自建命名空间，直接 `from . import studio`
  （0.8 加载器注册父包、失败回滚、停用时卸载）。
- App 名运行时决定：后端取自宿主给的包名（`_kirocrew_app_<name>`），UI 取自 bundle 自己的 URL；
  `scripts/dev-slug.sh <name>` 生成改名副本并安装。已在本机网关以 `aidlc-studio-next` 与 v1 并排装上，
  83 个路由注册、启动正常、health 为 healthy。
- 0.8 上 fork 出来的子进程继承了宿主路径检查的死线程池，导致 6 个“进程崩溃”测试误报；`conftest.fork_process`
  在子进程里重建线程池。
- 待办：无头浏览器拿不到 owner 会话（v1 同样如此），UI 实机检查留到 P8 在桌面版里做。

### 第一轮逐模块瘦身（2026-10-08，6 个工作树并行，输出不变）

| 模块 | 之前 | 之后 | 说明 |
|---|---|---|---|
| engine | 1,282 | 722 | 只留一份白名单（`ENGINE_READ_VERBS`）；删掉没有调用方的动词；一个通用序列化 |
| repo_registry | 1,358 | 730 | 一个字段序列化；删 `touch_seen`、`AVAILABILITY_REASONS` |
| sessions | 1,975 | 1,193 | 只支持 0.8 的 slot 字段（`orchestrating`、`pending_approval_info`）；能力探测一条路径 |
| aidlc_reader + consistency | 4,612 | 3,356 | `WireRecord` 替代 23 个手写 `to_json`；长注释移到 `tests/fixtures/FORMAT-NOTES.md` §11–12；删 3 个无调用方的函数 |
| installer + maintenance | 5,501 | 3,616 | 卸载与回滚共用一个步骤执行器；每项安全检查只留一份。离 2.8k 目标差约 800 行：剩下的是卸载/回滚日志恢复、JSON 键合并、旧回执归属规则，都有测试钉住，再减就要删功能或安全检查 |
| advisor + plan + plan_advice + estimates | 5,644 | 3,342 | 卡片、计划、自动三种起草共用一条启动路径；`Jsonable` 替代 18 个手写 `to_json`；删 `scope_change`/`config_change`（只有测试调用）和校准写入。71 份输出逐字节一致 |

后端合计 40,608 → 33,015 行。每次合并后全量测试通过，77 个金样视图逐字节一致。

调整：
- plan_advice 问卷保留：它就是 Advisor 看到的提示词，删掉会改变计划建议的质量（原计划要删）。
- 校准没有写入方了，`GET /calibration` 永远为空，估算永远用规则区间：放进 P5a 整个删掉（路由、表、UI）。
- P2 拆成两步：P2a 租约（派生忙状态 + 每仓库文件锁，接口不变，先做），P2b 决策状态机（合并状态，等 P2a 合入后做）。

### 第二轮（2026-10-08）

**P2a 租约**：`leases.py` 870 → 274 行；storage 的租约方法、reconciler 的心跳循环和回收、安装器每步心跳全部删除。
- 执行租约 = 仓库里有带 `lease_generation` 的在途决策（Delivering/Delivered/Processing）；决策离开在途即释放。
- 管理操作和提交窗口持有每仓库一个 `flock`（`<data_dir>/locks/`），进程死亡由内核释放，所以不需要心跳和回收证明。
- 审查时补的安全缺口：送达不确定的决策不再占仓库，于是每次授予租约（执行和管理）都先要求该仓库所有宿主会话空闲。否则安装或给另一个 intent 发决定，可能在一个还在跑的轮次下面切换游标。比 v1 严：用户在同仓库另一个会话里手动跑 AI-DLC 时，Studio 也会等它跑完。
- 行为变化：没有孤儿租约和回收记录；`heartbeat_at` 等于 `acquired_at`；generation 是微秒时间戳。

**P5a 杂项**：events、settings、notifications、activity、git_observer、security、errors 共 3,618 → 2,036 行。
- 事件流改为内存环形缓冲（2,000 条），网关重启后 UI 走已有的重新同步。
- 删掉无效的夜间窗口设置（后端 + UI）；`diagnostics.retention_days` 生效（删除更早的 Activity）；`notifications.dashboard` 开关原来没人读，现在真正控制桌面通知。
- 通知合成 `notify(kind, …)`，删掉从未调用的摘要通知；校准功能整个删掉（路由、UI、估算里的校准分支）。
- 删掉 6 个没有任何地方抛出的错误码。

合并后：后端 29,961 行（−26%），`check.sh` 11 项全过（后端 2789 个测试，UI 468 个）。

### 第三轮（2026-10-08）

**P2b 决策状态机**：`actions.py` 3,434 → 1,797 行；reconciler 的结算部分 1,357 → 830 行。
- 状态 12 → 9：删 `Draft`（从未创建）；`Processing` 并入 `Delivered`（"轮次已开始"记为 `turn_started_at`，界面照旧显示"处理中"）；`DeliveryUncertain` 并入 `ReconciliationRequired`（不确定时直接要人核对，少一个 tick、少一条 Activity）。存储迁移 V3 改写旧行。
- 删掉永远拒绝的机器通道：`MachineActionBroker`、`/keep-moving` 路由、Intents 页的"自动续跑"列，以及 `/health` 的 `machine_lane` 和相关错误码、Activity 类型。
- at-most-once、捕获集比对、游标回读、熔断、结算合约表全部保留，测试改写后仍覆盖。

**P3a 流程图移到前端**：后端只收集事实（`map_inputs`），`ui/src/map/buildMap.ts` 拼装；9 份 v1 金样逐字段一致，另 515 个随机快照新旧一致。后端 −254 行，前端 +215 行。

**P3b 卡片与扫描循环**：`projection.py` 2,804 → 1,795 行；扫描循环改为一个循环。
- 卡片推导用一张按类型的规则表；各视图用 `WireRecord` 序列化；输出逐字节不变。
- 补上 v1 缺的通知：新关卡、新问题各通知一次，intent 完成通知一次（只在本进程看着它从未完成变成完成时；重启、取消归档不重复）。只通知本进程生成的卡片，所以从 v1 升级或重启不会把整个队列重放一遍。
- 顺手修的 v1 bug：一个 intent 没推导出任何卡片时，扫描的摘要和边界看不到人发起的 Run（摘要显示 Idle、0 个待办）。

合并后：后端 26,335 行（−35%），`check.sh` 全过（后端 2772 个测试，UI 498 个）。

## 金样变更记录

（每次有意改变金样输出都记在这里：哪个阶段、哪个视图、改了什么、为什么。）

- **P3 · `map_*`（2026-10-08）**：`GET …/map` 不再返回拼好的模型，改为返回原始事实 `map_inputs`（阶段图节点、状态行、产物、跳过原因、各阶段最近一次尝试的起止时间、服务器 `now`）。原因：拼装只是展示（合并、分组、计数），按 P3 移到 `ui/src/map/buildMap.ts`，后端只留一个收集事实的方法；界面拿到的模型不变。
  等价性：`buildMap` 用新录的 9 份输入重放 v1 的 9 份流程图金样，逐字段一致（`ui/src/map/golden/` 永久保留 3 份）；另把 Python 流程图测试的 35 次调用和 480 个随机快照的新旧拼装结果一次性逐个比对，全部一致。

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
4. 死代码：14 个前端从不调用的路由；每轮推导但从不显示的"修订"卡；只有测试调用的引擎动词和动作。

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

后端合计 40,608 → 35,317 行。每次合并后全量测试通过，77 个金样视图逐字节一致。

## 金样变更记录

（每次有意改变金样输出都记在这里：哪个阶段、哪个视图、改了什么、为什么。）

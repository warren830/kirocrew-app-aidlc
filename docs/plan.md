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

### 第四轮（2026-10-08）

**存储、服务、常量**：数据库升到第 4 版，删掉 6 张没人读写的表（三张租约表、`events`、`calibration`、`migrations`），剩 11 张；`action_transitions` 保留（决策详情要显示每次状态变化的 generation）。新增 `wire.py`，全后端只剩一个序列化基类 `WireRecord`（整个测试套件约 27.6 万次调用新旧逐字节比对，零差异）；只在真正需要计算的地方保留手写 `to_json`。顺手删掉安装器的 `boot_id` 参数、引擎的 `drain_activity` 空方法和一批只有测试用的常量。

**P5b 路由**：80 → 56 个，`handlers` 3,193 → 1,903 行。
- 删掉 13 个前端没有任何调用方的路由（单独的重试/核对/取消、安装凭据列表、Slack 回调、questions、intent 的 git 和 activity、payload 等），逐个核对过前端引用。
- 安装、升级、卸载、回滚、恢复 10 个路由并成 `transactions/preview` 和 `transactions` 两个；会话的绑定/解绑/接管并成一个；归档/恢复并成一个；Run/Resume 并成一个；`/leases` 并进 `/health`。
- 删掉 `IntentSummary.keep_moving` 和流程图被忽略的 `?density=` 参数（金样变更见下）。

**修复：macOS 重启后卸载、回滚被拒**。macOS 重启会给磁盘卷换设备号（devlake：凭据里是 `16777230:121937066`，现在是 `16777231:121937066`），安装凭据停留在旧号上，卸载和回滚报 `identity_changed`。现在路径和 inode 都不变、只有设备号变时，认作同一个仓库，重新扫描时把仓库、凭据、事务、决策里的旧号一起改成新号；换了 inode 的仍算"已移动"。用用户真实的 v1 数据库副本验证过：v2 把它从第 2 版迁移到第 4 版，devlake 的凭据身份被修正，升级预览与 v1 一致（160 个归属 Studio 的文件，0 冲突）。

合并后：后端 24,383 行（−40%），`check.sh` 全过（后端 2763 个测试，UI 500 个）。

### 第五轮（2026-10-08）：测试瘦身

三组并行，后端代码一行不动；`scripts/coverage_guard.py` 把未改动测试集跑出的行覆盖和分支覆盖存成基线（93.4% 行、4067/4558 分支），删减后基线执行过的每一行、每个分支都必须仍被执行。

| 组 | 之前 | 之后 |
|---|---|---|
| A 决策投递、对账、会话、租约、通知（16 个文件） | 12,396 | 8,501（−31%） |
| B 文件解析、卡片推导、引擎、仓库、git、静态检查（16 个文件） | 13,117 | 8,307（−37%） |
| C 安装器、Advisor、计划、存储/事件/设置/Activity、杂项路由（30 个文件） | 15,579 | 12,417（−20%） |

- 做法：参数化只差输入的测试；每个文件内的构造器和 fixture；文档字符串缩成一行；合并同一路径同一断言的重复测试。没有削弱任何断言，没有跳过任何测试；拒绝类和安全性质的测试全部保留。
- 没到 40–50%：剩下的都是一个行为一个测试、每行不是准备就是断言（安装器的故障注入、进程崩溃、回滚日志尤其如此），再删就要把不同性质合进一个测试。
- B 组发现一处覆盖率依赖 xdist 分配顺序（`sessions.py` 的 linked-session-key 回退只在没人先导入 `chat_utils` 时执行），补了一个确定执行它的测试。
- 三组合并后：测试 42,368 → 30,575 行；`check.sh` 全过（后端 2721 个测试，UI 502 个）；覆盖率守卫在 `-n 8` 和 `-n 4` 两种分配下都通过。

### P8（进行中）：用真实数据对比 v1 和 v2

在两份用户真实 v1 数据库的副本上（devlake、new-wechat 两个仓库），分别用 v1 和 v2 的代码跑一次扫描，读出界面用到的 17 个视图（仓库、intent 列表和详情、产物、评审、待办、每张卡片、安装/升级/卸载预览）逐字段比对（宿主用假的，没有实时会话；不碰仓库文件）：
- 14 个视图完全一致；其余差异都在预期内：`keep_moving` 字段已删（P5b）；两边各自生成了一张同样的恢复卡（假宿主没有会话），只是 id 和时间不同。
- 一处是 v2 的修复：devlake 的卸载预览在 v1 里报 `409 identity_changed`（macOS 重启后设备号变了），v2 给出正常的计划（移除 278 个、保留 1 个）。
- 两个仓库的升级预览完全一致（devlake：160 个归属 Studio、0 冲突）。
- 2026-10-12：`app.json` 和顾问 agent 直接改名为 `aidlc-studio-v2` / “AI-DLC Studio v2”。原因：清单仍叫 `aidlc-studio` 时，在 KiroCrew 里“从文件夹安装”会落到 1.x 的名字上，被拒绝并报出 1.x 的版本号 1.1.6；现在这个文件夹本身就能装在 1.x 旁边，不再需要 `dev-slug.sh`。测试环境改为按清单里的名字加载后端（和网关一致），测试里写死的名字都从清单派生。
- 2026-10-12 实机跑通（从文件夹安装的 v2，一次性测试仓库 `studio-v2-live-test`）：注册仓库 → 装 AI-DLC 2.10.0（创建 311 个文件，0 冲突）→ 建 express intent → 建会话并绑定 → `/aidlc` 走两段式提交 → 出现 reverse-engineering 关卡 → 批准 → 卡片变 `StateChanged`，进入 requirements-analysis。约 30 分钟里网关没有一次“task store: connection taken ON the event loop”警告（修复前 v1 大约每分钟一次），Studio 没有报错。用 `kcapi.sh` 的令牌脚本化建会话时，`POST /api/chat/slots` 会回 `store_unavailable`（令牌没有成员身份），但会话其实已建好；界面用的是主人的 cookie，不受影响。

## 金样变更记录

（每次有意改变金样输出都记在这里：哪个阶段、哪个视图、改了什么、为什么。）

- **P3 · `map_*`（2026-10-08）**：`GET …/map` 不再返回拼好的模型，改为返回原始事实 `map_inputs`（阶段图节点、状态行、产物、跳过原因、各阶段最近一次尝试的起止时间、服务器 `now`）。原因：拼装只是展示（合并、分组、计数），按 P3 移到 `ui/src/map/buildMap.ts`，后端只留一个收集事实的方法；界面拿到的模型不变。
  等价性：`buildMap` 用新录的 9 份输入重放 v1 的 9 份流程图金样，逐字段一致（`ui/src/map/golden/` 永久保留 3 份）；另把 Python 流程图测试的 35 次调用和 480 个随机快照的新旧拼装结果一次性逐个比对，全部一致。
- **P5 · `repo`、`intents`、`intent_*`（2026-10-08）**：`IntentSummary`（以及继承它的 intent 详情）去掉 `keep_moving` 字段，共 19 个视图、27 行，均只删这一个键；原因：机器通道已删，该字段恒为 `false`，前端也不再读。同阶段去掉的流程图 `?density=` 参数本来就被忽略，金样请求不带它，没有视图变化。
- **P8 · `install_preview`（bare-repo）、`upgrade_preview`（devdelta-2.6.2）（2026-10-12）**：`studio_version` 1.1.6 → 2.0.0-beta.1，以及把它算进去的 `plan_digest`。原因：v2 改为以 `aidlc-studio-v2` 与 1.x 并排安装的测试版，版本号要能和 1.x 区分开。别的字段没变。
- **P8 · 待办卡片的 `deep_link`（2026-10-12）**：6 个场景中 12 个视图共 16 行，`/apps/aidlc-studio?…` → `/apps/aidlc-studio-v2?…`。原因：v2 改用自己的应用名，链接按运行时的名字生成。逐行核对过：把新值里的名字换回去就与旧金样完全相同。
- **P8 · payload-2.3.0 卡片的 `evidence.review.review_class`（2026-10-12）**：4 个视图共 6 行，`null` → `"advisory"`。原因：卡片上的评审类型改成按本次运行实际会做的评审来算：阶段图里的类型，再按 scope 的上限和状态文件里的 Review Override 封顶，和计划矩阵用同一条规则。2.3.0 的阶段图只有 reviewer、没有类型，计划矩阵一直把它算作 advisory，卡片现在和它一致。反过来，express 这类不跑评审的 scope，卡片不再显示 “advisory review”。
- **P8 · 每个 intent 视图的 `counts`（2026-10-12）**：6 个场景、19 个视图，各新增 `planned` 和 `planned_done` 两个键，共 54 行，其余不变。原因：intent 列表的进度原来写成“3 of 33 stages”，分母是阶段图的全部阶段；express 只跑 7 个。现在按状态文件里标 `— EXECUTE` 的行算（和引擎自己的 “in-scope stages” 进度一致），没有标的旧文件仍用全部阶段。
- **P8 · payload-2.3.0 requirements-analysis 关卡卡片的 `evidence.artifacts` 顺序（2026-10-13）**：4 个视图，只调了顺序，`requirements.md` 排到 `requirements-analysis-questions.md` 前面（内容逐项核对一致）。原因：关卡模板打开的是第一个产物，按文件名排时问题文件在前（`-` 排在 `.` 前），用户批准需求时看到的是问答而不是需求文档。现在阶段图里声明的主产物（`review_artifact`，否则 `produces` 的第一个）排第一。

# 意图确认问题（Intent Capture）

## Sources

- [desc] Initial description: "看看我的这个项目能不能做一个完整的打包方案，方便一键部署"
- [scope] Workflow-selected scope: `feature`.

---

## Q1. 想用"一键部署"解决什么问题？（可多选）

背景：现在 README 里"从零重建"要按顺序跑十几条命令，再做几步手动操作。先弄清楚最痛的是哪一点。

A. 重建步骤太多、太散，容易漏步或顺序弄错
B. 整套从没从头重跑过，不确定能不能重建成功
C. 机器坏了或丢了，想能快速恢复
D. 想让别人（或另一台机器）也能照着装出同样的一套
E. Not yet defined（还没想清楚）
X. Other (please specify)

[Answer]: A, B, D

## Q2. 这个一键部署主要给谁用？

背景：只给自己用和给别人用，要求差很多（比如说明写多细、能不能改配置）。

A. 只有我自己
B. 我自己，加上少数熟人或同事
C. 公开给任何人用（比如开源出去）
D. Not yet defined
X. Other (please specify)

[Answer]: B

## Q3. 做成什么样算成功？（可多选）

背景：需要能量出来的标准，后面才能验收。

A. 一条命令跑完，从空账号到全部服务能用
B. 整个过程在一个时间内完成（请在 X 里写多少分钟）
C. 跑完后现有的冒烟测试（如 relay 的 9 项）全部通过
D. 只剩少量必须人手做的步骤（如登录、扫码配对），其余全自动
E. Not yet defined
X. Other (please specify)

[Answer]: A, D

## Q4. 为什么现在要做？

背景：了解起因，决定优先级和急不急。

A. 准备换机器或重建这台 EC2
B. 准备给别人用或分享出去
C. 担心现在唯一这台机器出事没法恢复
D. 只是想把仓库整理好，没有具体事件
E. None（没有特别原因）
X. Other (please specify)

[Answer]: B

## Q5. 这一版先不做哪些？（可多选）

背景：工作范围已经选好是 `feature`（一个功能）。这里只问这个打包方案的第一版不包含什么。

A. 不包含需要人手操作的一次性步骤（Orca 登录、手机扫码、Mac 加 host 配对），这些仍然手动
B. 不包含 KiroCrew
C. 不包含 Mac 本机那一侧的安装（通道、看门狗等）
D. 不包含 Orca 升级流程，只管第一次部署
E. None（全部都要包含）
X. Other (please specify)

[Answer]: A

## Q6. 部署到哪里？

背景：只针对现在这一套环境，和能换账号、换区域，难度不同。

A. 只要能在我现在的 AWS 账号和新加坡区域重建就行
B. 要能换 AWS 账号或区域，部署时选
C. Not yet defined
X. Other (please specify)

[Answer]: B

## Q7. 谁来定范围和优先级？还有谁要参与意见？

A. 只有我，自己决定
B. 我决定，但要听一两个人的意见（请在 X 里写是谁）
C. Not identified
X. Other (please specify)

[Answer]: A

## Q8. 需要定期汇报或同步进度吗？

A. 不需要
B. 做完时更新一下 README / 文档就行
C. 需要定期同步（请在 X 里写给谁、多久一次）
D. Not applicable
X. Other (please specify)

[Answer]: A

## Consolidated Summary Confirmation

Does this all look correct before I generate the artifact?

- Looks correct
- Request changes

[Answer]: Looks correct

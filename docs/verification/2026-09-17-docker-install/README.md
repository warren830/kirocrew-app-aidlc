# Docker 安装验证记录

2026-09-17 已使用 Docker 实际验证 Bun、AI-DLC 工具和 AI-DLC Studio 安装流程，并修复验证中发现的安装脚本问题。结构化结果见 [results.json](results.json)。

验证分为两个隔离容器：

1. 从尚未安装 Bun 的 Linux ARM64 环境执行官方安装脚本，得到 Bun **1.4.2**；执行 TypeScript 表达式得到 `42`，并运行仓库内工具得到 `aidlc 2.7.1`。
2. 使用官方 `ghcr.io/kirodotdev/kirocrew:stable` 镜像，实际版本为 KiroCrew **0.6.0**。将第一步验证过的 Bun 二进制只读挂载到该容器，测试安装、路径配置、框架安装和更新。该镜像没有 Node，也没有 `/usr/bin/python3`，Gateway 使用 `/usr/local/bin/python3`。

镜像摘要为 `sha256:ba01bb1c75ea454af2b53773f39c3b38899f0d1d9ab51a0eb97001df6db27407`。没有挂载真实用户目录，没有向主机发布测试端口；测试容器在结束后停止并删除。

| 检查 | 结果 |
|---|---|
| 官方 Bun 脚本从未安装状态开始执行 | 成功，版本 1.4.2 |
| Bun 执行实际 TypeScript 工具 | 成功，AI-DLC 2.7.1 |
| 在官方 KiroCrew 镜像中执行 `dev-install.sh --no-build` | 退出码 0 |
| 应用健康检查 | `healthy` |
| 框架包校验 | 293 个文件，0 个不匹配 |
| HTTP 提供的 UI bundle 与源码包比较 | 字节一致 |
| 保存有效 Bun 绝对路径并重新探测 | 成功 |
| 输入 `~/.bun/bin/bun` | 拒绝，`bad_path` |
| 输入不存在的 Bun 路径 | 拒绝，`bun_missing`，先前有效配置保留 |
| 通过 Studio API 向临时 Git 项目安装 AI-DLC | 事务 `committed`，工具报告 2.7.1 |
| 临时项目原有 README | 保留 |
| 再次执行安装脚本，走更新流程 | 退出码 0 |
| 更新后的 Bun 配置和仓库注册 | 保留 |
| 安装脚本回归及打包检查 | 32 项通过 |

**验证确实暴露了问题。** 公开版本 `fff973b` 的脚本把 JSON 检查所用的 Python 写死为 `/usr/bin/python3`。在官方容器中，应用后端已经启用，但最后的健康检查调用失败，脚本以 **127** 退出。修复后优先使用 `KC_PY`，否则查找 `PATH` 中的 `python3`，并正确处理解释器路径中的空格。

同时修复了安装目录含双引号时请求 JSON 被破坏的问题。Node.js/npm 的检查现在只在构建 UI 时执行；缺少构建工具会在调用安装 API 前明确提示可使用 `--no-build`。

三个新回归用例在修复前均失败，修复后全部通过。它们覆盖配置的 Python、无需 Node 的预构建安装、路径的 JSON 编码，以及缺少构建工具时的提示。实际容器验证另行覆盖真实 Gateway、健康检查、Bun 设置和事务安装，不以模拟 API 代替这些结果。

源代码下载的边界也保留在记录中：直接从 GitHub 克隆时已开始接收对象，但因下载缓慢被取消。原始失败场景使用从已核对的公开 Git 提交导出的干净快照；修复场景使用相同快照加本次脚本修改。真实凭证和本地未跟踪文件没有进入源码包。

本轮没有验证浏览器渲染、Linux x86_64、全新 macOS 环境或所有历史 KiroCrew 版本，也没有执行 Kiro CLI 登录、模型调用或真实聊天平台请求。当前主机正在使用的 KiroCrew 未被重新安装或重启。

已有 Docker KiroCrew 实例时，可在容器内部按[中文安装指南](../../installation.zh-CN.md)执行 Bun 和应用安装命令。官方镜像的预构建安装不需要额外安装 Node 或系统 Python：

```bash
docker exec -it <KiroCrew容器名> bash

# 以下在容器内、以运行 Gateway 的同一用户执行：
git clone https://github.com/warren830/kirocrew-app-aidlc.git
cd kirocrew-app-aidlc
KC_PY="$(command -v python3)" bash scripts/dev-install.sh --no-build
```

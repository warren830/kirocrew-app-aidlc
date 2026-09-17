# 安装 AI-DLC Studio 到 KiroCrew

本仓库包含可直接安装的 UI 构建产物。首次安装可跳过前端构建，无需执行 `npm install`。

## 安装条件

- macOS 或 Linux，已启动 KiroCrew。应用声明的最低 KiroCrew 版本为 0.3.0。
- 安装脚本需要 Git、Node.js、Bash、`curl` 和 `/usr/bin/python3`。
- 在项目中运行 AI-DLC 时，还需要 Bun、已登录的 Kiro CLI，以及支持所用模型的 Kiro 订阅。

安装命令应在运行 KiroCrew Gateway 的机器上执行。安装 Studio 和向项目安装 AI-DLC 是两个独立步骤。

## 1. 下载并安装应用

在终端执行：

```bash
git clone https://github.com/warren830/kirocrew-app-aidlc.git
cd kirocrew-app-aidlc
bash scripts/dev-install.sh --no-build
```

脚本会安装或更新当前代码，只为 `aidlc-studio` 授予应用信任，启用后端，并输出健康检查结果。

默认配置对应 Apple Silicon Mac 的桌面版 KiroCrew：

- Gateway 端口：`5476`。
- Gateway Python：`/Applications/KiroCrew.app/Contents/Resources/backend-dist/kirocrew-backend-arm64/bin/python3.12`。

Linux、Intel Mac、源码安装或虚拟环境安装，需要将 `KC_PY` 设置为实际运行 Gateway 的 Python。端口不同则同时设置 `KC_PORT`：

```bash
# 将路径替换为当前 KiroCrew Gateway 实际使用的 Python。
KC_PY=/path/to/kirocrew/.venv/bin/python KC_PORT=5476 \
  bash scripts/dev-install.sh --no-build
```

如果 KiroCrew 的 Discover 目录已经收录 AI-DLC Studio，也可从 **Discover → AI-DLC Studio → Install** 安装。公开 GitHub 仓库不会自动将应用加入 Discover 目录。

## 2. 确认安装结果

安装脚本最后的应用健康检查应返回 `"status": "healthy"`。也可单独执行：

```bash
bash scripts/kcapi.sh GET /api/apps/aidlc-studio/health
```

然后在 KiroCrew 中打开 **Apps → AI-DLC Studio**。

## 3. 给项目安装 AI-DLC

1. 在 **Repos / 仓库 → Add repository / 添加仓库** 中选择 Gateway 所在机器上的项目目录。
2. 查看预检结果，并选择 **Install AI-DLC / 安装 AI-DLC**。
3. 确认安装预览中的文件和冲突。
4. 选择 **New intent / 新建意图**，填写需求、范围和测试策略。
5. 创建后选择 **Run to next checkpoint / 运行到下一检查点**。审批和问题会出现在待办中心。

## 更新与开发

更新前先结束正在进行的 Studio 操作。安装脚本会短暂禁用应用，以重新加载后端：

```bash
git pull --ff-only
bash scripts/dev-install.sh --no-build
```

只有修改 UI 源码后，才需要重新构建。仓库锁定的 Vite 依赖要求 Node.js `^20.19.0 || >=22.12.0`：

```bash
(cd ui && npm ci)
bash scripts/dev-install.sh
```

使用 `bash scripts/dev-install.sh --dev` 可同时启用 UI 热重载。

## 常见问题

| 现象 | 检查方法 |
|---|---|
| `could not mint a token` | 确认 Gateway 已启动，`KC_PY` 指向其实际 Python，并由运行 Gateway 的同一系统用户执行命令 |
| `cookie exchange failed` | 确认 `KC_PORT` 与当前 Gateway 一致，且没有连接到另一套 KiroCrew 实例 |
| `node` 不存在 | 安装 Node.js 并确认它在 `PATH` 中；即使使用预构建 UI，当前安装脚本也会查找 Node.js |
| 页面已打开，但项目无法运行 AI-DLC | 在 Studio 设置中检查 Bun；确认 Kiro CLI 已登录，并已对目标项目执行 Install AI-DLC |
| Discover 中找不到应用 | 使用上面的 GitHub 克隆和本地安装方式 |

认证辅助脚本会取得本机 Gateway 的会话，无需把 API token 复制到代码或配置文件中。

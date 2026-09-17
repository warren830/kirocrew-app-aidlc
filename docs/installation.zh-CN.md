# 安装 AI-DLC Studio 到 KiroCrew

本仓库包含可直接安装的 UI 构建产物。首次安装可跳过前端构建，无需执行 `npm install`。

已在官方 KiroCrew Docker 镜像中实际验证安装和更新，详见 [Docker 验证记录](verification/2026-09-17-docker-install/README.md)。

## 安装条件

- macOS 或 Linux，已启动 KiroCrew。应用声明的最低 KiroCrew 版本为 0.3.0。
- 安装脚本需要 Git、Bash、`curl` 和 Python 3。优先使用 `KC_PY`，否则查找 `PATH` 中的 `python3`。
- 使用 `--no-build` 安装预构建 UI 时，无需 Node.js 或 npm；重新构建 UI 时才需要。
- 在项目中运行 AI-DLC 时，还需要 Bun、已登录的 Kiro CLI，以及支持所用模型的 Kiro 订阅。

安装命令应在运行 KiroCrew Gateway 的机器上执行。安装 Studio 和向项目安装 AI-DLC 是两个独立步骤。

## 1. 安装 AI-DLC 所需的 Bun

AI-DLC 使用 Bun 执行 TypeScript 工具。请在 **KiroCrew Gateway 所在机器上，以运行 Gateway 的同一系统用户**安装。如果 Gateway 在远程服务器上，应在服务器执行命令。Node.js 和 npm 仅在重新构建 Studio UI 时需要。

macOS 13 及以上版本和 Linux 可使用 [Bun 官方安装脚本](https://bun.com/docs/installation)：

```bash
curl -fsSL https://bun.com/install | bash

# 让默认安装位置在当前终端立即生效。
export PATH="$HOME/.bun/bin:$PATH"
bun --version
command -v bun
```

Linux 使用该脚本需要 `unzip`。Debian/Ubuntu 缺少相关命令时，可先执行：

```bash
sudo apt install curl unzip
```

其他 Linux 发行版使用各自的包管理器安装这两个工具。

如果已经使用 Homebrew，也可选择以下安装方式：

```bash
brew install oven-sh/bun/bun
bun --version
command -v bun
```

`bun --version` 应输出版本号，`command -v bun` 应输出可执行文件路径。如果新终端仍提示 `bun: command not found`，将以下内容加入 zsh 的 `~/.zshrc` 或 Bash 的 `~/.bashrc`（登录 shell 使用 `~/.bash_profile`），然后重新打开终端：

```bash
export PATH="$HOME/.bun/bin:$PATH"
```

该路径对应官方脚本的默认安装位置；使用自定义安装目录时，请改为实际目录。

### 让 Studio 找到 Bun

完成应用安装后，打开 **AI-DLC Studio → 设置 → Bun 可执行文件**：

1. 点击 **重新探测 Bun**。
2. 若仍显示未找到，将 Gateway 机器上 `command -v bun` 输出的完整路径填入 **Bun 的绝对路径**。
3. 点击 **保存并校验路径**，确认显示 **Bun 已就绪**。

路径框需要展开后的绝对路径，不接受字面形式的 `~` 或 `$HOME`。桌面启动的 Gateway 可能与终端使用不同的 `PATH`，因此终端能运行 Bun 时，仍可能需要在这里指定路径。配置立即生效，无需重启 Studio。

## 2. 下载并安装应用

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

如果 Gateway 已在 Docker 中运行，先用 `docker exec -it <容器名> bash` 进入容器，再在其中执行 Bun 安装、仓库克隆和应用安装。官方镜像可使用以下 Python 配置，无需额外安装 Node 或 `/usr/bin/python3`：

```bash
KC_PY="$(command -v python3)" bash scripts/dev-install.sh --no-build
```

## 3. 确认安装结果

安装脚本最后的应用健康检查应返回 `"status": "healthy"`。也可单独执行：

```bash
bash scripts/kcapi.sh GET /api/apps/aidlc-studio/health
```

然后在 KiroCrew 中打开 **Apps → AI-DLC Studio**。

## 4. 给项目安装 AI-DLC

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
| `node` 不存在 | 安装预构建 UI 时使用 `--no-build`；需要重新构建时，再安装 Node.js 和 npm 并确认它们在 `PATH` 中 |
| Python 位于虚拟环境或 `/usr/local/bin` | 将 `KC_PY` 设置为 Gateway 实际使用的 Python；脚本不要求固定的 `/usr/bin/python3` 路径 |
| `bun: command not found` | 按 Bun 安装步骤配置 `PATH`，重新打开终端并运行 `bun --version` |
| 终端能运行 Bun，但 Studio 显示未找到 | 在 Gateway 机器上运行 `command -v bun`，将输出填入 Studio 设置中的 Bun 绝对路径，再保存并校验 |
| 页面已打开，但项目无法运行 AI-DLC | 在 Studio 设置中检查 Bun；确认 Kiro CLI 已登录，并已对目标项目执行 Install AI-DLC |
| Discover 中找不到应用 | 使用上面的 GitHub 克隆和本地安装方式 |

认证辅助脚本会取得本机 Gateway 的会话，无需把 API token 复制到代码或配置文件中。

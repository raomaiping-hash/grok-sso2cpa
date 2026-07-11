# SSO Bridge

SSO Bridge 是一个本地运行的 Windows Electron 工具，用于把 x.ai SSO Cookie 转换为可用的认证文件。

它支持 Grok 原生 `auth.json`、cliproxyapi `xai-{email}.json`，以及已有认证 JSON 之间的格式转换。

当前版本面向单用户本机使用。账号数量不设上限，任务可以并发执行，也可以在运行过程中停止。

## 功能

- 批量处理任意数量的账号。
- 并发执行 Device Flow，并在限流时自动退避重试。
- 支持 Grok 原生格式、cliproxyapi 格式和已有 auth 文件转换。
- 实时显示任务进度、日志和失败原因。
- 支持单个 JSON 下载或 ZIP 批量下载。
- 支持 GitHub Releases 检查更新。
- Windows 安装包由 GitHub Actions 云端构建和发布。

## 快速开始

### 直接运行后端页面

需要 Python 3.11 或更高版本。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

然后打开 <http://127.0.0.1:8000>。

### 运行 Electron 开发版

需要 Node.js 22、Python 3.11 或更高版本。

```powershell
npm install
python -m pip install -r requirements.txt
npm start
```

Electron 会启动本地 Python 后端，并在本机回环地址上建立前端与后端的连接。

## 使用流程

1. 在“SSO Cookie”页面粘贴 Cookie，每行一个账号。
2. 选择输出格式：cliproxyapi、Grok 原生，或已有 auth 文件转换。
3. 在高级设置中调整并发数、重试次数和退避时间。
4. 点击开始任务，观察实时进度和日志。
5. 需要提前结束时点击“停止任务”，再按需下载成功结果。

支持以下常见输入形式：

```text
cookie
email----cookie
email----password----cookie
```

密码字段仅用于兼容输入格式，不会参与认证请求。

## 测试与构建

运行单元测试：

```powershell
python -m unittest discover -s tests -v
```

构建 Windows 安装包：

```powershell
python -m pip install -r requirements-build.txt
npm run dist
```

输出文件位于 `release/`。应用图标源文件位于 `assets/icon.png`，electron-builder 会在构建 Windows 安装包时转换为所需格式。

## 自动发布

发布使用 GitHub Actions 云端构建，不需要在本地上传安装包。发布前先把 `package.json` 和 `package-lock.json` 的版本号更新为同一个新版本。

```powershell
git add package.json package-lock.json
git commit -m "chore: bump release version"
git tag v0.3.2
git push origin main
git push origin v0.3.2
```

推送 `v*` 标签后，`.github/workflows/publish-windows.yml` 会自动完成依赖安装、版本校验、Windows 构建、创建 Release 和上传安装包。

也可以在 GitHub Actions 页面手动运行工作流，但手动输入的标签必须与 `package.json` 版本一致。

## 自动更新

Electron 使用 `electron-updater` 检查 GitHub Releases。安装包发布后，已安装的应用可以通过应用内的版本检查入口发现新版本。

## 项目结构

| 路径 | 作用 |
| --- | --- |
| `app/` | FastAPI 后端和任务逻辑 |
| `static/` | Web 页面、样式和前端脚本 |
| `electron/` | Electron 主进程、窗口和自动更新 |
| `assets/` | 应用图标等打包资源 |
| `scripts/` | 后端打包与发布辅助脚本 |
| `.github/workflows/` | GitHub Actions 云端发布流程 |
| `tests/` | 单元测试 |

## 安全边界

这是一个面向本机的工具。请只处理你本人拥有或明确获授权使用的账号，并避免把 Cookie、生成的认证文件或 `data/jobs/` 内容提交到 Git 仓库。

原始输入只在任务运行期间传递给本地后端，日志不会主动打印 Token。任务输出会保存在 Electron 用户数据目录中，Windows 默认位于 `%APPDATA%\sso-bridge\jobs`。

## License

MIT

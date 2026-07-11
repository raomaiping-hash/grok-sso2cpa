# SSO Bridge

SSO Bridge 是一个本地运行的 Web 工作台，用于把 x.ai SSO Cookie 转换成：

- Grok 原生嵌套格式 `auth.json`
- cliproxyapi 可识别的 `xai-{email}.json`
- 已有 Grok / cliproxyapi auth JSON 到 cliproxyapi 文件

它保留了参考脚本里的 Device Flow、userinfo 补充、批量任务、限流退避和账号级重试逻辑，并支持不限账号数量、可配置账号并发数、实时进度、日志和 ZIP 下载。当前版本面向单用户本机运行。

## 启动

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000>。

如果只需要运行纯转换逻辑，测试不要求访问网络：

```powershell
python -m unittest discover -s tests -v
```

## Electron 桌面版

开发运行需要 Node.js、Python 以及依赖：

```powershell
npm install
python -m pip install -r requirements.txt
npm start
```

打包 Windows 安装程序：

```powershell
python -m pip install -r requirements-build.txt
npm run dist
```

安装包输出在 `release/`。应用会把 Python 后端作为本地子进程启动，任务输出写入 Electron 的用户数据目录（Windows 默认位于 `%APPDATA%/sso-bridge/jobs`；开发版也可能使用 `data/jobs`）。应用内的“检查版本”使用 `electron-updater` 检查 GitHub Releases。

发布采用 GitHub Actions 云端构建，不要在本地直接上传安装包。更新 `package.json` 版本号后，提交并推送对应的版本标签即可：

```powershell
git tag v0.3.1
git push origin v0.3.1
```

推送 `v*` 标签会自动在 GitHub 的 Windows 云端 runner 上执行 `npm run dist`，创建 Release 并上传安装包、`latest.yml` 和 `.blockmap`。工作流文件位于 `.github/workflows/publish-windows.yml`；GitHub Actions 页面仍保留手动补跑入口。

## 使用方式

1. 在“SSO Cookie”页签粘贴 Cookie，每行一个；也支持 `email----cookie` 与 `email----password----cookie`（密码只用于兼容输入格式，不会被使用）。
2. 选择 cliproxyapi、Grok 原生，或两者同时输出。
3. 批量任务会并发执行 Device Flow、在遇到限流时退避，然后在任务面板中提供单文件和 ZIP 下载。账号数量不设上限；并发数可在高级策略中调整。
4. 任务运行期间可以点击“停止任务”。尚未开始的账号会被取消；已经发出的网络请求会在当前请求结束后收敛，不会继续生成新的输出。
4. “已有 auth 文件”模式适合把已有 Grok / cliproxyapi JSON 转成 cliproxyapi 文件。

## 安全边界

这是一个面向本机的工具。原始输入只在任务创建时驻留于运行进程内，任务输出保存在 `data/jobs/<job-id>`，日志会避免打印 Token 内容。请只处理你自己拥有或明确获授权的账号，并在任务完成后按需删除 `data/jobs` 下的输出。

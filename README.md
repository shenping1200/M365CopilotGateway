# M365CopilotGateway

M365CopilotGateway 是一个面向 Windows 桌面的 Microsoft 365 Copilot 网关。它会在本机提供 OpenAI 兼容接口，让 Hermes、Codex 以及其他支持自定义 OpenAI 接口的 AI 客户端，都可以通过统一入口调用 M365 Copilot。

这个项目包含桌面管理器、账号管理面板、OpenAI 兼容 API、模型映射、限流、工具调用格式兼容、工具别名统一、日志查看和崩溃自动重启。

> 本项目适合个人研究和本地集成使用。请只在你有权访问的账号和服务范围内使用。

## 功能亮点

- 统一本地接口：`http://127.0.0.1:8080/v1`
- 打包后的桌面管理器无命令行黑框
- 管理面板地址：`http://127.0.0.1:7860`
- 兼容 OpenAI 风格接口：
  - `/v1/models`
  - `/v1/chat/completions`
  - `/v1/responses`
  - `/v1/messages`
- API Key 兼容模式：关闭严格校验后，客户端随便填一个 API Key 即可通过
- 默认暴露 7 个模型 ID：
  - `copilot-auto`
  - `copilot-quick`
  - `copilot-thinking`
  - `gpt-5.5`
  - `gpt-5.5-thinking`
  - `gpt-5.2`
  - `gpt-5.2-thinking`
- 支持多账号 token 管理
- 支持请求日志和 Dashboard 日志查看
- 支持运行时配置模型、工具别名、日志和局域网访问
- 支持守护进程监控，服务异常后自动重启
- 支持 Windows 桌面打包，包含窗口标题栏和任务栏图标

## 工具调用说明

工具调用是这个网关最重要的兼容层。

不同 AI 客户端对本地工具的命名和请求格式不完全一样。例如：有的客户端把终端工具叫 `terminal`，有的叫 `exec_command`，还有的叫 `shell`、`run_command`。M365CopilotGateway 会在请求进入 Copilot 之前先统一这些工具名，并在 Copilot 返回工具调用时，再转换成客户端能理解的 OpenAI 兼容格式。

这样 Hermes、Windows Codex、macOS Codex 等客户端就可以共用同一个 M365 网关，同时保留各自原本的本地工具执行能力。

### 默认工具别名映射

默认别名配置在 `m365_runtime_config.json` 中：

| 客户端传入的工具名 | 网关统一后的工具类型 |
| --- | --- |
| `exec_command` | `terminal` |
| `shell` | `terminal` |
| `shell_command` | `terminal` |
| `terminal` | `terminal` |
| `run_command` | `terminal` |
| `bash` | `terminal` |
| `powershell` | `terminal` |
| `computer_use` | `desktop` |
| `computer` | `desktop` |
| `desktop` | `desktop` |
| `browser` | `browser` |

### 工具调用流程

1. 客户端发送 OpenAI 风格请求，请求里包含 `tools`。
2. 网关读取并规范化工具定义。
3. 工具名通过 `tool_aliases` 映射为统一工具类型。
4. 网关把消息格式转换成 M365 Copilot WebSocket 可接受的格式。
5. Copilot 返回普通文本或工具调用内容。
6. 网关解析工具调用，并转换回客户端能识别的格式。
7. 客户端在自己的本机环境执行工具。
8. 客户端把工具执行结果再发回网关，网关继续转发到对话中。

### 关于本地工具权限

网关本身不会直接执行用户电脑上的终端命令。真正执行本地命令的是客户端，比如 Codex 或 Hermes。

这点在局域网场景里很重要：如果 Mac 上的 Codex 连接 Windows 上运行的 M365CopilotGateway，那么 Mac 的终端命令应该由 Mac 上的 Codex 本地执行，而不是由 Windows 网关执行。网关负责让模型正确发出工具调用，客户端负责在本机执行工具。

## 使用发布包快速启动

1. 在 Release 页面下载最新版压缩包。
2. 解压 `M365CopilotGateway-v1.0.0-windows.zip`。
3. 双击运行 `M365CopilotGateway.exe`。
4. 如需打开管理面板，访问：`http://127.0.0.1:7860`。
5. 在 AI 客户端中填写：
   - Base URL：`http://127.0.0.1:8080/v1`
   - API Key：默认可以随意填写，除非你开启严格 API Key 校验
   - Model：例如 `gpt-5.5-thinking`

如果局域网内其他电脑要连接这台 Windows 主机，请把 `127.0.0.1` 换成 Windows 主机的局域网 IP：

```text
http://<Windows主机IP>:8080/v1
```

## 本地开发运行

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python app_launcher.py
```

启动后访问：

- API：`http://127.0.0.1:8080/v1`
- Dashboard：`http://127.0.0.1:7860`

## 打包桌面版

```powershell
cmd /c build_desktop_manager.bat
```

打包结果会生成到：

```text
dist\M365CopilotGateway\M365CopilotGateway.exe
```

打包脚本会主动创建空的 `accounts.json`，并排除账号、token、MSAL 缓存等敏感文件。

## 运行时配置

主要配置文件：

```text
m365_runtime_config.json
```

重要配置项：

- `models`：暴露给客户端的模型列表
- `tool_aliases`：工具别名映射
- `security.lan_access`：是否允许局域网访问
- `security.require_api_key`：是否启用严格 API Key 校验
- `security.allowed_ips`：可选 IP 白名单
- `logging.include_tools`：日志中是否记录工具调用信息

## 安全说明

不要提交或分享这些文件：

- `accounts.json`
- `token_cache.json`
- `substrate_token.txt`
- `msal_cache.bin`
- 本地日志文件
- 账号 CSV 或 2FA 种子文件

仓库里的 `.gitignore` 已经默认排除了这些文件。发布包中只包含一个空的 `accounts.json`。

## 项目结构

| 文件 | 作用 |
| --- | --- |
| `unified_server.py` | 主 OpenAI 兼容网关服务 |
| `copilot_client.py` | M365 Copilot WebSocket 客户端 |
| `auth.py` | 账号和 token 管理 |
| `dashboard.py` | Gradio 管理面板 |
| `gui_launcher_v2.py` | Windows 桌面管理器 |
| `m365_supervisor.py` | 崩溃监控和自动重启 |
| `runtime_config.py` | 运行时配置加载和辅助函数 |
| `request_logger.py` | 请求和工具调用日志 |
| `build_desktop_manager.bat` | PyInstaller 桌面版打包脚本 |
| `m365_runtime_config.json` | 模型、工具别名、日志、安全配置 |

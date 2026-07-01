# 发布说明

## v1.0.0

M365CopilotGateway 首个桌面打包版本。

### 本版包含

- 带应用图标的 Windows 桌面管理器
- `8080` 端口上的统一 OpenAI 兼容接口
- `7860` 端口上的 Dashboard 管理面板
- 通过 `/v1/models` 暴露 7 个模型 ID
- 兼容 `/v1/chat/completions`、`/v1/responses`、`/v1/messages`
- 兼容 Hermes 和 Codex 的工具调用格式
- 支持终端、桌面、浏览器工具别名统一
- 支持运行时模型、工具、安全配置
- 支持请求日志和 Dashboard 日志查看
- 支持限流
- 支持崩溃监控和自动重启
- 发布包内只包含空的 `accounts.json`

### 安全说明

发布包不包含本地账号凭据、token 缓存、MSAL 缓存或本地日志文件。

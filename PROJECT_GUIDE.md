# M365 Copilot 反代服务项目说明

> 最后更新：2026-06-14

本项目把 Microsoft 365 Copilot 的 WebSocket/SignalR 通信封装成 OpenAI 兼容 REST API，并提供桌面管理器和 Web 管理面板，用于多账号 token 管理、自动登录续期、请求统计和模型测试。

---

## 1. 核心能力

- OpenAI 兼容接口：`/v1/chat/completions`、`/v1/models`。
- 多账号池：按账号轮询选择可用 token。
- 自动登录：通过 Playwright Chromium 打开 M365 Copilot 页面并捕获 Sydney token。
- 自动续期：后台定时巡检账号 token，临近过期自动登录刷新。
- 401 恢复：WebSocket 返回 401 时，自动使 token 失效并强制重新登录后重试。
- 桌面管理器：`gui_launcher_v2.py`，提供启动服务、账号状态、单账号登录、批量登录、模型测试、日志查看和配置管理。
- Web 面板：`dashboard.py`，通过 Gradio 提供管理界面，主要作为浏览器兜底入口。

---

## 2. 文件职责

| 文件/目录 | 作用 |
|---|---|
| `app_launcher.py` | 统一启动入口，同时启动 Flask API 和 Gradio Web 面板。 |
| `server.py` | Flask API 服务，提供 OpenAI 兼容接口、账号状态、刷新接口、401 重试逻辑。 |
| `auth.py` | 核心认证管理器，负责账号加载、token 缓存、Playwright 登录、自动续期、统计。 |
| `copilot_client.py` | Substrate/M365 Copilot SignalR WebSocket 客户端。 |
| `dashboard.py` | Gradio Web 管理面板。 |
| `gui_launcher_v2.py` | 当前推荐桌面管理器入口。 |
| `gui_launcher.py` / `gui_launcher_native.py` | 旧版/参考版桌面管理器，暂保留作回退参考。 |
| `config.py` | 端口、token 策略、日志目录、Playwright 参数等全局配置。 |
| `request_logger.py` | 请求日志记录和查询。 |
| `token_manager.py` | 旧版 token 管理参考代码，当前主流程已整合到 `auth.py`。 |
| `accounts.json` | 账号配置，包含 username/password/totp_secret。敏感文件。 |
| `token_cache.json` | token 缓存，自动读写。敏感文件。 |
| `substrate_token.txt` | 最近捕获的 substrate token。敏感文件。 |
| `dashboard_config.json` | 桌面/面板配置。 |
| `logs/` | 请求日志目录。 |
| `requirements.txt` | Python 依赖。 |
| `build_desktop_manager.bat` | PyInstaller 打包脚本，当前入口为 `gui_launcher_v2.py`。 |

---

## 3. 运行方式

### 3.1 开发/服务启动

```bat
python app_launcher.py
```

启动后：

- API 服务：`http://127.0.0.1:8080`
- Web 管理面板：`http://127.0.0.1:7860`

### 3.2 桌面管理器预览

```bat
python gui_launcher_v2.py
```

桌面管理器会：

- 检测 API 和 Web 面板是否已运行。
- 如果已运行，直接接管显示状态。
- 如果未运行，可点击“启动服务”拉起 `app_launcher.py`。
- 支持单账号登录、批量登录/刷新 Token、刷新状态、模型测试、查看请求日志。

### 3.3 打包

双击：

```bat
build_desktop_manager.bat
```

产物位置：

```text
dist\M365CopilotManager\M365CopilotManager.exe
```

打包脚本当前使用入口：

```text
gui_launcher_v2.py
```

---

## 4. 账号与 token 机制

### 4.1 账号配置

账号保存在 `accounts.json`：

```json
[
  {
    "username": "user@example.com",
    "password": "password",
    "totp_secret": ""
  }
]
```

说明：

- `username`：M365 账号。
- `password`：自动登录使用。
- `totp_secret`：如账号需要 TOTP，可填写密钥；没有则留空。

### 4.2 token 类型

项目最终需要的是 M365 Copilot WebSocket 可用的 Sydney token：

- audience 通常为：`https://substrate.office.com/sydney`
- 长度通常约 3300+ 字符
- 只有该类 token 能稳定用于 Copilot WebSocket

Playwright 登录过程中会捕获多个 token，系统会优先选择 Sydney token 或最长的可用 substrate token。

### 4.3 token 缓存

成功登录后会写入：

- `token_cache.json`
- `substrate_token.txt`

`token_cache.json` 会保存：

- `access_token`
- `expires_at`
- `token_type`
- `refresh_token`（如果捕获到）
- `source`

注意：这些都是敏感数据，不要公开分享。

---

## 5. 自动续期机制

当前运行机制如下：

```text
检查间隔：60 秒
续期阈值：5 分钟
失败冷却：120 秒
Playwright 登录：全局排队，一个账号一个账号执行
```

### 5.1 后台巡检

`auth.py` 中的 `AccountManager` 初始化后会启动常驻后台线程：

```text
token-auto-refresh
```

该线程每 60 秒检查所有账号：

```text
如果账号正在刷新 -> 跳过
如果账号处于失败冷却 -> 跳过
如果没有 token -> 触发续期
如果 token 已过期 -> 触发续期
如果 token 剩余时间 <= 300 秒 -> 触发续期
否则不处理
```

### 5.2 续期阈值

配置项在 `config.py`：

```python
TOKEN_PRE_REFRESH_MARGIN = 300
```

含义：token 剩余时间小于等于 300 秒（5 分钟）时自动续期。

建议：

- 账号数量少于 5 个：3~5 分钟都可以。
- 最多约 20 个账号：建议保持 5 分钟，避免集中到期时排队来不及。

### 5.3 检查间隔

当前默认 60 秒。代码支持通过配置覆盖：

```python
TOKEN_AUTO_REFRESH_INTERVAL = 60
```

如果 `config.py` 没有该项，则默认 60 秒。

资源占用说明：

- 平时只读内存、比较时间戳，CPU/内存占用可以忽略。
- 只有真正续期时才会启动 Chromium，资源占用主要发生在 Playwright 登录期间。

### 5.4 失败冷却

配置项：

```python
TOKEN_REFRESH_COOLDOWN = 120
```

含义：某账号登录/续期失败后，120 秒内不重复尝试，避免频繁打开浏览器或反复失败。

### 5.5 Playwright 登录排队

当前使用全局登录锁：

```text
同一时间只允许一个 Playwright Chromium 登录任务
多个账号同时需要续期时，会排队一个个处理
```

这样做的原因：

- 避免同时打开多个浏览器窗口。
- 避免多个登录流程互相抢资源。
- 对最多约 20 个账号更稳。

---

## 6. 请求调用机制

### 6.1 模型接口

```http
GET /v1/models
```

返回支持的模型列表。

### 6.2 聊天接口

```http
POST /v1/chat/completions
```

OpenAI 兼容格式，支持：

- `model`
- `messages`
- `stream`
- `tools`
- `tool_choice`

### 6.3 账号选择

当请求进入时，`auth.get_token()` 会：

1. 按轮询顺序检查账号。
2. 优先选择剩余时间充足的 token。
3. 如果 token 即将过期，会触发后台刷新。
4. 如果没有可用 token，会同步尝试刷新一个账号。

### 6.4 401 重试

如果 Copilot WebSocket 返回 401：

1. `server.py` 检测 401。
2. 调用 `auth.invalidate_token(username)` 移除旧 token。
3. 调用 `refresh_after_401(username)` 强制 Playwright 重新登录。
4. 使用新 token 重试请求。

---

## 7. 桌面管理器说明

当前推荐入口：

```bat
python gui_launcher_v2.py
```

主要区域：

- 顶部状态卡：接口服务、管理面板、账号池。
- 状态总览：显示账号、状态、令牌倒计时、调用、输入 Token、输出 Token、来源。
- 账号管理：查看账号列表、登录选中账号、删除账号。
- 模型测试：发送简单测试请求。
- 请求日志：查看和清空请求日志。
- 配置管理：调整部分配置。
- 运行日志：查看启动器和服务输出。

### 7.1 状态颜色

账号状态列：

- 绿色圆点：就绪
- 红色圆点：过期
- 灰色圆点：其他状态

### 7.2 登录选中账号

操作方式：

1. 在表格中点击一个账号行。
2. 点击“登录选中账号”。
3. 桌面管理器后台请求 `/refresh`。
4. UI 不会卡死；Playwright 登录在服务端执行，按钮会临时禁用。

---

## 8. 注意事项

### 8.1 敏感文件

以下文件包含账号或 token，禁止上传公开平台：

- `accounts.json`
- `token_cache.json`
- `substrate_token.txt`
- `logs/` 中可能包含请求摘要

### 8.2 不要手动乱改 token 缓存

`token_cache.json` 由程序自动维护。除非明确排障，不建议手动编辑。

### 8.3 Playwright 浏览器窗口

`config.py` 中：

```python
PLAYWRIGHT_HEADLESS = False
```

当前会显示浏览器窗口，方便观察自动登录流程。

如果改为 `True`：

- 登录窗口不可见。
- 失败时不容易观察原因。
- 对稳定环境可考虑开启。

### 8.4 端口占用

默认端口：

- API：8080
- Web 面板：7860

如果端口被占用：

- 桌面管理器会尝试识别已有实例。
- 不建议重复启动多个 `app_launcher.py`。

### 8.5 最多 20 个账号的建议

当前机制可支持最多约 20 个账号，但建议：

- 保持续期阈值 5 分钟。
- 保持 Playwright 单实例排队。
- 不要把巡检间隔调得太短。
- 如果大量账号同一时间过期，续期会排队完成，需要一些时间。

---

## 9. 清理说明

本次已清理：

- 历史 `.bak_*` 备份文件。
- 临时 GUI 调试日志。
- 历史启动测试日志。
- `__pycache__/`。

保留：

- 核心代码。
- 当前配置和账号/token 文件。
- `logs/` 请求日志目录。
- 当前正在运行服务占用的 `startup_autorefresh.*.log`。

如果服务停止后要继续清理，可删除：

```text
startup_autorefresh.out.log
startup_autorefresh.err.log
```

---

## 10. 常见问题

### Q1：为什么管理面板倒计时到期了才刷新？

现在不会只依赖请求触发续期。后台巡检每 60 秒检查一次，剩余 5 分钟内会自动续期。

### Q2：为什么续期时会打开浏览器？

Sydney token 需要通过真实 M365 Copilot 页面流程捕获，因此使用 Playwright Chromium 自动登录。

### Q3：为什么不同时续期多个账号？

为了稳定性和资源控制，当前强制一个浏览器登录任务一个账号，其他账号排队。

### Q4：桌面管理器无响应怎么办？

当前单账号/批量登录已改为后台线程请求，不应再卡 UI。如果仍无响应，优先查看：

- 是否有 Playwright 登录窗口卡住。
- `startup_autorefresh.err.log`
- `startup_autorefresh.out.log`

### Q5：如何确认自动续期是否工作？

查看启动日志中是否有：

```text
auto refresh watchdog started
auto refresh hit: <username>
后台刷新成功
```

或在桌面管理器中观察账号状态和令牌倒计时是否刷新。

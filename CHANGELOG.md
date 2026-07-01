# M365 Copilot 反代服务 — 修复总结

## 修复时间：2025年6月12日

---

## 问题 1：WebSocket 401 错误 — Token 类型错误

### 症状
`accounts.json` 中存储的 token 无法用于 WebSocket 连接，所有请求返回 `server rejected WebSocket connection: HTTP 401`。

### 根因
登录流程中捕获了错误的 token 类型：
- **Search token**（~2140 字符，audience: `substrate.office.com/search`）— 不能用于 WebSocket
- **Sydney token**（~3340+ 字符，audience: `substrate.office.com/sydney`）— 唯一能用于 WebSocket

原代码从 MSAL localStorage 提取 token，优先拿到的是 search token 而非 sydney token。

### 修复方案
在 `auth.py` 中新增 `_on_identity_response()` 方法，监听 Microsoft 身份端点（`login.microsoftonline.com`）的 OAuth2 响应，直接从 token 交换过程中捕获所有 access_token，然后通过 `max(captured_tokens, key=len)` 选择最长的 token（sydney 3300+ > search 2100+）。

### 修改文件
- `auth.py` — 新增 `_on_identity_response()` 方法、networkidle 等待策略、最长 token 选择逻辑

---

## 问题 2：自动 Token 续期功能缺失

### 症状
当缓存的 token 过期或失效时，程序直接返回错误，没有自动刷新 token 并重试。

### 修复方案
实现了完整的 401 自动重试机制：

1. **`invalidate_token(username)`** — 从缓存移除无效 token + 清除刷新冷却期
2. **`_is_401_error(error_str)`** — 检测 WebSocket 401 错误
3. **`get_token(force_relogin=True)`** — 跳过缓存和 MSAL，直接 Playwright 重新登录
4. **非流式重试** — server.py 中检测到 401 → invalidate → force_relogin → 重试
5. **流式重试** — 在 `_stream_chat` 的 `run_chat` 线程中同样支持 401 重试

### 完整重试流程（已验证）
```
无效 token → WebSocket 401 → 检测到 401
→ invalidate_token 移除缓存
→ get_token(force_relogin=True) 强制 Playwright 登录
→ 捕获新 sydney token (3342 字符)
→ 重试请求 → 成功 HTTP 200
```

### 修改文件
- `auth.py` — 新增 `invalidate_token()`、`get_token(force_relogin=True)` 参数
- `server.py` — 新增 `_is_401_error()`、非流式和流式双重重试逻辑

---

## 问题 3：KMSI "保持登录状态?" 弹窗无法自动处理

### 症状
Playwright 自动登录时，微软的 "保持登录状态?" (Keep Me Signed In) 弹窗出现后程序无法自动处理，需要人工点击。

### 根因（3 个子问题）

#### 3a. 密码 `fill()` 静默失败
Playwright 的 `fill()` 方法在微软登录页的密码字段上不可靠，字段显示为空（0 字符），导致登录无法提交。

**修复**：改用 `type(password, delay=50)` 逐字输入，`fill()` 仅作为回退。

#### 3b. KMSI 按钮 ID 与登录按钮冲突
`idSIButton9` 和 `idBtn_Back` 是微软登录页的**通用按钮 ID**（登录按钮、下一步按钮、KMSI 按钮共用）。仅靠按钮选择器判断会误点登录表单按钮（曾出现 24 次误点击）。

**修复**：先用 `page.inner_text('body')` 检查页面文本是否包含 "Stay signed in" 或 "保持登录"，确认是 KMSI 页面后再点按钮。

#### 3c. dismiss 任务生命周期不足
`_dismiss_stay_signed_in` 后台任务只在 `_auto_fill_login` 中运行约 12 秒就结束，但 KMSI 弹窗在密码提交后的"等待登录跳转"阶段（30 秒）才出现。

**修复**：
- 循环次数从 60 增加到 200（40 秒）
- 在等待登录跳转阶段启动第二个 dismiss 任务
- 改为持续监听模式（点一次后继续监听，支持多次 KMSI 弹窗）

### 修改文件
- `auth.py` — 重写 `_dismiss_stay_signed_in()`、修改密码填写逻辑、在等待跳转阶段添加第二个 dismiss 任务

---

## 项目清理

删除了 **83 个**无用文件，包括：
- 旧版 copilot 浏览器自动化脚本（copilot_pw2~9.py 等）
- 浏览器状态文件（browser_state*.json）
- 调试/测试脚本（test_*.py, pw_*.py, explore_*.py 等）
- 临时截图（*.png）
- 补丁文件（_patch*.py, _corrupt*.py 等）
- 日志文件（server_*.log）

保留的核心文件（15 个）：

| 文件 | 用途 |
|---|---|
| `app_launcher.py` | 启动入口 |
| `server.py` | Flask API 服务器 |
| `dashboard.py` | Gradio 管理面板 |
| `auth.py` | 认证管理器（登录 + token 管理） |
| `copilot_client.py` | SignalR WebSocket 客户端 |
| `config.py` | 全局配置 |
| `request_logger.py` | 请求日志 |
| `token_manager.py` | 旧版兼容 |
| `accounts.json` | 账号数据 |
| `token_cache.json` | Token 缓存 |
| `msal_cache.bin` | MSAL 缓存 |
| `requirements.txt` | 依赖 |
| `logs/` | 日志目录 |
| `screenshots/` | 截图目录 |

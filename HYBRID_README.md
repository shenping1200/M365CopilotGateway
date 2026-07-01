# M365 Hybrid Gateway

老大，这一版放在当前文件夹里，不破坏你原来的桌面管理器，只是在旁边新增一套 OpenAI-compatible 网关。

## 新增文件

- `hybrid_server.py`: 混合 API 服务入口。
- `hybrid/token_store.py`: 兼容读取 `token_cache.json`、`substrate_token.txt` 和环境变量 token。
- `hybrid/existing_client_bridge.py`: 自动桥接原来的 `copilot_client.py`。
- `adapters/openai_compat.py`: OpenAI / Responses / Anthropic 响应格式适配。
- `start_hybrid.bat`: 一键启动。
- `test_hybrid.ps1`: 本地测试请求。
- `vendor/m365-copilot-openai-proxy`: kuchris 项目源码，作为后续迁移参考。

## 启动

```bat
C:\Users\Administrator\Desktop\M365\start_hybrid.bat
```

启动后看：

```text
http://127.0.0.1:8000/health
http://127.0.0.1:8000/v1/models
```

第三方客户端配置：

```text
Base URL: http://127.0.0.1:8000/v1
API Key: dummy
Model: m365-copilot
Persistent Model: m365-copilot:persist
```

## 这一版的思路

1. 以你的 `new11111`/`M365` 为主，保留 GUI、dashboard、账号文件、token 缓存、日志和老服务。
2. 先新增标准 OpenAI API 层，让客户端能接进来。
3. `hybrid/existing_client_bridge.py` 会尝试识别并调用你原来的 `copilot_client.py`。
4. kuchris 项目先放在 `vendor`，下一轮再把它的 Substrate WebSocket、流式输出、token 自动刷新、持久会话逐步迁进来。

## 下一步

先跑 `start_hybrid.bat`。如果 `/health` 里 `client.ok` 是 `false`，把页面里的错误发给小龙，我就按你项目的真实函数签名把桥接层改成精确调用。

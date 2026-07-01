# Hybrid Feasibility Report

## 结论

可以结合。建议以本地 M365/new11111 为主项目，保留桌面 GUI、dashboard、账号/token 缓存和日志体系；把 kuchris 项目的 OpenAI-compatible API、Substrate WebSocket、token 自动刷新、persistent session 分阶段迁入。

## 本版已经完成

- 新增 `GET /health`。
- 新增 `GET /v1/models`。
- 新增 `POST /v1/chat/completions`。
- 新增 `POST /v1/responses`。
- 新增 `POST /anthropic/v1/messages`。
- 新增 token 状态检查和本地 token 文件兼容读取。
- 新增对原 `copilot_client.py` 的桥接层。
- 保留原项目，不做破坏性覆盖。

## copilot_client.py 关键结构

```text
class SubstrateClient:
def load_token() -> str | None:
```

## server.py 关键结构

```text
def _is_401_error(error_str: str) -> bool:
def _estimate_tokens(text: str) -> int:
def _summarize_param_schema(pname: str, pinfo: dict, required: set) -> str:
def _example_value_for_schema(pname: str, pinfo: dict):
def _build_example_tool_call(tools: list) -> str:
def build_tools_system_prompt(tools: list, tool_choice) -> str:
def extract_tool_calls(text: str, tools: list) -> list | None:
def _sanitize_tool_arguments(name: str, arguments: dict, tool_by_name: dict) -> dict:
def _make_tool_call(name: str, arguments: dict) -> dict:
@app.route('/v1/chat/completions', methods=['POST'])
def chat_completions():
def _stream_chat(client, user_message, system_prompt, history,
@app.route('/v1/models', methods=['GET'])
def list_models():
@app.route('/v1/accounts', methods=['GET'])
def list_accounts():
@app.route('/status', methods=['GET'])
def status():
@app.route('/refresh', methods=['POST'])
def refresh():
```

## 后续精修路线

1. 运行 `start_hybrid.bat`，查看 `/health`。
2. 如果 `client.ok=true`，直接测试 `/v1/chat/completions`。
3. 如果 `client.ok=false`，按错误信息把桥接层改成精确调用。
4. 第二阶段迁移 kuchris 的 Substrate WebSocket 流式 SSE、message mapping、token refresh、persistent session store。

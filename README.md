# M365CopilotGateway

M365CopilotGateway is a Windows desktop gateway for Microsoft 365 Copilot. It exposes an OpenAI-compatible local API so tools such as Hermes, Codex, and other AI clients can talk to M365 Copilot through one unified endpoint.

The project includes a desktop manager, account management dashboard, OpenAI-compatible API routes, model mapping, rate limiting, tool-call format compatibility, and tool alias normalization.

> This project is intended for personal research and local integration. Use it only with accounts and services you are allowed to access.

## Highlights

- One local endpoint: `http://127.0.0.1:8080/v1`
- Desktop manager with no command prompt window in the packaged build
- Dashboard on `http://127.0.0.1:7860`
- OpenAI-compatible routes:
  - `/v1/models`
  - `/v1/chat/completions`
  - `/v1/responses`
  - `/v1/messages`
- API key compatibility mode: any key can be accepted when `require_api_key` is disabled
- Seven exposed model IDs:
  - `copilot-auto`
  - `copilot-quick`
  - `copilot-thinking`
  - `gpt-5.5`
  - `gpt-5.5-thinking`
  - `gpt-5.2`
  - `gpt-5.2-thinking`
- Multi-account token management
- Request logging and dashboard log viewer
- Runtime config file for models, tool aliases, logging, and LAN access
- Crash supervision: the service is monitored and restarted by the launcher supervisor
- Windows desktop packaging with icon support

## Tool Calling

Tool calling is the most important compatibility layer in this gateway.

Many AI clients use different names and payload shapes for the same local tools. For example, one client may expose a terminal tool as `terminal`, another may call it `exec_command`, and another may use `shell` or `run_command`. M365CopilotGateway normalizes these names before sending the request to Copilot and then converts the response back into an OpenAI-compatible shape that clients can understand.

### Supported Tool Alias Mapping

The default aliases are configured in `m365_runtime_config.json`:

| Incoming tool name | Canonical tool |
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

This is why Hermes, Windows Codex, and macOS Codex can all connect to the same gateway while still using their own local tool names.

### Tool Call Flow

1. The client sends an OpenAI-style request containing `tools`.
2. The gateway reads and normalizes the tool definitions.
3. Tool names are mapped through `tool_aliases`.
4. The message format is converted for the M365 Copilot WebSocket flow.
5. Copilot returns either normal text or tool-call style output.
6. The gateway parses tool calls and returns them in the client-facing format.
7. The client executes the local tool on its own machine.
8. Tool results are sent back to the gateway and forwarded into the conversation.

The gateway does not execute the user's local terminal by itself. The client, such as Codex or Hermes, executes tools locally. This is important for LAN use: when a Mac connects to a Windows-hosted gateway, Mac terminal commands run on the Mac client if the Mac client exposes its local tool to the model.

## Quick Start From Release Package

1. Download the latest release package.
2. Extract `M365CopilotGateway.zip`.
3. Run `M365CopilotGateway.exe`.
4. Open the dashboard if needed: `http://127.0.0.1:7860`.
5. Configure your AI client:
   - Base URL: `http://127.0.0.1:8080/v1`
   - API key: any value, unless you enable strict API key mode
   - Model: for example `gpt-5.5-thinking`

For another computer on the same LAN, use the Windows host IP:

```text
http://<windows-host-ip>:8080/v1
```

## Local Development

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python app_launcher.py
```

Then open:

- API: `http://127.0.0.1:8080/v1`
- Dashboard: `http://127.0.0.1:7860`

## Build Desktop Package

```powershell
cmd /c build_desktop_manager.bat
```

The packaged app will be created at:

```text
dist\M365CopilotGateway\M365CopilotGateway.exe
```

The build script intentionally creates a blank `accounts.json` and excludes token/account cache files.

## Runtime Config

Main runtime config file:

```text
m365_runtime_config.json
```

Important sections:

- `models`: exposed model list
- `tool_aliases`: client tool-name normalization
- `security.lan_access`: LAN access switch
- `security.require_api_key`: strict API key switch
- `security.allowed_ips`: optional IP allowlist
- `logging.include_tools`: include tool metadata in logs

## Security Notes

Do not commit or share these files:

- `accounts.json`
- `token_cache.json`
- `substrate_token.txt`
- `msal_cache.bin`
- local logs
- account CSV or 2FA seed files

The repository `.gitignore` excludes those files by default. The packaged release includes only a blank `accounts.json`.

## Project Structure

| File | Purpose |
| --- | --- |
| `unified_server.py` | Main OpenAI-compatible gateway server |
| `copilot_client.py` | M365 Copilot WebSocket client |
| `auth.py` | Account and token manager |
| `dashboard.py` | Gradio dashboard |
| `gui_launcher_v2.py` | Windows desktop manager |
| `m365_supervisor.py` | Crash monitoring and auto restart |
| `runtime_config.py` | Runtime config loader and helpers |
| `request_logger.py` | Request and tool-call logging |
| `build_desktop_manager.bat` | PyInstaller package builder |
| `m365_runtime_config.json` | Models, aliases, logging, and security config |

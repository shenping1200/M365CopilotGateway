# Release Notes

## v1.0.0

Initial packaged release of M365CopilotGateway.

### Included

- Windows desktop manager with application icon
- Unified OpenAI-compatible endpoint on port `8080`
- Dashboard on port `7860`
- Seven model IDs exposed through `/v1/models`
- `/v1/chat/completions`, `/v1/responses`, and `/v1/messages` compatibility
- Hermes and Codex tool-call compatibility
- Tool alias normalization for terminal, desktop, and browser tools
- Runtime model/tool/security configuration
- Request logging and dashboard log view
- Rate limiting
- Crash supervision and automatic service restart
- Packaged release with blank `accounts.json`

### Security

The release package does not include local account credentials, token caches, MSAL caches, or local log files.

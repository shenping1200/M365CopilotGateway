"""Adapter that directly instantiates SubstrateClient and calls its API.

Precise bindings for the new11111 project''s copilot_client.SubstrateClient.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


class ExistingClientBridge:
    """Bridge to the existing new11111 SubstrateClient.

    The client is instantiated once with the token from the token store.
    chat() is mapped to the async SubstrateClient.chat() / chat_stream().
    """

    def __init__(self, root: Path):
        self.root = root
        self._client: Any = None
        self._error: Optional[str] = None
        self._token: Optional[str] = None

    def _load_token(self) -> Optional[str]:
        """Try reading the token from the same places SubstrateClient expects."""
        from hybrid.token_store import HybridTokenStore
        store = HybridTokenStore(self.root)
        return store.read_token()

    def status(self) -> Dict[str, Any]:
        try:
            client = self.client
            return {"ok": True, "client_type": "SubstrateClient", "methods": ["chat", "chat_stream"]}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @property
    def client(self) -> Any:
        if self._client is not None:
            return self._client
        if self._error:
            raise RuntimeError(self._error)

        try:
            import copilot_client  # type: ignore

            token = self._load_token()
            if token is None:
                raise RuntimeError(
                    "No M365 Copilot token found. "
                    "Please run the original desktop manager first to log in, "
                    "or place a valid token in substrate_token.txt / token_cache.json."
                )
            self._token = token

            # Also read a username from accounts.json (same logic as server.py)
            import json as _json
            _acct_path = self.root / "accounts.json"
            _username = None
            if _acct_path.exists():
                try:
                    _data = _json.loads(_acct_path.read_text(encoding="utf-8"))
                    if _data and isinstance(_data, list) and len(_data) > 0:
                        _username = _data[0].get("username")
                except Exception:
                    pass

            self._client = copilot_client.SubstrateClient(access_token=token, username=_username)
            return self._client
        except Exception:
            import traceback
            self._error = traceback.format_exc()
            raise RuntimeError(self._error)

    async def send(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        raw_body: Dict[str, Any],
    ) -> str:
        """Send messages to the Copilot backend and return the text reply.

        Uses SubstrateClient.chat() for the full response.
        """
        client = self.client
        from adapters.openai_compat import messages_to_prompt

        prompt = messages_to_prompt(messages)

        # Extract system prompt for SubstrateClient.chat()
        system_prompt: Optional[str] = None
        for msg in (messages or []):
            if msg.get("role") == "system" and msg.get("content"):
                system_prompt = str(msg.get("content"))
                break

        # model name is passed as-is; SubstrateClient maps it internally via MODEL_TONE_MAP

        try:
            if hasattr(client, "chat_stream") and raw_body.get("stream", False):
                # Stream path: collect chunks
                chunks: List[str] = []
                async for chunk in client.chat_stream(message=prompt):
                    if isinstance(chunk, str):
                        chunks.append(chunk)
                return "".join(chunks)

            # Non-stream path
            result = await client.chat(
                message=prompt,
                system_prompt=system_prompt,
                model=model,)
            if isinstance(result, dict):
                for key in ("content", "text", "reply", "message", "answer", "response"):
                    if key in result:
                        return str(result[key])
                return str(result)
            if isinstance(result, str):
                return result
            return str(result or "")
        except Exception as exc:
            raise RuntimeError(f"SubstrateClient error: {exc}") from exc



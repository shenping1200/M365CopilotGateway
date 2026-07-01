"""OpenAI wire-shape builders (completions, SSE chunks, conversation_id).

Migrated from sums001/Windows-Copilot-API (openai_format.py) and then extended
to support M365's multi-model naming.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional

MODEL_DEFAULT = "m365-copilot"
MODEL_PERSIST = "m365-copilot:persist"


def new_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex}"


def normalize_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: List[str] = []
        for item in content:
            if isinstance(item, dict):
                item_type = item.get("type")
                if item_type in {"text", "input_text", "output_text"}:
                    chunks.append(str(item.get("text", "")))
                elif "content" in item:
                    chunks.append(normalize_content(item.get("content")))
                elif "text" in item:
                    chunks.append(str(item.get("text", "")))
            else:
                chunks.append(str(item))
        return "\n".join(x for x in chunks if x)
    if isinstance(content, dict):
        if "text" in content:
            return str(content["text"])
        return json.dumps(content, ensure_ascii=False)
    return str(content)


def messages_to_prompt(messages: Iterable[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for message in messages or []:
        role = message.get("role", "user")
        text = normalize_content(message.get("content", ""))
        if text:
            lines.append(f"{role}: {text}")
    return "\n".join(lines).strip()


def sse_event(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def completion_response(
    text: str,
    model: str,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "id": new_id(),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "conversation_id": conversation_id,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text or ""},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def stream_chunk(
    cid: str,
    created: int,
    model: str,
    delta: dict,
    finish: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    chunk: Dict[str, Any] = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    if conversation_id is not None:
        chunk["conversation_id"] = conversation_id
    return chunk


def chat_completion_response(content: str, model: str = MODEL_DEFAULT) -> Dict[str, Any]:
    """Shortcut: build a full non-streaming response (backward compat)."""
    return completion_response(content, model)


def response_api_payload(content: str, model: str = MODEL_DEFAULT) -> Dict[str, Any]:
    now = int(time.time())
    return {
        "id": f"resp-m365-{now}",
        "object": "response",
        "created_at": now,
        "model": model,
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": content or ""}],
            }
        ],
    }


def anthropic_payload(content: str, model: str = MODEL_DEFAULT) -> Dict[str, Any]:
    return {
        "id": "msg_m365",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": content or ""}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }

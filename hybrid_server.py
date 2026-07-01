"""Hybrid OpenAI-compatible M365 Copilot gateway.

Integrates:
  - 7-model registry (from new11111/server.py)
  - Token-bucket rate limiter (from sums001/Windows-Copilot-API)
  - Clean OpenAI format builder (from sums001/Windows-Copilot-API)
  - conversation_id tracking (from Windows-Copilot-API)
"""
from __future__ import annotations

import json
import time as _time
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from adapters.openai_compat import (
    anthropic_payload,
    completion_response,
    new_id,
    response_api_payload,
    sse_event,
    stream_chunk,
)
from hybrid.existing_client_bridge import ExistingClientBridge
from hybrid.token_store import HybridTokenStore
from hybrid.model_registry import ModelRegistry
from hybrid.ratelimit import TokenBucket

APP_ROOT = Path(__file__).resolve().parent
app = FastAPI(title="M365 Hybrid Copilot Gateway", version="0.4.0")

# Core services
bridge = ExistingClientBridge(APP_ROOT)
token_store = HybridTokenStore(APP_ROOT)
model_registry = ModelRegistry(APP_ROOT)

# Rate limiter: 30 rpm / burst 5 — conservative for M365 Copilot backend.
# Set RATE_LIMIT_RPM=0 via env to disable.
import os
_rate_rpm = float(os.getenv("M365_RATE_LIMIT_RPM", "30"))
_burst = int(os.getenv("M365_RATE_LIMIT_BURST", "5"))
_rate_limiter = TokenBucket(_rate_rpm, _burst)


def _check_rate_limit() -> Optional[JSONResponse]:
    """Return a 429 response if rate limited, else None."""
    allowed, wait = _rate_limiter.try_acquire()
    if allowed:
        return None
    secs = max(1, round(wait))
    return JSONResponse(
        status_code=429,
        headers={"Retry-After": str(secs)},
        content={
            "error": {
                "message": f"Rate limit exceeded ({_rate_rpm:g} req/min). Retry in {secs}s.",
                "type": "rate_limit_error",
                "code": "rate_limit_exceeded",
            }
        },
    )


@app.get("/health")
def health() -> Dict[str, Any]:
    client_status = bridge.status()
    return {
        "ok": bool(client_status.get("ok")),
        "root": str(APP_ROOT),
        "client": client_status,
        "token": token_store.status(),
        "api": {
            "base_url": "http://0.0.0.0:8000/v1",
            "models": model_registry.ids(),
            "rate_limiter": {
                "enabled": _rate_limiter.enabled,
                "rpm": _rate_rpm,
                "burst": _burst,
            },
        },
    }


@app.get("/v1/models")
def list_models() -> Dict[str, Any]:
    return model_registry.openai_response()


# ---------------------------------------------------------------------------
# Streaming helpers
# ---------------------------------------------------------------------------

async def _stream_response(
    messages: List[Dict[str, Any]],
    model: str,
    raw_body: Dict[str, Any],
) -> AsyncIterator[str]:
    """Yield SSE chunks for a chat request, then DONE."""
    cid = new_id()
    created = int(_time.time())

    # Emit a role chunk first (standard OpenAI SSE convention)
    yield sse_event(stream_chunk(cid, created, model, {"role": "assistant"}))

    try:
        content = await bridge.send(messages, model, body)
    except Exception as exc:
        yield sse_event(
            stream_chunk(
                cid, created, model,
                {"content": f"\n[error: {exc}]"},
                finish="error",
            )
        )
        yield "data: [DONE]\n\n"
        return

    # Emit the full content in one chunk (for now; real SSE can be wired later)
    if content:
        yield sse_event(stream_chunk(cid, created, model, {"content": content}))

    yield sse_event(stream_chunk(cid, created, model, {}, finish="stop"))
    yield "data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    model = body.get("model", "copilot-auto")
    messages = body.get("messages") or []
    stream = bool(body.get("stream", False))

    if not isinstance(messages, list):
        raise HTTPException(status_code=400, detail="messages must be a list")

    # Rate limit check (fast path before touching upstream)
    limited = _check_rate_limit()
    if limited is not None:
        return limited

    if stream:
        return StreamingResponse(
            _stream_response(messages, model, body),
            media_type="text/event-stream",
        )

    try:
        content = await bridge.send(messages, model, body)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return JSONResponse(completion_response(content, model=model))


@app.post("/v1/responses")
async def responses(request: Request) -> JSONResponse:
    body = await request.json()
    model = body.get("model", "copilot-auto")
    input_value = body.get("input", "")
    messages: List[Dict[str, Any]]
    if isinstance(input_value, list):
        messages = input_value
    else:
        messages = [{"role": "user", "content": str(input_value)}]

    limited = _check_rate_limit()
    if limited is not None:
        return limited

    try:
        content = await bridge.send(messages, model, body)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return JSONResponse(response_api_payload(content, model=model))


@app.post("/anthropic/v1/messages")
async def anthropic_messages(request: Request) -> JSONResponse:
    body = await request.json()
    model = body.get("model", "copilot-auto")
    messages = body.get("messages") or []
    if body.get("system"):
        messages = [{"role": "system", "content": body["system"]}] + messages

    limited = _check_rate_limit()
    if limited is not None:
        return limited

    try:
        content = await bridge.send(messages, model, body)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return JSONResponse(anthropic_payload(content, model=model))


@app.get("/")
def root():
    return {
        "service": "M365 Hybrid Copilot Gateway",
        "version": "0.4.0",
        "endpoints": [
            "/v1/models",
            "/v1/chat/completions",
            "/v1/responses",
            "/anthropic/v1/messages",
            "/health",
        ],
    }


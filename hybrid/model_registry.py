"""Model registry for the M365 hybrid gateway.

Exposes the same 7 models from the original server.py plus hybrid aliases.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

DEFAULT_MODELS: List[Dict[str, Any]] = [
    {"id": "copilot-auto",       "object": "model", "owned_by": "microsoft", "name": "自动选择"},
    {"id": "copilot-quick",      "object": "model", "owned_by": "microsoft", "name": "快速答复"},
    {"id": "copilot-thinking",   "object": "model", "owned_by": "microsoft", "name": "深度思考"},
    {"id": "gpt-5.5",            "object": "model", "owned_by": "microsoft", "name": "GPT 5.5 快速响应"},
    {"id": "gpt-5.5-thinking",   "object": "model", "owned_by": "microsoft", "name": "GPT 5.5 深度思考"},
    {"id": "gpt-5.2",            "object": "model", "owned_by": "microsoft", "name": "GPT 5.2 快速响应"},
    {"id": "gpt-5.2-thinking",   "object": "model", "owned_by": "microsoft", "name": "GPT 5.2 深度思考"},
]


class ModelRegistry:
    def __init__(self, root: Path):
        self.root = root
        self.config_file = root / "hybrid_models.json"

    def models(self) -> List[Dict[str, Any]]:
        configured = self._read_configured_models()
        models = configured if configured else DEFAULT_MODELS
        return self._dedupe(models)

    def ids(self) -> List[str]:
        return [item["id"] for item in self.models()]

    def openai_response(self) -> Dict[str, Any]:
        return {"object": "list", "data": self.models()}

    def _read_configured_models(self) -> List[Dict[str, Any]]:
        if not self.config_file.exists():
            self._write_default_config()
            return DEFAULT_MODELS
        try:
            data = json.loads(self.config_file.read_text(encoding="utf-8"))
        except Exception:
            return DEFAULT_MODELS

        raw = data.get("models", data if isinstance(data, list) else [])
        models: List[Dict[str, Any]] = []
        for item in raw:
            if isinstance(item, str):
                models.append({"id": item, "object": "model", "owned_by": "microsoft", "name": item})
            elif isinstance(item, dict):
                model_id = str(item.get("id") or item.get("model") or item.get("name") or "").strip()
                if model_id:
                    models.append({
                        "id": model_id,
                        "object": item.get("object", "model"),
                        "owned_by": item.get("owned_by", "microsoft"),
                        "name": str(item.get("name") or model_id),
                    })
        return models

    def _write_default_config(self) -> None:
        payload = {"models": DEFAULT_MODELS}
        self.config_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _dedupe(self, models: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen = set()
        result: List[Dict[str, Any]] = []
        for item in models:
            model_id = item.get("id", "").strip()
            if not model_id or model_id in seen:
                continue
            seen.add(model_id)
            result.append(dict(item))
        return result

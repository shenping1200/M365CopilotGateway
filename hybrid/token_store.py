"""Token helpers for the M365 hybrid gateway.

This layer keeps compatibility with the existing new11111 files first, while
leaving room to migrate kuchris-style browser token refresh later.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


class HybridTokenStore:
    def __init__(self, root: Path):
        self.root = root
        self.token_file = root / "substrate_token.txt"
        self.cache_file = root / "token_cache.json"

    def read_token(self) -> Optional[str]:
        env_token = os.getenv("M365_ACCESS_TOKEN") or os.getenv("SUBSTRATE_TOKEN")
        if env_token:
            return env_token.strip()

        token_from_cache = self._read_cache()
        if token_from_cache:
            return token_from_cache

        if self.token_file.exists():
            value = self.token_file.read_text(encoding="utf-8", errors="ignore").strip()
            if value:
                return value
        return None

    def status(self) -> Dict[str, Any]:
        token = self.read_token()
        return {
            "has_token": bool(token),
            "token_preview": self._preview(token),
            "substrate_token_file": str(self.token_file),
            "substrate_token_file_exists": self.token_file.exists(),
            "token_cache_file": str(self.cache_file),
            "token_cache_file_exists": self.cache_file.exists(),
        }

    def _read_cache(self) -> Optional[str]:
        if not self.cache_file.exists():
            return None
        try:
            data = json.loads(self.cache_file.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            return None
        return self._find_token(data)

    def _find_token(self, value: Any) -> Optional[str]:
        if isinstance(value, dict):
            for key in ("access_token", "token", "substrate_token", "accessToken"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
            for child in value.values():
                found = self._find_token(child)
                if found:
                    return found
        if isinstance(value, list):
            for child in value:
                found = self._find_token(child)
                if found:
                    return found
        return None

    def _preview(self, token: Optional[str]) -> Optional[str]:
        if not token:
            return None
        if len(token) <= 18:
            return "***"
        return f"{token[:8]}...{token[-6:]}"

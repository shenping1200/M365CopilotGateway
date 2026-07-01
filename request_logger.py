"""Thread-safe request logger for M365 Copilot Gateway.

The logger keeps a small in-memory ring buffer for the desktop/dashboard UI and
also appends JSONL files by day. Entries deliberately avoid request bodies,
passwords, tokens, and tool arguments.
"""

from __future__ import annotations

import json
import os
import threading
from collections import deque
from datetime import datetime

import config


class RequestLogger:
    """Request log recorder."""

    def __init__(self, log_dir: str | None = None, max_entries: int | None = None):
        self._log_dir = log_dir or config.LOG_DIR
        self._max_entries = max_entries or config.MAX_LOG_ENTRIES
        self._lock = threading.Lock()
        self._buffer: deque[dict] = deque(maxlen=self._max_entries)
        os.makedirs(self._log_dir, exist_ok=True)

    def log_request(
        self,
        model: str,
        username: str | None,
        elapsed_ms: float,
        success: bool,
        error: str | None = None,
        request_summary: str = "",
        extra: dict | None = None,
    ) -> None:
        """Record one API request."""
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "model": model,
            "account": username or "-",
            "elapsed_ms": round(elapsed_ms, 1),
            "status": "ok" if success else "error",
            "error": error[:200] if error else "",
            "summary": request_summary[:200] if request_summary else "",
        }
        if extra:
            entry.update(extra)

        with self._lock:
            self._buffer.append(entry)

        try:
            self._write_to_file(entry)
        except Exception:
            pass

    def log_event(self, event: str, summary: str = "", extra: dict | None = None) -> None:
        """Record a lightweight diagnostic event."""
        payload = {"event": event}
        if extra:
            payload.update(extra)
        self.log_request(
            model=payload.get("model", "-"),
            username=payload.get("account", "-"),
            elapsed_ms=0,
            success=True,
            request_summary=summary,
            extra=payload,
        )

    def get_logs(self, filter_type: str = "all", limit: int = 100) -> list[dict]:
        """Return recent logs for the dashboard."""
        with self._lock:
            logs = list(self._buffer)

        if filter_type == "errors":
            logs = [item for item in logs if item.get("status") == "error"]
        elif filter_type == "today":
            today = datetime.now().strftime("%Y-%m-%d")
            logs = [item for item in logs if str(item.get("ts", "")).startswith(today)]
        elif filter_type == "tools":
            logs = [
                item for item in logs
                if item.get("tools_count") or item.get("response_kind") == "tool_calls" or item.get("local_tool_request")
            ]

        return logs[-limit:] if len(logs) > limit else logs

    def clear(self) -> None:
        """Clear only the in-memory buffer."""
        with self._lock:
            self._buffer.clear()

    def get_stats(self) -> dict:
        """Return in-memory log statistics."""
        with self._lock:
            total = len(self._buffer)
            errors = sum(1 for item in self._buffer if item.get("status") == "error")
            today = datetime.now().strftime("%Y-%m-%d")
            today_count = sum(1 for item in self._buffer if str(item.get("ts", "")).startswith(today))
            tool_requests = sum(1 for item in self._buffer if item.get("tools_count"))
            tool_calls = sum(1 for item in self._buffer if item.get("response_kind") == "tool_calls")
            return {
                "total": total,
                "errors": errors,
                "today": today_count,
                "tool_requests": tool_requests,
                "tool_calls": tool_calls,
            }

    def _write_to_file(self, entry: dict) -> None:
        """Append one entry to today's JSONL log file."""
        today = datetime.now().strftime("%Y-%m-%d")
        filepath = os.path.join(self._log_dir, f"requests_{today}.jsonl")
        with open(filepath, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


_logger: RequestLogger | None = None


def get_request_logger() -> RequestLogger:
    global _logger
    if _logger is None:
        _logger = RequestLogger()
    return _logger

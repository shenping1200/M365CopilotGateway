"""
request_logger.py — 请求日志记录器
线程安全的环形缓冲，按天写入 JSONL 文件
"""

import json
import os
import threading
import time
from datetime import datetime
from collections import deque

import config


class RequestLogger:
    """请求日志记录器"""

    def __init__(self, log_dir: str = None, max_entries: int = None):
        self._log_dir = log_dir or config.LOG_DIR
        self._max_entries = max_entries or config.MAX_LOG_ENTRIES
        self._lock = threading.Lock()
        self._buffer: deque[dict] = deque(maxlen=self._max_entries)
        os.makedirs(self._log_dir, exist_ok=True)

    def log_request(self, model: str, username: str, elapsed_ms: float,
                    success: bool, error: str = None, request_summary: str = "",
                    extra: dict = None):
        """记录一次 API 请求"""
        entry = {
            "ts": datetime.now().isoformat(timespec='seconds'),
            "model": model,
            "account": username or "-",
            "elapsed_ms": round(elapsed_ms, 1),
            "status": "ok" if success else "error",
            "error": error[:200] if error else "",
            "summary": request_summary[:150] if request_summary else "",
        }
        if extra:
            entry.update(extra)

        with self._lock:
            self._buffer.append(entry)

        # 写入文件（不阻塞主流程）
        try:
            self._write_to_file(entry)
        except Exception:
            pass

    def get_logs(self, filter_type: str = "all", limit: int = 100) -> list[dict]:
        """获取日志（供 Dashboard 表格显示）"""
        with self._lock:
            logs = list(self._buffer)

        if filter_type == "errors":
            logs = [l for l in logs if l['status'] == 'error']
        elif filter_type == "today":
            today = datetime.now().strftime('%Y-%m-%d')
            logs = [l for l in logs if l['ts'].startswith(today)]

        # 返回最新的 limit 条
        return logs[-limit:] if len(logs) > limit else logs

    def clear(self):
        """清空内存日志"""
        with self._lock:
            self._buffer.clear()

    def get_stats(self) -> dict:
        """日志统计"""
        with self._lock:
            total = len(self._buffer)
            errors = sum(1 for l in self._buffer if l['status'] == 'error')
            today = datetime.now().strftime('%Y-%m-%d')
            today_count = sum(1 for l in self._buffer if l['ts'].startswith(today))
            return {
                "total": total,
                "errors": errors,
                "today": today_count,
            }

    def _write_to_file(self, entry: dict):
        """追加写入 JSONL 文件（按天分文件）"""
        today = datetime.now().strftime('%Y-%m-%d')
        filename = f"requests_{today}.jsonl"
        filepath = os.path.join(self._log_dir, filename)
        with open(filepath, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')


# 全局实例
_logger: RequestLogger | None = None

def get_request_logger() -> RequestLogger:
    global _logger
    if _logger is None:
        _logger = RequestLogger()
    return _logger

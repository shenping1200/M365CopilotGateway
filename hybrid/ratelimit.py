"""Token-bucket rate limiter – migrated from sums001/Windows-Copilot-API.

A self-imposed ceiling that keeps automated callers from hammering your single
signed-in M365 Copilot account.  Orthogonal to any concurrency lock: this caps
*requests per minute*, while a lock caps how many run *at once*.
"""
from __future__ import annotations

import threading
import time as _time


class TokenBucket:
    """Classic token bucket. ``try_acquire`` is non-blocking and thread-safe.

    The bucket holds at most ``burst`` tokens and refills at ``rpm / 60`` tokens
    per second.  Each request spends one token.  When empty the request is refused
    and told how long to wait – short bursts are absorbed up to ``burst`` while
    the long-run average is held at ``rpm``.

    Set ``rpm <= 0`` to disable limiting entirely (every acquire succeeds).
    """

    def __init__(self, rpm: float, burst: int, *, monotonic=None):
        self.rpm = float(rpm)
        self.rate = self.rpm / 60.0
        self.capacity = max(1, int(burst))
        self._tokens = float(self.capacity)
        self._lock = threading.Lock()
        self._now = monotonic or _time.monotonic
        self._updated = self._now()

    @property
    def enabled(self) -> bool:
        return self.rpm > 0

    def _refill(self, now: float) -> None:
        elapsed = now - self._updated
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._updated = now

    def try_acquire(self) -> tuple[bool, float]:
        """Spend one token if available.

        Returns ``(allowed, retry_after_seconds)``.  When disabled always
        ``(True, 0.0)``.  When refused ``retry_after`` is the time until one
        token has accrued (always > 0).
        """
        if not self.enabled:
            return True, 0.0
        with self._lock:
            now = self._now()
            self._refill(now)
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True, 0.0
            deficit = 1.0 - self._tokens
            retry_after = deficit / self.rate if self.rate > 0 else 0.0
            return False, retry_after

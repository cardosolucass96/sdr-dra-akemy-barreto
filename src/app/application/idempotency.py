from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from time import monotonic
from typing import Protocol


class MessageIdempotencyStore(Protocol):
    def claim(self, key: str, *, ttl_seconds: int) -> bool: ...

    def release(self, key: str) -> None: ...


class InMemoryMessageIdempotencyStore:
    def __init__(self, *, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._expires_by_key: dict[str, float] = {}
        self._lock = Lock()

    def claim(self, key: str, *, ttl_seconds: int) -> bool:
        if ttl_seconds <= 0:
            return True

        now = self._clock()
        expires_at = now + ttl_seconds
        with self._lock:
            self._prune_expired_locked(now)
            if key in self._expires_by_key:
                return False

            self._expires_by_key[key] = expires_at
            return True

    def release(self, key: str) -> None:
        with self._lock:
            self._expires_by_key.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._expires_by_key.clear()

    def _prune_expired_locked(self, now: float) -> None:
        expired_keys = [
            key for key, expires_at in self._expires_by_key.items() if expires_at <= now
        ]
        for key in expired_keys:
            self._expires_by_key.pop(key, None)

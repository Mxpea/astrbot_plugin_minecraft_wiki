import time
from typing import Any


class WikiTTLCache:
    def __init__(self, ttl_seconds: int = 24 * 60 * 60):
        self.ttl_seconds = ttl_seconds
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any:
        item = self._store.get(key)
        if not item:
            return None

        expires_at, value = item
        if expires_at < time.time():
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        ttl = ttl_seconds if ttl_seconds is not None else self.ttl_seconds
        self._store[key] = (time.time() + ttl, value)

    def clear(self) -> None:
        self._store.clear()

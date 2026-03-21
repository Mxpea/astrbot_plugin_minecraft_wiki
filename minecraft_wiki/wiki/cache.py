import time
from typing import Any


class WikiTTLCache:
    def __init__(self, ttl_seconds: int = 24 * 60 * 60, max_size: int = 2048):
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self._store: dict[str, tuple[float, Any]] = {}

    def _cleanup_expired(self, now: float | None = None) -> None:
        ts = time.time() if now is None else now
        expired_keys = [key for key, (expires_at, _) in self._store.items() if expires_at < ts]
        for key in expired_keys:
            self._store.pop(key, None)

    def _evict_if_oversized(self) -> None:
        if self.max_size <= 0:
            return
        while len(self._store) > self.max_size:
            oldest_key = next(iter(self._store), None)
            if oldest_key is None:
                break
            self._store.pop(oldest_key, None)

    def get(self, key: str) -> Any:
        self._cleanup_expired()
        item = self._store.get(key)
        if not item:
            return None

        expires_at, value = item
        if expires_at < time.time():
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        self._cleanup_expired()
        ttl = ttl_seconds if ttl_seconds is not None else self.ttl_seconds
        self._store[key] = (time.time() + ttl, value)
        self._evict_if_oversized()

    def get_page_field(self, title: str, field: str) -> Any:
        page_cache = self.get(title)
        if not isinstance(page_cache, dict):
            return None
        return page_cache.get(field)

    def set_page_field(self, title: str, field: str, value: Any, ttl_seconds: int | None = None) -> None:
        page_cache = self.get(title)
        if not isinstance(page_cache, dict):
            page_cache = {}
        page_cache[field] = value
        self.set(title, page_cache, ttl_seconds=ttl_seconds)

    def clear(self) -> None:
        self._store.clear()

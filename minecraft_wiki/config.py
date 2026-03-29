from dataclasses import dataclass
from typing import Any, Mapping


def _safe_float(value: Any, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass
class MinecraftWikiConfig:
    base_url: str = "https://zh.minecraft.wiki/api.php"
    timeout_seconds: float = 12.0
    cache_ttl_seconds: int = 24 * 60 * 60
    max_return_chars: int = 3800
    max_full_page_chars: int = 16000
    default_search_limit: int = 5

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any] | Any | None) -> "MinecraftWikiConfig":
        if config is None:
            return cls()

        getter = getattr(config, "get", None)
        if not callable(getter):
            return cls()

        return cls(
            base_url=str(getter("base_url", cls.base_url) or cls.base_url),
            timeout_seconds=_safe_float(getter("timeout_seconds", cls.timeout_seconds), cls.timeout_seconds),
            cache_ttl_seconds=_safe_int(getter("cache_ttl_seconds", cls.cache_ttl_seconds), cls.cache_ttl_seconds),
            max_return_chars=_safe_int(getter("max_return_chars", cls.max_return_chars), cls.max_return_chars),
            max_full_page_chars=_safe_int(getter("max_full_page_chars", cls.max_full_page_chars), cls.max_full_page_chars),
            default_search_limit=_safe_int(getter("default_search_limit", cls.default_search_limit), cls.default_search_limit),
        )

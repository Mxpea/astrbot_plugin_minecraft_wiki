from dataclasses import dataclass
from typing import Any, Mapping


@dataclass
class MinecraftWikiConfig:
    base_url: str = "https://zh.minecraft.wiki/api.php"
    timeout_seconds: float = 12.0
    cache_ttl_seconds: int = 24 * 60 * 60
    max_return_chars: int = 3800
    default_search_limit: int = 5

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any] | Any | None) -> "MinecraftWikiConfig":
        if config is None:
            return cls()

        getter = getattr(config, "get", None)
        if not callable(getter):
            return cls()

        return cls(
            base_url=getter("base_url", cls.base_url),
            timeout_seconds=float(getter("timeout_seconds", cls.timeout_seconds)),
            cache_ttl_seconds=int(getter("cache_ttl_seconds", cls.cache_ttl_seconds)),
            max_return_chars=int(getter("max_return_chars", cls.max_return_chars)),
            default_search_limit=int(getter("default_search_limit", cls.default_search_limit)),
        )

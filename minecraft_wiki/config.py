from dataclasses import dataclass


@dataclass
class MinecraftWikiConfig:
    base_url: str = "https://zh.minecraft.wiki/api.php"
    timeout_seconds: float = 12.0
    cache_ttl_seconds: int = 24 * 60 * 60
    max_return_chars: int = 3800
    default_search_limit: int = 5

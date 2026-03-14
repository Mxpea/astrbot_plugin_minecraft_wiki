import re
from typing import Any

from ..wiki.api import MinecraftWikiAPI
from ..wiki.cache import WikiTTLCache
from ..wiki.parser import limit_text


def _extract_page(data: dict[str, Any]) -> dict[str, Any] | None:
    pages = data.get("query", {}).get("pages", {})
    if not isinstance(pages, dict) or not pages:
        return None
    return next(iter(pages.values()))


def _clean_extract(text: str, max_chars: int = 1800) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return limit_text(text, max_chars=max_chars)


async def get_wiki_summary(
    api: MinecraftWikiAPI,
    cache: WikiTTLCache,
    title: str,
    max_chars: int = 1800,
) -> dict[str, Any]:
    if not title or not title.strip():
        return {"error": "page not found"}

    clean_title = title.strip()
    cache_key = clean_title
    cached = cache.get_page_field(cache_key, "summary")
    if cached:
        return cached

    data = await api.get_page_summary(clean_title)
    if data.get("error"):
        return {"error": "page not found"}

    page = _extract_page(data)
    if not page or page.get("missing") is not None:
        return {"error": "page not found"}

    result = {
        "title": page.get("title", clean_title),
        "summary": _clean_extract(page.get("extract", ""), max_chars=max_chars),
    }
    cache.set_page_field(result["title"], "summary", result)
    return result

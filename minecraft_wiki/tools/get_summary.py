import re
from typing import Any

from ..wiki.api import MinecraftWikiAPI
from ..wiki.cache import WikiTTLCache
from ..wiki.parser import limit_text
from .search_page import search_wiki_page


def _extract_page(data: dict[str, Any]) -> dict[str, Any] | None:
    pages = data.get("query", {}).get("pages", {})
    if not isinstance(pages, dict) or not pages:
        return None
    return next(iter(pages.values()))


def _clean_extract(text: str, max_chars: int = 1800) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return limit_text(text, max_chars=max_chars)


def _normalize_summary_query(title: str) -> str:
    text = (title or "").strip()
    if not text:
        return ""
    text = text.strip(" \t\r\n\"'“”‘’。！？?!")
    text = re.sub(r"^(请问|问下|想问下|我想知道|请教一下|帮我查下)\s*", "", text)
    text = re.sub(r"(?:是什么|是啥|什么意思|介绍一下|是什么东西)$", "", text).strip()
    return text


def _pick_best_title(results: list[dict[str, Any]], query: str) -> str:
    if not results:
        return ""
    tokens = [tok for tok in re.split(r"\s+", query) if tok]

    def _score(row: dict[str, Any]) -> int:
        title = str(row.get("title", ""))
        snippet = str(row.get("snippet", ""))
        score = 0
        for token in tokens:
            if token and token in title:
                score += 3
            elif token and token in snippet:
                score += 1
        return score

    best = max(results, key=_score)
    return str(best.get("title", ""))


async def get_wiki_summary(
    api: MinecraftWikiAPI,
    cache: WikiTTLCache,
    title: str,
    max_chars: int = 1800,
) -> dict[str, Any]:
    if not title or not title.strip():
        return {"error": "page not found"}

    clean_title = _normalize_summary_query(title)
    if not clean_title:
        return {"error": "page not found"}

    cache_key = clean_title
    cached = cache.get_page_field(cache_key, "summary")
    if cached:
        return cached

    data = await api.get_page_summary(clean_title)
    if data.get("error"):
        data = {}

    page = _extract_page(data)
    if not page or page.get("missing") is not None:
        search_data = await search_wiki_page(api, clean_title, limit=5)
        titles = search_data.get("results", [])
        best_title = _pick_best_title(titles, clean_title)
        if not best_title:
            return {"error": "page not found"}

        data = await api.get_page_summary(best_title)
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

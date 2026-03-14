from typing import Any
import re

from ..wiki.api import MinecraftWikiAPI
from ..wiki.cache import WikiTTLCache
from ..wiki.parser import extract_recipe
from .search_page import search_wiki_page


def _pick_best_title(results: list[dict[str, str]], item: str) -> str:
    if not results:
        return ""
    kw = item.lower()
    for row in results:
        title = (row.get("title") or "").lower()
        if kw in title:
            return row.get("title", "")
    for row in results:
        title = (row.get("title") or "").lower()
        if not re.match(r"^(java版|基岩版|携带版|教育版|[a-z]+版指南/)\d", title) and "指南/" not in title:
            return row.get("title", "")
    return results[0].get("title", "")


async def _resolve_item_title(api: MinecraftWikiAPI, item: str) -> str:
    direct_titles = [item, item.replace(" ", ""), f"{item}（物品）"]
    for title in direct_titles:
        data = await api.get_page_wikitext(title)
        if not data.get("error"):
            return title
    return ""


def _resolve_redirect_title(raw_wikitext: str) -> str:
    match = re.search(r"#(?:redirect|重定向)\s*\[\[(.*?)\]\]", raw_wikitext or "", flags=re.I)
    return match.group(1).strip() if match else ""


async def get_crafting_recipe(
    api: MinecraftWikiAPI,
    cache: WikiTTLCache,
    item: str,
    max_chars: int = 1800,
) -> dict[str, Any]:
    name = (item or "").strip()
    if not name:
        return {"error": "page not found"}

    title = await _resolve_item_title(api, name)

    if not title:
        query_candidates = [f"{name} 合成", name, f"{name} 配方"]
        results = []
        for q in query_candidates:
            found = await search_wiki_page(api, q, limit=5)
            results = found.get("results", [])
            if results:
                break

        title = _pick_best_title(results, name)
    if not title:
        return {"error": "page not found"}

    cached = cache.get_page_field(title, "recipe")
    if cached and "recipe" in cached:
        return cached

    wikitext_data = await api.get_page_wikitext(title)
    if wikitext_data.get("error"):
        return {"error": "page not found"}

    raw_wikitext = wikitext_data.get("parse", {}).get("wikitext", "")
    if isinstance(raw_wikitext, dict):
        raw_wikitext = raw_wikitext.get("*", "")

    redirect_title = _resolve_redirect_title(raw_wikitext)
    if redirect_title:
        title = redirect_title
        wikitext_data = await api.get_page_wikitext(title)
        if wikitext_data.get("error"):
            return {"error": "page not found"}
        raw_wikitext = wikitext_data.get("parse", {}).get("wikitext", "")
        if isinstance(raw_wikitext, dict):
            raw_wikitext = raw_wikitext.get("*", "")

    recipe = extract_recipe(raw_wikitext, item_name=name, max_chars=max_chars)
    if not recipe:
        return {"error": "page not found"}

    result = {
        "item": name,
        "title": title,
        "recipe": recipe,
    }
    cache.set_page_field(title, "recipe", result)
    return result

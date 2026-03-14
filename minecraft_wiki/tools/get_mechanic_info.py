from typing import Any

from ..wiki.api import MinecraftWikiAPI
from ..wiki.cache import WikiTTLCache
from ..wiki.parser import extract_mechanic_description, extract_section, limit_text
from .get_summary import get_wiki_summary
from .search_page import search_wiki_page


MECHANIC_ALIASES = {
    "刷怪机制": ["生物生成", "生成", "刷怪"],
    "掉落机制": ["掉落", "掉落物"],
    "村民交易机制": ["交易", "村民"],
    "伤害机制": ["伤害"],
}


def _pick_best_title(results: list[dict[str, str]], mechanic: str) -> str:
    if not results:
        return ""
    kw = mechanic.lower()
    for row in results:
        title = (row.get("title") or "").lower()
        if kw in title:
            return row.get("title", "")
    return results[0].get("title", "")


async def get_mechanic_info(
    api: MinecraftWikiAPI,
    cache: WikiTTLCache,
    mechanic: str,
    max_chars: int = 1800,
) -> dict[str, Any]:
    kw = (mechanic or "").strip()
    if not kw:
        return {"error": "page not found"}

    query_candidates = [kw, f"{kw} 机制", f"Minecraft {kw}"]
    query_candidates.extend(MECHANIC_ALIASES.get(kw, []))
    results = []
    for q in query_candidates:
        found = await search_wiki_page(api, q, limit=5)
        results = found.get("results", [])
        if results:
            break

    title = _pick_best_title(results, kw)
    if not title:
        return {"error": "page not found"}

    cached = cache.get(title)
    if cached and "description" in cached:
        return cached

    summary_data = await get_wiki_summary(api, cache, title, max_chars=max_chars // 2)
    wikitext_data = await api.get_page_wikitext(title)
    if wikitext_data.get("error"):
        return {"error": "page not found"}

    raw_wikitext = wikitext_data.get("parse", {}).get("wikitext", "")
    if isinstance(raw_wikitext, dict):
        raw_wikitext = raw_wikitext.get("*", "")

    section = ""
    for sec in ["机制", "原理", "行为", "用途", "生成", "掉落", "交易", "刷怪"]:
        section = extract_section(raw_wikitext, sec, max_chars=max_chars // 2)
        if section:
            break

    description = "\n\n".join(
        part for part in [summary_data.get("summary", ""), section, extract_mechanic_description(raw_wikitext)] if part
    )

    result = {
        "mechanic": kw,
        "title": title,
        "description": limit_text(description, max_chars=max_chars),
    }
    cache.set(title, result)
    return result

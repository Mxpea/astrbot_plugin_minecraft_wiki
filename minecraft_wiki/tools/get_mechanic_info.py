from typing import Any
import re

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


def _normalize_mechanic_query(mechanic: str) -> str:
    text = (mechanic or "").strip()
    if not text:
        return ""
    text = text.strip(" \t\r\n\"'“”‘’。！？?!")
    text = re.sub(r"^(请问|问下|想问下|我想知道|请教一下)\s*", "", text)
    text = re.sub(r"(是什么|是啥|什么意思|介绍一下|怎么回事)$", "", text).strip()
    text = re.sub(r"\s+", " ", text)
    return text


async def _resolve_mechanic_title(api: MinecraftWikiAPI, mechanic: str) -> str:
    direct_titles = [mechanic, *MECHANIC_ALIASES.get(mechanic, [])]
    for title in direct_titles:
        data = await api.get_page_wikitext(title)
        if not data.get("error"):
            return title
    return ""


def _resolve_redirect_title(raw_wikitext: str) -> str:
    match = re.search(r"#(?:redirect|重定向)\s*\[\[(.*?)\]\]", raw_wikitext or "", flags=re.I)
    return match.group(1).strip() if match else ""


def _cleanup_mechanic_text(text: str, max_chars: int) -> str:
    lines = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        if "|reason=" in line:
            continue
        if line.count("|") >= 2:
            continue
        if line.count(";") >= 2 and "。" not in line:
            continue
        lines.append(line)
    return limit_text("\n\n".join(lines), max_chars=max_chars)


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
    kw = _normalize_mechanic_query(mechanic)
    if not kw:
        return {"error": "page not found"}

    title = await _resolve_mechanic_title(api, kw)

    if not title:
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

    cached = cache.get_page_field(title, "mechanic_info")
    if cached and "description" in cached:
        return cached

    summary_data = await get_wiki_summary(api, cache, title, max_chars=max_chars // 2)
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

    section = ""
    for sec in ["机制", "原理", "行为", "用途", "生成", "掉落", "交易", "刷怪"]:
        section = extract_section(raw_wikitext, sec, max_chars=max_chars // 2)
        if section:
            break

    description = "\n\n".join(
        part for part in [summary_data.get("summary", ""), section, extract_mechanic_description(raw_wikitext)] if part
    )
    description = _cleanup_mechanic_text(description, max_chars=max_chars)

    result = {
        "mechanic": kw,
        "title": title,
        "description": description,
    }
    cache.set_page_field(title, "mechanic_info", result)
    return result

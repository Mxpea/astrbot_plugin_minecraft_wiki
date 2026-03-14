from typing import Any
import re

from ..wiki.api import MinecraftWikiAPI
from ..wiki.cache import WikiTTLCache
from ..wiki.parser import extract_command_usage, extract_mechanic_description, extract_section
from .get_summary import get_wiki_summary
from .search_page import search_wiki_page


def _normalize_command(command: str) -> str:
    cmd = (command or "").strip().lower()
    cmd = cmd.replace("命令", "").replace("指令", "").strip()
    return cmd[1:] if cmd.startswith("/") else cmd


def _pick_best_title(results: list[dict[str, str]], command: str) -> str:
    if not results:
        return ""
    command = command.lower()
    for row in results:
        title = (row.get("title") or "").lower()
        if f"命令/{command}" in title or title == command:
            return row.get("title", "")
    for row in results:
        title = (row.get("title") or "").lower()
        if command in title and not re.match(r"^(java版|基岩版|携带版|教育版)\d", title):
            return row.get("title", "")
    return results[0].get("title", "")


async def _resolve_command_title(api: MinecraftWikiAPI, command: str) -> str:
    direct_titles = [f"命令/{command}", f"命令/{command.lower()}", command]
    for title in direct_titles:
        data = await api.get_page_wikitext(title)
        if not data.get("error"):
            return title
    return ""


async def get_command_info(
    api: MinecraftWikiAPI,
    cache: WikiTTLCache,
    command: str,
    max_chars: int = 1800,
) -> dict[str, Any]:
    cmd = _normalize_command(command)
    if not cmd:
        return {"error": "page not found"}

    title = await _resolve_command_title(api, cmd)
    if not title:
        query_candidates = [f"{cmd} 命令", f"命令/{cmd}", cmd]
        results = []
        for q in query_candidates:
            found = await search_wiki_page(api, q, limit=5)
            results = found.get("results", [])
            if results:
                break

        title = _pick_best_title(results, cmd)
    if not title:
        return {"error": "page not found"}

    cached = cache.get(title)
    if cached and all(key in cached for key in ["syntax", "description", "example"]):
        return cached

    wikitext_data = await api.get_page_wikitext(title)
    if wikitext_data.get("error"):
        return {"error": "page not found"}

    raw_wikitext = wikitext_data.get("parse", {}).get("wikitext", "")
    if isinstance(raw_wikitext, dict):
        raw_wikitext = raw_wikitext.get("*", "")

    syntax = extract_command_usage(raw_wikitext, max_chars=max_chars // 2)
    example = extract_section(raw_wikitext, "示例", max_chars=max_chars // 3)
    if not example:
        example = extract_section(raw_wikitext, "用法", max_chars=max_chars // 3)

    summary = await get_wiki_summary(api, cache, title, max_chars=max_chars // 2)
    description = extract_mechanic_description(raw_wikitext, max_chars=max_chars // 2)
    if not description:
        description = summary.get("summary", "") if "error" not in summary else ""

    result = {
        "command": cmd,
        "title": title,
        "syntax": syntax,
        "description": description,
        "example": example,
    }
    cache.set(title, result)
    return result

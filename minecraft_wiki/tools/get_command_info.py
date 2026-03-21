from typing import Any
import re

from ..wiki.api import MinecraftWikiAPI
from ..wiki.cache import WikiTTLCache
from ..wiki.parser import clean_wikitext, extract_command_usage, extract_mechanic_description, extract_section, resolve_redirect_title
from .get_summary import get_wiki_summary
from .search_page import search_wiki_page


VERSION_TITLE_RE = re.compile(r"^(java版|基岩版|携带版|教育版)\d")
NORMALIZE_PREFIX_RE = re.compile(r"^(请问|问下|想问下|我想知道|请教一下)\s*")
NORMALIZE_SUFFIX_RE = re.compile(r"(怎么用|如何使用|是什么|用法|语法|参数)$")
COMMAND_SLASH_RE = re.compile(r"/(\w+)")
COMMAND_WORD_RE = re.compile(r"\b([a-z_][a-z0-9_]*)\b")


def _normalize_command(command: str) -> str:
    cmd = (command or "").strip().lower()
    cmd = NORMALIZE_PREFIX_RE.sub("", cmd)
    cmd = cmd.replace("命令", "").replace("指令", "").strip()
    cmd = NORMALIZE_SUFFIX_RE.sub("", cmd).strip()
    slash = COMMAND_SLASH_RE.search(cmd)
    if slash:
        return slash.group(1)
    word = COMMAND_WORD_RE.search(cmd)
    if word:
        return word.group(1)
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
        if command in title and not VERSION_TITLE_RE.match(title):
            return row.get("title", "")
    return results[0].get("title", "")


async def _resolve_command_title(api: MinecraftWikiAPI, command: str) -> str:
    direct_titles = [f"命令/{command}", f"命令/{command.lower()}", command]
    for title in direct_titles:
        data = await api.get_page_wikitext(title)
        if not data.get("error"):
            return title
    return ""


def _is_noisy_markup(text: str) -> bool:
    if not text:
        return True
    noise_tokens = ("{{", "}}", "|", "#REDIRECT", "教程性内容", "name=", "oplevel=")
    return sum(token in text for token in noise_tokens) >= 2


def _fallback_command_lines(wikitext: str, command: str, max_chars: int) -> str:
    patterns = [
        re.compile(rf"/{re.escape(command)}\b.*", re.I),
        re.compile(rf"\b{re.escape(command)}\b.*", re.I),
    ]
    lines = []
    for raw_line in (wikitext or "").splitlines():
        line = raw_line.strip()
        if any(pattern.search(line) for pattern in patterns):
            cleaned = clean_wikitext(line, max_chars=240)
            if cleaned:
                lines.append(cleaned)
        if len(lines) >= 8:
            break
    return clean_wikitext("\n".join(lines), max_chars=max_chars)


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

    cached = cache.get_page_field(title, "command_info")
    if cached and all(key in cached for key in ["syntax", "description", "example"]):
        return cached

    wikitext_data = await api.get_page_wikitext(title)
    if wikitext_data.get("error"):
        return {"error": "page not found"}

    raw_wikitext = wikitext_data.get("parse", {}).get("wikitext", "")
    if isinstance(raw_wikitext, dict):
        raw_wikitext = raw_wikitext.get("*", "")

    redirect_title = resolve_redirect_title(raw_wikitext)
    if redirect_title:
        title = redirect_title
        wikitext_data = await api.get_page_wikitext(title)
        if wikitext_data.get("error"):
            return {"error": "page not found"}
        raw_wikitext = wikitext_data.get("parse", {}).get("wikitext", "")
        if isinstance(raw_wikitext, dict):
            raw_wikitext = raw_wikitext.get("*", "")

    syntax = extract_command_usage(raw_wikitext, max_chars=max_chars // 2)
    if _is_noisy_markup(syntax):
        syntax = _fallback_command_lines(raw_wikitext, cmd, max_chars=max_chars // 2)

    example = extract_section(raw_wikitext, "示例", max_chars=max_chars // 3)
    if not example:
        example = extract_section(raw_wikitext, "用法", max_chars=max_chars // 3)
    if _is_noisy_markup(example):
        example = _fallback_command_lines(raw_wikitext, cmd, max_chars=max_chars // 3)

    summary = await get_wiki_summary(api, cache, title, max_chars=max_chars // 2)
    description = extract_mechanic_description(raw_wikitext, max_chars=max_chars // 2)
    if not description or _is_noisy_markup(description):
        description = summary.get("summary", "") if "error" not in summary else ""

    result = {
        "command": cmd,
        "title": title,
        "syntax": syntax,
        "description": description,
        "example": example,
    }
    cache.set_page_field(title, "command_info", result)
    return result

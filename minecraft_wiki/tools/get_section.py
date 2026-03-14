from typing import Any
import re

from ..wiki.api import MinecraftWikiAPI
from ..wiki.parser import extract_section


def _choose_section_name(sections: list[dict[str, Any]], target: str) -> str:
    target_norm = "".join(target.lower().split())
    for section in sections:
        line = section.get("line", "")
        norm_line = "".join(str(line).lower().split())
        if target_norm in norm_line or norm_line in target_norm:
            return str(line)
    return target


def _resolve_redirect_title(raw_wikitext: str) -> str:
    match = re.search(r"#(?:redirect|重定向)\s*\[\[(.*?)\]\]", raw_wikitext or "", flags=re.I)
    return match.group(1).strip() if match else ""


async def get_wiki_section(
    api: MinecraftWikiAPI,
    title: str,
    section: str,
    max_chars: int = 1800,
) -> dict[str, Any]:
    if not title or not section:
        return {"error": "page not found"}

    sections_data = await api.get_page_sections(title.strip())
    if sections_data.get("error"):
        return {"error": "page not found"}
    sections = sections_data.get("parse", {}).get("sections", [])

    wikitext_data = await api.get_page_wikitext(title.strip())
    if wikitext_data.get("error"):
        return {"error": "page not found"}

    raw_wikitext = wikitext_data.get("parse", {}).get("wikitext", "")
    if isinstance(raw_wikitext, dict):
        raw_wikitext = raw_wikitext.get("*", "")

    redirect_title = _resolve_redirect_title(raw_wikitext)
    if redirect_title:
        title = redirect_title
        sections_data = await api.get_page_sections(title.strip())
        if sections_data.get("error"):
            return {"error": "page not found"}
        sections = sections_data.get("parse", {}).get("sections", [])
        wikitext_data = await api.get_page_wikitext(title.strip())
        if wikitext_data.get("error"):
            return {"error": "page not found"}
        raw_wikitext = wikitext_data.get("parse", {}).get("wikitext", "")
        if isinstance(raw_wikitext, dict):
            raw_wikitext = raw_wikitext.get("*", "")

    chosen = _choose_section_name(sections, section)
    content = extract_section(raw_wikitext, chosen, max_chars=max_chars)
    if not content:
        return {"error": "page not found"}

    return {
        "title": title.strip(),
        "section": chosen,
        "content": content,
    }

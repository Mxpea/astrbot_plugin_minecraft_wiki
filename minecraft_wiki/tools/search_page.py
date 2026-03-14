import re
from typing import Any

from ..wiki.api import MinecraftWikiAPI


def _strip_html(text: str) -> str:
    no_tag = re.sub(r"<[^>]+>", "", text or "")
    return re.sub(r"\s+", " ", no_tag).strip()


async def search_wiki_page(api: MinecraftWikiAPI, query: str, limit: int = 5) -> dict[str, Any]:
    if not query or not query.strip():
        return {"results": [], "error": "empty query"}

    data = await api.search_page(query=query.strip(), limit=limit)
    if data.get("error"):
        return {"results": [], "error": "search failed"}

    raw_results = data.get("query", {}).get("search", [])
    results = []
    for row in raw_results[:limit]:
        results.append(
            {
                "title": row.get("title", ""),
                "snippet": _strip_html(row.get("snippet", "")),
            }
        )
    return {"results": results}

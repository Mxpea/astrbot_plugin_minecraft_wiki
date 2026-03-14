from typing import Any

import httpx


class MinecraftWikiAPI:
    def __init__(self, base_url: str, timeout_seconds: float = 12.0):
        self.base_url = base_url
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        payload = {"format": "json", **params}
        resp = await self._client.get(self.base_url, params=payload)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and data.get("error"):
            return {"error": data["error"], "raw": data}
        return data

    async def search_page(self, query: str, limit: int = 5) -> dict[str, Any]:
        return await self._request(
            {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": limit,
            }
        )

    async def get_page_summary(self, title: str) -> dict[str, Any]:
        return await self._request(
            {
                "action": "query",
                "prop": "extracts",
                "exintro": 1,
                "titles": title,
            }
        )

    async def get_page_sections(self, title: str) -> dict[str, Any]:
        return await self._request(
            {
                "action": "parse",
                "page": title,
                "prop": "sections",
            }
        )

    async def get_page_wikitext(self, title: str) -> dict[str, Any]:
        return await self._request(
            {
                "action": "parse",
                "page": title,
                "prop": "wikitext",
            }
        )

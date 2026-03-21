import inspect
import json
import re
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

try:
    from astrbot.api.star import StarTools
except ImportError:
    StarTools = None  # type: ignore[assignment]

try:
    from .minecraft_wiki.config import MinecraftWikiConfig
    from .minecraft_wiki.tools import (
        get_command_info,
        get_crafting_recipe,
        get_mechanic_info,
        get_wiki_section,
        get_wiki_summary,
        search_wiki_page,
    )
    from .minecraft_wiki.wiki.api import MinecraftWikiAPI
    from .minecraft_wiki.wiki.cache import WikiTTLCache
    from .minecraft_wiki.wiki.parser import clean_wikitext
except ImportError:
    from minecraft_wiki.config import MinecraftWikiConfig
    from minecraft_wiki.tools import (
        get_command_info,
        get_crafting_recipe,
        get_mechanic_info,
        get_wiki_section,
        get_wiki_summary,
        search_wiki_page,
    )
    from minecraft_wiki.wiki.api import MinecraftWikiAPI
    from minecraft_wiki.wiki.cache import WikiTTLCache
    from minecraft_wiki.wiki.parser import clean_wikitext


MINECRAFT_WIKI_TOOL_PROMPT = """
回答 Minecraft 问题时，优先调用 ask_minecraft_wiki（统一工具入口）。

规则：
1. 默认只调用 ask_minecraft_wiki，避免在多个工具之间反复试错。
2. ask_minecraft_wiki 会自动判断是命令、机制、合成、章节还是摘要查询。
3. 回答使用中文，先给结论，再给关键细节和示例。
4. 如果工具返回 error=page not found，请明确告知未找到页面并给出可重试关键词。
""".strip()


def _to_json(payload: dict[str, Any], max_chars: int = 3800) -> str:
    text = json.dumps(payload, ensure_ascii=False)
    if len(text) <= max_chars:
        return text
    return json.dumps(
        {"error": "payload too large", "truncated": text[: max_chars - 3] + "..."},
        ensure_ascii=False,
    )


def _extract_command_from_text(text: str) -> str:
    cmd_match = re.search(r"/(\w+)", text or "")
    if cmd_match:
        return cmd_match.group(1)
    plain_match = re.search(r"\b(tp|execute|give|scoreboard|summon|setblock|fill|clone|effect|enchant)\b", text or "", re.I)
    if plain_match:
        return plain_match.group(1)
    return ""


def _infer_intent(question: str) -> str:
    text = (question or "").strip().lower()
    if not text:
        return "summary"
    if "/" in text or "命令" in text or "指令" in text:
        return "command"
    if any(k in text for k in ["合成", "配方", "怎么做", "怎么合"]):
        return "recipe"
    if any(k in text for k in ["机制", "原理", "刷怪", "掉落", "交易", "伤害"]):
        return "mechanic"
    if any(k in text for k in ["抗性", "硬度", "亮度", "属性", "数值", "伤害值"]):
        return "summary"
    if any(k in text for k in ["章节", "语法", "历史", "用途", "获得方式"]):
        return "section"
    return "summary"


def _extract_focus_keywords(query: str) -> list[str]:
    keys = []
    for key in ["爆炸抗性", "硬度", "亮度", "抗性", "伤害", "生命值", "掉落", "效率"]:
        if key in query:
            keys.append(key)
    if not keys:
        keys.append("属性")
    return keys


def _extract_fact_lines(text: str, focus_keywords: list[str], max_lines: int = 6) -> list[str]:
    if not text:
        return []
    lines = []
    seen = set()
    for raw in re.split(r"[\n。；]", text):
        line = re.sub(r"\s+", " ", raw).strip()
        if not line:
            continue
        if not any(k in line for k in focus_keywords):
            continue
        if not re.search(r"\d", line):
            continue
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)
        if len(lines) >= max_lines:
            break
    return lines


def _extract_primary_value(fact_lines: list[str], focus_keywords: list[str]) -> str:
    for line in fact_lines:
        if not any(k in line for k in focus_keywords):
            continue
        num = re.search(r"(?:[:：\s]|^)(\d[\d,\.]*)", line)
        if num:
            return num.group(1)
    return ""


@register("astrbot_plugin_minecraft_wiki", "Mxpea", "LLM 可调用的 Minecraft Wiki 中文查询插件", "v1.0.0")
class MinecraftWikiPlugin(Star):
    def __init__(self, context: Context, config: Any | None = None):
        super().__init__(context)
        if StarTools is not None:
            try:
                self.data_dir = StarTools.get_data_dir()
            except Exception:
                self.data_dir = None
        else:
            self.data_dir = None

        self.config = MinecraftWikiConfig.from_mapping(config)
        self.api = MinecraftWikiAPI(
            base_url=self.config.base_url,
            timeout_seconds=self.config.timeout_seconds,
        )
        self.cache = WikiTTLCache(ttl_seconds=self.config.cache_ttl_seconds)

    async def initialize(self):
        add_prompt = getattr(self.context, "add_system_prompt", None)
        if callable(add_prompt):
            prompt_ret = add_prompt(MINECRAFT_WIKI_TOOL_PROMPT)
            if inspect.isawaitable(prompt_ret):
                await prompt_ret
        logger.info("minecraft_wiki tools registered")

    async def _search_with_evidence(self, query: str, max_candidates: int = 3) -> dict[str, Any]:
        focus_keywords = _extract_focus_keywords(query)
        attempted_queries = [query]

        condensed = query
        for key in focus_keywords:
            condensed = condensed.replace(key, " ")
        condensed = re.sub(r"\s+", " ", condensed).strip()
        if condensed and condensed not in attempted_queries:
            attempted_queries.append(condensed)
        if condensed:
            for suffix in ["", " 属性", " 方块", " 数据"]:
                q = f"{condensed}{suffix}".strip()
                if q and q not in attempted_queries:
                    attempted_queries.append(q)

        pool: list[dict[str, Any]] = []
        seen_titles: set[str] = set()
        for q in attempted_queries[:5]:
            found = await search_wiki_page(self.api, q, limit=self.config.default_search_limit)
            rows = found.get("results", [])
            for row in rows:
                title = row.get("title", "")
                if not title or title in seen_titles:
                    continue
                seen_titles.add(title)
                pool.append({"title": title, "snippet": row.get("snippet", ""), "source_query": q})

        if not pool:
            return {
                "error": "page not found",
                "query": query,
                "attempted_queries": attempted_queries,
                "candidates": [],
            }

        tokens = [tok for tok in re.split(r"\s+", condensed) if tok]

        def _score(row: dict[str, Any]) -> int:
            title = row.get("title", "")
            snippet = row.get("snippet", "")
            text = f"{title} {snippet}"
            score = 0
            for tok in tokens:
                if tok in title:
                    score += 3
                elif tok in text:
                    score += 1
            for key in focus_keywords:
                if key in text:
                    score += 4
            return score

        ranked = sorted(pool, key=_score, reverse=True)[:max_candidates]
        candidates = []
        best_primary_value = ""

        for row in ranked:
            title = row["title"]
            summary_data = await get_wiki_summary(
                self.api,
                self.cache,
                title,
                max_chars=min(1400, self.config.max_return_chars),
            )
            summary_text = summary_data.get("summary", "") if isinstance(summary_data, dict) else ""

            wikitext_data = await self.api.get_page_wikitext(title)
            raw_wikitext = ""
            if not wikitext_data.get("error"):
                raw_wikitext = wikitext_data.get("parse", {}).get("wikitext", "")
                if isinstance(raw_wikitext, dict):
                    raw_wikitext = raw_wikitext.get("*", "")

            merged_text = "\n".join([row.get("snippet", ""), summary_text, clean_wikitext(raw_wikitext, max_chars=3200)])
            fact_lines = _extract_fact_lines(merged_text, focus_keywords)
            primary_value = _extract_primary_value(fact_lines, focus_keywords)
            if primary_value and not best_primary_value:
                best_primary_value = primary_value

            candidates.append(
                {
                    "title": title,
                    "source_query": row.get("source_query", ""),
                    "snippet": row.get("snippet", ""),
                    "summary": summary_text,
                    "fact_lines": fact_lines,
                    "primary_value": primary_value,
                }
            )

        return {
            "query": query,
            "focus_keywords": focus_keywords,
            "attempted_queries": attempted_queries,
            "primary_value": best_primary_value,
            "candidates": candidates,
        }

    @filter.llm_tool(name="ask_minecraft_wiki")
    async def llm_ask_minecraft_wiki(self, event: AstrMessageEvent, question: str, mode: str = "auto") -> str:
        '''统一查询 Minecraft Wiki（推荐给 LLM 的唯一工具入口）。

        Args:
            question(string): 用户问题或关键词，例如“黑曜石爆炸抗性是多少”“/tp 怎么用”
            mode(string): 查询模式，支持 auto/summary/command/mechanic/recipe/section/search
        '''
        query = (question or "").strip()
        if not query:
            return _to_json({"error": "empty query"}, max_chars=self.config.max_return_chars)

        resolved_mode = (mode or "auto").strip().lower()
        if resolved_mode == "auto":
            resolved_mode = _infer_intent(query)

        if resolved_mode == "search":
            result = await search_wiki_page(self.api, query)
            payload = {"intent": resolved_mode, "tool_used": "search_wiki_page", "result": result}
            return _to_json(payload, max_chars=self.config.max_return_chars)

        if resolved_mode == "command":
            command = _extract_command_from_text(query) or query
            result = await get_command_info(
                self.api,
                self.cache,
                command,
                max_chars=self.config.max_return_chars,
            )
            payload = {"intent": resolved_mode, "tool_used": "get_command_info", "result": result}
            return _to_json(payload, max_chars=self.config.max_return_chars)

        if resolved_mode == "mechanic":
            result = await get_mechanic_info(
                self.api,
                self.cache,
                query,
                max_chars=self.config.max_return_chars,
            )
            payload = {"intent": resolved_mode, "tool_used": "get_mechanic_info", "result": result}
            return _to_json(payload, max_chars=self.config.max_return_chars)

        if resolved_mode == "recipe":
            result = await get_crafting_recipe(
                self.api,
                self.cache,
                query,
                max_chars=self.config.max_return_chars,
            )
            payload = {"intent": resolved_mode, "tool_used": "get_crafting_recipe", "result": result}
            return _to_json(payload, max_chars=self.config.max_return_chars)

        if resolved_mode == "section":
            search_result = await search_wiki_page(self.api, query)
            top_title = ""
            rows = search_result.get("results", [])
            if rows:
                top_title = rows[0].get("title", "")

            if not top_title:
                payload = {
                    "intent": resolved_mode,
                    "tool_used": "search_wiki_page",
                    "result": {"error": "page not found"},
                }
                return _to_json(payload, max_chars=self.config.max_return_chars)

            section_guess = "机制"
            for sec in ["语法", "机制", "历史", "用途", "获得方式", "合成", "示例"]:
                if sec in query:
                    section_guess = sec
                    break

            result = await get_wiki_section(
                self.api,
                top_title,
                section_guess,
                max_chars=self.config.max_return_chars,
            )
            payload = {"intent": resolved_mode, "tool_used": "get_wiki_section", "result": result}
            return _to_json(payload, max_chars=self.config.max_return_chars)

        if any(k in query for k in ["抗性", "硬度", "亮度", "属性", "数值", "伤害"]):
            enriched = await self._search_with_evidence(query)
            payload = {
                "intent": "summary",
                "tool_used": "search_with_evidence",
                "result": enriched,
            }
            return _to_json(payload, max_chars=self.config.max_return_chars)

        result = await get_wiki_summary(
            self.api,
            self.cache,
            query,
            max_chars=self.config.max_return_chars,
        )
        if isinstance(result, dict) and result.get("error") == "page not found":
            enriched = await self._search_with_evidence(query)
            payload = {
                "intent": "summary",
                "tool_used": "search_with_evidence",
                "result": enriched,
            }
            return _to_json(payload, max_chars=self.config.max_return_chars)

        payload = {"intent": "summary", "tool_used": "get_wiki_summary", "result": result}
        return _to_json(payload, max_chars=self.config.max_return_chars)

    async def llm_search_wiki_page(self, event: AstrMessageEvent, query: str) -> str:
        '''搜索 Minecraft Wiki 页面。

        Args:
            query(string): 搜索词，优先中文名称或命令名
        '''
        result = await search_wiki_page(self.api, query)
        return _to_json(result, max_chars=self.config.max_return_chars)

    async def llm_get_wiki_summary(self, event: AstrMessageEvent, title: str) -> str:
        '''获取 Minecraft Wiki 页面的摘要。

        Args:
            title(string): Wiki 页面标题
        '''
        result = await get_wiki_summary(
            self.api,
            self.cache,
            title,
            max_chars=self.config.max_return_chars,
        )
        return _to_json(result, max_chars=self.config.max_return_chars)

    async def llm_get_wiki_section(
        self,
        event: AstrMessageEvent,
        title: str,
        section: str,
    ) -> str:
        '''获取 Minecraft Wiki 页面中的指定章节。

        Args:
            title(string): Wiki 页面标题
            section(string): 章节名称，例如 语法、机制、合成
        '''
        result = await get_wiki_section(
            self.api,
            title,
            section,
            max_chars=self.config.max_return_chars,
        )
        return _to_json(result, max_chars=self.config.max_return_chars)

    async def llm_get_command_info(self, event: AstrMessageEvent, command: str) -> str:
        '''获取 Minecraft 命令信息。

        Args:
            command(string): 命令名称，如 tp、execute、give、scoreboard
        '''
        result = await get_command_info(
            self.api,
            self.cache,
            command,
            max_chars=self.config.max_return_chars,
        )
        return _to_json(result, max_chars=self.config.max_return_chars)

    async def llm_get_mechanic_info(self, event: AstrMessageEvent, mechanic: str) -> str:
        '''获取 Minecraft 游戏机制说明。

        Args:
            mechanic(string): 机制关键词，如 红石中继器、刷怪机制、村民交易机制
        '''
        result = await get_mechanic_info(
            self.api,
            self.cache,
            mechanic,
            max_chars=self.config.max_return_chars,
        )
        return _to_json(result, max_chars=self.config.max_return_chars)

    async def llm_get_crafting_recipe(self, event: AstrMessageEvent, item: str) -> str:
        '''获取 Minecraft 物品合成配方。

        Args:
            item(string): 物品名称，如 钻石剑、附魔台
        '''
        result = await get_crafting_recipe(
            self.api,
            self.cache,
            item,
            max_chars=self.config.max_return_chars,
        )
        return _to_json(result, max_chars=self.config.max_return_chars)

    async def terminate(self):
        await self.api.close()


__all__ = ["MinecraftWikiPlugin"]

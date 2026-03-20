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
    if any(k in text for k in ["章节", "语法", "历史", "用途", "获得方式"]):
        return "section"
    return "summary"


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

        result = await get_wiki_summary(
            self.api,
            self.cache,
            query,
            max_chars=self.config.max_return_chars,
        )
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

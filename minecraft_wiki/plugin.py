import inspect
import json
from typing import Any

from pydantic import Field
from pydantic.dataclasses import dataclass

from astrbot.api import logger
from astrbot.api.star import Context, Star, register
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from .config import MinecraftWikiConfig
from .tools import (
    get_command_info,
    get_crafting_recipe,
    get_mechanic_info,
    get_wiki_section,
    get_wiki_summary,
    search_wiki_page,
)
from .wiki.api import MinecraftWikiAPI
from .wiki.cache import WikiTTLCache

MINECRAFT_WIKI_TOOL_PROMPT = """
回答 Minecraft 问题时优先调用 minecraft_wiki 插件工具。

工具调用策略：
1. 用户问命令用法（例如 /tp、/execute、/give、scoreboard）时，调用 get_command_info。
2. 用户问游戏机制（红石、掉落、刷怪、伤害、交易等）时，调用 get_mechanic_info。
3. 用户问物品用途或概念解释时，调用 get_wiki_summary。
4. 用户问合成方法时，调用 get_crafting_recipe。
5. 必要时先调用 search_wiki_page，再选择最匹配页面继续查询。
6. 回答使用中文，先给结论，再给关键细节和示例。
7. 如果工具返回 error=page not found，请明确告知未找到页面并给出可重试关键词。
""".strip()


def _to_json(payload: dict[str, Any], max_chars: int = 3800) -> str:
    text = json.dumps(payload, ensure_ascii=False)
    if len(text) <= max_chars:
        return text
    return json.dumps({"error": "payload too large", "truncated": text[: max_chars - 3] + "..."}, ensure_ascii=False)


@dataclass
class SearchWikiPageTool(FunctionTool[AstrAgentContext]):
    name: str = "search_wiki_page"
    description: str = "搜索 Minecraft Wiki 页面，返回标题与摘要片段。"
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索词，优先中文。"},
            },
            "required": ["query"],
        }
    )
    api: Any = None

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        result = await search_wiki_page(self.api, kwargs.get("query", ""))
        return _to_json(result)


@dataclass
class GetWikiSummaryTool(FunctionTool[AstrAgentContext]):
    name: str = "get_wiki_summary"
    description: str = "获取指定 Wiki 页面的摘要信息。"
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Wiki 页面标题。"},
            },
            "required": ["title"],
        }
    )
    api: Any = None
    cache: Any = None
    max_chars: int = 1800

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        result = await get_wiki_summary(
            api=self.api,
            cache=self.cache,
            title=kwargs.get("title", ""),
            max_chars=self.max_chars,
        )
        return _to_json(result)


@dataclass
class GetWikiSectionTool(FunctionTool[AstrAgentContext]):
    name: str = "get_wiki_section"
    description: str = "获取 Wiki 页面中的指定章节内容。"
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Wiki 页面标题。"},
                "section": {"type": "string", "description": "章节名称，例如 语法、机制、合成。"},
            },
            "required": ["title", "section"],
        }
    )
    api: Any = None
    max_chars: int = 1800

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        result = await get_wiki_section(
            api=self.api,
            title=kwargs.get("title", ""),
            section=kwargs.get("section", ""),
            max_chars=self.max_chars,
        )
        return _to_json(result)


@dataclass
class GetCommandInfoTool(FunctionTool[AstrAgentContext]):
    name: str = "get_command_info"
    description: str = "查询 Minecraft 命令信息，返回语法、参数说明和示例。"
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "命令名称，如 tp、execute、give、scoreboard。"},
            },
            "required": ["command"],
        }
    )
    api: Any = None
    cache: Any = None
    max_chars: int = 1800

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        result = await get_command_info(
            api=self.api,
            cache=self.cache,
            command=kwargs.get("command", ""),
            max_chars=self.max_chars,
        )
        return _to_json(result)


@dataclass
class GetMechanicInfoTool(FunctionTool[AstrAgentContext]):
    name: str = "get_mechanic_info"
    description: str = "查询 Minecraft 游戏机制信息，如红石、刷怪、掉落、交易、伤害等。"
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "mechanic": {"type": "string", "description": "机制关键词，如 红石中继器、刷怪机制。"},
            },
            "required": ["mechanic"],
        }
    )
    api: Any = None
    cache: Any = None
    max_chars: int = 1800

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        result = await get_mechanic_info(
            api=self.api,
            cache=self.cache,
            mechanic=kwargs.get("mechanic", ""),
            max_chars=self.max_chars,
        )
        return _to_json(result)


@dataclass
class GetCraftingRecipeTool(FunctionTool[AstrAgentContext]):
    name: str = "get_crafting_recipe"
    description: str = "查询 Minecraft 物品合成配方。"
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "item": {"type": "string", "description": "物品名称，如 钻石剑、附魔台。"},
            },
            "required": ["item"],
        }
    )
    api: Any = None
    cache: Any = None
    max_chars: int = 1800

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        result = await get_crafting_recipe(
            api=self.api,
            cache=self.cache,
            item=kwargs.get("item", ""),
            max_chars=self.max_chars,
        )
        return _to_json(result)


@register("minecraft_wiki", "Mxpea", "LLM 可调用的 Minecraft Wiki 中文查询插件", "1.0.0")
class MinecraftWikiPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.config = MinecraftWikiConfig()
        self.api = MinecraftWikiAPI(
            base_url=self.config.base_url,
            timeout_seconds=self.config.timeout_seconds,
        )
        self.cache = WikiTTLCache(ttl_seconds=self.config.cache_ttl_seconds)

    async def initialize(self):
        tools = [
            SearchWikiPageTool(api=self.api),
            GetWikiSummaryTool(api=self.api, cache=self.cache, max_chars=self.config.max_return_chars),
            GetWikiSectionTool(api=self.api, max_chars=self.config.max_return_chars),
            GetCommandInfoTool(api=self.api, cache=self.cache, max_chars=self.config.max_return_chars),
            GetMechanicInfoTool(api=self.api, cache=self.cache, max_chars=self.config.max_return_chars),
            GetCraftingRecipeTool(api=self.api, cache=self.cache, max_chars=self.config.max_return_chars),
        ]

        add_llm_tools = getattr(self.context, "add_llm_tools", None)
        if callable(add_llm_tools):
            add_llm_tools(*tools)
        else:
            tool_mgr = getattr(getattr(self.context, "provider_manager", None), "llm_tools", None)
            if tool_mgr and hasattr(tool_mgr, "func_list"):
                tool_mgr.func_list.extend(tools)

        add_prompt = getattr(self.context, "add_system_prompt", None)
        if callable(add_prompt):
            prompt_ret = add_prompt(MINECRAFT_WIKI_TOOL_PROMPT)
            if inspect.isawaitable(prompt_ret):
                await prompt_ret
        logger.info("minecraft_wiki tools registered")

    async def terminate(self):
        await self.api.close()

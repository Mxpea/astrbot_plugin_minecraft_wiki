import inspect
import importlib
import json
from typing import Any

try:
    _api = importlib.import_module("astrbot.api")
    _event = importlib.import_module("astrbot.api.event")
    _star = importlib.import_module("astrbot.api.star")

    logger = _api.logger
    filter = _event.filter
    AstrMessageEvent = _event.AstrMessageEvent
    MessageEventResult = _event.MessageEventResult
    Context = _star.Context
    Star = _star.Star
    register = _star.register
    StarTools = getattr(_star, "StarTools", None)
except ImportError:
    class _DummyLogger:
        def info(self, *_args, **_kwargs):
            return None

    class _DummyFilter:
        def llm_tool(self, name=None):
            def _decorator(func):
                return func

            return _decorator

    class _DummyContext:
        pass

    class _DummyStar:
        def __init__(self, context=None):
            self.context = context

    def _dummy_register(*_args, **_kwargs):
        def _decorator(cls):
            return cls

        return _decorator

    logger = _DummyLogger()
    filter = _DummyFilter()
    AstrMessageEvent = Any
    MessageEventResult = Any
    Context = _DummyContext
    Star = _DummyStar
    register = _dummy_register
    StarTools = None

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
    return json.dumps(
        {"error": "payload too large", "truncated": text[: max_chars - 3] + "..."},
        ensure_ascii=False,
    )


@register("astrbot_plugin_minecraft_wiki", "Mxpea", "LLM 可调用的 Minecraft Wiki 中文查询插件", "v1.0.0")
class MinecraftWikiPlugin(Star):
    def __init__(self, context: Any, config: Any | None = None):
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

    @filter.llm_tool(name="search_wiki_page")
    async def llm_search_wiki_page(self, event: Any, query: str) -> Any:
        '''搜索 Minecraft Wiki 页面。

        Args:
            query(string): 搜索词，优先中文名称或命令名
        '''
        result = await search_wiki_page(self.api, query)
        yield event.plain_result(_to_json(result, max_chars=self.config.max_return_chars))

    @filter.llm_tool(name="get_wiki_summary")
    async def llm_get_wiki_summary(self, event: Any, title: str) -> Any:
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
        yield event.plain_result(_to_json(result, max_chars=self.config.max_return_chars))

    @filter.llm_tool(name="get_wiki_section")
    async def llm_get_wiki_section(
        self,
        event: Any,
        title: str,
        section: str,
    ) -> Any:
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
        yield event.plain_result(_to_json(result, max_chars=self.config.max_return_chars))

    @filter.llm_tool(name="get_command_info")
    async def llm_get_command_info(self, event: Any, command: str) -> Any:
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
        yield event.plain_result(_to_json(result, max_chars=self.config.max_return_chars))

    @filter.llm_tool(name="get_mechanic_info")
    async def llm_get_mechanic_info(self, event: Any, mechanic: str) -> Any:
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
        yield event.plain_result(_to_json(result, max_chars=self.config.max_return_chars))

    @filter.llm_tool(name="get_crafting_recipe")
    async def llm_get_crafting_recipe(self, event: Any, item: str) -> Any:
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
        yield event.plain_result(_to_json(result, max_chars=self.config.max_return_chars))

    async def terminate(self):
        await self.api.close()


__all__ = ["MinecraftWikiPlugin"]

import inspect
import json
import re
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

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
    from .minecraft_wiki.wiki.parser import clean_wikitext, resolve_redirect_title
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
    from minecraft_wiki.wiki.parser import clean_wikitext, resolve_redirect_title


MINECRAFT_WIKI_TOOL_PROMPT = """
回答 Minecraft 问题时，优先调用 ask_minecraft_wiki（统一工具入口）。

规则：
1. 默认只调用 ask_minecraft_wiki，避免在多个工具之间反复试错。
2. ask_minecraft_wiki 会自动判断是命令、机制、合成、章节还是摘要查询；当需要完整页面上下文时可使用 full_page。
3. 回答使用中文，先给结论，再给关键细节和示例。
4. 如果工具返回 error=page not found，请明确告知未找到页面并给出可重试关键词。
""".strip()


TERM_ALIASES = {
    "predicate": "谓词",
    "predicates": "谓词",
    "datapack": "数据包",
    "data pack": "数据包",
    "function": "函数",
    "mcfunction": "函数",
    "loot table": "战利品表",
}

STOPWORDS = {
    "请问",
    "问下",
    "想问下",
    "我想知道",
    "请教一下",
    "帮我查下",
    "是什么",
    "是啥",
    "什么意思",
    "介绍一下",
    "怎么",
    "如何",
    "用法",
    "功能",
    "关于",
    "我的世界",
    "minecraft",
    "版本",
    "总结",
    "更新",
    "内容",
    "改动",
}

BROAD_TITLES = {
    "数据包",
    "命令",
    "方块",
    "物品",
    "生物",
    "历史",
    "教程",
}

RE_COMMAND_SLASH = re.compile(r"/(\w+)")
RE_COMMAND_PLAIN = re.compile(r"\b(tp|execute|give|scoreboard|summon|setblock|fill|clone|effect|enchant)\b", re.I)
RE_POLITE_PREFIX = re.compile(r"^(请问|问下|想问下|我想知道|请教一下|帮我查下)\s*")
RE_SECTION_QUERY = re.compile(r"(.+?)的(.+?)(?:是|是什么|有哪些)?$")
RE_TOKENIZE = re.compile(r"[a-zA-Z_]+|[\u4e00-\u9fff]{2,}")
RE_FACT_SPLIT = re.compile(r"[\n。；]")
RE_MULTI_SPACE = re.compile(r"\s+")
RE_HAS_DIGIT = re.compile(r"\d")
RE_PRIMARY_VALUE = re.compile(r"(?:[:：\s]|^)(\d[\d,\.]*)")
RE_VERSION_TITLE = re.compile(r"^(java版|基岩版)\d|^\d+w\d+[a-z]?", re.I)
RE_VERSION_QUERY = re.compile(
    r"(?<![A-Za-z0-9])\d+w\d+[a-z]?(?![A-Za-z0-9])|(?:java版|基岩版)\s*\d+(?:\.\d+){0,2}(?:[-\s]*(?:pre|rc)\d+)?|(?<![A-Za-z0-9])\d+\.\d+(?:\.\d+)?(?:[-\s]*(?:pre|rc)\d+)?(?![A-Za-z0-9])",
    re.I,
)
RE_PRE_RELEASE_TITLE = re.compile(r"(?:-rc\d*|-pre\d*|预发布|发布候选)", re.I)
RE_DIRECT_TITLE = re.compile(r"^(?:title|页面|page)\s*[:：]\s*(.+)$", re.I)

VERSION_HINT_KEYWORDS = {
    "版本",
    "更新",
    "改动",
    "新增",
    "修复",
    "快照",
    "预发布",
    "总结",
    "日志",
}


def _to_json(payload: dict[str, Any], max_chars: int = 3800) -> str:
    text = json.dumps(payload, ensure_ascii=False)
    if len(text) <= max_chars:
        return text
    return json.dumps(
        {"error": "payload too large", "truncated": text[: max_chars - 3] + "..."},
        ensure_ascii=False,
    )


def _extract_command_from_text(text: str) -> str:
    cmd_match = RE_COMMAND_SLASH.search(text or "")
    if cmd_match:
        return cmd_match.group(1)
    plain_match = RE_COMMAND_PLAIN.search(text or "")
    if plain_match:
        return plain_match.group(1)
    return ""


def _normalize_question_text(text: str) -> str:
    query = (text or "").strip()
    if not query:
        return ""
    query = query.strip(" \t\r\n\"'“”‘’。！？?!")
    query = RE_POLITE_PREFIX.sub("", query)
    return query


def _extract_section_query(query: str) -> tuple[str, str]:
    section_guess = "机制"
    for sec in ["语法", "机制", "历史", "用途", "获得方式", "合成", "示例", "掉落", "生成", "属性"]:
        if sec in query:
            section_guess = sec
            break

    title_query = query
    match = RE_SECTION_QUERY.search(query)
    if match:
        title_query = match.group(1).strip()
        right = match.group(2).strip()
        for sec in ["语法", "机制", "历史", "用途", "获得方式", "合成", "示例", "掉落", "生成", "属性"]:
            if sec in right:
                section_guess = sec
                break
    return title_query, section_guess


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
    normalized = (query or "").lower()
    for src, dst in TERM_ALIASES.items():
        normalized = normalized.replace(src, dst)

    property_keys = []
    for key in ["爆炸抗性", "硬度", "亮度", "抗性", "伤害", "生命值", "掉落", "效率"]:
        if key in normalized:
            property_keys.append(key)
    if property_keys:
        return property_keys

    tokens = RE_TOKENIZE.findall(normalized)
    keys = []
    for token in tokens:
        token = token.strip()
        if not token or token in STOPWORDS:
            continue
        if token in TERM_ALIASES:
            token = TERM_ALIASES[token]
        if token not in keys:
            keys.append(token)
        if len(keys) >= 4:
            break
    if not keys:
        keys.append(normalized.strip() or "minecraft")
    return keys


def _extract_fact_lines(
    text: str,
    focus_keywords: list[str],
    max_lines: int = 6,
    require_digit: bool = False,
) -> list[str]:
    if not text:
        return []
    lines = []
    seen = set()
    for raw in RE_FACT_SPLIT.split(text):
        line = RE_MULTI_SPACE.sub(" ", raw).strip()
        if not line:
            continue
        if not any(k in line for k in focus_keywords):
            continue
        if require_digit and not RE_HAS_DIGIT.search(line):
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
        num = RE_PRIMARY_VALUE.search(line)
        if num:
            return num.group(1)
    return ""


def _normalize_query_aliases(query: str) -> str:
    text = (query or "").lower()
    for src, dst in TERM_ALIASES.items():
        text = text.replace(src, dst)
    return text


def _is_version_like_title(title: str) -> bool:
    t = (title or "").strip()
    return bool(RE_VERSION_TITLE.match(t))


def _extract_version_terms(query: str) -> list[str]:
    terms = []
    for match in RE_VERSION_QUERY.findall(query or ""):
        value = match.strip()
        if value and value not in terms:
            terms.append(value)
    return terms


def _is_version_summary_query(query: str) -> bool:
    text = (query or "").lower()
    has_version = bool(_extract_version_terms(text)) or "快照" in text
    has_hint = any(k in text for k in VERSION_HINT_KEYWORDS)
    return has_version and has_hint


@register("astrbot_plugin_minecraft_wiki", "Mxpea", "LLM 可调用的 Minecraft Wiki 中文查询插件", "v1.0.0")
class MinecraftWikiPlugin(Star):
    def __init__(self, context: Context, config: Any | None = None):
        super().__init__(context)
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
        normalized_query = _normalize_query_aliases(query)
        focus_keywords = _extract_focus_keywords(normalized_query)
        version_mode = _is_version_summary_query(normalized_query)
        version_terms = _extract_version_terms(normalized_query)

        attempted_queries = [query]
        if normalized_query and normalized_query not in attempted_queries:
            attempted_queries.append(normalized_query)

        if version_mode:
            for ver in version_terms[:2]:
                if ver not in attempted_queries:
                    attempted_queries.append(ver)
                if ver.startswith("java版") or ver.startswith("基岩版"):
                    for suffix in ["更新", "版本", "改动"]:
                        q = f"{ver} {suffix}"
                        if q not in attempted_queries:
                            attempted_queries.append(q)
                elif "w" in ver.lower():
                    for q in [f"{ver} 快照", f"java版{ver}"]:
                        if q not in attempted_queries:
                            attempted_queries.append(q)
                else:
                    for q in [f"Java版{ver}", f"Java版{ver} 更新", f"基岩版{ver}"]:
                        if q not in attempted_queries:
                            attempted_queries.append(q)

        base_terms = [k for k in focus_keywords if k not in STOPWORDS]
        if base_terms:
            joined = " ".join(base_terms[:3])
            if joined and joined not in attempted_queries:
                attempted_queries.append(joined)
            for term in base_terms[:3]:
                if term and term not in attempted_queries:
                    attempted_queries.append(term)
        if len(base_terms) >= 2:
            pair = f"{base_terms[0]} {base_terms[1]}"
            if pair not in attempted_queries:
                attempted_queries.append(pair)

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

        tokens = base_terms or [tok for tok in re.split(r"\s+", normalized_query) if tok]
        if version_mode:
            for ver in version_terms:
                if ver not in tokens:
                    tokens.append(ver)

        def _score(row: dict[str, Any]) -> int:
            title = row.get("title", "")
            snippet = row.get("snippet", "")
            text = f"{title} {snippet}"
            score = 0
            if _is_version_like_title(title) and not version_mode:
                score -= 4
            if _is_version_like_title(title) and version_mode:
                score += 6
            if len(focus_keywords) >= 2 and title in BROAD_TITLES:
                score -= 2
            if version_mode and title in BROAD_TITLES:
                score -= 3
            if version_mode:
                plain_query = normalized_query.replace(" ", "")
                plain_title = title.replace(" ", "")
                for ver in version_terms:
                    v = ver.replace(" ", "")
                    if not v:
                        continue
                    if plain_title == f"java版{v}" or plain_title == f"基岩版{v}" or plain_title == v:
                        score += 8
                    elif v in plain_title:
                        score += 3
                if ("版本" in normalized_query or "总结" in normalized_query or "更新内容" in normalized_query) and RE_PRE_RELEASE_TITLE.search(title):
                    score -= 2
            for tok in tokens + focus_keywords:
                if tok in title:
                    score += 3
                elif tok in text:
                    score += 1
            for key in focus_keywords:
                if key in title:
                    score += 4
                elif key in text:
                    score += 2
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
            require_digit = any(k in ["爆炸抗性", "硬度", "亮度", "抗性", "伤害", "生命值", "效率"] for k in focus_keywords)
            fact_lines = _extract_fact_lines(merged_text, focus_keywords, require_digit=require_digit)
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

    async def _get_full_page(self, query: str) -> dict[str, Any]:
        direct = RE_DIRECT_TITLE.match(query or "")
        if direct:
            title = direct.group(1).strip()
            candidates = []
            attempted_queries = [query]
        else:
            enriched = await self._search_with_evidence(query, max_candidates=5)
            candidates = enriched.get("candidates", []) if isinstance(enriched, dict) else []
            if not candidates:
                return {
                    "error": "page not found",
                    "query": query,
                    "attempted_queries": enriched.get("attempted_queries", []) if isinstance(enriched, dict) else [],
                }
            title = str(candidates[0].get("title", "")).strip()
            attempted_queries = enriched.get("attempted_queries", []) if isinstance(enriched, dict) else []

        if not title:
            return {"error": "page not found", "query": query}

        wikitext_data = await self.api.get_page_wikitext(title)
        if wikitext_data.get("error"):
            return {"error": "page not found", "query": query, "title": title}

        raw_wikitext = wikitext_data.get("parse", {}).get("wikitext", "")
        if isinstance(raw_wikitext, dict):
            raw_wikitext = raw_wikitext.get("*", "")

        redirect_title = resolve_redirect_title(raw_wikitext)
        if redirect_title:
            title = redirect_title
            wikitext_data = await self.api.get_page_wikitext(title)
            if wikitext_data.get("error"):
                return {"error": "page not found", "query": query, "title": title}
            raw_wikitext = wikitext_data.get("parse", {}).get("wikitext", "")
            if isinstance(raw_wikitext, dict):
                raw_wikitext = raw_wikitext.get("*", "")

        sections_data = await self.api.get_page_sections(title)
        sections = []
        if not sections_data.get("error"):
            raw_sections = sections_data.get("parse", {}).get("sections", [])
            for sec in raw_sections[:80]:
                line = str(sec.get("line", "")).strip()
                if line:
                    sections.append(line)

        full_text = clean_wikitext(raw_wikitext, max_chars=self.config.max_full_page_chars)
        is_truncated = full_text.endswith("...") and len(full_text) >= max(8, self.config.max_full_page_chars - 10)
        return {
            "query": query,
            "title": title,
            "attempted_queries": attempted_queries,
            "content_chars": len(full_text),
            "is_truncated": is_truncated,
            "sections": sections,
            "content": full_text,
        }

    @filter.llm_tool(name="ask_minecraft_wiki")
    async def llm_ask_minecraft_wiki(self, event: AstrMessageEvent, question: str, mode: str = "auto") -> str:
        '''统一查询 Minecraft Wiki（推荐给 LLM 的唯一工具入口）。

        Args:
            question(string): 用户问题或关键词，例如“黑曜石爆炸抗性是多少”“/tp 怎么用”
            mode(string): 查询模式，支持 auto/summary/command/mechanic/recipe/section/search/full_page
        '''
        query = _normalize_question_text(question)
        if not query:
            return _to_json({"error": "empty query"}, max_chars=self.config.max_return_chars)

        resolved_mode = (mode or "auto").strip().lower()
        if resolved_mode in {"full", "page"}:
            resolved_mode = "full_page"
        if resolved_mode == "auto":
            resolved_mode = _infer_intent(query)

        if resolved_mode == "search":
            result = await search_wiki_page(self.api, query)
            payload = {"intent": resolved_mode, "tool_used": "search_wiki_page", "result": result}
            return _to_json(payload, max_chars=self.config.max_return_chars)

        if resolved_mode == "full_page":
            result = await self._get_full_page(query)
            payload = {"intent": resolved_mode, "tool_used": "get_full_page", "result": result}
            return _to_json(payload, max_chars=max(self.config.max_return_chars, self.config.max_full_page_chars + 2000))

        if resolved_mode == "command":
            command = _extract_command_from_text(query) or query
            result = await get_command_info(
                self.api,
                self.cache,
                command,
                max_chars=self.config.max_return_chars,
            )
            if isinstance(result, dict) and result.get("error") == "page not found":
                enriched = await self._search_with_evidence(query)
                payload = {"intent": resolved_mode, "tool_used": "search_with_evidence", "result": enriched}
                return _to_json(payload, max_chars=self.config.max_return_chars)
            payload = {"intent": resolved_mode, "tool_used": "get_command_info", "result": result}
            return _to_json(payload, max_chars=self.config.max_return_chars)

        if resolved_mode == "mechanic":
            result = await get_mechanic_info(
                self.api,
                self.cache,
                query,
                max_chars=self.config.max_return_chars,
            )
            if isinstance(result, dict) and result.get("error") == "page not found":
                enriched = await self._search_with_evidence(query)
                payload = {"intent": resolved_mode, "tool_used": "search_with_evidence", "result": enriched}
                return _to_json(payload, max_chars=self.config.max_return_chars)
            payload = {"intent": resolved_mode, "tool_used": "get_mechanic_info", "result": result}
            return _to_json(payload, max_chars=self.config.max_return_chars)

        if resolved_mode == "recipe":
            result = await get_crafting_recipe(
                self.api,
                self.cache,
                query,
                max_chars=self.config.max_return_chars,
            )
            if isinstance(result, dict) and result.get("error") == "page not found":
                enriched = await self._search_with_evidence(query)
                payload = {"intent": resolved_mode, "tool_used": "search_with_evidence", "result": enriched}
                return _to_json(payload, max_chars=self.config.max_return_chars)
            payload = {"intent": resolved_mode, "tool_used": "get_crafting_recipe", "result": result}
            return _to_json(payload, max_chars=self.config.max_return_chars)

        if resolved_mode == "section":
            title_query, section_guess = _extract_section_query(query)
            search_result = await search_wiki_page(self.api, title_query)
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

            result = await get_wiki_section(
                self.api,
                top_title,
                section_guess,
                max_chars=self.config.max_return_chars,
            )
            if isinstance(result, dict) and result.get("error") == "page not found":
                enriched = await self._search_with_evidence(query)
                payload = {"intent": resolved_mode, "tool_used": "search_with_evidence", "result": enriched}
                return _to_json(payload, max_chars=self.config.max_return_chars)
            payload = {"intent": resolved_mode, "tool_used": "get_wiki_section", "result": result}
            return _to_json(payload, max_chars=self.config.max_return_chars)

        enriched = await self._search_with_evidence(query)
        payload = {
            "intent": "summary",
            "tool_used": "search_with_evidence",
            "result": enriched,
        }
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

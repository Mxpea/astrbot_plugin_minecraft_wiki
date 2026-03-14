import re


VARIANT_KEYWORDS = {
    "下界合金": "Netherite",
    "钻石": "Diamond",
    "金": "Golden",
    "铁": "Iron",
    "铜": "Copper",
    "石": "Stone",
    "木": "Wooden",
}

COMMON_TERM_TRANSLATIONS = {
    "Wooden Sword": "木剑",
    "Stone Sword": "石剑",
    "Copper Sword": "铜剑",
    "Iron Sword": "铁剑",
    "Golden Sword": "金剑",
    "Diamond Sword": "钻石剑",
    "Netherite Sword": "下界合金剑",
    "Diamond": "钻石",
    "Stick": "木棍",
    "Iron Ingot": "铁锭",
    "Gold Ingot": "金锭",
    "Copper Ingot": "铜锭",
    "Any Planks": "任意木板",
    "Any stone-tier block": "任意石质材料",
}


def limit_text(text: str, max_chars: int = 6000) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _strip_templates(text: str) -> str:
    old = None
    cur = text
    # 迭代删除简单模板，避免单次正则无法处理嵌套。
    while old != cur:
        old = cur
        cur = re.sub(r"\{\{[^{}]*\}\}", "", cur)
    return cur


def clean_wikitext(text: str, max_chars: int = 6000) -> str:
    if not text:
        return ""

    text = re.sub(r"\{\{cmd\|(?:long=\d\|)?(?:link=none\|)?([^{}|]+)\}\}", r"\1", text, flags=re.I)
    text = re.sub(r"\{\{cd\|([^{}|]+)\}\}", r"\1", text, flags=re.I)
    text = re.sub(r"\{\{[Cc]ollapse\|title=.*?\|content=(.*?)\}\}", r"\1", text, flags=re.S)
    text = re.sub(r"\{\{[^{}|]+\|([^{}]+?)\}\}", r"\1", text)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.S | re.I)
    text = re.sub(r"<ref[^/]*/>", "", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = _strip_templates(text)

    text = re.sub(r"\[\[(?:[^\]|]+\|)?([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[https?://[^\s\]]+\s+([^\]]+)\]", r"\1", text)
    text = re.sub(r"\[https?://[^\s\]]+\]", "", text)

    text = re.sub(r"'{2,}", "", text)
    text = re.sub(r"-\{([^{}]+)\}-", r"\1", text)
    text = re.sub(r"\*:\s*", "", text)
    text = re.sub(r"^\s*=+\s*(.*?)\s*=+\s*$", r"\1", text, flags=re.M)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return limit_text(text.strip(), max_chars=max_chars)


def extract_section(wikitext: str, section_name: str, max_chars: int = 3000) -> str:
    if not wikitext or not section_name:
        return ""

    heading_re = re.compile(r"^(={2,6})\s*(.*?)\s*\1\s*$", re.M)
    matches = list(heading_re.finditer(wikitext))
    if not matches:
        return ""

    target = section_name.strip().lower()
    for idx, match in enumerate(matches):
        heading = re.sub(r"\s+", "", match.group(2)).lower()
        if target in heading or heading in target:
            start = match.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(wikitext)
            section_raw = wikitext[start:end]
            return clean_wikitext(section_raw, max_chars=max_chars)
    return ""


def extract_command_usage(wikitext: str, max_chars: int = 1800) -> str:
    for section_name in ["语法", "Syntax", "命令格式", "参数", "用法"]:
        content = extract_section(wikitext, section_name, max_chars=max_chars)
        if content:
            return content

    lines = []
    for line in (wikitext or "").splitlines():
        striped = line.strip()
        if striped.startswith("/") or "<player>" in striped or "<targets>" in striped:
            lines.append(striped)
        if len(lines) >= 12:
            break
    return clean_wikitext("\n".join(lines), max_chars=max_chars)


def _pick_variant_value(value: str, target_index: int) -> str:
    parts = [part.strip() for part in value.split(";")]
    if len(parts) > target_index:
        return parts[target_index]
    return value.strip()


def translate_terms(text: str) -> str:
    translated = text
    for source, target in COMMON_TERM_TRANSLATIONS.items():
        translated = translated.replace(source, target)
    return translated


def _infer_output_index(item_name: str, raw_output: str) -> int:
    outputs = [part.strip() for part in raw_output.split(";")]
    if not item_name:
        return 0

    normalized_item_name = clean_wikitext(item_name, max_chars=120).lower()
    for idx, candidate in enumerate(outputs):
        cleaned_candidate = clean_wikitext(candidate, max_chars=120).lower()
        if normalized_item_name in cleaned_candidate:
            return idx

    for zh_keyword, en_keyword in VARIANT_KEYWORDS.items():
        if zh_keyword in item_name:
            for idx, candidate in enumerate(outputs):
                if en_keyword.lower() in clean_wikitext(candidate, max_chars=120).lower():
                    return idx

    return 0


def extract_recipe(wikitext: str, item_name: str = "", max_chars: int = 1800) -> str:
    template_match = re.search(r"\{\{\s*(Crafting|合成).*?\}\}", wikitext or "", flags=re.S | re.I)
    if template_match:
        template = template_match.group(0)
        params = dict(re.findall(r"\|\s*([^=|\n]+?)\s*=\s*([^|\n]+)", template))
        output_index = 0
        output = ""
        for key in ["Output", "output", "产物", "结果"]:
            if key in params and params[key].strip():
                raw_output = params[key].strip()
                output_index = _infer_output_index(item_name, raw_output)
                output = translate_terms(clean_wikitext(_pick_variant_value(raw_output, output_index), max_chars=120))
                break

        ingredients = []
        for key, value in params.items():
            normalized_key = key.strip().lower()
            if re.fullmatch(r"[abc]?[1-3]|[a-z]\d|材料\d+|input\d*|in\d*", normalized_key) and value.strip():
                picked_value = _pick_variant_value(value.strip(), output_index)
                cleaned_value = translate_terms(clean_wikitext(picked_value, max_chars=120))
                if cleaned_value:
                    ingredients.append(cleaned_value)

        lines = []
        if output:
            lines.append(f"合成产物: {output}")
        if ingredients:
            lines.append("材料: " + "、".join(ingredients[:9]))
        if lines:
            return limit_text("\n".join(lines), max_chars=max_chars)

    for section_name in ["合成", "配方", "Crafting", "用途", "获取"]:
        content = extract_section(wikitext, section_name, max_chars=max_chars)
        if content:
            return content

    recipe_lines = []
    for line in (wikitext or "").splitlines():
        lower = line.lower()
        if "recipe" in lower or "craft" in lower or "合成" in line:
            recipe_lines.append(line)
        if len(recipe_lines) >= 14:
            break
    return clean_wikitext("\n".join(recipe_lines), max_chars=max_chars)


def extract_mechanic_description(wikitext: str, max_chars: int = 1800) -> str:
    for section_name in ["机制", "原理", "行为", "用途", "生成", "交易", "刷怪", "掉落"]:
        content = extract_section(wikitext, section_name, max_chars=max_chars)
        if content:
            return content

    return clean_wikitext(wikitext, max_chars=max_chars)

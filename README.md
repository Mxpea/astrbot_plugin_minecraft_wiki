# Minecraft Wiki 查询插件

这是一个给 AstrBot LLM 使用的 Minecraft Wiki 查询插件。

目标：

- 让 LLM 用一个统一工具就能查询命令、机制、配方、版本更新和百科内容。
- 对关键词不稳定的场景，返回候选证据，减少误命中。
- 支持整页内容返回，给 LLM 更完整上下文。

## 功能概览

- 统一工具入口：ask_minecraft_wiki
- 支持模式：auto、summary、search、command、mechanic、recipe、section、full_page
- 版本总结增强：识别 1.20、1.20.5、25w09a、Java版1.21 等版本词
- 术语归一化：如 predicate -> 谓词、datapack -> 数据包
- 整页返回：可直接返回页面全文、章节目录和截断标记

## 推荐调用方式

### 1. 普通问答

- mode 使用 auto
- 示例问题：
  黑曜石爆炸抗性是多少；/tp 命令怎么用；兔肉煲的配方是什么

### 2. 关键词不稳定或术语复杂

- 先 mode=search 看候选，再用 mode=full_page 获取整页上下文

### 3. 需要指定页面全文

- mode=full_page
- question 支持前缀：title:页面名、页面:页面名、page:页面名
- 示例：title:谓词

## mode 说明

- auto：自动判断意图（命令/机制/配方/章节/摘要）
- summary：概要型查询
- search：仅返回搜索候选
- command：命令语法、说明、示例
- mechanic：机制说明
- recipe：合成结果与材料
- section：指定页面的章节内容
- full_page：返回整页内容（适合让 AI 深度阅读）

## 返回结构（核心）

插件统一返回 JSON 字符串。

外层字段：

- intent：本次意图
- tool_used：实际使用的内部查询路径
- result：查询结果

常见 result 结构：

### 1. search_with_evidence（默认主路径之一）

- query：原问题
- focus_keywords：提取关键词
- attempted_queries：尝试过的检索词
- primary_value：提取出的主数值（如 1200）
- candidates：候选页面数组

候选项字段：

- title
- source_query
- snippet
- summary
- fact_lines
- primary_value

### 2. full_page

- query
- title
- attempted_queries
- sections：章节目录
- content：清洗后的全文
- content_chars：内容字符数
- is_truncated：是否被截断

### 3. 失败场景

- error: page not found

## 配置项

可在插件配置里设置：

- base_url：Wiki API 地址，默认中文站 API
- timeout_seconds：请求超时时间
- cache_ttl_seconds：缓存有效期（秒）
- max_return_chars：常规模式返回上限
- max_full_page_chars：full_page 模式返回上限
- default_search_limit：默认搜索候选数

## 实战建议（提升命中率）

### 1. 两段式策略

- 先 search
- 再 full_page（按候选 title 精确抓整页）

### 2. 版本问题尽量带版本号

- 例如：Java版1.21更新内容、25w09a快照更新了什么

### 3. 技术术语混输

- 中英混合可提升召回
- 例如：数据包 谓词 predicate

### 4. 超长页面阅读

- 先 full_page 获取上下文
- 再让 LLM 追问具体章节

## 已知限制

- 某些过于泛化的关键词仍可能先命中泛页面。
- full_page 内容很长时会按 max_full_page_chars 截断。
- 如需更稳定，可先指定页面：title:页面名。

## 版本

- 当前插件信息见 [metadata.yaml](metadata.yaml)

<!--
  Prompt 模板：content-repurposing（一稿多发）
  改写自 ZJU-REAL/Easel v0.2.0 (Apache 2.0)
  skills/openclaw/skill-content-repurposing/SKILL.md
  及 references/conversion-recipes.md Recipe 9 / Recipe 10 / 公众号适配

  ★ 本文件是提示词的唯一权威来源 ★
  - 本地演示版 local/prompts.py 直接读取 TEMPLATE 标记块
  - Worker 版 src/prompts.js 为同内容的 JS 版（改这里后需同步那边）
  变量：{{PROFILE}} {{SOURCE}} {{TARGETS}}
-->

# 用途

把一篇已生成的小红书笔记（或任意源文本）改编成其他平台的**原生内容**：抖音口播脚本、微信公众号短文、微博短博。一次 LLM 调用输出全部目标平台版本。

# 变量

| 变量 | 来源 | 说明 |
|---|---|---|
| `{{PROFILE}}` | 商户画像 | 语气/受众对齐 |
| `{{SOURCE}}` | contents.payload（生成的笔记 JSON）或用户粘贴的文本 | 源内容 |
| `{{TARGETS}}` | 请求体 targets | douyin / wechat / weibo 的中文名列表 |

# 改编四原则（源自原 SKILL）

1. **平台原生**：每条读起来像专门为该平台写的，不是机械裁剪
2. **核心一致**：所有版本传达同一核心信息，不跑题不矛盾
3. **独立成立**：单条单独阅读也完整有价值
4. **格式适配**：严格遵守各平台长度、格式、标签规则

# 模板正文（TEMPLATE 标记块内为实际注入 prompt）

<!-- TEMPLATE:START -->
你是跨平台内容改编师。把下面的源内容改编成 {{TARGETS}} 的原生内容，严格只输出一个 JSON 对象，不要多余文字、不要 markdown 代码块围栏。

【商户画像】
{{PROFILE}}

【源内容】
{{SOURCE}}

## 输出 JSON Schema
{
  "core_message": "所有版本共同传达的核心信息（一句话）",
  "pieces": [
    { "platform": "douyin|wechat|weibo", "title": "该平台标题/开头一句", "body": "正文", "hashtags": ["话题标签"], "post_tip": "发布建议（时间/配图/注意事项，一句话）" }
  ]
}
pieces 数组必须覆盖全部目标平台，每个平台恰好一条。

## 各平台改编配方

### 抖音（douyin）口播脚本（30-60 秒竖版）
- 前 3 秒 hook：悬念/冲突/反常识/利益点，决定完播
- 主体 3-5 个要点，每点一句话，标注画面提示，格式如「[3-10s] 要点1｜画面：老板娘现包现蒸特写」
- 结尾一句记忆点；**弱引导**——不要出现「点赞关注」等显式话术（易限流）
- title ≤ 55 字，激发好奇；hashtags 2-5 个
- 全文口语、短句、强节奏

### 微信公众号（wechat）街坊短文（300-500 字）
- 标题生活化、不说教
- 开头 2 句交代与街坊的关系或场景，正文 3-4 段讲清楚信息
- 结尾自然引导到店（地址/营业时间/一句人话 CTA），不堆营销话术
- 语气比小红书稍沉稳，保留烟火气

### 微博（weibo）短博（≤140 字）
- 核心观点情绪化/话题感表达，压到 140 字内
- 正文嵌 2-3 个 #话题#（与内容真相关，不硬蹭）
- 观点鲜明可转发

## 硬性要求
- 不虚构源内容里没有的信息（数据、活动、价格）
- 遵守画像雷区
- 每条正文里不得出现其他平台名称和外站引导
<!-- TEMPLATE:END -->

# Apache 2.0 归因

改编原则与转换配方源自 Easel 项目 skill-content-repurposing（github.com/ZJU-REAL/Easel，Apache License 2.0）。

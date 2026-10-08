<!--
  Prompt 模板：content-calendar（月度排期表）
  改写自 ZJU-REAL/Easel v0.2.0 (Apache 2.0) skills/openclaw/skill-content-calendar/SKILL.md
  排列规则照搬原文（活动帖先锁 / 推广≤20% / 支柱均匀分布 / 选题必须具体）

  ★ 本文件是提示词的唯一权威来源 ★
  - 本地演示版 local/prompts.py 直接读取 TEMPLATE 标记块
  - Worker 版 src/prompts.js 为同内容的 JS 版（改这里后需同步那边）
  变量：{{PROFILE}} {{FREQUENCY}} {{GOAL}} {{CONTEXT}}
-->

# 用途

为商户生成一个月（4 周）的内容排期表：每条帖子具体到「文案写手看了就能直接动笔」。

# 变量

| 变量 | 来源 | 默认值 |
|---|---|---|
| `{{PROFILE}}` | 商户画像（含内容支柱） | 必填 |
| `{{FREQUENCY}}` | 用户选择 | 每周 3 条 |
| `{{GOAL}}` | 用户选择：到店引流/促转化/涨曝光/社区互动 | 到店引流 |
| `{{CONTEXT}}` | 本月活动/节日/新品 | 无 |

# 模板正文（TEMPLATE 标记块内为实际注入 prompt）

<!-- TEMPLATE:START -->
你是社媒内容策划师。为下面这家小商户规划一个月（4 周）的内容排期。严格只输出一个 JSON 对象，不要多余文字、不要 markdown 代码块围栏。

【商户画像】
{{PROFILE}}

【发布频率】{{FREQUENCY}}
【本月目标】{{GOAL}}
【本月活动与节点】{{CONTEXT}}

## 输出 JSON Schema
{
  "schedule": [
    {
      "week": 1,
      "weekday": "一|三|五|六|日 之一",
      "platform": "小红书",
      "pillar": "支柱名（来自画像内容支柱）",
      "format": "图文|探店实拍|口播|清单|轮播",
      "goal": "曝光|互动|转化",
      "topic": "具体选题——具体到能直接动笔，不许出现「发一条教程」这类模糊指令",
      "angle": "具体切入点——什么让这条值得停下来看",
      "visual": "1 句话描述配图/视频画面",
      "note": "时效、活动关联等备注（可为空字符串）"
    }
  ],
  "summary": { "total_posts": n, "promo_ratio": "推广类占比（百分比）", "reminder": "一句运营提醒" }
}

## 排列规则（必须遵守）
1. 活动帖先锁定位置（有【本月活动】时先排活动，再填常规内容）
2. 推广/优惠类帖占比不超过 20%，且相邻推广帖之间至少隔 1 条非推广内容
3. 内容支柱均匀分布，不把同一支柱集中在同一周
4. 多用周末与当地生活节奏（早市/午市/周末家庭餐）安排高互动帖
5. 选题必须具体：不是「新品宣传」，而是「虾仁肠粉首周 7.8 折，前 20 名送豆浆」级别
6. 每条 topic/angle 不与画像雷区冲突，不出现其他平台名称与外站引导
<!-- TEMPLATE:END -->

# Apache 2.0 归因

排列规则与方法论源自 Easel 项目 skill-content-calendar（github.com/ZJU-REAL/Easel，Apache License 2.0）。

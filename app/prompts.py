"""prompts.py — 从 prompts/*.md 的 TEMPLATE 标记块加载模板并渲染。

单一来源：prompts/xhs-note-creator.md、prompts/skill-quality-gate.md
（Worker 版 src/prompts.js 是同一内容的 JS 版，改动需两边同步）
"""

import json
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

START = "<!-- TEMPLATE:START -->"
END = "<!-- TEMPLATE:END -->"

_cache: dict[str, str] = {}


def load_template(name: str) -> str:
    """读出 md 文件中 TEMPLATE 标记块之间的正文。"""
    if name in _cache:
        return _cache[name]
    text = (PROMPTS_DIR / name).read_text(encoding="utf-8")
    if START not in text or END not in text:
        raise RuntimeError(f"{name} 缺少 TEMPLATE 标记块")
    body = text.split(START, 1)[1].split(END, 1)[0].strip()
    _cache[name] = body
    return body


def render(template: str, **vars) -> str:
    out = template
    for k, v in vars.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out


# ---------- 画像 → 自然语言 ----------
def profile_to_text(p: dict | None, category: str = "餐饮") -> str:
    if not p:
        return (
            f"无画像，使用默认：通用{category}种草号；"
            "受众为门店 3km 内街坊与上班族；调性=街坊闺蜜感；无特殊雷区。"
        )

    def arr(v):
        if isinstance(v, list):
            return "、".join(map(str, v)) if v else "（无）"
        if isinstance(v, str) and v.strip():
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return "、".join(map(str, parsed)) if parsed else "（无）"
            except Exception:
                return v
        return "（无）"

    loc = ""
    try:
        loc = p.get("location") or ""
    except Exception:
        loc = ""

    feats = p.get("features") or ""
    return "\n".join([
        f"商户名称：{p.get('name') or '（未填）'}",
        f"品类：{p.get('category') or category}",
        f"门店位置：{loc or '（未填，不要编造具体地址，用「就在附近」这类模糊说法）'}",
        f"特色/产品（老板原话）：{feats or '（未填）'}",
        f"账号定位：{p.get('positioning') or '（未填，按品类通用定位）'}",
        f"目标受众：{p.get('audience') or '（未填）'}",
        f"内容调性：{p.get('tone') or '（未填）'}",
        f"运营目标：{p.get('goals') or '（未填）'}",
        f"主要卖什么（招牌产品）：{arr(p.get('pillars'))}",
        f"雷区（绝不出现）：{arr(p.get('taboo'))}",
    ])


# ---------- 模板 1：小红书笔记 ----------
def build_note_prompt(profile_text: str, topic: str, card_count: int = 6, material: str = "") -> str:
    return render(
        load_template("xhs-note-creator.md"),
        PROFILE=profile_text,
        TOPIC=topic,
        CARD_COUNT=card_count,
        MATERIAL=material or "无",
    )


# ---------- 模板 2：质检 ----------
XHS_RULES = """1. 导流外站（高风险）：其他电商平台名/链接、二维码、群号、个人联系方式、谐音变体绕过、引导私信获取外链。
2. 软广未标注：**仅当内容呈现为第三方探店、收费合作、赠品置换或含返利/佣金时检测**；商家在自家账号介绍自家产品/活动/门店信息不算软广，不要报此项。
3. 限流风险（中风险，给替代建议）：攻击性竞品对比、刷屏式营销话术（买它/冲/闭眼入堆砌）、价格折扣信息过度堆砌。"""

CATEGORY_RULES = {
    "餐饮": "餐饮：重点盯「最/第一/独家」绝对化用语 + 食材功效暗示（降火、养胃等医疗表述）。",
    "幼教": "幼教：教育培训广告限制——不出现提分承诺、升学暗示、师资效果保证、押题命中类表述。",
}

# 一句话生成（one-shot）的品类特化：避免踩垂类广告法红线
ONESHOT_CATEGORY_RULES = {
    "餐饮": (
        "餐饮门店。不得暗示食材有治疗/保健功效（降火、养胃、祛湿、排毒等一律不写）；"
        "不得夸大分量和食材等级；不得使用「最/第一/独家」；"
        "可以做：做法细节、时间、现做现卖、回头客、老店年头、实在的价格。"
    ),
    "幼教": (
        "幼教/教培。不得承诺提分、升学、考级结果，不得保证师资效果、不得暗示名额稀缺制造焦虑；"
        "可以做：孩子的具体成长瞬间、课堂真实片段、老师的具体做法、家长的真实反馈、"
        "安全与卫生细节、接送便利。"
    ),
    "零售": (
        "零售门店。不得虚假标价（划线价需真实）、不得使用「最/第一/全网最低」；"
        "可以做：上新、产地、选品理由、试用体验、搭配建议、到店可看可试。"
    ),
    "服务": (
        "本地服务门店。不得承诺效果、不得使用绝对化用语；"
        "可以做：服务流程细节、师傅手艺与资历、真实案例过程、价格透明、预约便利。"
    ),
}


def build_oneshot_category_rules(category: str) -> str:
    return ONESHOT_CATEGORY_RULES.get(category, (
        f"{category or '通用'}门店。遵守通用规则：不用绝对化用语、不承诺效果、不编造活动与价格；"
        "把内容落在具体的人、时间、做法、场景上。"
    ))


# ---------- 模板 6：一句话生成四平台成品（产品主入口） ----------
def build_one_shot_prompt(profile_text: str, demand: str, material: str = "",
                          category: str = "餐饮") -> str:
    return render(
        load_template("one-shot.md"),
        PROFILE=profile_text,
        DEMAND=demand,
        MATERIAL=material or "老板没有上传素材，只按需求写，画面建议要基于门店常见场景",
        CATEGORY_RULES=build_oneshot_category_rules(category),
    )


# ---------- 模板 4：一稿多发（Easel skill-content-repurposing） ----------
def build_extra_rules(platform: str, category: str) -> str:
    parts = []
    if (platform or "").lower() in ("xiaohongshu", "xhs", "小红书"):
        parts.append("【小红书特化】\n" + XHS_RULES)
    else:
        parts.append("【平台】无平台特有规则，仅用通用规则。")
    if category in CATEGORY_RULES:
        parts.append("【垂类】" + CATEGORY_RULES[category])
    return "\n\n".join(parts)


def build_quality_prompt(content: str, platform: str = "xiaohongshu", category: str = "餐饮") -> str:
    return render(
        load_template("skill-quality-gate.md"),
        CONTENT=content,
        PLATFORM=platform or "xiaohongshu",
        CATEGORY=category or "餐饮",
        EXTRA_RULES=build_extra_rules(platform, category),
    )


# ---------- 模板 3：选题（七维口径源自 Easel scoring-dimensions.md） ----------
# 推荐权重（Easel 原版）：流量 .25 / 匹配 .20 / 差异化 .15 / 变现 .15 / 时效 .10 / 成本 .08 / 合规 .07
# cost 与 compliance 为反向维度（分越高越有利），加权前无需转换
SCORE_WEIGHTS = {
    "traffic": 0.25, "fit": 0.20, "differentiation": 0.15, "monetization": 0.15,
    "timeliness": 0.10, "cost": 0.08, "compliance": 0.07,
}

SCORE_TEMPLATE = """你是自媒体选题评审。对给定选题按七维打分（各 1-10 整数）：流量潜力、账号匹配、竞争差异化、时效价值、变现潜力、制作成本（越高成本分越低）、合规风险（风险越高分越低）。严格只输出一个 JSON 对象，不要多余文字：
{"dimensions":{"traffic":n,"fit":n,"differentiation":n,"timeliness":n,"monetization":n,"cost":n,"compliance":n},"comment":"一句话总评"}

【商户画像】
{profile}

【选题】
{topic}"""


def build_score_prompt(profile_text: str, topic: str) -> str:
    return SCORE_TEMPLATE.format(profile=profile_text, topic=topic)


def build_topic_matrix_prompt(profile_text: str, count: int = 8, context: str = "") -> str:
    return render(
        load_template("topic-matrix.md"),
        PROFILE=profile_text,
        COUNT=count,
        CONTEXT=context or "无",
    )


# ---------- 模板 4：一稿多发（Easel skill-content-repurposing） ----------
def build_repurpose_prompt(profile_text: str, source: str, targets: list[str]) -> str:
    name_map = {"douyin": "抖音", "wechat": "微信公众号", "weibo": "微博"}
    t = "、".join(name_map.get(x, x) for x in targets)
    return render(
        load_template("content-repurposing.md"),
        PROFILE=profile_text,
        SOURCE=source,
        TARGETS=t,
    )


# ---------- 模板 5：月度排期表（Easel skill-content-calendar） ----------
def build_calendar_prompt(profile_text: str, frequency: str = "每周 3 条",
                          goal: str = "到店引流", context: str = "") -> str:
    return render(
        load_template("content-calendar.md"),
        PROFILE=profile_text,
        FREQUENCY=frequency,
        GOAL=goal,
        CONTEXT=context or "无",
    )


# ---------- 模板 7：卖点提炼（商家档案 → 启用卖点） ----------
def build_selling_points_prompt(profile_text: str, features: str) -> str:
    return render(
        load_template("selling-points.md"),
        PROFILE=profile_text,
        FEATURES=features or "（老板没额外描述，只从档案里提炼）",
    )


# ---------- 模板 8：选题（今日推荐 / 7天计划 / 自由生成） ----------
TOPIC_MODES = {
    "today":  {"count": 3, "desc": "今日推荐：今天就能拍能发的选题"},
    "weekly": {"count": 7, "desc": "7 天计划：一周每天一条，角度互相错开"},
    "free":   {"count": 5, "desc": "自由生成：不限时态的常规选题"},
}

CHIP_LABELS = {
    "no_open": "今天不出摊",
    "no_time": "没时间拍",
    "signature": "推招牌菜",
    "boss_ip": "老板IP",
    "local": "同城流量",
    "promo": "活动营销",
}


def build_boss_topics_prompt(profile_text: str, points_text: str, mode: str,
                             chips: list[str] | None = None, context: str = "") -> str:
    m = TOPIC_MODES.get(mode, TOPIC_MODES["today"])
    chip_names = [CHIP_LABELS[c] for c in (chips or []) if c in CHIP_LABELS]
    chip_text = "、".join(chip_names) if chip_names else "无"
    return render(
        load_template("boss-topics.md"),
        COUNT=m["count"],
        MODE_DESC=m["desc"],
        PROFILE=profile_text,
        POINTS=points_text or "（还没提炼卖点，按档案出题）",
        CHIPS=chip_text,
        CONTEXT=context or "无",
    )


def points_to_text(points: list | None) -> str:
    """启用的卖点 → 自然语言（只取 on=True 的）。"""
    out = []
    for p in points or []:
        if isinstance(p, str):
            out.append(p)
            continue
        if not p.get("on", True):
            continue
        line = p.get("point", "")
        if p.get("detail"):
            line += f"（用法：{p['detail']}）"
        if line:
            out.append(line)
    return "\n".join(f"{i+1}. {x}" for i, x in enumerate(out)) or "（无）"


# ---------- 模板 9：选题 → 单一成品（图文/视频共用文案） ----------
def build_boss_create_prompt(profile_text: str, points_text: str, topic: str,
                             angle: str = "", category: str = "餐饮",
                             extra: str = "") -> str:
    return render(
        load_template("boss-create.md"),
        PROFILE=profile_text,
        POINTS=points_text,
        TOPIC=topic,
        ANGLE=angle or "（按选题本身发挥）",
        EXTRA=extra or "（无）",
        CATEGORY_RULES=build_oneshot_category_rules(category),
    )


# ---------- 模板 10：成品文案 → AI 配音视频分镜 ----------
def build_video_script_prompt(content_text: str, materials_text: str,
                              category: str = "餐饮") -> str:
    return render(
        load_template("video-script.md"),
        CONTENT=content_text,
        MATERIALS=materials_text or "（无素材）",
        CATEGORY_RULES=build_oneshot_category_rules(category),
    )


def weighted_total(dimensions: dict) -> float:
    """服务端重算百分制总分，不信任模型算术。"""
    total = sum(SCORE_WEIGHTS.get(k, 0) * (float(dimensions.get(k) or 0)) for k in SCORE_WEIGHTS)
    return round(total * 10, 1)


if __name__ == "__main__":
    # 自检：能加载、能渲染、无残留占位符
    pt = profile_to_text({"name": "阿珍肠粉店", "category": "餐饮", "pillars": ["招牌肠粉"], "taboo": ["不贬低竞品"]})
    n = build_note_prompt(pt, "新品虾仁肠粉", 6)
    q = build_quality_prompt("全广州最好吃的肠粉！100%好评", "xiaohongshu", "餐饮")
    for label, t in (("note", n), ("quality", q)):
        left = [x for x in ("{{PROFILE}}", "{{TOPIC}}", "{{CARD_COUNT}}", "{{MATERIAL}}",
                            "{{CONTENT}}", "{{PLATFORM}}", "{{CATEGORY}}", "{{EXTRA_RULES}}") if x in t]
        print(f"{label}: {len(t)} 字符，残留占位符={left or '无'}")
    print("加权总分自检:", weighted_total({"traffic": 8, "fit": 9, "differentiation": 7,
                                            "timeliness": 6, "monetization": 7, "cost": 8, "compliance": 9}))

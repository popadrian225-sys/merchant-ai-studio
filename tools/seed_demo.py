#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""seed_demo.py — 一键生成演示数据（虚构的「阿珍肠粉店」）。

为什么不直接调 AI：演示数据只是为了让界面有东西可看、可截图，
离线写进库里就够了 —— 不消耗任何 API 额度，也不需要 key。

用法：
    python tools/seed_demo.py            # 库里已有数据时不动，提示加 --reset
    python tools/seed_demo.py --reset    # 清掉旧库与生成物，重建演示数据
"""
import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
sys.path.insert(0, str(APP))

SHOP = {
    "name": "阿珍肠粉店",
    "category": "餐饮",
    "location": "广州荔湾区老街菜市场旁",
    "positioning": "老广街坊早餐店，开了 12 年",
    "audience": "门店 3km 内街坊与上班族，25-55 岁",
    "tone": "街坊闺蜜感，实在不浮夸",
    "goals": "到店引流，工作日上午客流",
    "pillars": ["招牌肠粉", "店家故事", "优惠活动", "食客互动"],
    "taboo": ["不贬低竞品", "不承诺治病功效", "不写外卖配送"],
    "features": (
        "米浆每天凌晨四点现磨，一天只做两桶，中午前卖完；"
        "布拉肠是祖传手法，粉皮薄得能透光；"
        "开了 12 年，街坊从小孩吃到带孙子来；"
        "猪肠粉 6 元、加蛋 2 元，十年只涨过 1 块；"
        "店里只有 6 张台，早上 7 点到 9 点基本满座"
    ),
}

POINTS = [
    {"point": "凌晨四点现磨米浆", "detail": "一天只磨两桶，卖完就收，不隔夜", "on": True},
    {"point": "祖传布拉肠手法", "detail": "粉皮薄到透光，卷起来不断", "on": True},
    {"point": "开了 12 年的老街坊店", "detail": "街坊从小孩吃到带孙子来", "on": True},
    {"point": "十年只涨过一块钱", "detail": "猪肠粉 6 元，加蛋 2 元", "on": True},
    {"point": "只有 6 张台的小店", "detail": "早上 7-9 点常满座，来晚了要等", "on": False},
]

TOPICS = [
    ("today", "凌晨四点的米浆，才是这碟肠粉的底气",
     "拍磨米浆 + 蒸粉的过程，讲「为什么非要四点起」", "老板真实的一天，比任何广告都能打动人", "今天降温，街坊想喝热粥"),
    ("today", "降温天，街坊都来点这碗粥",
     "粥配肠粉的搭配 + 天冷的关怀感", "天气是最好用的选题由头，当天发当天有客流", "今天降温"),
    ("today", "街坊最常问的一句话：今天卖完没有？",
     "用一句真实对话开头，带出卖完就收的规矩", "互动型选题，评论区容易起来", ""),
    ("weekly", "12 年只涨过 1 块钱，是怎么算的账",
     "算一笔成本账，讲为什么能十年不涨价", "价格是街坊最敏感的点，说透就是信任", ""),
    ("weekly", "带孙子来的那位婆婆，又来了",
     "一个老顾客的故事，落回到店场景", "人物故事最容易被转发", ""),
    ("weekly", "米浆为什么不能隔夜",
     "科普向：隔夜米浆的变化，落到「所以只做两桶」", "把「限量」讲成「讲究」", ""),
    ("free", "肠粉的三种吃法，你站哪一个",
     "酱油党 / 辣酱党 / 原味党，投票互动", "低门槛互动，涨评论", ""),
    ("free", "蒸粉的 30 秒，是手艺最藏不住的地方",
     "特写蒸粉手法 + 时间感", "展示手艺，建立专业感", ""),
    ("free", "店里 6 张台，坐过最多的是谁",
     "盘点店里的「常客位」，人情味", "让人想打卡", ""),
]

CONTENTS = [
    {
        "topic": "凌晨四点的米浆，才是这碟肠粉的底气",
        "topic_id": 1,
        "master": {
            "title": "凌晨四点的米浆，才是这碟肠粉的底气 🥢",
            "body": (
                "很多人问我为什么非要四点起。\n\n"
                "其实是没办法。米浆磨出来会在两个钟头里慢慢变沉，蒸出来的粉皮就厚、就发黏，"
                "不是那个味道了。所以我们家一天只磨两桶，早上七点第一桶，十点第二桶，卖完就收摊。\n\n"
                "布拉肠是我爸教的，粉皮要薄到能透光，卷起来还不能断。这个手感说不清楚，"
                "蒸够 30 秒还是 35 秒，全靠看。\n\n"
                "老街上开了 12 年，猪肠粉还是 6 块钱，加蛋 2 块。有街坊说你们怎么还不涨，"
                "我说街坊早餐就该是这个价。\n\n"
                "今天上午想吃的早点来，卖完就没有了。"
            ),
            "hashtags": ["#广州美食", "#老街早餐", "#肠粉", "#街坊食堂", "#本地人推荐"],
            "cards": [
                {"seq": 1, "text": "凌晨四点的米浆，才是这碟肠粉的底气"},
                {"seq": 2, "text": "米浆放两小时就会沉，粉皮就厚了 —— 所以一天只磨两桶"},
                {"seq": 3, "text": "粉皮薄到能透光，卷起来还不断"},
                {"seq": 4, "text": "12 年，猪肠粉 6 元，加蛋 2 元"},
            ],
            "shoot_tips": [
                "拍磨米浆时给个特写，米浆流下来的丝线最有说服力",
                "蒸粉用俯拍，30 秒揭盖的那一下最抓人",
                "最后拍一张卖完收摊的空蒸柜，不用配字",
            ],
        },
    },
    {
        "topic": "降温天，街坊都来点这碗粥",
        "topic_id": 2,
        "master": {
            "title": "降温了，街坊都来点这碗粥 🍲",
            "body": (
                "今天一早风挺大的，进来的人都缩着脖子。\n\n"
                "这种天点单最多的是艇仔粥，配一碟肠粉，一碗下去手就暖了。"
                "粥是现熬的，早上五点半就开始煲，米粒开花但不烂。\n\n"
                "有位阿姨说她孙子只喝我们家的粥，别的太咸。我们家确实下手轻，"
                "街坊吃惯了的味道，不敢乱改。\n\n"
                "天冷了，早点来，热乎的。"
            ),
            "hashtags": ["#广州早餐", "#艇仔粥", "#降温了", "#街坊食堂", "#老广味道"],
            "cards": [
                {"seq": 1, "text": "降温了，街坊都来点这碗粥"},
                {"seq": 2, "text": "五点半开始煲，米粒开花但不烂"},
                {"seq": 3, "text": "一碗粥配一碟肠粉，手就暖了"},
            ],
            "shoot_tips": [
                "拍一碗冒热气的粥，逆光拍蒸汽最明显",
                "背景带上窗外的风、路上的行人",
            ],
        },
    },
    {
        "topic": "12 年只涨过 1 块钱，是怎么算的账",
        "topic_id": 4,
        "master": {
            "title": "12 年只涨过 1 块钱，我们是怎么算账的",
            "body": (
                "有人劝我涨价，说这条街房租都翻了几轮了。\n\n"
                "算过。米、油、蛋加起来占成本一大半，我们不做外卖、不做宣传、不请人，"
                "店里就我和我老公两个人，省下的都是人工和平台的钱。\n\n"
                "所以涨的那一块，是去年米价涨得实在顶不住才动的。街坊早餐就得是这个价，"
                "一天六块钱，人家才吃得心安。\n\n"
                "钱少赚点，人来得勤点，账是平的。"
            ),
            "hashtags": ["#广州小店", "#早餐价格", "#老街故事", "#街坊食堂"],
            "cards": [
                {"seq": 1, "text": "12 年只涨过 1 块钱"},
                {"seq": 2, "text": "不做外卖、不请人，省下的都留给街坊"},
                {"seq": 3, "text": "6 块钱的早餐，吃起来才安心"},
            ],
            "shoot_tips": ["拍价目表特写", "拍两个人忙碌的背影，比拍脸自然"],
        },
    },
]

QUALITY = [
    (1, "pass", "low", {
        "verdict": "pass", "risk_level": "low",
        "issues": [],
        "dimensions": {"合规": 9, "真实感": 9, "可读性": 8, "转化": 7, "平台适配": 9},
        "top_fixes": ["可以在结尾加一句到店信息，转化会更直接"],
    }),
    (2, "pass", "low", {
        "verdict": "pass", "risk_level": "low",
        "issues": [],
        "dimensions": {"合规": 10, "真实感": 9, "可读性": 9, "转化": 7, "平台适配": 8},
        "top_fixes": [],
    }),
]

SCORES = [
    (1, "凌晨四点的米浆，才是这碟肠粉的底气",
     {"traffic": 8, "fit": 9, "differentiation": 9, "timeliness": 7, "monetization": 7, "cost": 7, "compliance": 10}, 82.5),
    (2, "降温天，街坊都来点这碗粥",
     {"traffic": 7, "fit": 9, "differentiation": 6, "timeliness": 10, "monetization": 8, "cost": 9, "compliance": 10}, 80.4),
    (3, "街坊最常问的一句话：今天卖完没有？",
     {"traffic": 7, "fit": 8, "differentiation": 7, "timeliness": 6, "monetization": 6, "cost": 9, "compliance": 10}, 73.1),
]

DEMO_MATERIAL_NOTE = "招牌肠粉出锅（演示素材）"


def make_demo_material(path: Path):
    """画一张演示用素材图（纯色块 + 文字），避免把真实客户照片带进仓库。"""
    from PIL import Image, ImageDraw, ImageFont
    W, H = 900, 1200
    img = Image.new("RGB", (W, H), (238, 229, 211))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 380], fill=(196, 122, 92))
    d.ellipse([W // 2 - 210, 470, W // 2 + 210, 890], fill=(226, 214, 190))
    d.ellipse([W // 2 - 170, 510, W // 2 + 170, 850], fill=(243, 238, 226))
    d.rectangle([80, 960, W - 80, 1060], fill=(214, 202, 178))
    font = None
    for cand in (ROOT / "app" / "assets" / "fonts" / "NotoSansSC-sub.ttf",):
        if cand.exists():
            font = ImageFont.truetype(str(cand), 46)
    if font:
        d.text((90, 150), "演示素材", font=font, fill=(255, 250, 244))
        d.text((90, 980), "招牌肠粉出锅", font=font, fill=(120, 104, 84))
    img.save(path, quality=88)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="删掉旧库与生成物后重建")
    a = ap.parse_args()

    import server  # noqa: E402  （导入即完成建表与迁移）

    db_path = APP / "data.db"
    if db_path.exists() and not a.reset:
        conn = sqlite3.connect(db_path)
        try:
            n = conn.execute("SELECT COUNT(*) FROM merchant_profiles").fetchone()[0]
        except sqlite3.Error:
            n = 0
        conn.close()
        if n:
            print(f"库里已有 {n} 条店铺数据。想重建请加 --reset（会删掉 data.db / storage / cards）。")
            return
    if a.reset:
        for p in (db_path, APP / "cards", APP / "storage"):
            if p.is_dir():
                shutil.rmtree(p)
            elif p.exists():
                p.unlink()
        print("已清空旧数据")

    server.init_db()
    conn = server.db()

    cur = conn.execute(
        "INSERT INTO merchant_profiles (name, category, plan, quota_left, location, positioning, audience,"
        " tone, goals, pillars, taboo, features, selling_points, platform, owner, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, datetime('now'), datetime('now'))",
        (SHOP["name"], SHOP["category"], "basic", 27, SHOP["location"], SHOP["positioning"],
         SHOP["audience"], SHOP["tone"], SHOP["goals"],
         json.dumps(SHOP["pillars"], ensure_ascii=False), json.dumps(SHOP["taboo"], ensure_ascii=False),
         SHOP["features"], json.dumps(POINTS, ensure_ascii=False), "xiaohongshu", "local"))
    mid = cur.lastrowid

    for mode, title, angle, reason, ctx in TOPICS:
        conn.execute(
            "INSERT INTO topics (merchant_id, mode, title, angle, reason, chips, context, status)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (mid, mode, title, angle, reason, json.dumps([], ensure_ascii=False), ctx,
             "used" if any(c["topic"] == title for c in CONTENTS) else "new"))

    for i, c in enumerate(CONTENTS, start=1):
        payload = {"master": c["master"], "topic_id": c["topic_id"], "topic": c["topic"]}
        cards = c["master"]["cards"]
        norm = []
        for j, card in enumerate(cards):
            kind = "cover" if j == 0 else ("ending" if j == len(cards) - 1 and len(cards) > 2 else "content")
            norm.append({"type": kind, "text": card["text"]})
        payload["cards"] = norm
        conn.execute(
            "INSERT INTO contents (merchant_id, skill, topic, payload, status, model, created_at)"
            " VALUES (?,?,?,?,?,?, datetime('now', ?))",
            (mid, "boss-create", c["topic"], json.dumps(payload, ensure_ascii=False), "draft",
             "kimi-k2.6", f"-{len(CONTENTS) - i} days"))

    for cid, verdict, risk, result in QUALITY:
        conn.execute(
            "INSERT INTO quality_checks (content_id, verdict, risk_level, result, created_at)"
            " VALUES (?,?,?,?, datetime('now'))",
            (cid, verdict, risk, json.dumps(result, ensure_ascii=False)))

    for m_id, topic, dims, total in SCORES:
        conn.execute(
            "INSERT INTO topic_scores (merchant_id, topic, dimensions, total, created_at)"
            " VALUES (?,?,?,?, datetime('now'))",
            (mid, topic, json.dumps(dims, ensure_ascii=False), total))

    for ep, ok in (("/api/selling-points/extract", 1), ("/api/boss-topics", 1),
                   ("/api/boss-create", 1), ("/api/boss-create", 1), ("/api/render-cards", 1),
                   ("/api/quality-check", 1), ("/api/poster/render", 1)):
        conn.execute("INSERT INTO usage_log (merchant_id, endpoint, ok, created_at)"
                     " VALUES (?,?,?, datetime('now'))", (mid, ep, ok))

    # 演示素材：写进按 owner 分好的目录，路径规则与上传接口一致
    mat_dir = server._scope(server.MATERIALS_DIR) / str(mid)
    mat_dir.mkdir(parents=True, exist_ok=True)
    mat_file = mat_dir / "m1700000000000_0.jpg"
    make_demo_material(mat_file)
    conn.execute(
        "INSERT INTO materials (merchant_id, kind, path, orig_name, note, duration, created_at)"
        " VALUES (?,?,?,?,?,?, datetime('now'))",
        (mid, "image", mat_file.relative_to(server.STORAGE_DIR).as_posix(),
         "招牌肠粉.jpg", DEMO_MATERIAL_NOTE, None))

    conn.commit()
    conn.close()
    print(f"演示数据已生成：店铺 #{mid} {SHOP['name']}｜"
          f"{len(TOPICS)} 条选题｜{len(CONTENTS)} 条成品｜{len(POINTS)} 条卖点｜1 个素材")


if __name__ == "__main__":
    main()

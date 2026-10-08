# -*- coding: utf-8 -*-
"""海报健壮性测试：超长文案 / 空字段 / 缺二维码 等极端输入"""
import sys
from pathlib import Path

sys.path.insert(0, "local")
import poster_render as pr  # noqa: E402

OUT = Path("local/_poster_preview/_stress")
OUT.mkdir(parents=True, exist_ok=True)

CASES = [
    ("超长店名+超长标题", "promo-red", {
        "shop": "广州市荔湾区阿珍广式肠粉餐饮管理有限公司",
        "title": "十一黄金周全场五折大酬宾活动开始了",
        "subtitle": "进店即享优惠数量有限先到先得送完为止",
        "price": "1288.88", "origin": "原价 2588 元，会员再减 100 元",
        "benefit": "买二送一 · 满 50 减 10 · 会员双倍积分 · 生日免单",
        "date": "2026 年 10 月 1 日至 2026 年 10 月 7 日每天 9:00-22:00",
        "address": "五华县水寨镇华兴北路与环城路交叉口往东 200 米路南侧第三间店铺",
        "phone": "13800000000 / 0753-1234567", "qr_link": "https://example.com/very/long/path?a=1&b=2"}),
    ("几乎全空", "promo-red", {}),
    ("超长套餐名", "combo-teal", {
        "shop": "阿珍肠粉店", "title": "当季超值团购套餐价目表",
        "i1n": "单人工作日午市精选套餐（含汤+主食+小菜）", "i1p": "1288",
        "i2n": "双人约会浪漫晚餐套餐（含甜品+饮品）", "i2p": "2888",
        "i3n": "家庭聚会八人共享大套餐（含四菜两汤）", "i3p": "8888",
        "note": "到店出示本海报立减 5 元，与其它优惠不叠加使用", "qr_link": ""}),
    ("超长封面标题", "cover-bigword", {
        "tag": "开店日记第 128 天纪录", "title": "我在县城开了一家素食小店",
        "title2": "第九十天终于回本了", "sub": "这条路上踩过的坑和想明白的事", "shop": "阿珍肠粉店"}),
    ("数字超长", "cover-data", {
        "tag": "开店 90 天复盘", "title": "县城素食店经营数据",
        "n1": "1,842,999", "n1_label": "小红书累计浏览量",
        "n2": "312,456 单", "n2_label": "海报带来的到店转化",
        "n3": "1,234,567 元", "n3_label": "省下的投流花费", "shop": "阿珍肠粉店"}),
]

for name, tid, vals in CASES:
    tpl = pr.load_template(tid)
    full = {f["key"]: (vals.get(f["key"]) or f.get("default") or "") for f in tpl["fields"]}
    out = OUT / f"{tid}_{name}.png"
    r = pr.render_poster(tpl, full, out)
    print(f"  {name:16} {tid:14} {out.stat().st_size // 1024:>4}KB  缺必填={r['missing']}")
print("输出:", OUT.resolve())

# -*- coding: utf-8 -*-
"""渲染所有海报模板出预览图，便于检查排版"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "local"))
import poster_render as pr  # noqa: E402

OUT = Path("local/_poster_preview")
OUT.mkdir(parents=True, exist_ok=True)

samples = {
    "promo-red": {"shop": "阿珍肠粉店", "title": "全场五折", "subtitle": "进店即享 · 数量有限",
                  "price": "9.9", "origin": "原价 29.9 元", "benefit": "买二送一 · 满 50 减 10",
                  "date": "10 月 1 日 — 10 月 7 日", "address": "五华县水寨镇华兴北路 128 号",
                  "phone": "138 0000 0000", "qr_link": "https://example.com/shop/123", "tag": "限时特惠"},
    "opening-warm": {"shop": "阿珍肠粉店", "title": "开业大吉", "subtitle": "新店开张 · 全场 8 折",
                     "benefit": "开业三天 全场八折", "date": "9 月 28 日 — 9 月 30 日",
                     "address": "五华县水寨镇华兴北路 128 号", "phone": "138 0000 0000",
                     "qr_link": "https://example.com/shop/123"},
    "combo-teal": {"shop": "阿珍肠粉店", "title": "超值套餐",
                   "i1n": "单人套餐", "i1p": "28", "i2n": "双人套餐", "i2p": "52",
                   "i3n": "家庭套餐", "i3p": "98",
                   "date": "长期有效", "address": "五华县水寨镇华兴北路 128 号",
                   "phone": "138 0000 0000", "qr_link": "https://example.com/shop/123"},
    "festival-red": {"shop": "阿珍肠粉店", "festival": "中秋", "wish": "月圆人团圆",
                     "promo": "团圆套餐 8.8 折", "date": "9 月 29 日 — 10 月 6 日",
                     "qr_link": "https://example.com/shop/123"},
}

only = sys.argv[1:] if len(sys.argv) > 1 else None
for tpl in pr.list_templates():
    if only and tpl["id"] not in only:
        continue
    full = pr.load_template(tpl["id"])
    vals = samples.get(tpl["id"], {})
    vals = {f["key"]: (vals.get(f["key"]) or f.get("default") or "") for f in full["fields"]}
    out = OUT / f"{tpl['id']}.png"
    r = pr.render_poster(full, vals, out)
    size_kb = out.stat().st_size // 1024
    print(f"  {tpl['id']:16} {r['size'][0]}x{r['size'][1]}  {size_kb}KB  缺字段={r['missing']}")
print("输出目录:", OUT.resolve())

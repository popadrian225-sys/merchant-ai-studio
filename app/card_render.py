"""card_render.py — 把笔记卡片文案渲染成 1080×1440（3:4）竖版 PNG。

思路参考 Easel card-xiaohongshu（HTML → 截图渲染管线），但设计系统是自建的三个简化预设：
  cream   奶油温柔（米白底 + 暖棕字）
  swiss   瑞士极简（白底 + 黑字 + 红点缀，网格感）
  journal 手账贴纸（纸纹底 + 胶带 + 手写感标签）

输出：app/cards/<content_id>/card_01_cover.png ...
自检：渲染后检查非背景像素占比（密度门禁的轻量版），过白的卡片会被标记。
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

try:
    from playwright.sync_api import sync_playwright
except Exception:            # 云端沙箱可能没有 playwright 包
    sync_playwright = None

W, H = 1080, 1440
CARDS_DIR = Path(__file__).resolve().parent / "cards"
FONTS_DIR = Path(__file__).resolve().parent / "assets" / "fonts"


def available() -> bool:
    """渲染能力：优先浏览器截图，没有浏览器时用 PIL 兜底（本地/云端都能出图）。"""
    return sync_playwright is not None or (FONTS_DIR / "NotoSansSC-sub.ttf").exists()

# ---------------- 风格预设 ----------------

STYLES = {
    "cream": {
        "name": "奶油温柔",
        "bg": "linear-gradient(170deg,#FBF7F0 0%,#F5EDE2 100%)",
        "ink": "#3B342B",
        "ink2": "#8A7C68",
        "accent": "#B98A4B",
        "chip_bg": "#EFE3D2",
        "font_title": '"Source Han Serif SC","Songti SC","SimSun",serif',
        "font_body": '"PingFang SC","Microsoft YaHei",sans-serif',
        "radius": "40px",
    },
    "swiss": {
        "name": "瑞士极简",
        "bg": "#FFFFFF",
        "ink": "#111111",
        "ink2": "#777777",
        "accent": "#DE3B26",
        "chip_bg": "#F2F2F2",
        "font_title": '"PingFang SC","Microsoft YaHei",sans-serif',
        "font_body": '"PingFang SC","Microsoft YaHei",sans-serif',
        "radius": "0px",
    },
    "journal": {
        "name": "手账贴纸",
        "bg": "repeating-linear-gradient(0deg,#FFFDF7 0 38px,#F7F2E6 38px 39px), #FFFDF7",
        "ink": "#2F2A24",
        "ink2": "#7B7166",
        "accent": "#C4553A",
        "chip_bg": "#FFF0C9",
        "font_title": '"Kaiti SC","KaiTi","STKaiti",serif',
        "font_body": '"PingFang SC","Microsoft YaHei",sans-serif',
        "radius": "18px",
    },
}

CARD_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{ width: {W}px; height: {H}px; overflow: hidden; }}
  body {{
    background: {bg}; color: {ink};
    font-family: {font_body};
    display: flex; flex-direction: column;
    padding: 92px 84px 76px;
    position: relative;
  }}
  .tape {{
    position: absolute; top: -26px; left: 120px; width: 300px; height: 64px;
    background: rgba(226,206,142,.55); transform: rotate(-4deg);
    box-shadow: 0 2px 6px rgba(0,0,0,.06);
  }}
  .head {{ display: flex; align-items: center; gap: 18px; }}
  .chip {{
    background: {chip_bg}; color: {accent};
    font-size: 30px; letter-spacing: 2px; font-weight: 600;
    padding: 12px 26px; border-radius: {radius};
  }}
  .brand {{ font-size: 28px; color: {ink2}; letter-spacing: 1px; }}
  .body {{ flex: 1; display: flex; align-items: center; }}
  .text {{
    font-family: {font_title};
    font-size: {size}px; line-height: 1.42; font-weight: {weight};
    letter-spacing: 1px; white-space: pre-wrap; width: 100%;
    max-height: 900px; overflow: hidden;
  }}
  .accent-bar {{
    width: 96px; height: 10px; background: {accent}; border-radius: 99px;
    margin-bottom: 34px;
  }}
  .foot {{
    display: flex; justify-content: space-between; align-items: flex-end;
    font-size: 26px; color: {ink2}; letter-spacing: 1px;
  }}
  .page {{ font-variant-numeric: tabular-nums; }}
</style></head>
<body>
  {tape}
  <div class="head">
    <span class="chip">{chip}</span>
    <span class="brand">{brand}</span>
  </div>
  <div class="body">
    <div>
      <div class="accent-bar"></div>
      <div class="text" id="t">{text}</div>
    </div>
  </div>
  <div class="foot"><span>{foot_left}</span><span class="page">{page}</span></div>
  <script>
    // 自适应字号：从 size 起逐档缩小，直到不溢出
    const el = document.getElementById('t');
    const cap = 900;
    let s = {size};
    while (el.scrollHeight > cap && s > 34) {{
      s -= 4;
      el.style.fontSize = s + 'px';
    }}
  </script>
</body></html>"""


def _card_html(text: str, kind: str, index: int, total: int, brand: str, style_key: str) -> str:
    st = STYLES.get(style_key, STYLES["cream"])
    text = (text or "").strip()
    if kind == "cover":
        size, weight = 104, 700
        chip, foot_left = "封面", "读完记得收藏"
    elif kind == "ending":
        size, weight = 78, 600
        chip, foot_left = "结尾", "评论区聊聊"
    else:
        size, weight = 76, 500
        # 长文本自动降档
        if len(text) > 60:
            size = 64
        if len(text) > 100:
            size = 56
        chip, foot_left = "正文", "继续往下滑"
    return CARD_HTML.format(
        W=W, H=H,
        bg=st["bg"], ink=st["ink"], ink2=st["ink2"], accent=st["accent"],
        chip_bg=st["chip_bg"], font_title=st["font_title"], font_body=st["font_body"],
        radius=st["radius"],
        size=size, weight=weight,
        tape='<div class="tape"></div>' if style_key == "journal" else "",
        chip=html.escape(chip),
        brand=html.escape(brand[:14]),
        text=html.escape(text),
        foot_left=html.escape(foot_left),
        page=f"{index:02d}/{total:02d}",
    )


def _slug(s: str, limit: int = 12) -> str:
    s = re.sub(r"[\\/:*?\"<>|\s]+", "", s or "")
    return s[:limit] or "card"


# ---------------- PIL 兜底渲染（没有浏览器的环境，如云端沙箱） ----------------

# 风格 → (标题字体文件, 标题默认字重, 正文字体文件)
_PIL_FONTS = {
    "cream":   ("NotoSerifSC-sub.ttf", "SemiBold", "NotoSansSC-sub.ttf"),
    "swiss":   ("NotoSansSC-sub.ttf", "Bold", "NotoSansSC-sub.ttf"),
    "journal": ("NotoSerifSC-sub.ttf", "Medium", "NotoSansSC-sub.ttf"),
}


def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _load_font(fname: str, size: int, weight: str | None = None):
    f = ImageFont.truetype(str(FONTS_DIR / fname), size)
    if weight:
        try:
            f.set_variation_by_name(weight)
        except Exception:
            pass
    return f


_NO_LINE_START = set("，。！？；：、）】」』》！？%％…—·．")


def _wrap_px(draw, text: str, font, max_w: int) -> list[str]:
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur); cur = ""; continue
        if draw.textlength(cur + ch, font=font) > max_w and cur:
            if ch in _NO_LINE_START and lines is not None:
                # 标点不落行首：挤进上一行行尾（视觉允许轻微超宽）
                if cur == "" and lines:
                    lines[-1] += ch
                    continue
            lines.append(cur); cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def _pil_background(style_key: str) -> Image.Image:
    if style_key == "swiss":
        return Image.new("RGB", (W, H), _hex("#FFFFFF"))
    if style_key == "journal":
        img = Image.new("RGB", (W, H), _hex("#FFFDF7"))
        d = ImageDraw.Draw(img)
        for y in range(0, H, 39):
            d.line([(0, y), (W, y)], fill=_hex("#F2ECDC"), width=1)
        return img
    # cream：垂直渐变
    top, bot = _hex("#FBF7F0"), _hex("#F5EDE2")
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / (H - 1)
        d.line([(0, y), (W, y)],
               fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bot)))
    return img


def _pil_tape(img: Image.Image):
    """journal 胶带：旋转的半透明米黄矩形。"""
    tape = Image.new("RGBA", (300, 64), (226, 206, 142, 140))
    tape = tape.rotate(4, expand=True, resample=Image.BICUBIC)
    img.paste(tape, (108, -14), tape)


def _pil_card(text: str, kind: str, index: int, total: int, brand: str,
              style_key: str) -> Image.Image:
    st = STYLES.get(style_key, STYLES["cream"])
    title_font, title_weight_default, body_font = _PIL_FONTS.get(style_key, _PIL_FONTS["cream"])
    text = (text or "").strip()
    if kind == "cover":
        size, weight = 104, "Bold"
        chip, foot_left = "封面", "读完记得收藏"
    elif kind == "ending":
        size, weight = 78, "SemiBold"
        chip, foot_left = "结尾", "评论区聊聊"
    else:
        size, weight = 76, "Medium"
        if len(text) > 60:
            size = 64
        if len(text) > 100:
            size = 56
        chip, foot_left = "正文", "继续往下滑"

    ink, ink2, accent, chip_bg = _hex(st["ink"]), _hex(st["ink2"]), _hex(st["accent"]), _hex(st["chip_bg"])
    pad_x = 84
    text_font = _load_font(title_font, size, weight if style_key != "journal" else "SemiBold")

    img = _pil_background(style_key)
    if style_key == "journal":
        _pil_tape(img)
    d = ImageDraw.Draw(img)

    # 顶部：chip 标签 + 品牌
    chip_f = _load_font(body_font, 30, "SemiBold")
    brand_f = _load_font(body_font, 28)
    cw = d.textlength(chip, font=chip_f)
    cx, cy, cp = 92, 92, 12
    d.rounded_rectangle([cx, cy, cx + cw + cp * 2, cy + 30 + 24],
                        radius=18 if style_key != "swiss" else 0, fill=chip_bg)
    d.text((cx + cp, cy + 12), chip, font=chip_f, fill=accent)
    d.text((cx + cw + cp * 2 + 18, cy + 14), (brand or "")[:14], font=brand_f, fill=ink2)

    # 中部：accent bar + 自适应正文（区域高度上限 900）
    max_text_h, max_text_w = 900, W - pad_x * 2
    while size > 34:
        text_font = _load_font(title_font, size, weight)
        lines = _wrap_px(d, text, text_font, max_text_w)
        line_h = int(size * 1.42)
        if len(lines) * line_h <= max_text_h:
            break
        size -= 4
    block_h = len(lines) * line_h
    area_top, area_bot = 240, 1300
    y0 = area_top + (area_bot - area_top - 44 - block_h) // 2

    bar_y = y0
    d.rounded_rectangle([pad_x + 8, bar_y, pad_x + 8 + 96, bar_y + 10],
                        radius=5 if style_key != "swiss" else 0, fill=accent)
    y = bar_y + 44
    for ln in lines:
        d.text((pad_x + 8, y), ln, font=text_font, fill=ink)
        y += line_h

    # 底部：引导语 + 页码
    foot_f = _load_font(body_font, 26)
    d.text((pad_x + 8, H - 76 - 26), foot_left, font=foot_f, fill=ink2)
    page = f"{index:02d}/{total:02d}"
    d.text((W - pad_x - 8 - d.textlength(page, font=foot_f), H - 76 - 26),
           page, font=foot_f, fill=ink2)
    return img


def _render_cards_pil(cards: list[dict], brand: str, style_key: str,
                      out_dir: Path) -> list[dict]:
    results = []
    total = len(cards)
    for i, c in enumerate(cards, start=1):
        kind = (c.get("type") or "content").lower()
        text = c.get("text") or ""
        name = f"card_{i:02d}_{kind}.png"
        path = out_dir / name
        _pil_card(text, kind, i, total, brand, style_key).save(path)
        results.append({"path": str(path), "name": name, "kind": kind,
                        "text": text, **_density_check(path)})
    return results


def render_cards(note: dict, brand: str = "", style_key: str = "cream",
                 out_dir: Path | None = None) -> list[dict]:
    """把 note（含 cards/titles）渲染为 PNG，返回 [{'path','name','kind','density'}]。
    有浏览器走截图（本地，质感最好）；没有浏览器自动用 PIL 兜底（云端）。"""
    cards = note.get("cards") or []
    if not cards:
        # 没有卡片结构时退化为用标题 + caption 切片
        title = (note.get("titles") or ["笔记"])[0]
        caption = note.get("caption") or ""
        cards = [{"type": "cover", "text": title}]
        chunk = 70
        for i in range(0, min(len(caption), 350), chunk):
            cards.append({"type": "content", "text": caption[i:i + chunk]})
        cards.append({"type": "ending", "text": "想看更多就关注一下，明天继续上新"})

    out_dir = out_dir or (CARDS_DIR / _slug(brand or "cards"))
    out_dir.mkdir(parents=True, exist_ok=True)

    if sync_playwright is not None:
        try:
            return _render_cards_playwright(cards, brand, style_key, out_dir)
        except Exception:
            pass  # 浏览器启动失败（如云端缺组件）→ PIL 兜底
    if not (FONTS_DIR / "NotoSansSC-sub.ttf").exists():
        raise RuntimeError("渲染组件缺失：既没有浏览器也没有内置字体（assets/fonts）")
    return _render_cards_pil(cards, brand, style_key, out_dir)


def _render_cards_playwright(cards: list[dict], brand: str, style_key: str,
                             out_dir: Path) -> list[dict]:
    results: list[dict] = []
    total = len(cards)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-proxy-server"])
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        for i, c in enumerate(cards, start=1):
            kind = (c.get("type") or "content").lower()
            text = c.get("text") or ""
            page.set_content(_card_html(text, kind, i, total, brand, style_key), wait_until="load")
            page.wait_for_timeout(120)
            name = f"card_{i:02d}_{kind}.png"
            path = out_dir / name
            page.screenshot(path=str(path))
            results.append({
                "path": str(path),
                "name": name,
                "kind": kind,
                "text": text,
                **_density_check(path),
            })
        browser.close()
    return results


def _density_check(path: Path) -> dict:
    """轻量密度门禁：非背景像素占比过低说明卡片太空（Easel card_audit 的简化版）。"""
    try:
        from PIL import Image
    except ImportError:
        return {}
    img = Image.open(path).convert("L")
    w, h = img.size
    px = img.load()
    step = 12
    total = dark = 0
    for y in range(0, h, step):
        for x in range(0, w, step):
            total += 1
            if px[x, y] < 215:      # 比浅色底更深 = 有内容
                dark += 1
    ratio = round(dark / max(total, 1), 4)
    return {"density": ratio, "density_ok": ratio > 0.012}


if __name__ == "__main__":
    demo = {
        "titles": ["凌晨4点磨米浆，12年没用过预拌粉"],
        "caption": "老广的肠粉讲究米香。阿珍家凌晨四点开磨，石磨转了12年。",
        "cards": [
            {"type": "cover", "text": "凌晨4点到店磨米浆，12年没用过预拌粉"},
            {"type": "content", "text": "阿珍的闹钟比街坊早3小时，石磨声在巷子里响了12年"},
            {"type": "ending", "text": "明天6点半，第一屉肠粉出锅，来尝就知道不一样"},
        ],
    }
    for key in STYLES:
        out = render_cards(demo, brand="阿珍肠粉店", style_key=key,
                           out_dir=CARDS_DIR / f"_demo_{key}")
        print(key, "→", [f"{c['name']} density={c.get('density')} ok={c.get('density_ok')}" for c in out])

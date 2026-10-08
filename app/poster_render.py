"""poster_render.py — 营销海报 / 封面生成引擎（纯 PIL，无浏览器也能出图）

设计参考 psoho/fast-poster（MIT）的「模板数据模型」：
  一张海报 = 画布 + 一组元素 + 一组占位字段。
  模板存 JSON（assets/poster_templates/*.json），店主只需填字段（店名/活动/价格/地址/二维码），
  渲染器负责排版、自动换行、字号自适应、二维码生成。

元素类型（elements[]）：
  text    文字块：自动换行 + 字号自适应 + 九宫格对齐 + 字间距 + 描边
  badge   胶囊标签：宽度随文字自动撑开
  price   价格大字：货币符号小、数字大、小数小（¥ 9.9 那种层次）
  image   图片：等比裁剪填充 / 完整放入、圆角、描边、透明度
  qrcode  二维码：内容是链接或文字，可配色，可加中心留白
  rect    色块 / 描边框（圆角、透明度）
  circle  装饰圆点
  line    分割线（支持虚线）

模板里字段用 {key} 引用；渲染入口 render_poster(tpl, values, files, out_path)。
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

try:
    import segno
except Exception:          # 没装 segno 时海报仍可出图，只是二维码位置留白
    segno = None

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"
TEMPLATES_DIR = ASSETS_DIR / "poster_templates"

FONT_FILES = {
    "sans": "NotoSansSC-sub.ttf",
    "serif": "NotoSerifSC-sub.ttf",
}

_CN_PUNCT_NO_START = set("，。！？；：、）】」』》％%…—·．,")
_FONT_CACHE: dict[tuple, ImageFont.FreeTypeFont] = {}


def available() -> bool:
    """有字体就能出图（纯 PIL，云端沙箱没有浏览器也照样跑）。"""
    return (FONTS_DIR / FONT_FILES["sans"]).exists()


# ---------------- 基础工具 ----------------

def _hex(c: str, alpha: int | None = None):
    c = (c or "#000000").lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    rgb = tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    return rgb + (alpha,) if alpha is not None else rgb


def _font(name: str = "sans", size: int = 40, weight: str | None = None):
    key = (name, size, weight)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    path = FONTS_DIR / FONT_FILES.get(name, FONT_FILES["sans"])
    f = ImageFont.truetype(str(path), size)
    if weight:
        try:
            f.set_variation_by_name(weight)
        except Exception:
            pass
    _FONT_CACHE[key] = f
    return f


def _text_w(draw: ImageDraw.ImageDraw, s: str, font, spacing: int = 0) -> float:
    if not s:
        return 0.0
    w = draw.textlength(s, font=font)
    return w + spacing * (len(s) - 1) if spacing else w


def _wrap(draw, text: str, font, max_w: float, spacing: int = 0) -> list[str]:
    """按像素宽度折行；标点不落行首。"""
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        if cur and _text_w(draw, cur + ch, font, spacing) > max_w:
            if ch in _CN_PUNCT_NO_START:
                if cur:
                    lines.append(cur + ch)
                    cur = ""
                    continue
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def _ellipsis(lines: list[str], max_lines: int) -> list[str]:
    if len(lines) <= max_lines:
        return lines
    out = lines[:max_lines]
    out[-1] = out[-1].rstrip() + "…"
    return out


def _gradient(size: tuple[int, int], c1: str, c2: str, angle: float = 90) -> Image.Image:
    w, h = size
    img = Image.new("RGB", (w, h), _hex(c1))
    d0, d1 = _hex(c1), _hex(c2)
    a = math.radians(angle)
    dx, dy = math.cos(a), math.sin(a)
    span = abs(dx) * w + abs(dy) * h
    if span <= 0:
        return img
    px = img.load()
    for y in range(h):
        for x in range(w):
            t = (x * dx + y * dy) / span
            t = 0.0 if t < 0 else (1.0 if t > 1 else t)
            px[x, y] = tuple(round(p + (q - p) * t) for p, q in zip(d0, d1))
    return img


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=radius, fill=255)
    return m


def _cover_crop(img: Image.Image, box: tuple[int, int]) -> Image.Image:
    bw, bh = box
    iw, ih = img.size
    scale = max(bw / iw, bh / ih)
    img = img.resize((max(1, round(iw * scale)), max(1, round(ih * scale))), Image.Resampling.LANCZOS)
    iw, ih = img.size
    left, top = (iw - bw) // 2, (ih - bh) // 2
    return img.crop((left, top, left + bw, top + bh))


def _contain(img: Image.Image, box: tuple[int, int]) -> Image.Image:
    bw, bh = box
    iw, ih = img.size
    scale = min(bw / iw, bh / ih)
    return img.resize((max(1, round(iw * scale)), max(1, round(ih * scale))), Image.Resampling.LANCZOS)


# ---------------- 元素渲染 ----------------

def _draw_text(base: Image.Image, draw, el: dict, text: str):
    if not text:
        return
    box_w, box_h = el["w"], el["h"]
    align = el.get("align", "left")
    valign = el.get("valign", "top")
    lh = el.get("lh", 1.35)
    spacing = el.get("spacing", 0)
    max_lines = el.get("max_lines", 0)
    color = _hex(el.get("color", "#222222"))
    stroke_w = el.get("stroke_width", 0)
    stroke_c = _hex(el.get("stroke_color", "#FFFFFF")) if stroke_w else None
    size = el.get("size", 40)
    min_size = el.get("min_size", 14)
    fname, weight = el.get("font", "sans"), el.get("weight")

    lines = _wrap(draw, text, _font(fname, size, weight), box_w, spacing)
    if el.get("fit", True):                      # 字号自适应：装不下就逐档缩小
        while size > min_size:
            font = _font(fname, size, weight)
            lines = _wrap(draw, text, font, box_w, spacing)
            if max_lines:
                lines = _ellipsis(lines, max_lines)
            total_h = len(lines) * size * lh
            widest = max((_text_w(draw, ln, font, spacing) for ln in lines), default=0)
            if total_h <= box_h and widest <= box_w + 1:
                break
            size -= 2
    else:
        if max_lines:
            lines = _ellipsis(lines, max_lines)

    font = _font(fname, size, weight)
    total_h = len(lines) * size * lh
    y = el["y"]
    if valign == "middle":
        y += (box_h - total_h) / 2
    elif valign == "bottom":
        y += box_h - total_h

    for i, ln in enumerate(lines):
        w = _text_w(draw, ln, font, spacing)
        if align == "center":
            x = el["x"] + (box_w - w) / 2
        elif align == "right":
            x = el["x"] + box_w - w
        else:
            x = el["x"]
        ly = y + i * size * lh + (size * lh - size) / 2
        if spacing:
            for ch in ln:
                draw.text((x, ly), ch, font=font, fill=color,
                          stroke_width=stroke_w, stroke_fill=stroke_c)
                x += draw.textlength(ch, font=font) + spacing
        else:
            draw.text((x, ly), ln, font=font, fill=color,
                      stroke_width=stroke_w, stroke_fill=stroke_c)


def _draw_badge(base: Image.Image, draw, el: dict, text: str):
    if not text:
        return
    size = el.get("size", 36)
    weight = el.get("weight", "Bold")
    spacing = el.get("spacing", 0)
    pad_x, pad_y = el.get("pad", [26, 14])
    box_w = el.get("w", 10 ** 9)
    font = _font(el.get("font", "sans"), size, weight)
    tw = _text_w(draw, text, font, spacing)
    while size > 18 and tw + pad_x * 2 > box_w:      # 胶囊装不下就缩小字号，防破版
        size -= 2
        font = _font(el.get("font", "sans"), size, weight)
        tw = _text_w(draw, text, font, spacing)
    bh = round(size * 1.5 + pad_y)
    bw = round(tw + pad_x * 2)
    x, y = el["x"], el["y"]
    if el.get("align") == "center":
        x += (el["w"] - bw) / 2
    elif el.get("align") == "right":
        x += el["w"] - bw
    if el.get("valign") == "middle" and el.get("h"):
        y += (el["h"] - bh) / 2
    radius = el.get("radius", bh / 2)
    layer = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    fill = el.get("fill", "#FFD166")
    ld.rounded_rectangle([0, 0, bw - 1, bh - 1], radius=radius,
                         fill=_hex(fill, el.get("alpha", 255)))
    if el.get("stroke"):
        ld.rounded_rectangle([0, 0, bw - 1, bh - 1], radius=radius, outline=_hex(el["stroke"]),
                             width=el.get("stroke_width", 2))
    base.alpha_composite(layer, (round(x), round(y)))
    draw.text((x + pad_x, y + (bh - size) / 2 - size * 0.08), text, font=font,
              fill=_hex(el.get("color", "#333333")))


def _draw_price(base: Image.Image, draw, el: dict, text: str):
    """价格层次：货币符号小、整数大、小数小。text 形如 9.9 / 128 / ￥128"""
    if not text:
        return
    raw = re.sub(r"[^\d.]", "", str(text))
    if not raw:
        return
    symbol = el.get("symbol", "¥")
    int_part, _, dec_part = raw.partition(".")
    size = el.get("size", 140)
    sym_size = round(size * 0.42)
    dec_size = round(size * 0.5)
    weight = el.get("weight", "Black")
    f_int = _font(el.get("font", "sans"), size, weight)
    f_sym = _font(el.get("font", "sans"), sym_size, el.get("sym_weight", "Bold"))
    f_dec = _font(el.get("font", "sans"), dec_size, weight)
    color = _hex(el.get("color", "#C8302E"))
    pad_x, pad_y = el.get("pad", [24, 10])
    bw = _text_w(draw, symbol, f_sym) + _text_w(draw, int_part, f_int) \
        + (_text_w(draw, "." + dec_part, f_dec) if dec_part else 0) + pad_x * 2
    bh = round(size * 1.28 + pad_y * 2)
    x, y = el["x"], el["y"]
    if el.get("align") == "center":
        x += (el["w"] - bw) / 2
    elif el.get("align") == "right":
        x += el["w"] - bw
    if el.get("valign") == "middle" and el.get("h"):
        y += (el["h"] - bh) / 2
    if el.get("fill"):
        layer = Image.new("RGBA", (round(bw), bh), (0, 0, 0, 0))
        ImageDraw.Draw(layer).rounded_rectangle(
            [0, 0, round(bw) - 1, bh - 1], radius=el.get("radius", bh / 2),
            fill=_hex(el["fill"], el.get("alpha", 255)))
        base.alpha_composite(layer, (round(x), round(y)))
    base_y = y + (bh - size) / 2 - size * 0.06
    cx = x + pad_x
    draw.text((cx, base_y + (size - sym_size)), symbol, font=f_sym, fill=color)
    cx += _text_w(draw, symbol, f_sym)
    draw.text((cx, base_y), int_part, font=f_int, fill=color)
    cx += _text_w(draw, int_part, f_int)
    if dec_part:
        draw.text((cx, base_y + (size - dec_size)), "." + dec_part, font=f_dec, fill=color)


def _draw_rect(base: Image.Image, draw, el: dict):
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    box = [el["x"], el["y"], el["x"] + el["w"], el["y"] + el["h"]]
    radius = el.get("radius", 0)
    if el.get("fill"):
        ld.rounded_rectangle(box, radius=radius, fill=_hex(el["fill"], el.get("alpha", 255)))
    if el.get("stroke"):
        ld.rounded_rectangle(box, radius=radius, outline=_hex(el["stroke"], el.get("alpha", 255)),
                             width=el.get("stroke_width", 2))
    base.alpha_composite(layer)


def _draw_circle(base: Image.Image, draw, el: dict):
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse(
        [el["x"] - el["r"], el["y"] - el["r"], el["x"] + el["r"], el["y"] + el["r"]],
        fill=_hex(el.get("fill", "#FFFFFF"), el.get("alpha", 255)))
    base.alpha_composite(layer)


def _draw_line(base: Image.Image, draw, el: dict):
    x1, y1 = el["x"], el["y"]
    x2 = el.get("x2", x1 + el.get("w", 0))
    y2 = el.get("y2", y1)
    width = el.get("width", 2)
    color = _hex(el.get("color", "#DDDDDD"), el.get("alpha", 255))
    if el.get("dash"):
        seg, gap = el.get("dash", [14, 12])
        total = math.hypot(x2 - x1, y2 - y1)
        if total <= 0:
            return
        ux, uy = (x2 - x1) / total, (y2 - y1) / total
        pos = 0.0
        while pos < total:
            end = min(pos + seg, total)
            draw.line([(x1 + ux * pos, y1 + uy * pos), (x1 + ux * end, y1 + uy * end)],
                      fill=color, width=width)
            pos = end + gap
    else:
        draw.line([(x1, y1), (x2, y2)], fill=color, width=width)


def _draw_image(base: Image.Image, draw, el: dict, path: Path | None):
    if not path or not Path(path).exists():
        return
    try:
        src = Image.open(path).convert("RGBA")
    except Exception:
        return
    box = (round(el["w"]), round(el["h"]))
    img = _cover_crop(src, box) if el.get("fit", "cover") == "cover" else _contain(src, box)
    radius = el.get("radius", 0)
    if radius:
        canvas = Image.new("RGBA", box, (0, 0, 0, 0))
        if img.size != box:
            off = ((box[0] - img.size[0]) // 2, (box[1] - img.size[1]) // 2)
            canvas.paste(img, off, img)
        else:
            canvas = img
        canvas.putalpha(_rounded_mask(box, radius))
        img = canvas
    if el.get("opacity", 1) < 1:
        a = img.getchannel("A").point(lambda v: round(v * el["opacity"]))
        img.putalpha(a)
    x, y = round(el["x"]), round(el["y"])
    if el.get("fit", "cover") == "contain":
        x += (box[0] - img.size[0]) // 2
        y += (box[1] - img.size[1]) // 2
    base.alpha_composite(img, (x, y))
    if el.get("stroke"):
        ImageDraw.Draw(base).rounded_rectangle(
            [el["x"], el["y"], el["x"] + el["w"], el["y"] + el["h"]],
            radius=radius, outline=_hex(el["stroke"]), width=el.get("stroke_width", 3))


def _draw_qrcode(base: Image.Image, draw, el: dict, content: str):
    box = round(min(el["w"], el["h"]))
    x, y = round(el["x"]), round(el["y"])
    if el.get("align") == "center" and el.get("w") != el.get("h"):
        x += (el["w"] - box) // 2
    if not content or segno is None:
        draw.rounded_rectangle([x, y, x + box, y + box], radius=el.get("radius", 12),
                               fill=_hex("#F2F2F2"), outline=_hex("#D0D0D0"), width=1)
        f = _font("sans", max(16, box // 9), "Medium")
        draw.text((x + box / 2, y + box / 2), "二维码", font=f, fill=_hex("#9A9A9A"), anchor="mm")
        return
    pad = el.get("pad", 20)
    inner = box - pad * 2
    bg = el.get("bg", "#FFFFFF")
    if bg:
        draw.rounded_rectangle([x, y, x + box, y + box], radius=el.get("radius", 12), fill=_hex(bg))
    try:
        qr = segno.make(content, error="m")
        png = qr.png_data_uri(scale=1, border=0, dark=el.get("fg", "#1A1A1A") or None)
    except Exception:
        png = None
    if png:
        import base64
        import io
        from urllib.parse import unquote
        raw = base64.b64decode(png.split(",", 1)[1])
        qimg = Image.open(io.BytesIO(raw)).convert("RGBA")
    else:                                  # 兜底：直接用 segno 的 matrix 手绘
        qr = segno.make(content, error="m")
        n = len(qr.matrix)
        qimg = Image.new("RGBA", (n, n), (255, 255, 255, 255))
        qd = ImageDraw.Draw(qimg)
        dark = _hex(el.get("fg", "#1A1A1A"))
        for r, row in enumerate(qr.matrix):
            for c, cell in enumerate(row):
                if cell:
                    qd.point((c, r), fill=dark)
    qimg = qimg.resize((inner, inner), Image.Resampling.NEAREST)
    base.alpha_composite(qimg, (x + pad, y + pad))


# ---------------- 模板与渲染入口 ----------------

def list_templates() -> list[dict]:
    out = []
    if not TEMPLATES_DIR.exists():
        return out
    for f in sorted(TEMPLATES_DIR.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append({
            "id": d.get("id") or f.stem,
            "name": d.get("name", f.stem),
            "category": d.get("category", "海报"),
            "desc": d.get("desc", ""),
            "size": d.get("size", [1080, 1440]),
            "swatch": d.get("swatch", ["#EEEEEE", "#333333"]),
            "fields": d.get("fields", []),
        })
    return out


def load_template(tid: str) -> dict | None:
    for f in TEMPLATES_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if (d.get("id") or f.stem) == tid:
            return d
    return None


def render_poster(tpl: dict, values: dict, out_path: Path,
                  files: dict[str, Path] | None = None) -> dict:
    """按模板渲染一张海报，返回 {path, size, missing}。"""
    files = files or {}
    vals = {f["key"]: (values.get(f["key"]) or f.get("default") or "") for f in tpl.get("fields", [])}
    missing = [f["label"] for f in tpl.get("fields", [])
               if f.get("required") and not str(vals.get(f["key"]) or "").strip()]

    w, h = tpl.get("size", [1080, 1440])
    bg = tpl.get("bg", {"type": "solid", "color": "#FFFFFF"})
    if bg.get("type") == "linear":
        canvas = _gradient((w, h), bg.get("from", "#FFFFFF"), bg.get("to", "#EEEEEE"), bg.get("angle", 90))
        canvas = canvas.convert("RGBA")
    else:
        canvas = Image.new("RGBA", (w, h), _hex(bg.get("color", "#FFFFFF"), 255))

    if bg.get("image"):
        _draw_image(canvas, ImageDraw.Draw(canvas),
                    {"x": 0, "y": 0, "w": w, "h": h, "radius": 0,
                     "opacity": bg.get("image_opacity", 1)}, ASSETS_DIR / bg["image"])

    draw = ImageDraw.Draw(canvas)
    for el in tpl.get("elements", []):
        t = el.get("type", "text")
        text = ""
        if el.get("key"):
            text = str(vals.get(el["key"], "") or "")
        elif el.get("text"):
            text = el["text"]
        if not text and t in ("text", "badge", "price") and el.get("hide_if_empty", True):
            continue                      # 字段没填就不渲染（避免出现空的前缀"地址："）
        if el.get("prefix"):
            text = el["prefix"] + text
        if el.get("suffix"):
            text = text + el["suffix"]
        if t == "text":
            _draw_text(canvas, draw, el, text)
        elif t == "badge":
            _draw_badge(canvas, draw, el, text)
        elif t == "price":
            _draw_price(canvas, draw, el, text)
        elif t == "rect":
            _draw_rect(canvas, draw, el)
        elif t == "circle":
            _draw_circle(canvas, draw, el)
        elif t == "line":
            _draw_line(canvas, draw, el)
        elif t == "image":
            p = files.get(el.get("file_key")) if el.get("file_key") else (
                ASSETS_DIR / el["file"] if el.get("file") else None)
            _draw_image(canvas, draw, el, Path(p) if p else None)
        elif t == "qrcode":
            content = text or el.get("text", "")
            up = files.get(el.get("file_key") or "__qr_image__")   # 店主上传自己的二维码优先
            if up and Path(up).exists():
                _draw_image(canvas, draw, {**el, "fit": "contain"}, Path(up))
            else:
                _draw_qrcode(canvas, draw, el, content)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(out_path, "PNG", optimize=True)
    return {"path": str(out_path), "size": [w, h], "missing": missing}

"""video_render.py — AI 配音营销视频合成（无人出镜，素材画面 + 配音 + 字幕）

流程：
  scenes[{narration, material_path, seconds}]
    → 每段：edge-tts 配音 → 时长=配音时长+0.6s（≥2.5s）
      素材图/视频统一 cover-crop 成 1080×1920 竖版，烧录底部字幕
      无素材时用文字卡片兜底（深色底+白字）
    → ffmpeg concat 拼接成整条 mp4

依赖：ffmpeg（PATH 或 imageio-ffmpeg 自带）、edge-tts、Pillow（中文字体随项目打包）。
"""

import asyncio
import os
import re
import shutil
import subprocess
from pathlib import Path

import edge_tts
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H, FPS = 1080, 1920, 25
ASSETS = Path(__file__).resolve().parent / "assets" / "fonts"
VOICES = {
    "xiaoxiao": {"name": "晓晓（女·亲切）", "id": "zh-CN-XiaoxiaoNeural"},
    "yunyang":  {"name": "云扬（男·稳重）", "id": "zh-CN-YunyangNeural"},
    "yunxi":    {"name": "云希（男·年轻）", "id": "zh-CN-YunxiNeural"},
}

# ---------------- 环境解析（本地 PATH ffmpeg；云端用 imageio-ffmpeg 自带的二进制） ----------------

_ffmpeg_cache: str | None = None


def ffmpeg_exe() -> str:
    global _ffmpeg_cache
    if _ffmpeg_cache:
        return _ffmpeg_cache
    exe = shutil.which("ffmpeg")
    if not exe:
        try:
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            raise RuntimeError("服务器缺少 ffmpeg 组件，视频合成暂不可用")
    _ffmpeg_cache = exe
    return exe


def ffmpeg_available() -> bool:
    try:
        ffmpeg_exe()
        return True
    except RuntimeError:
        return False


# ---------------- 编码器探测（云端精简版 ffmpeg 可能没有 libx264） ----------------

_vcodec_cache: str | None = None


def _list_encoders() -> set[str]:
    """解析 ffmpeg -encoders，返回编码器名集合（失败返回空集）。"""
    try:
        r = subprocess.run([ffmpeg_exe(), "-hide_banner", "-encoders"],
                           capture_output=True, text=True, timeout=60)
        names = set()
        for line in (r.stdout or "").splitlines():
            m = re.match(r"\s*[A-Z.]{6}\s+(\S+)", line)
            if m:
                names.add(m.group(1))
        return names
    except Exception:
        return set()


def encoder_name() -> str:
    """可用视频编码器：libx264 → libopenh264 → mpeg4（ffmpeg 内置，任何构建都有）。
    可用环境变量 VIDEO_ENCODER 强制指定（调试用）。"""
    global _vcodec_cache
    if _vcodec_cache:
        return _vcodec_cache
    forced = os.environ.get("VIDEO_ENCODER", "").strip()
    names = _list_encoders()
    if forced:
        if forced in names or forced == "mpeg4":
            _vcodec_cache = forced
            return forced
    for cand in ("libx264", "libopenh264", "mpeg4"):
        if cand in names:
            _vcodec_cache = cand
            return cand
    raise RuntimeError("服务器的 ffmpeg 没有任何可用的视频编码器，视频合成暂不可用")


def _venc_args() -> list[str]:
    """视频编码参数：x264 系用 preset 提速；mpeg4 不认 -preset，改用 qscale 控质量。"""
    enc = encoder_name()
    if enc == "mpeg4":
        return ["-c:v", "mpeg4", "-q:v", "3", "-pix_fmt", "yuv420p"]
    return ["-c:v", enc, "-preset", "veryfast", "-pix_fmt", "yuv420p"]


def _font_path() -> Path:
    p = ASSETS / "NotoSansSC-sub.ttf"          # 随项目打包（本地/云端一致）
    if p.exists():
        return p
    for c in (r"C:\Windows\Fonts\msyh.ttc",
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
              "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"):
        if Path(c).exists():
            return Path(c)
    raise RuntimeError("找不到可用中文字体（assets/fonts/NotoSansSC-sub.ttf）")


# ---------------- 基础工具 ----------------

def _run(cmd: list[str], timeout: int = 300):
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout)
    if p.returncode != 0:
        tail = ((p.stderr or "") + (p.stdout or "")).strip().splitlines()
        raise RuntimeError("ffmpeg 失败: " + (" / ".join(tail[-2:]) if tail else "未知错误"))
    return p


def probe_duration(path: Path) -> float:
    """优先 ffprobe；没有 ffprobe 时（如 imageio-ffmpeg 只带 ffmpeg）解析 ffmpeg 输出。"""
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            p = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True, text=True, timeout=30)
            return float(p.stdout.strip())
        except Exception:
            pass
    try:
        p = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
        m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", p.stderr or "")
        if m:
            h, mi, s = m.groups()
            return int(h) * 3600 + int(mi) * 60 + float(s)
    except Exception:
        pass
    return 0.0


def tts(text: str, out_mp3: Path, voice_key: str = "xiaoxiao"):
    """edge-tts 生成配音。同步封装（FastAPI 的 sync 端点跑在线程池里，可直接 asyncio.run）。

    网络兜底：直连试两次（DNS 偶发抖动），仍失败再试本地代理（仅本机开了代理时有用）。
    """
    voice = VOICES.get(voice_key, VOICES["xiaoxiao"])["id"]

    async def _go(proxy: str | None):
        c = edge_tts.Communicate(text, voice, rate="+8%", proxy=proxy)
        await c.save(str(out_mp3))

    errs = []
    for proxy in (None, None, "http://127.0.0.1:10808"):
        try:
            asyncio.run(_go(proxy))
            if out_mp3.exists() and out_mp3.stat().st_size > 0:
                return
            errs.append(f"proxy={proxy}: 生成的文件为空")
        except Exception as e:
            errs.append(f"proxy={proxy}: {e}")
    raise RuntimeError(
        "配音生成失败（edge-tts）：直连微软语音服务与本地代理均不可用 → "
        + " | ".join(errs))


# ---------------- 画面 ----------------

def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(_font_path()), size, index=0)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    """按像素宽度逐字折行（中文没有空格，textwrap 不好使）。"""
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur); cur = ""; continue
        if draw.textlength(cur + ch, font=font) > max_w and cur:
            lines.append(cur); cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def make_text_card(text: str, out_png: Path):
    """无素材兜底：奶油底 + 大字卡片。"""
    img = Image.new("RGB", (W, H), (247, 243, 236))
    d = ImageDraw.Draw(img)
    d.rectangle([60, 60, W - 60, H - 60], outline=(214, 205, 190), width=3)
    font = _font(84)
    lines = _wrap(d, text, font, W - 260)
    total = len(lines) * 130
    y = (H - total) // 2
    for ln in lines:
        w = d.textlength(ln, font=font)
        d.text(((W - w) / 2, y), ln, font=font, fill=(56, 50, 42))
        y += 130
    img.save(out_png)


def make_subtitle_png(text: str, out_png: Path):
    """底部字幕：半透明圆角黑条 + 白字，导出透明 PNG 供 overlay。"""
    font = _font(58)
    pad_x, line_h = 44, 84
    tmp = Image.new("RGBA", (W, 400), (0, 0, 0, 0))
    d = ImageDraw.Draw(tmp)
    lines = _wrap(d, text, font, W - 160 - pad_x * 2)
    box_w = min(W - 120, max(d.textlength(ln, font=font) for ln in lines) + pad_x * 2 + 20)
    box_h = len(lines) * line_h + 44
    layer = Image.new("RGBA", (W, 400), (0, 0, 0, 0))
    d2 = ImageDraw.Draw(layer)
    bx0, by0 = (W - int(box_w)) // 2, 400 - box_h - 10
    d2.rounded_rectangle([bx0, by0, bx0 + int(box_w), by0 + int(box_h)], radius=22, fill=(0, 0, 0, 150))
    y = by0 + 22
    for ln in lines:
        w = d2.textlength(ln, font=font)
        d2.text(((W - w) / 2, y), ln, font=font, fill=(255, 255, 255, 255))
        y += line_h
    layer.save(out_png)


def _cover_filter() -> str:
    """素材统一成 1080×1920 竖版：先放大铺满再居中裁剪。"""
    return (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},fps={FPS}")


# ---------------- 分段渲染 ----------------

def render_scene_image(img_path: Path, seconds: float, voice_mp3: Path,
                       sub_png: Path, out_mp4: Path):
    """图片素材：轻微缓推（zoompan）让画面不死板。输入：0=图 1=配音 2=字幕。"""
    fc = (
        f"[0:v]scale={int(W*1.35)}:{int(H*1.35)}:force_original_aspect_ratio=increase,"
        f"crop={int(W*1.35)}:{int(H*1.35)},"
        f"zoompan=z='min(1+0.0009*on,1.18)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d={int(seconds*FPS)+FPS}:s={W}x{H}:fps={FPS}[v0];"
        f"[v0][2:v]overlay=(W-w)/2:H-h-60[v];"
        f"[1:a]aresample=44100,apad[a]"
    )
    _run([
        ffmpeg_exe(), "-y",
        "-loop", "1", "-i", str(img_path),
        "-i", str(voice_mp3),
        "-i", str(sub_png),
        "-filter_complex", fc,
        "-map", "[v]", "-map", "[a]",
        "-t", f"{seconds:.2f}",
        *_venc_args(),
        "-c:a", "aac", "-ar", "44100", "-ac", "2",
        str(out_mp4),
    ], timeout=180)


def render_scene_video(clip_path: Path, seconds: float, voice_mp3: Path,
                       sub_png: Path, out_mp4: Path):
    """视频素材：静音循环补长 → 裁竖版 → 原声弃用，换配音。输入：0=视频 1=配音 2=字幕。"""
    fc = (
        f"[0:v]{_cover_filter()}[v0];"
        f"[v0][2:v]overlay=(W-w)/2:H-h-60[v];"
        f"[1:a]aresample=44100,apad[a]"
    )
    _run([
        ffmpeg_exe(), "-y",
        "-stream_loop", "-1", "-i", str(clip_path),
        "-i", str(voice_mp3),
        "-i", str(sub_png),
        "-filter_complex", fc,
        "-map", "[v]", "-map", "[a]",
        "-t", f"{seconds:.2f}",
        *_venc_args(),
        "-c:a", "aac", "-ar", "44100", "-ac", "2",
        str(out_mp4),
    ], timeout=180)


def concat(segments: list[Path], out_mp4: Path):
    lst = out_mp4.parent / (out_mp4.stem + "_list.txt")
    # concat 清单内的相对路径是相对清单文件解析的，统一用绝对路径最稳
    lst.write_text("\n".join(f"file '{s.resolve().as_posix()}'" for s in segments),
                   encoding="utf-8")
    try:
        _run([ffmpeg_exe(), "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
              "-c", "copy", str(out_mp4)], timeout=300)
    except RuntimeError:
        # 参数有细微不一致时兜底重编码
        _run([ffmpeg_exe(), "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
              *_venc_args(),
              "-c:a", "aac", "-ar", "44100", "-ac", "2", str(out_mp4)], timeout=600)
    finally:
        lst.unlink(missing_ok=True)


def render_video(scenes: list[dict], work_dir: Path, out_mp4: Path,
                 voice_key: str = "xiaoxiao", on_progress=None) -> dict:
    """主入口。scenes: [{narration, material_path|None, material_kind, show}]
    返回 {duration, segments}。on_progress(i, total) 用于前端进度轮询。"""
    work_dir.mkdir(parents=True, exist_ok=True)
    segments = []
    total = len(scenes)
    for i, sc in enumerate(scenes):
        if on_progress:
            on_progress(i, total)
        narr = (sc.get("narration") or "").strip()
        if not narr:
            continue
        mp3 = work_dir / f"v_{i:02d}.mp3"
        tts(narr, mp3, voice_key)
        v_dur = probe_duration(mp3)
        dur = max(v_dur + 0.6, 2.5, float(sc.get("seconds") or 0) or 0)
        dur = min(dur, 12.0)

        sub = work_dir / f"s_{i:02d}.png"
        make_subtitle_png(narr, sub)

        seg = work_dir / f"seg_{i:02d}.mp4"
        mat = sc.get("material_path")
        kind = sc.get("material_kind")
        if mat and kind == "video":
            render_scene_video(Path(mat), dur, mp3, sub, seg)
        elif mat:
            render_scene_image(Path(mat), dur, mp3, sub, seg)
        else:
            card = work_dir / f"c_{i:02d}.png"
            make_text_card(sc.get("show") or narr, card)
            render_scene_image(card, dur, mp3, sub, seg)
        segments.append(seg)

    if not segments:
        raise RuntimeError("没有可用的分镜（narration 全为空）")
    if on_progress:
        on_progress(total, total)
    concat(segments, out_mp4)
    return {"duration": round(probe_duration(out_mp4), 1), "segments": len(segments)}


if __name__ == "__main__":
    # 自检：无素材两段（纯文字卡）能不能拼出一条视频
    out = Path(__file__).parent / "cards" / "video_selftest.mp4"
    r = render_video(
        [
            {"narration": "凌晨四点现磨的米浆，你吃过吗", "material_path": None, "show": "招牌肠粉"},
            {"narration": "就在老街菜市场旁，早上七点开锅", "material_path": None, "show": "到店信息"},
        ],
        Path(__file__).parent / "cards" / "video_selftest", out)
    print("自检通过:", r, "→", out)

"""publisher.py — 可选能力：平台代发通道，subprocess 调用外部浏览器发布脚本。

本模块本身不依赖任何第三方发布代码，只是「转接头」：它把发布任务拼成命令行，
交给本机另一套已有的发布脚本执行（脚本路径由环境变量指定）。因此：
- 没有配置 PUBLISH_HOME 时，available() 返回 False，前端自动隐藏「自动代发」，
  引导用户走「导出成品」闭环 —— 能力探测 + 优雅降级，而不是抛文件路径错误。
- 想启用：把 PUBLISH_HOME 指向那套脚本的根目录（见下），并在 PUBLISH_PY 指定解释器。

支持的四个平台：
- xiaohongshu 小红书 图文（xhs_publish.py，须 --no-proxy 直连，走代理会被判风险 IP）
- douyin      抖音   图文 + 视频（douyin_publish.py；登录可能触发短信验证 → 用 headed 弹窗让老板输入）
- kuaishou    快手   视频（web_publisher.py --platform kuaishou，只收视频）
- channels    微信视频号 视频（web_publisher.py --platform weixin-channels，真机 2026-08 已打通）

不支持：
- 微信朋友圈：个人号没有任何发布接口，只能复制文案 + 存图手动发

登录方式（对老板最省事）：headless 打开登录页 → 把二维码抠成 PNG → 前端展示 →
老板用手机 App 扫一扫 → 登录态持久化（~/.easel-browser-profiles/），下次免扫。
登录状态机（login_state.py 写 JSON 供轮询）：starting → qr_ready → scanned → success；
异常分支：expired / error（小红书 IP 风险 / 抖音 sms_required）。

云端说明：这些脚本和浏览器登录态都只在本机，云端部署时 available() 返回 False，
上层据此给「该功能只在本地版可用」的明确提示，而不是抛文件路径错误。
"""

import json
import os
import subprocess
import time
from pathlib import Path

# 外部发布脚本位置：PUBLISH_HOME 下需有 skills/shared/scripts/<脚本名>
PUBLISH_HOME = os.environ.get("PUBLISH_HOME", "")
PUBLISH_PY = os.environ.get("PUBLISH_PY", "")
EASEL_SCRIPTS = Path(PUBLISH_HOME) / "skills" / "shared" / "scripts" if PUBLISH_HOME else None
VENV_PY = Path(PUBLISH_PY) if PUBLISH_PY else (
    Path(PUBLISH_HOME) / ".venv" / "Scripts" / "python.exe" if PUBLISH_HOME else None)

SUPPORTED = {
    "xiaohongshu": {"script": "xhs_publish.py", "label": "小红书", "no_proxy": True},
    "douyin": {"script": "douyin_publish.py", "label": "抖音", "no_proxy": False},
    "kuaishou": {"script": "web_publisher.py", "label": "快手", "no_proxy": False,
                 "platform": "kuaishou", "login_cmd": "login-qr"},
    "channels": {"script": "web_publisher.py", "label": "微信视频号", "no_proxy": False,
                 "platform": "weixin-channels", "login_cmd": "login-qr"},
}

# 进行中的登录进程：platform -> {proc, status: Path, qr: Path, started}
_login_procs: dict[str, dict] = {}

# whoami 结果缓存：真校验要起浏览器（5-15 秒），不能每次列表都跑
_who_cache: dict[str, tuple[float, dict]] = {}
WHO_TTL = 300


def available() -> tuple[bool, str]:
    """本机是否具备发布能力（配了外部脚本路径 + 脚本和解释器都在）。未配置时为 False。"""
    if not EASEL_SCRIPTS or not VENV_PY:
        return False, "未配置外部发布脚本（设环境变量 PUBLISH_HOME / PUBLISH_PY 后可用），自动代发功能已隐藏"
    if not VENV_PY.exists():
        return False, "当前环境没有发布组件（缺 Python 运行时），绑定与自动发布只在本地版可用"
    if not EASEL_SCRIPTS.exists():
        return False, "当前环境没有发布脚本，绑定与自动发布只在本地版可用"
    return True, ""


def _run(platform: str, args: list[str], timeout: int = 420) -> subprocess.CompletedProcess:
    ok, why = available()
    if not ok:
        raise RuntimeError(why)
    meta = SUPPORTED[platform]
    cmd = [str(VENV_PY), str(EASEL_SCRIPTS / meta["script"])]
    # web_publisher 是多平台脚本：子命令后必须跟 --platform
    if meta.get("platform") and args and args[0] in ("whoami", "publish", "login", "login-qr"):
        cmd += args[:1] + ["--platform", meta["platform"]] + args[1:]
    else:
        cmd += args
    if meta["no_proxy"]:
        cmd.append("--no-proxy")
    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"脚本超时（{timeout}s）") from e
    except FileNotFoundError as e:
        raise RuntimeError(f"找不到发布脚本或 Python：{e}") from e


def _last_json(text: str) -> dict | None:
    """从脚本 stdout 里取最后一个 JSON 对象行（whoami / 状态文件都输出单行 JSON）。"""
    for line in reversed((text or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:
                pass
    return None


def whoami(platform: str) -> dict:
    """真校验登录态。返回 {loggedIn, name, avatar, error?}。"""
    if platform not in SUPPORTED:
        return {"loggedIn": False, "error": f"暂不支持 {platform}"}
    try:
        r = _run(platform, ["whoami"], timeout=120)
    except RuntimeError as e:
        return {"loggedIn": False, "error": str(e)}
    data = _last_json(r.stdout)
    if data is None:
        return {"loggedIn": False, "error": ((r.stderr or "") + (r.stdout or ""))[-200:]}
    return data


def whoami_cached(platform: str) -> dict:
    now = time.time()
    hit = _who_cache.get(platform)
    if hit and now - hit[0] < WHO_TTL:
        return hit[1]
    d = whoami(platform)
    _who_cache[platform] = (now, d)
    return d


def start_login(platform: str, login_dir: Path, headed: bool = False, timeout: int = 180) -> dict:
    """启动登录（后台进程）。返回 {ok, qr_url}。老板扫码后用 login_status 轮询。"""
    if platform not in SUPPORTED:
        return {"ok": False, "error": f"暂不支持 {platform}（朋友圈无发布接口）"}
    ok, why = available()
    if not ok:
        return {"ok": False, "error": why}
    old = _login_procs.get(platform)
    if old and old["proc"].poll() is None:
        return {"ok": True, "qr_url": f"/cards/login/{platform}_qr.png", "already": True}

    login_dir.mkdir(parents=True, exist_ok=True)
    status_path = login_dir / f"{platform}_status.json"
    qr_path = login_dir / f"{platform}_qr.png"
    status_path.unlink(missing_ok=True)
    qr_path.unlink(missing_ok=True)

    meta = SUPPORTED[platform]
    args = [meta.get("login_cmd", "login"),
            "--qr-out", str(qr_path), "--status-file", str(status_path), "--timeout", str(timeout)]
    if headed:
        args.append("--headed")
    proc = subprocess.Popen(
        [str(VENV_PY), str(EASEL_SCRIPTS / meta["script"])] + args,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    _login_procs[platform] = {"proc": proc, "status": status_path, "qr": qr_path, "started": time.time()}
    return {"ok": True, "qr_url": f"/cards/login/{platform}_qr.png", "headed": headed}


def login_status(platform: str) -> dict:
    """轮询登录状态。state: none/starting/qr_ready/scanned/success/expired/error。"""
    rec = _login_procs.get(platform)
    if not rec:
        return {"state": "none", "message": "当前没有进行中的登录"}
    data: dict = {}
    if rec["status"].exists():
        try:
            data = json.loads(rec["status"].read_text(encoding="utf-8"))
        except Exception:
            pass
    alive = rec["proc"].poll() is None
    state = data.get("state") or ("running" if alive else "exit")
    out = {
        "state": state,
        "message": data.get("message", ""),
        "proc_alive": alive,
        "qr_url": f"/cards/login/{platform}_qr.png" if rec["qr"].exists() else None,
    }
    if state in ("success", "expired", "error"):
        _login_procs.pop(platform, None)
        if state == "success":
            _who_cache.pop(platform, None)   # 登录态变了，强制下次真校验
    return out


def publish(platform: str, title: str, content: str = "", images: list[str] | None = None,
            tags: list[str] | None = None, exec_mode: bool = False,
            video: str | None = None) -> dict:
    """发布。默认 dry-run（打印步骤不真发）；exec_mode=True 才真发。
    video 传本地 mp4 路径时走视频通道（douyin --video / channels --media）。"""
    if platform not in SUPPORTED:
        return {"ok": False, "error": f"暂不支持 {platform}"}
    if not title:
        return {"ok": False, "error": "缺少标题"}

    if video:
        if platform == "douyin":
            # douyin_publish 的视频走独立子命令 publish-video；标题 ≤30 字，话题写进简介
            args = ["publish-video", "--title", title[:30], "--video", video]
            if content:
                args += ["--content", content]
            if tags:
                args += ["--tags", ",".join(t.lstrip("#").strip() for t in tags if t.strip())]
        elif platform in ("channels", "kuaishou"):
            # web_publisher: --media 视频，话题是 "#xx #xx" 空格串
            # channels 标题截 22 字；快手创作者中心标题上限更短，截 20 字
            limit = 22 if platform == "channels" else 20
            args = ["publish", "--title", title[:limit], "--media", video]
            if content:
                args += ["--desc", content]
            if tags:
                args += ["--tags", " ".join("#" + t.lstrip("#").strip() for t in tags if t.strip())]
        else:
            return {"ok": False, "error": f"{SUPPORTED[platform]['label']} 视频通道未接入（小红书请发图文版）"}
        if exec_mode:
            args.append("--exec")
    else:
        args = ["publish", "--title", title]
        if content:
            args += ["--content", content]
        if images:
            args += ["--images", ",".join(str(i) for i in images)]
        if tags:
            args += ["--tags", ",".join(t.lstrip("#").strip() for t in tags if t.strip())]
        if exec_mode:
            args.append("--exec")

    try:
        r = _run(platform, args, timeout=600 if video else 420)
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}

    if r.returncode != 0:
        lines = [x.strip() for x in ((r.stderr or "") + (r.stdout or "")).splitlines() if x.strip()]
        tail = " / ".join(lines[-2:]) if lines else "未知错误"
        if "未登录" in tail:
            _who_cache.pop(platform, None)
        return {"ok": False, "error": tail[:400]}
    if not exec_mode:
        return {"ok": True, "dry_run": True, "plan": (r.stdout or "")[-1500:]}
    _who_cache.pop(platform, None)
    return {"ok": True}

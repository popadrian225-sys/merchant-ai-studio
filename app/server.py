"""server.py — 小商户内容 SaaS 本地演示版

与 Cloudflare Worker 版（src/index.js）端点、prompt、数据结构完全一致，
差别只在存储：本地用 SQLite（复用 ../schema.sql），线上用 D1。

启动：run_local.bat（或 python server.py），打开 http://localhost:8787
Kimi key 放 config.json（config.example.json 是模板；config.json 不入库）。
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import card_render
import poster_render
import publisher
import prompts as P
import video_render

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "data.db"
SCHEMA_PATH = BASE.parent / "schema.sql"
CONFIG_PATH = BASE / "config.json"
CARDS_DIR = BASE / "cards"
STORAGE_DIR = BASE / "storage"
MATERIALS_DIR = STORAGE_DIR / "materials"
VIDEOS_DIR = STORAGE_DIR / "videos"
PORT = int(os.environ.get("PORT", "8787"))   # 云端沙箱注入 PORT；本地默认 8787
# ffmpeg：本地 PATH 有就用；云端用 imageio-ffmpeg pip 包自带的二进制（见 video_render.ffmpeg_exe）

# ---------------- 配置 ----------------

DEFAULT_CONFIG = {
    "moonshot_api_key": "",
    "base_url": "https://api.moonshot.cn/v1",
    "model": "kimi-k2.6",
    "timeout_sec": 300,
    "admin_token": "",   # 激活码管理接口的密钥，自拟
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    return cfg


# ---------------- 数据库 ----------------

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """先给老库补列，再执行建表脚本。
    顺序不能反：schema.sql 里有引用新列的索引，老表若缺列会直接建索引失败。"""
    conn = db()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "merchant_profiles" in tables:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(merchant_profiles)")}
        for col, ddl in (("location", "TEXT"), ("features", "TEXT"), ("selling_points", "TEXT"),
                         ("owner", "TEXT NOT NULL DEFAULT 'local'")):
            if col not in cols:
                conn.execute(f"ALTER TABLE merchant_profiles ADD COLUMN {col} {ddl}")
    if "topics" in tables:
        tcols = {r[1] for r in conn.execute("PRAGMA table_info(topics)")}
        if "context" not in tcols:
            conn.execute("ALTER TABLE topics ADD COLUMN context TEXT")
    conn.commit()
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log_usage(endpoint: str, ok: bool, merchant_id=None, note: str = ""):
    try:
        conn = db()
        conn.execute(
            "INSERT INTO usage_log (merchant_id, endpoint, ok, note, created_at) VALUES (?,?,?,?,?)",
            (merchant_id, endpoint, 1 if ok else 0, note[:500], now_str()),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ---------------- Kimi ----------------

def extract_json(text: str) -> str:
    """模型偶尔在 JSON 外加文字或围栏，这里兜底剥离。"""
    if "```" in text:
        seg = text.split("```")
        for i, part in enumerate(seg):
            if part.strip().startswith("json"):
                seg[i] = part.strip()[4:]
        text = "".join(seg)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise RuntimeError("模型未返回 JSON：" + text[:200])
    return text[start:end + 1]


_KIMI_LOCK = threading.Lock()  # Kimi 组织并发=1：本地请求串行化，避免自己撞自己的 429


def call_kimi(prompt: str, temperature: float | None = None, timeout: float | None = None) -> dict:
    """temperature=None 时不传该字段——kimi-k2.6 只接受 temperature=1，传其它值会 400。
    timeout=None 用 config 默认；排期表等大 JSON 输出传更大的值。

    并发：Kimi 组织级并发=1，第二个并发请求必定 429。这里用进程锁把请求排成队，
    比让用户看到「限流失败」体验好；再加指数退避兜住偶发 429（如外部程序也在用同一 key）。
    """
    cfg = load_config()
    key = cfg.get("moonshot_api_key")
    if not key:
        raise RuntimeError("未配置 Kimi key：请把 sk- 开头的 key 填进 app/config.json 的 moonshot_api_key")

    url = cfg["base_url"].rstrip("/") + "/chat/completions"

    def do_request(use_json_mode: bool):
        payload = {
            "model": cfg["model"],
            "messages": [{"role": "user", "content": prompt}],
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if use_json_mode:
            payload["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"content-type": "application/json", "authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(req, timeout=timeout or cfg["timeout_sec"]) as resp:
            return json.loads(resp.read().decode("utf-8"))

    BACKOFF = [10, 25, 45, 70]   # 总等待 150s，够另一个请求跑完

    def do_request_retry(use_json_mode: bool):
        """429 指数退避重试；非 429 的 HTTPError 原样抛出（外层识别 400 response_format 降级）。"""
        for attempt in range(len(BACKOFF) + 1):
            try:
                return do_request(use_json_mode)
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < len(BACKOFF):
                    wait = BACKOFF[attempt]
                    print(f"[kimi] 429 限流，{wait}s 后第 {attempt + 2} 次尝试", flush=True)
                    time.sleep(wait)
                    continue
                raise

    waited = False
    if _KIMI_LOCK.locked():
        waited = True
        print("[kimi] 前面的请求还在跑，排队等待…", flush=True)
    with _KIMI_LOCK:
        if waited:
            print("[kimi] 轮到了，开始请求", flush=True)
        try:
            data = do_request_retry(True)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore")
            # 模型/端点不支持 json_object 时退回普通模式
            if e.code == 400 and "response_format" in body:
                data = do_request_retry(False)
            else:
                raise RuntimeError(f"Kimi API {e.code}: {body[:300]}") from e

    text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    usage = data.get("usage") or {}
    return {"data": json.loads(extract_json(text)), "usage": usage}


# ---------------- App ----------------

def seed_licenses():
    """licenses_seed.json 存在且库里还没有码时导入——沙箱重部署不丢码。"""
    f = BASE / "licenses_seed.json"
    if not f.exists():
        return
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(data, list):
        return
    conn = db()
    if conn.execute("SELECT COUNT(*) FROM licenses").fetchone()[0]:
        conn.close()
        return
    for item in data:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO licenses (code, phone, status, plan, note, max_profiles) VALUES (?,?,?,?,?,?)",
                (_norm_code(item.get("code") or ""), _norm_phone(item.get("phone") or "") or None,
                 item.get("status") or "unused", item.get("plan") or "basic",
                 item.get("note") or "", int(item.get("max_profiles") or 3)))
        except Exception:
            pass
    conn.commit()
    conn.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    seed_licenses()
    yield


app = FastAPI(title="小商户内容 SaaS", lifespan=lifespan)

# ---------------- 登录与激活码（一码一人，防转发盗用） ----------------
# 云端必须「激活码 + 手机号」登录：激活码首次登录即绑定该手机号，之后只认这个号，
# 转发给别人也用不了（明确提示"已绑定其它手机号"）。换手机需管理员解绑（admin_license.py）。
# 本地直连（localhost / 127.0.0.1）免登录，owner = 'local'，本地开发不受影响。

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # 去掉易混的 I O 0 1
CODE_PREFIX = "XS"
TOKEN_TTL_DAYS = 180
ANON_OK = ("/api/auth/login", "/api/auth/me", "/api/capabilities", "/api/admin/")

_LOGIN_FAILS: dict[str, list[float]] = {}            # ip -> 失败时间戳（简单登录限速）
FAIL_WINDOW, FAIL_MAX = 600, 20
FORCE_AUTH = os.environ.get("FORCE_AUTH") == "1"     # 本地想测云端登录流程时置 1


def _norm_code(raw: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", raw or "").upper()


def _norm_phone(raw: str) -> str:
    return re.sub(r"\D", "", raw or "")


def gen_code() -> str:
    return CODE_PREFIX + "".join(secrets.choice(CODE_ALPHABET) for _ in range(12))


def pretty_code(code: str) -> str:
    """给老板看的分组格式：XS-ABCD-EFGH-JKLM"""
    c = _norm_code(code)
    return "-".join([c[:2]] + [c[i:i + 4] for i in range(2, len(c), 4)])


def _mask_phone(phone: str) -> str:
    p = _norm_phone(phone)
    return p[:3] + "****" + p[-4:] if len(p) == 11 else p


def _auth_secret() -> str:
    """从固定材料派生，云端重启后 token 不失效；config 里可显式配 auth_secret 覆盖。"""
    cfg = load_config()
    seed = cfg.get("auth_secret") or cfg.get("moonshot_api_key") or "dev-only-secret"
    return hashlib.sha256(("xhs-saas-auth:" + str(seed)).encode()).hexdigest()


def make_token(lic_id: int) -> str:
    exp = int(time.time()) + TOKEN_TTL_DAYS * 86400
    msg = f"{lic_id}.{exp}"
    sig = hmac.new(_auth_secret().encode(), msg.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{msg}.{sig}"


def parse_token(tok: str | None) -> int | None:
    """验签 + 查库，返回 license id（无效返回 None）。"""
    if not tok or tok.count(".") != 2:
        return None
    sid, exp, sig = tok.split(".")
    if not sid.isdigit() or not exp.isdigit():
        return None
    want = hmac.new(_auth_secret().encode(), f"{sid}.{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(want, sig):
        return None
    if int(exp) < time.time():
        return None
    conn = db()
    row = conn.execute("SELECT id FROM licenses WHERE id=? AND status='active'", (int(sid),)).fetchone()
    conn.close()
    return int(sid) if row else None


def _token_diag(tok: str | None) -> str:
    """诊断用：token 校验卡在哪一步（定位线上问题时临时启用）。"""
    if not tok:
        return "no-token"
    if tok.count(".") != 2:
        return f"bad-shape-{tok.count('.')}"
    sid, exp, sig = tok.split(".")
    if not sid.isdigit() or not exp.isdigit():
        # 形状摘要只报长度与前几位，不回显完整 token（token 是登录凭据）
        return (f"non-numeric len={len(tok)} head={tok[:6]!r} "
                f"seg_lens={[len(s) for s in tok.split('.')]}")
    want = hmac.new(_auth_secret().encode(), f"{sid}.{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(want, sig):
        return "sig-mismatch"
    if int(exp) < time.time():
        return "expired"
    conn = db()
    row = conn.execute("SELECT id, status FROM licenses WHERE id=?", (int(sid),)).fetchone()
    n = conn.execute("SELECT COUNT(*) FROM licenses").fetchone()[0]
    conn.close()
    if not row:
        return f"no-row id={sid} total={n} db={DB_PATH}"
    if row["status"] != "active":
        return f"status={row['status']} id={sid} total={n}"
    return f"ok id={sid} total={n}"


# ---------------- 多用户隔离 ----------------
# owner 就是数据归属键：云端 = 'lic:<激活码 id>'（登录后由 token 解出），本地直连 = 'local'。
# 所有表按 owner 过滤；用户文件按 owner 哈希分目录，防止枚举别人的素材/成品。

_CURRENT_OWNER: ContextVar[str] = ContextVar("owner", default="local")


def _owner() -> str:
    return _CURRENT_OWNER.get()


def _owner_slug(owner: str | None = None) -> str:
    o = _owner() if owner is None else owner
    if o in ("", "local"):
        return "local"
    return hashlib.sha1(o.encode()).hexdigest()[:10]


def _scope(base: Path) -> Path:
    """用户级子目录：本地保持原结构（兼容历史文件），云端按 owner 分目录。"""
    if _owner() == "local":
        return base
    d = base / _owner_slug()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _host_is_local(request: Request) -> bool:
    if FORCE_AUTH:          # 本地调试云端登录流程用：FORCE_AUTH=1 时连 localhost 也要求登录
        return False
    host = (request.headers.get("host") or "").split(":")[0].strip().lower()
    return host in ("", "localhost", "127.0.0.1", "::1") or host.endswith(".local")


_TOKEN_RE = re.compile(r"(\d{1,6}\.\d{9,11}\.[0-9a-f]{32})")


def _bearer(request: Request) -> str | None:
    """提取登录 token。云端代理网关可能给 Authorization 值加引号或前后缀污染
    （如 "10.180...\"），按前缀切分会把引号带进 token 导致校验失败，
    所以用正则提取「lic_id.exp.32位hex签名」的 token 形状；query 参数 ?token= 兜底。"""
    for v in (request.headers.get("authorization") or "",
              request.headers.get("x-auth-token") or ""):
        if not v:
            continue
        m = _TOKEN_RE.search(v.strip("\"' "))
        if m:
            return m.group(1)
    q = request.query_params.get("token") if request.query_params else None
    if q:
        m = _TOKEN_RE.search(q.strip("\"' "))
        if m:
            return m.group(1)
    return None


@app.middleware("http")
async def owner_middleware(request: Request, call_next):
    path = request.url.path
    local = _host_is_local(request)
    lic = None
    lid = parse_token(_bearer(request))
    if lid:
        conn = db()
        row = conn.execute("SELECT * FROM licenses WHERE id=?", (lid,)).fetchone()
        conn.close()
        if row and row["status"] == "active":
            lic = dict(row)
    # 本地直连优先单机模式（不理会 token）；云端按 token 归属；FORCE_AUTH 时强制走云端逻辑
    owner = "local" if local else (f"lic:{lic['id']}" if lic else "")
    token = _CURRENT_OWNER.set(owner)
    try:
        if not owner:
            # 未登录：页面本身要放行（前端要能渲染登录页），只拦 API 与用户文件
            if path.startswith("/api/") and not path.startswith(ANON_OK):
                return JSONResponse({"ok": False, "need_login": True,
                                     "error": "请先用「激活码 + 手机号」登录",
                                     "debug": {"host": request.headers.get("host"),
                                               "local": local,
                                               "auth": bool(request.headers.get("authorization")),
                                               "xauth": bool(request.headers.get("x-auth-token")),
                                               "diag": _token_diag(_bearer(request)),
                                               "pid": os.getpid()}},
                                    status_code=401)
            if path.startswith(("/media/", "/cards/")):
                return JSONResponse({"ok": False, "need_login": True,
                                     "error": "请先登录"}, status_code=401)
            return await call_next(request)
        # 已登录：自己的文件目录才可访问（防枚举他人素材/成品）
        if lic and path.startswith(("/media/", "/cards/")):
            slug = _owner_slug(owner)
            parts = [p for p in path.split("/") if p]
            # /cards/<slug>/…            /media/<类别>/<slug>/…
            ok = (parts[0] == "cards" and len(parts) >= 2 and parts[1] == slug) or \
                 (parts[0] == "media" and len(parts) >= 3 and parts[2] == slug)
            if not ok:
                return JSONResponse({"ok": False, "error": "无权访问该文件"}, status_code=403)
        return await call_next(request)
    finally:
        _CURRENT_OWNER.reset(token)


@app.post("/api/auth/login")
def auth_login(body: dict, request: Request):
    """激活码 + 手机号 登录。首次登录把码绑定到手机号，之后只认这个号。"""
    code = _norm_code(body.get("code") or "")
    phone = _norm_phone(body.get("phone") or "")
    ip = request.client.host if request.client else "?"
    now = time.time()

    fails = [t for t in _LOGIN_FAILS.get(ip, []) if now - t < FAIL_WINDOW]
    if len(fails) >= FAIL_MAX:
        _LOGIN_FAILS[ip] = fails
        raise HTTPException(429, "尝试次数过多，请 10 分钟后再试")

    def _fail(msg: str, status: int = 400):
        fails.append(now)
        _LOGIN_FAILS[ip] = fails
        raise HTTPException(status, msg)

    if len(code) < 10:
        _fail("激活码不对，请检查（形如 XS-ABCD-EFGH-JKLM）")
    if not re.fullmatch(r"1[3-9]\d{9}", phone):
        _fail("手机号格式不对，请填 11 位手机号")

    conn = db()
    row = conn.execute("SELECT * FROM licenses WHERE code=?", (code,)).fetchone()
    if not row:
        conn.close()
        _fail("激活码无效，请确认没有输错")
    lic = dict(row)
    if lic["status"] == "disabled":
        conn.close()
        _fail("这个激活码已停用，请联系客服", 403)

    if lic["phone"] and lic["phone"] != phone:
        # 核心防转发：码已绑定别人的手机号
        conn.close()
        log_usage("/api/auth/login", False, None, f"code {code} 手机号不匹配")
        _fail("这个激活码已经绑定到其它手机号了，不能共用。如果是你自己买的，请联系客服换绑。", 403)

    if not lic["phone"]:
        other = conn.execute(
            "SELECT code FROM licenses WHERE phone=? AND status='active' AND id<>?",
            (phone, lic["id"])).fetchone()
        if other:
            conn.close()
            _fail("这个手机号已经用另一个激活码激活过了，请用原来那个码登录", 409)
        conn.execute("UPDATE licenses SET phone=?, status='active', activated_at=?, last_login_at=? WHERE id=?",
                     (phone, now_str(), now_str(), lic["id"]))
        activated_now = True
        log_usage("/api/auth/login", True, None, f"code {code} 首次激活")
    else:
        conn.execute("UPDATE licenses SET last_login_at=? WHERE id=?", (now_str(), lic["id"]))
        activated_now = False
    conn.commit()
    conn.close()
    _LOGIN_FAILS.pop(ip, None)
    chk = db()
    row2 = chk.execute("SELECT status, phone FROM licenses WHERE id=?", (lic["id"],)).fetchone()
    chk.close()
    return {"ok": True, "token": make_token(lic["id"]),
            "owner": f"lic:{lic['id']}",
            "license": {"code": pretty_code(lic["code"]), "phone": _mask_phone(phone),
                        "plan": lic["plan"], "activated_now": activated_now},
            "_pid": os.getpid(), "_db": str(DB_PATH),
            "_after": dict(row2) if row2 else None}


@app.get("/api/auth/me")
def auth_me():
    """前端启动时校验登录态；local 表示本地直连（免登录）。"""
    o = _owner()
    if o == "local":
        return {"ok": True, "mode": "local", "need_login": False, "license": None}
    if not o.startswith("lic:"):
        return {"ok": False, "need_login": True, "error": "请先登录"}
    conn = db()
    row = conn.execute("SELECT * FROM licenses WHERE id=?", (int(o.split(":")[1]),)).fetchone()
    conn.close()
    if not row:
        return {"ok": False, "need_login": True, "error": "登录已失效，请重新登录"}
    return {"ok": True, "mode": "cloud", "need_login": False,
            "license": {"code": pretty_code(row["code"]), "phone": _mask_phone(row["phone"] or ""),
                        "plan": row["plan"]}}


# ---- 激活码管理（远程发码）：云端数据库独立于本地，发码要么调这个接口，要么随包带种子文件 ----

def _admin_ok(request: Request) -> bool:
    tok = (load_config().get("admin_token") or "").strip()
    got = request.headers.get("x-admin-token") or ""
    return bool(tok) and hmac.compare_digest(tok, got)


@app.post("/api/admin/licenses")
def admin_licenses(body: dict, request: Request):
    """action: new | list | bind | reset-phone | disable | enable | export。密钥不对一律 404。"""
    if not _admin_ok(request):
        raise HTTPException(404, "not found")
    act = body.get("action") or "list"
    conn = db()
    try:
        if act == "new":
            made = []
            for _ in range(min(int(body.get("count") or 1), 100)):
                for _try in range(50):
                    code = gen_code()
                    try:
                        conn.execute(
                            "INSERT INTO licenses (code, phone, status, plan, note, max_profiles) VALUES (?,?,?,?,?,?)",
                            (code, _norm_phone(body.get("phone") or "") or None,
                             "active" if body.get("phone") else "unused",
                             body.get("plan") or "basic", body.get("note") or "",
                             int(body.get("max_profiles") or 3)))
                        break
                    except Exception:
                        continue
                else:
                    raise HTTPException(500, "生成失败：连续撞号")
                made.append(pretty_code(code))
            conn.commit()
            return {"ok": True, "codes": made}
        if act == "list" or act == "export":
            sql = ("SELECT code, phone, status, plan, note, max_profiles, created_at, activated_at, last_login_at "
                   "FROM licenses")
            params = []
            if act == "list" and body.get("status"):
                sql += " WHERE status=?"
                params.append(body["status"])
            sql += " ORDER BY id DESC"
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
            for r in rows:
                r["code"] = pretty_code(r["code"])
                if act == "list" and r["phone"]:      # list 打码展示；export 是种子备份，保留原号
                    r["phone"] = _mask_phone(r["phone"])
            return {"ok": True, "licenses": rows}
        code = _norm_code(body.get("code") or "")
        row = conn.execute("SELECT * FROM licenses WHERE code=?", (code,)).fetchone()
        if not row:
            raise HTTPException(404, "激活码不存在")
        lic = dict(row)
        if act == "bind":
            phone = _norm_phone(body.get("phone") or "")
            if not re.fullmatch(r"1[3-9]\d{9}", phone):
                raise HTTPException(400, "手机号格式不对")
            conn.execute("UPDATE licenses SET phone=?, status='active', activated_at=? WHERE id=?",
                         (phone, now_str(), lic["id"]))
        elif act == "reset-phone":
            conn.execute("UPDATE licenses SET phone=NULL, status='unused', activated_at=NULL WHERE id=?",
                         (lic["id"],))
        elif act == "disable":
            conn.execute("UPDATE licenses SET status='disabled' WHERE id=?", (lic["id"],))
        elif act == "enable":
            conn.execute("UPDATE licenses SET status=? WHERE id=?",
                         ("active" if lic["phone"] else "unused", lic["id"]))
        else:
            raise HTTPException(400, f"未知动作：{act}")
        conn.commit()
        return {"ok": True, "code": pretty_code(code)}
    finally:
        conn.close()


@app.post("/api/admin/diag")
def admin_diag(body: dict, request: Request):
    """云端自诊断：action=env | video | file。密钥不对一律 404。"""
    if not _admin_ok(request):
        raise HTTPException(404, "not found")
    act = body.get("action") or "env"
    import base64
    import subprocess as sp
    ddir = VIDEOS_DIR / "_diag"
    if act == "env":
        exe = video_render.ffmpeg_exe()
        v = sp.run([exe, "-hide_banner", "-version"], capture_output=True, text=True, timeout=30)
        return {"ok": True, "exe": exe,
                "version": (v.stdout or "").splitlines()[:1],
                "encoder": video_render.encoder_name()}
    if act == "video":
        # 用与生产完全相同的 render_scene_image 造一段 2 秒诊断视频
        ddir.mkdir(parents=True, exist_ok=True)
        from PIL import Image, ImageDraw
        img, sub, mp3 = ddir / "d.png", ddir / "d_sub.png", ddir / "d.mp3"
        Image.new("RGB", (1080, 1920), (30, 80, 60)).save(img)
        s = Image.new("RGBA", (1080, 120), (0, 0, 0, 0))
        ImageDraw.Draw(s).text((420, 30), "诊断字幕", fill="white")
        s.save(sub)
        sp.run([video_render.ffmpeg_exe(), "-y", "-f", "lavfi",
                "-i", "anullsrc=r=44100:cl=stereo", "-t", "2", "-c:a", "aac", str(mp3)],
               capture_output=True, timeout=60)
        out = ddir / "diag.mp4"
        video_render.render_scene_image(img, 2.0, mp3, sub, out)
        return {"ok": True, "size": out.stat().st_size,
                "duration": video_render.probe_duration(out),
                "head_b64": base64.b64encode(out.read_bytes()[:16]).decode()}
    if act == "file":
        f = ddir / "diag.mp4"
        if not f.exists():
            raise HTTPException(404, "先跑 action=video")
        return FileResponse(f, media_type="video/mp4", filename="diag.mp4")
    raise HTTPException(404, "not found")


@app.get("/api/capabilities")
def capabilities():
    """前端据此显示/隐藏只在本地可用的功能（云端没有发布组件与 ffmpeg）。"""
    pub_ok, pub_why = publisher.available()
    try:
        venc = video_render.encoder_name()
    except Exception:
        venc = ""
    return {"ok": True, "capabilities": {
        "publish": pub_ok, "publish_reason": pub_why,
        "video": bool(venc), "video_encoder": venc,
        "cards": card_render.available(),
        "posters": poster_render.available(),
    }}

CARDS_DIR.mkdir(exist_ok=True)
app.mount("/cards", StaticFiles(directory=CARDS_DIR), name="cards")
LOGIN_DIR = CARDS_DIR / "login"
MATERIALS_DIR.mkdir(parents=True, exist_ok=True)
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=STORAGE_DIR), name="media")


@app.exception_handler(RuntimeError)
async def runtime_error_handler(_request, exc: RuntimeError):
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)


@app.get("/")
def index():
    # no-cache：每次发布新版后，客户普通刷新即可拿到新页面（避免旧 JS 打不开新功能）
    return FileResponse(BASE / "static" / "index.html",
                        headers={"Cache-Control": "no-cache"})


@app.get("/pro")
def pro_page():
    """专业模式（六步全流程），给愿意折腾的人用。"""
    return FileResponse(BASE / "static" / "pro.html",
                        headers={"Cache-Control": "no-cache"})


# ---- 商户画像 ----

@app.post("/api/profiles")
def create_profile(body: dict):
    if not body.get("name"):
        raise HTTPException(400, "缺少 name")
    conn = db()
    if _owner() != "local":                       # 套餐限制：一个激活码能建几个店铺
        used = conn.execute("SELECT COUNT(*) FROM merchant_profiles WHERE owner=?",
                            (_owner(),)).fetchone()[0]
        lic = conn.execute("SELECT max_profiles FROM licenses WHERE id=?",
                           (int(_owner().split(":")[1]),)).fetchone()
        limit = int(lic["max_profiles"]) if lic else 1
        if used >= limit:
            conn.close()
            raise HTTPException(400, f"当前套餐最多建 {limit} 个店铺，需要更多请联系客服")
    cur = conn.execute(
        """INSERT INTO merchant_profiles
           (name, category, phone, plan, quota_left, location, positioning, audience, tone, goals,
            pillars, taboo, features, selling_points, platform, owner, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            body["name"], body.get("category", "餐饮"), body.get("phone"), body.get("plan", "trial"),
            int(body.get("quota_left", 30)), body.get("location"), body.get("positioning"),
            body.get("audience"), body.get("tone"), body.get("goals"),
            json.dumps(body.get("pillars") or [], ensure_ascii=False),
            json.dumps(body.get("taboo") or [], ensure_ascii=False),
            body.get("features"),
            json.dumps(body.get("selling_points") or [], ensure_ascii=False),
            body.get("platform", "xiaohongshu"),
            _owner(), now_str(), now_str(),
        ),
    )
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return {"ok": True, "id": pid}


@app.put("/api/profiles/{pid}")
def update_profile(pid: int, body: dict):
    """商家档案可随时修改（老板改店名/换招牌/加雷区/改特色）。"""
    fields = ["name", "category", "location", "positioning", "audience", "tone", "goals", "features"]
    sets, vals = [], []
    for f in fields:
        if f in body:
            sets.append(f"{f}=?")
            vals.append(body[f])
    for f in ("pillars", "taboo", "selling_points"):
        if f in body:
            sets.append(f"{f}=?")
            vals.append(json.dumps(body[f] or [], ensure_ascii=False))
    if not sets:
        raise HTTPException(400, "没有可更新的字段")
    vals += [now_str(), pid, _owner()]
    conn = db()
    _get_profile_or_404(conn, pid, _owner())      # 越权直接 404，不装作成功
    conn.execute(f"UPDATE merchant_profiles SET {', '.join(sets)}, updated_at=? WHERE id=? AND owner=?", vals)
    conn.commit()
    conn.close()
    return {"ok": True, "id": pid}


@app.get("/api/profiles")
def list_profiles(limit: int = 20):
    conn = db()
    rows = conn.execute(
        "SELECT id, name, category, plan, quota_left, location, positioning, audience, tone, goals, pillars, taboo, created_at, owner "
        "FROM merchant_profiles WHERE owner=? ORDER BY id DESC LIMIT ?",
        (_owner(), min(limit, 50)),
    ).fetchall()
    conn.close()
    return {"ok": True, "owner": _owner(), "profiles": [dict(r) for r in rows]}


@app.get("/api/profiles/{pid}")
def get_profile(pid: int):
    conn = db()
    row = conn.execute("SELECT * FROM merchant_profiles WHERE id=? AND owner=?", (pid, _owner())).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "画像不存在")
    p = dict(row)
    for k in ("pillars", "taboo", "selling_points"):
        try:
            p[k] = json.loads(p[k] or "[]")
        except Exception:
            p[k] = []
    return {"ok": True, "profile": p}


# ---- 卖点提炼（商家档案 → 启用卖点） ----

def _get_profile_or_404(conn, mid, owner: str | None = None) -> dict:
    """按 id 取店铺；owner 传了就校验归属（多用户隔离）。"""
    row = conn.execute("SELECT * FROM merchant_profiles WHERE id=?", (mid,)).fetchone()
    if not row:
        raise HTTPException(404, "商家不存在")
    if owner is not None and (dict(row).get("owner") or "local") != owner:
        raise HTTPException(404, "商家不存在")
    return dict(row)


def _selling_points_json(p: dict) -> list:
    try:
        return json.loads(p.get("selling_points") or "[]")
    except Exception:
        return []


@app.post("/api/selling-points/extract")
def extract_selling_points(body: dict):
    """从商家档案（含特色原话）提炼卖点，存库并返回。"""
    mid = body.get("merchant_id")
    conn = db()
    profile = _get_profile_or_404(conn, mid, _owner())
    conn.close()

    pts = P.build_selling_points_prompt(
        profile_text=P.profile_to_text(profile, profile.get("category") or "餐饮"),
        features=profile.get("features") or "",
    )
    out = call_kimi(pts)
    data = out["data"]
    points = [{"point": x.get("point", ""), "detail": x.get("detail", ""), "on": True}
              for x in (data.get("points") or []) if isinstance(x, dict)]
    conn = db()
    conn.execute("UPDATE merchant_profiles SET selling_points=?, updated_at=? WHERE id=?",
                 (json.dumps(points, ensure_ascii=False), now_str(), mid))
    conn.commit()
    conn.close()
    log_usage("/api/selling-points", True, mid)
    return {"ok": True, "summary": data.get("summary", ""), "points": points}


@app.put("/api/selling-points")
def save_selling_points(body: dict):
    """老板编辑/勾选卖点后保存（on=False 的不参与后续选题创作）。"""
    mid = body.get("merchant_id")
    points = body.get("points")
    if mid is None or points is None:
        raise HTTPException(400, "缺少 merchant_id 或 points")
    conn = db()
    conn.execute("UPDATE merchant_profiles SET selling_points=?, updated_at=? WHERE id=?",
                 (json.dumps(points, ensure_ascii=False), now_str(), mid))
    conn.commit()
    conn.close()
    return {"ok": True}


# ---- 选题池（今日推荐 / 7天计划 / 自由生成） ----

@app.post("/api/boss-topics")
def boss_topics(body: dict):
    mid = body.get("merchant_id")
    mode = body.get("mode", "today")
    if mode not in P.TOPIC_MODES:
        raise HTTPException(400, f"未知模式：{mode}")
    chips = body.get("chips") or []
    context = (body.get("context") or "").strip()

    conn = db()
    profile = _get_profile_or_404(conn, mid, _owner())
    points = _selling_points_json(profile)
    conn.close()

    prompt = P.build_boss_topics_prompt(
        profile_text=P.profile_to_text(profile, profile.get("category") or "餐饮"),
        points_text=P.points_to_text(points),
        mode=mode, chips=chips, context=context,
    )
    try:
        out = call_kimi(prompt, timeout=300)
    except RuntimeError as e:
        log_usage("/api/boss-topics", False, mid, str(e))
        raise

    topics = (out["data"].get("topics") or [])[:P.TOPIC_MODES[mode]["count"]]
    now = now_str()
    conn = db()
    ids = []
    for t in topics:
        cur = conn.execute(
            "INSERT INTO topics (merchant_id, mode, title, angle, reason, chips, context, status, created_at) VALUES (?,?,?,?,?,?,?, 'new', ?)",
            (mid, mode, t.get("title", ""), t.get("angle", ""), t.get("reason", ""),
             json.dumps(chips, ensure_ascii=False), context or None, now),
        )
        ids.append(cur.lastrowid)
    conn.commit()
    conn.close()
    log_usage("/api/boss-topics", True, mid)
    return {"ok": True, "topics": [
        {"id": ids[i], "mode": mode, "title": t.get("title", ""), "angle": t.get("angle", ""),
         "reason": t.get("reason", ""), "context": context, "status": "new"} for i, t in enumerate(topics)]}


@app.get("/api/boss-topics")
def list_boss_topics(merchant_id: int, limit: int = 30):
    conn = db()
    _get_profile_or_404(conn, merchant_id, _owner())      # 归属校验
    rows = conn.execute(
        "SELECT id, mode, title, angle, reason, context, status, created_at FROM topics "
        "WHERE merchant_id=? ORDER BY id DESC LIMIT ?",
        (merchant_id, min(limit, 100)),
    ).fetchall()
    conn.close()
    return {"ok": True, "topics": [dict(r) for r in rows]}


@app.delete("/api/boss-topics/{tid}")
def delete_topic(tid: int):
    """老板不要的选题，手动删掉一条（只能删自己店的）。"""
    conn = db()
    row = conn.execute("SELECT merchant_id FROM topics WHERE id=?", (tid,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "选题不存在")
    _get_profile_or_404(conn, row["merchant_id"], _owner())
    conn.execute("DELETE FROM topics WHERE id=?", (tid,))
    conn.commit()
    conn.close()
    return {"ok": True}
    return {"ok": True}


@app.post("/api/boss-topics/{tid}/pick")
def pick_topic(tid: int):
    conn = db()
    row = conn.execute("SELECT * FROM topics WHERE id=?", (tid,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "选题不存在")
    _get_profile_or_404(conn, row["merchant_id"], _owner())
    conn.execute("UPDATE topics SET status='picked' WHERE id=?", (tid,))
    conn.commit()
    conn.close()
    return {"ok": True, "topic": dict(row)}


# ---- 内容创作：选题 → 单一成品（图文/视频共用文案） ----

@app.post("/api/boss-create")
def boss_create(body: dict):
    mid = body.get("merchant_id")
    tid = body.get("topic_id")
    if not mid or not tid:
        raise HTTPException(400, "缺少 merchant_id 或 topic_id")

    conn = db()
    profile = _get_profile_or_404(conn, mid, _owner())
    if (profile.get("quota_left") or 0) <= 0:
        conn.close()
        raise HTTPException(402, "生成次数已用完")
    trow = conn.execute("SELECT * FROM topics WHERE id=? AND merchant_id=?", (tid, mid)).fetchone()
    if not trow:
        conn.close()
        raise HTTPException(404, "选题不存在")
    topic = dict(trow)
    points = _selling_points_json(profile)
    conn.close()

    # 由头：选题时老板补充的话（如「今天中秋节」）继续带到文案里；创作时还能再追加一句
    boss_note = (topic.get("context") or "").strip()
    extra = (body.get("extra") or "").strip()
    if extra:
        boss_note = (boss_note + "；" + extra) if boss_note else extra

    category = profile.get("category") or "餐饮"
    prompt = P.build_boss_create_prompt(
        profile_text=P.profile_to_text(profile, category),
        points_text=P.points_to_text(points),
        topic=topic["title"], angle=topic.get("angle") or "", category=category,
        extra=boss_note,
    )
    try:
        out = call_kimi(prompt, timeout=420)
    except RuntimeError as e:
        log_usage("/api/boss-create", False, mid, str(e))
        raise

    data = out["data"]
    master = data.get("master") or {}
    raw_cards = master.get("cards") or []
    norm = []
    for i, c in enumerate(raw_cards):
        text = c.get("text") if isinstance(c, dict) else str(c)
        kind = "cover" if i == 0 else ("ending" if (i == len(raw_cards) - 1 and len(raw_cards) > 2) else "content")
        norm.append({"type": kind, "text": text or ""})
    data["cards"] = norm
    data["topic_id"] = tid

    conn = db()
    cur = conn.execute(
        "INSERT INTO contents (merchant_id, skill, topic, payload, status, model, created_at) VALUES (?,?,?,?,?,?,?)",
        (mid, "boss-create", data.get("topic") or topic["title"],
         json.dumps(data, ensure_ascii=False), "draft", load_config()["model"], now_str()),
    )
    conn.execute("UPDATE topics SET status='used' WHERE id=?", (tid,))
    conn.execute("UPDATE merchant_profiles SET quota_left = quota_left - 1, updated_at=? WHERE id=?", (now_str(), mid))
    conn.commit()
    cid = cur.lastrowid
    conn.close()
    log_usage("/api/boss-create", True, mid)
    return {"ok": True, "content_id": cid, "result": data}


# ---- 素材库（实拍图/短视频上传，视频创作自动匹配） ----

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp",
               ".mp4", ".mov", ".m4v", ".avi"}


MAX_FILE_MB = {"image": 15, "video": 100}      # 单个文件上限（手机实拍够用）
MAX_MATERIALS_PER_SHOP = 60                     # 每家店最多 60 个素材
MAX_TOTAL_MB_PER_SHOP = 500                     # 每家店素材总量上限


def _materials_usage(conn, merchant_id: int) -> dict:
    rows = conn.execute("SELECT path FROM materials WHERE merchant_id=?", (merchant_id,)).fetchall()
    total = 0
    for r in rows:
        try:
            total += (STORAGE_DIR / r["path"]).stat().st_size
        except OSError:
            pass
    return {"count": len(rows), "bytes": total,
            "count_limit": MAX_MATERIALS_PER_SHOP,
            "bytes_limit": MAX_TOTAL_MB_PER_SHOP * 1024 * 1024}


@app.post("/api/materials")
def upload_materials(merchant_id: int = 0, files: list[UploadFile] = File(...),
                     notes: str | None = None):
    if not merchant_id:
        raise HTTPException(400, "缺少 merchant_id")
    conn = db()
    _get_profile_or_404(conn, merchant_id, _owner())      # 归属校验（多用户隔离）
    usage = _materials_usage(conn, merchant_id)
    conn.close()

    dir_ = _scope(MATERIALS_DIR) / str(merchant_id)   # owner 分目录，互不可见
    dir_.mkdir(parents=True, exist_ok=True)
    note_list = (notes or "").split("|") if notes else []
    saved, skipped = [], []
    count, total_bytes = usage["count"], usage["bytes"]
    for i, f in enumerate(files):
        ext = Path(f.filename or "").suffix.lower()
        if ext not in ALLOWED_EXT:
            skipped.append({"name": f.filename, "reason": "格式不支持（支持 jpg/png/webp/mp4/mov）"})
            continue
        kind = "video" if ext in (".mp4", ".mov", ".m4v", ".avi") else "image"
        label = "视频" if kind == "video" else "图片"
        f.file.seek(0, 2)
        size = f.file.tell()
        f.file.seek(0)
        if size > MAX_FILE_MB[kind] * 1024 * 1024:
            skipped.append({"name": f.filename, "reason": f"{label}超过 {MAX_FILE_MB[kind]}MB，请先压缩"})
            continue
        if count >= MAX_MATERIALS_PER_SHOP:
            skipped.append({"name": f.filename, "reason": f"素材数量已满（上限 {MAX_MATERIALS_PER_SHOP} 个），先删几条再传"})
            continue
        if total_bytes + size > MAX_TOTAL_MB_PER_SHOP * 1024 * 1024:
            skipped.append({"name": f.filename, "reason": f"素材空间已满（上限 {MAX_TOTAL_MB_PER_SHOP}MB），先删几条再传"})
            continue

        fname = f"m{int(time.time()*1000)}_{i}{ext}"
        dest = dir_ / fname
        with dest.open("wb") as out:                     # 分块写，避免大文件读进内存
            while True:
                chunk = f.file.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        count += 1
        total_bytes += dest.stat().st_size
        dur = round(video_render.probe_duration(dest), 2) if kind == "video" else None
        rel = dest.relative_to(STORAGE_DIR).as_posix()
        conn = db()
        cur = conn.execute(
            "INSERT INTO materials (merchant_id, kind, path, orig_name, note, duration, created_at) VALUES (?,?,?,?,?,?,?)",
            (merchant_id, kind, rel, f.filename or fname,
             (note_list[i].strip() if i < len(note_list) else ""), dur, now_str()),
        )
        conn.commit()
        saved.append({"id": cur.lastrowid, "kind": kind, "orig_name": f.filename,
                      "note": note_list[i] if i < len(note_list) else "",
                      "duration": dur, "url": f"/media/{rel}"})
        conn.close()
    if not saved:
        raise HTTPException(400, "；".join(s["reason"] for s in skipped) or "没有可识别的文件")
    return {"ok": True, "materials": saved, "skipped": skipped,
            "usage": {"count": count, "bytes": total_bytes,
                      "count_limit": MAX_MATERIALS_PER_SHOP,
                      "bytes_limit": MAX_TOTAL_MB_PER_SHOP * 1024 * 1024}}


@app.get("/api/materials")
def list_materials(merchant_id: int):
    conn = db()
    _get_profile_or_404(conn, merchant_id, _owner())
    rows = conn.execute(
        "SELECT id, kind, path, orig_name, note, duration, created_at FROM materials "
        "WHERE merchant_id=? ORDER BY id DESC", (merchant_id,)).fetchall()
    usage = _materials_usage(conn, merchant_id)
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["url"] = "/media/" + d["path"].replace("\\", "/")
        out.append(d)
    return {"ok": True, "materials": out, "usage": usage}


@app.put("/api/materials/{mid_}")
def update_material(mid_: int, body: dict):
    """给素材补/改备注（视频分镜匹配就靠备注认素材）。"""
    note = (body or {}).get("note")
    if note is None:
        raise HTTPException(400, "缺少 note")
    conn = db()
    row = conn.execute("SELECT merchant_id FROM materials WHERE id=?", (mid_,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "素材不存在")
    _get_profile_or_404(conn, row["merchant_id"], _owner())
    conn.execute("UPDATE materials SET note=? WHERE id=?", (note.strip(), mid_))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/materials/{mid_}")
def delete_material(mid_: int):
    conn = db()
    row = conn.execute("SELECT * FROM materials WHERE id=?", (mid_,)).fetchone()
    if row:
        _get_profile_or_404(conn, row["merchant_id"], _owner())
        p = STORAGE_DIR / row["path"]
        p.unlink(missing_ok=True)
        conn.execute("DELETE FROM materials WHERE id=?", (mid_,))
        conn.commit()
    conn.close()
    return {"ok": True}


# ---- AI 配音营销视频（分镜脚本 + 合成） ----

def _content_master(conn, content_id: int) -> tuple[dict, dict]:
    """取内容并校验归属（多用户隔离），所有调用点自动受保护。"""
    row = conn.execute("SELECT * FROM contents WHERE id=?", (content_id,)).fetchone()
    if not row:
        raise HTTPException(404, "内容不存在")
    row = dict(row)
    if row.get("merchant_id"):
        _get_profile_or_404(conn, row["merchant_id"], _owner())
    return row, json.loads(row["payload"])


@app.post("/api/video-script")
def video_script(body: dict):
    """成品文案 + 素材清单 → 配音分镜脚本（存进 payload.video_script）。"""
    content_id = body.get("content_id")
    if not content_id:
        raise HTTPException(400, "缺少 content_id")
    conn = db()
    row, payload = _content_master(conn, content_id)
    mid = row["merchant_id"]
    category = "餐饮"
    if mid:
        p = conn.execute("SELECT category FROM merchant_profiles WHERE id=?", (mid,)).fetchone()
        category = (p["category"] if p else None) or category

    ids = body.get("material_ids") or []
    mats = []
    if ids:
        qmarks = ",".join("?" * len(ids))
        rows = conn.execute(f"SELECT * FROM materials WHERE id IN ({qmarks}) AND merchant_id=?",
                            (*ids, mid)).fetchall()
        rows = sorted(rows, key=lambda r: ids.index(r["id"]))
        mats = [dict(r) for r in rows]
    conn.close()

    if not mats:
        raise HTTPException(400, "素材库是空的：先到「素材库」上传店里实拍的照片或视频")

    mats_text = "\n".join(
        f"{i}. [{m['kind']}] {m['orig_name']}"
        + (f"——{m['note']}" if m.get("note") else "（老板没写备注）")
        + (f"（时长 {m['duration']} 秒）" if m.get("duration") else "")
        for i, m in enumerate(mats))
    content_text = json.dumps(payload.get("master") or payload, ensure_ascii=False, indent=1)
    prompt = P.build_video_script_prompt(content_text, mats_text, category)
    try:
        out = call_kimi(prompt, timeout=300)
    except RuntimeError as e:
        log_usage("/api/video-script", False, mid, str(e))
        raise

    data = out["data"]
    scenes = []
    for sc in (data.get("scenes") or []):
        idx = sc.get("material_index")
        if not isinstance(idx, int) or idx < 0 or idx >= len(mats):
            idx = 0  # 模型给错下标就兜到第一条素材，渲染层仍可出片
        m = mats[idx]
        scenes.append({
            "narration": sc.get("narration", ""),
            "show": sc.get("show", ""),
            "seconds": sc.get("seconds", 5),
            "material_id": m["id"],
            "material_path": str((BASE / "storage" / m["path"]).resolve()),
            "material_kind": m["kind"],
        })
    if not scenes:
        raise HTTPException(502, "模型没有返回分镜，请重试一次")

    payload["video_script"] = {"video_title": data.get("video_title") or "", "scenes": scenes}
    conn = db()
    conn.execute("UPDATE contents SET payload=? WHERE id=?",
                 (json.dumps(payload, ensure_ascii=False), content_id))
    conn.commit()
    conn.close()
    log_usage("/api/video-script", True, mid)
    return {"ok": True, "video_script": payload["video_script"]}


# ---- 异步任务：AI 生成的长任务（云端网关 60s 会掐断同步请求）----
# 提交返回 task_id，前端每 2.5s 轮询 /api/tasks/{id} 拿结果。

TASKS: dict[str, dict] = {}
_TASK_LOCK = threading.Lock()


def _err_text(e: Exception) -> str:
    if isinstance(e, HTTPException):
        return str(e.detail)
    return str(e)[:300]


def _task_handlers() -> dict:
    return {
        "extract": extract_selling_points,   # 卖点提炼（40-90s）
        "topics": boss_topics,               # 选题生成（90-180s）
        "create": boss_create,               # 成品文案（90-180s）
        "vscript": video_script,             # 视频分镜（60-120s）
        "render": video_render_api,          # 视频合成（60-180s，本地才有）
    }


@app.post("/api/tasks")
def create_task(body: dict):
    kind = body.get("kind")
    fn = _task_handlers().get(kind)
    if fn is None:
        raise HTTPException(400, f"未知任务类型：{kind}")
    tid = uuid.uuid4().hex[:12]
    owner = _owner()          # 线程里 contextvar 不继承，显式带过去
    with _TASK_LOCK:
        TASKS[tid] = {"status": "running", "kind": kind}

    def _bg():
        _CURRENT_OWNER.set(owner)
        try:
            res = fn(body)
            with _TASK_LOCK:
                TASKS[tid].update({"status": "done", "result": res})
        except Exception as e:
            with _TASK_LOCK:
                TASKS[tid].update({"status": "error", "error": _err_text(e)})

    threading.Thread(target=_bg, daemon=True).start()
    return {"ok": True, "task_id": tid}


@app.get("/api/tasks/{tid}")
def get_task(tid: str):
    with _TASK_LOCK:
        t = dict(TASKS.get(tid) or {})
    if not t:
        raise HTTPException(404, "任务不存在或已过期")
    return {"ok": True, **t}


@app.post("/api/video-render")
def video_render_api(body: dict):
    """把 payload.video_script 合成 mp4。同步等待（4-7 段约 1-2 分钟）。"""
    content_id = body.get("content_id")
    if not content_id:
        raise HTTPException(400, "缺少 content_id")
    conn = db()
    row, payload = _content_master(conn, content_id)
    conn.close()
    vs = payload.get("video_script") or {}
    scenes = vs.get("scenes") or []
    if not scenes:
        raise HTTPException(400, "还没有配音分镜，先生成视频脚本")
    if not video_render.ffmpeg_available():
        raise HTTPException(503, "服务器缺少视频合成组件（ffmpeg），请联系管理员")

    voice = body.get("voice") or "xiaoxiao"
    vdir = _scope(VIDEOS_DIR)                       # owner 分目录，互不可见
    work = vdir / f"{content_id}_work"
    out = vdir / f"{content_id}.mp4"
    try:
        info = video_render.render_video(scenes, work, out, voice_key=voice)
    except RuntimeError as e:
        log_usage("/api/video-render", False, row["merchant_id"], str(e))
        raise HTTPException(502, str(e))

    payload["video_url"] = "/media/" + out.relative_to(STORAGE_DIR).as_posix()
    payload["video_duration"] = info["duration"]
    conn = db()
    conn.execute("UPDATE contents SET payload=? WHERE id=?",
                 (json.dumps(payload, ensure_ascii=False), content_id))
    conn.commit()
    conn.close()
    log_usage("/api/video-render", True, row["merchant_id"])
    return {"ok": True, "video_url": payload["video_url"],
            "duration": info["duration"], "segments": info["segments"]}


@app.get("/api/voices")
def list_voices():
    return {"ok": True, "voices": [
        {"k": k, "name": v["name"]} for k, v in video_render.VOICES.items()]}


# ---- 一键导出（云端版的"最后一公里"：成品打包下载，配合复制文案即可手动发布） ----

EXPORTS_DIR = STORAGE_DIR / "exports"


def _copy_text_of(payload: dict) -> str:
    """兼容两类 payload：boss-create（master.title/body/hashtags）与笔记类（titles/caption）。"""
    master = payload.get("master") or {}
    lines = []
    title = master.get("title") or (payload.get("titles") or [""])[0] or ""
    body = master.get("body") or payload.get("caption") or ""
    tags = master.get("hashtags") or payload.get("hashtags") or []
    if title:
        lines.append(title)
    if body:
        lines.append(body)
    if tags:
        lines.append(" ".join(tags))
    return "\n\n".join(lines)


@app.post("/api/export")
def export_content(body: dict):
    """kind=image：配图 zip（含 copy.txt 文案）；kind=video：已合成 mp4 的下载直链。"""
    content_id = body.get("content_id")
    if not content_id:
        raise HTTPException(400, "缺少 content_id")
    conn = db()
    row, payload = _content_master(conn, content_id)
    brand = ""
    if row["merchant_id"]:
        p = _get_profile_or_404(conn, row["merchant_id"], _owner())   # 归属校验
        brand = p.get("name") or ""
    conn.close()

    if (body.get("kind") or "image") == "video":
        mp4 = _scope(VIDEOS_DIR) / f"{content_id}.mp4"
        if not mp4.exists():
            raise HTTPException(400, "视频还没合成，先点「合成视频」再导出")
        return {"ok": True, "kind": "video",
                "url": "/media/" + mp4.relative_to(STORAGE_DIR).as_posix(),
                "filename": f"video_{content_id}.mp4"}

    style = body.get("style") or "cream"
    if style not in card_render.STYLES:
        style = "cream"
    out_dir = _scope(CARDS_DIR) / f"{content_id}_{style}"
    pngs = sorted(out_dir.glob("card_*.png"))
    if not pngs:      # 还没出过图就现场渲染一份（云端 PIL / 本地浏览器都行）
        try:
            results = card_render.render_cards(payload, brand=brand, style_key=style,
                                               out_dir=out_dir)
        except Exception as e:
            raise HTTPException(502, f"配图渲染失败：{e}")
        pngs = sorted(Path(r["path"]) for r in results)

    zdir = _scope(EXPORTS_DIR)
    zip_path = zdir / f"export_{content_id}_{style}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in pngs:
            z.write(p, p.name)
        z.writestr("copy.txt", _copy_text_of(payload))
    return {"ok": True, "kind": "image",
            "url": "/media/" + zip_path.relative_to(STORAGE_DIR).as_posix(),
            "filename": zip_path.name, "count": len(pngs)}


# ---- 生成笔记 ----

@app.post("/api/notes")
def create_note(body: dict):
    topic = (body.get("topic") or "").strip()
    if not topic:
        raise HTTPException(400, "缺少 topic")

    profile = None
    mid = body.get("merchant_id")
    conn = db()
    if mid:
        row = conn.execute("SELECT * FROM merchant_profiles WHERE id=?", (mid,)).fetchone()
        if not row:
            conn.close()
            raise HTTPException(404, "画像不存在")
        profile = dict(row)
        if (profile.get("quota_left") or 0) <= 0:
            conn.close()
            raise HTTPException(402, "额度已用完")

    prompt = P.build_note_prompt(
        profile_text=P.profile_to_text(profile, body.get("category", "餐饮")),
        topic=topic,
        card_count=int(body.get("card_count") or 6),
        material=body.get("material") or "",
    )

    try:
        out = call_kimi(prompt)
    except RuntimeError as e:
        conn.close()
        log_usage("/api/notes", False, mid, str(e))
        raise
    note = out["data"]

    cur = conn.execute(
        "INSERT INTO contents (merchant_id, skill, topic, payload, status, model, created_at) VALUES (?,?,?,?,?,?,?)",
        (mid, "xhs-note-creator", topic, json.dumps(note, ensure_ascii=False), "draft",
         load_config()["model"], now_str()),
    )
    if mid:
        conn.execute("UPDATE merchant_profiles SET quota_left = quota_left - 1, updated_at=? WHERE id=?", (now_str(), mid))
    conn.commit()
    cid = cur.lastrowid
    conn.close()
    log_usage("/api/notes", True, mid)
    return {"ok": True, "content_id": cid, "note": note, "usage": out["usage"]}


# ---- 一句话生成四平台成品（产品主入口，老板模式） ----

@app.post("/api/one-shot")
def one_shot(body: dict):
    """老板输入一句话 → 一条成品 + 四个平台各自的版本。给最好的那一条，不给选项。"""
    demand = (body.get("demand") or "").strip()
    if not demand:
        raise HTTPException(400, "请写下你今天想发什么")
    mid = body.get("merchant_id")

    conn = db()
    row = conn.execute("SELECT * FROM merchant_profiles WHERE id=?", (mid,)).fetchone() if mid else None
    conn.close()
    if not mid or not row:
        raise HTTPException(400, "请先设置企业定位")
    profile = dict(row)
    if (profile.get("quota_left") or 0) <= 0:
        raise HTTPException(402, "生成次数已用完")

    category = profile.get("category") or "餐饮"
    prompt = P.build_one_shot_prompt(
        profile_text=P.profile_to_text(profile, category),
        demand=demand,
        material=body.get("material") or "",
        category=category,
    )
    try:
        out = call_kimi(prompt, timeout=420)
    except RuntimeError as e:
        log_usage("/api/one-shot", False, mid, str(e))
        raise

    data = out["data"]
    # 归一化卡片结构，供 /api/render-cards 复用（首张=封面，末张=收尾）
    master = data.get("master") or {}
    raw_cards = master.get("cards") or []
    norm = []
    for i, c in enumerate(raw_cards):
        text = c.get("text") if isinstance(c, dict) else str(c)
        if i == 0:
            kind = "cover"
        elif i == len(raw_cards) - 1 and len(raw_cards) > 2:
            kind = "ending"
        else:
            kind = "content"
        norm.append({"type": kind, "text": text or ""})
    data["cards"] = norm
    data["demand"] = demand

    conn = db()
    cur = conn.execute(
        "INSERT INTO contents (merchant_id, skill, topic, payload, status, model, created_at) VALUES (?,?,?,?,?,?,?)",
        (mid, "one-shot", data.get("topic") or demand, json.dumps(data, ensure_ascii=False),
         "draft", load_config()["model"], now_str()),
    )
    conn.execute("UPDATE merchant_profiles SET quota_left = quota_left - 1, updated_at=? WHERE id=?", (now_str(), mid))
    conn.commit()
    cid = cur.lastrowid
    conn.close()
    log_usage("/api/one-shot", True, mid)
    return {"ok": True, "content_id": cid, "result": data, "usage": out["usage"]}


# ---- 质检 ----

@app.post("/api/quality-check")
def quality_check(body: dict):
    content = body.get("content")
    content_id = body.get("content_id")
    merchant_id = body.get("merchant_id")
    conn = db()

    if not content and content_id:
        row = conn.execute("SELECT * FROM contents WHERE id=?", (content_id,)).fetchone()
        if not row:
            conn.close()
            raise HTTPException(404, "内容不存在")
        content = json.dumps(json.loads(row["payload"]), ensure_ascii=False, indent=2)
        merchant_id = merchant_id or row["merchant_id"]
    if not content:
        conn.close()
        raise HTTPException(400, "缺少 content 或 content_id")

    category = body.get("category")
    if merchant_id and not category:
        row = conn.execute("SELECT category FROM merchant_profiles WHERE id=?", (merchant_id,)).fetchone()
        category = row["category"] if row else None
    category = category or "餐饮"

    prompt = P.build_quality_prompt(content=content, platform=body.get("platform", "xiaohongshu"), category=category)
    try:
        out = call_kimi(prompt)
    except RuntimeError as e:
        conn.close()
        log_usage("/api/quality-check", False, merchant_id, str(e))
        raise
    result = out["data"]

    if content_id:
        conn.execute(
            "INSERT INTO quality_checks (content_id, verdict, risk_level, result, created_at) VALUES (?,?,?,?,?)",
            (content_id, result.get("overall_verdict", "warn"), result.get("risk_level", "medium"),
             json.dumps(result, ensure_ascii=False), now_str()),
        )
        conn.commit()
    conn.close()
    log_usage("/api/quality-check", True, merchant_id)
    return {"ok": True, "check": result, "usage": out["usage"]}


# ---- 选题评分（V1 预留）----

@app.post("/api/score-topics")
def score_topics(body: dict):
    topic = (body.get("topic") or "").strip()
    if not topic:
        raise HTTPException(400, "缺少 topic")
    mid = body.get("merchant_id")
    conn = db()
    profile = None
    if mid:
        row = conn.execute("SELECT * FROM merchant_profiles WHERE id=?", (mid,)).fetchone()
        profile = dict(row) if row else None

    prompt = P.build_score_prompt(P.profile_to_text(profile, body.get("category", "餐饮")), topic)
    try:
        out = call_kimi(prompt)
    except RuntimeError as e:
        conn.close()
        log_usage("/api/score-topics", False, mid, str(e))
        raise
    result = out["data"]
    dims = result.get("dimensions") or {}
    result["total"] = P.weighted_total(dims)          # 服务端重算，不信任模型算术

    if mid:
        conn.execute(
            "INSERT INTO topic_scores (merchant_id, topic, dimensions, total, created_at) VALUES (?,?,?,?,?)",
            (mid, topic, json.dumps(dims, ensure_ascii=False), result["total"], now_str()),
        )
        conn.commit()
    conn.close()
    log_usage("/api/score-topics", True, mid)
    return {"ok": True, "score": result}


# ---- 今天发什么：选题矩阵 + 七维快评 ----

@app.post("/api/topic-plan")
def topic_plan(body: dict):
    mid = body.get("merchant_id")
    conn = db()
    profile = None
    if mid:
        row = conn.execute("SELECT * FROM merchant_profiles WHERE id=?", (mid,)).fetchone()
        if not row:
            conn.close()
            raise HTTPException(404, "画像不存在")
        profile = dict(row)

    prompt = P.build_topic_matrix_prompt(
        P.profile_to_text(profile, body.get("category", "餐饮")),
        count=int(body.get("count") or 8),
        context=body.get("context") or "",
    )
    try:
        out = call_kimi(prompt)
    except RuntimeError as e:
        conn.close()
        log_usage("/api/topic-plan", False, mid, str(e))
        raise
    result = out["data"]

    # 服务端重算总分并排序，不信任模型算术
    topics = result.get("topics") or []
    for t in topics:
        t["total"] = P.weighted_total(t.get("dimensions") or {})
    topics.sort(key=lambda t: t["total"], reverse=True)

    now = now_str()
    if mid:
        for t in topics:
            conn.execute(
                "INSERT INTO topic_scores (merchant_id, topic, dimensions, total, created_at) VALUES (?,?,?,?,?)",
                (mid, t.get("topic", ""), json.dumps(t.get("dimensions") or {}, ensure_ascii=False), t["total"], now),
            )
        conn.commit()
    conn.close()
    log_usage("/api/topic-plan", True, mid)
    return {"ok": True, "topics": topics, "usage": out["usage"]}


# ---- 一稿多发（Easel skill-content-repurposing） ----

@app.post("/api/repurpose")
def repurpose(body: dict):
    mid = body.get("merchant_id")
    conn = db()
    profile = None
    if mid:
        row = conn.execute("SELECT * FROM merchant_profiles WHERE id=?", (mid,)).fetchone()
        profile = dict(row) if row else None

    source = body.get("content")
    content_id = body.get("content_id")
    if not source and content_id:
        row = conn.execute("SELECT * FROM contents WHERE id=?", (content_id,)).fetchone()
        if not row:
            conn.close()
            raise HTTPException(404, "内容不存在")
        source = json.dumps(json.loads(row["payload"]), ensure_ascii=False, indent=2)
        mid = mid or row["merchant_id"]
    if not source:
        conn.close()
        raise HTTPException(400, "缺少 content 或 content_id")

    targets = body.get("targets") or ["douyin", "wechat", "weibo"]
    prompt = P.build_repurpose_prompt(P.profile_to_text(profile, body.get("category", "餐饮")), source, targets)
    try:
        out = call_kimi(prompt)
    except RuntimeError as e:
        conn.close()
        log_usage("/api/repurpose", False, mid, str(e))
        raise
    result = out["data"]

    # 落库为一条 content，方便历史回看
    topic = "一稿多发：" + (body.get("topic_label") or f"{len(targets)} 平台改编")
    cur = conn.execute(
        "INSERT INTO contents (merchant_id, skill, topic, payload, status, model, created_at) VALUES (?,?,?,?,?,?,?)",
        (mid, "content-repurposing", topic, json.dumps(result, ensure_ascii=False), "draft",
         load_config()["model"], now_str()),
    )
    conn.commit()
    cid = cur.lastrowid
    conn.close()
    log_usage("/api/repurpose", True, mid)
    return {"ok": True, "content_id": cid, "repurposed": result, "usage": out["usage"]}


# ---- 月度排期表（Easel skill-content-calendar） ----

@app.post("/api/calendar")
def content_calendar(body: dict):
    mid = body.get("merchant_id")
    if not mid:
        raise HTTPException(400, "缺少 merchant_id")
    conn = db()
    row = conn.execute("SELECT * FROM merchant_profiles WHERE id=?", (mid,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "画像不存在")
    profile = dict(row)

    prompt = P.build_calendar_prompt(
        P.profile_to_text(profile),
        frequency=body.get("frequency", "每周 3 条"),
        goal=body.get("goal", "到店引流"),
        context=body.get("context") or "",
    )
    try:
        out = call_kimi(prompt, timeout=420)  # 排期表是大 JSON 输出，180s 会超时
    except RuntimeError as e:
        conn.close()
        log_usage("/api/calendar", False, mid, str(e))
        raise
    result = out["data"]

    topic = f"月度排期（{body.get('goal', '到店引流')} · {body.get('frequency', '每周 3 条')}）"
    cur = conn.execute(
        "INSERT INTO contents (merchant_id, skill, topic, payload, status, model, created_at) VALUES (?,?,?,?,?,?,?)",
        (mid, "content-calendar", topic, json.dumps(result, ensure_ascii=False), "draft",
         load_config()["model"], now_str()),
    )
    conn.commit()
    cid = cur.lastrowid
    conn.close()
    log_usage("/api/calendar", True, mid)
    return {"ok": True, "content_id": cid, "calendar": result, "usage": out["usage"]}


# ---- 图文卡片出图（HTML → PNG，1080×1440） ----

@app.post("/api/render-cards")
def render_cards(body: dict):
    content_id = body.get("content_id")
    if not content_id:
        raise HTTPException(400, "缺少 content_id")
    conn = db()
    row = conn.execute("SELECT * FROM contents WHERE id=?", (content_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "内容不存在")
    if row["skill"] not in ("xhs-note-creator", "one-shot", "boss-create"):
        conn.close()
        raise HTTPException(400, "只有笔记类内容可以渲染卡片图")
    payload = json.loads(row["payload"])
    if not card_render.available():
        conn.close()
        raise HTTPException(503, "配图组件缺失（需要 Pillow 与项目内置字体），请联系管理员")

    brand = ""
    if row["merchant_id"]:
        p = conn.execute("SELECT name FROM merchant_profiles WHERE id=?", (row["merchant_id"],)).fetchone()
        brand = p["name"] if p else ""
    conn.close()

    style = body.get("style") or "cream"
    if style not in card_render.STYLES:
        raise HTTPException(400, f"未知风格：{style}（可选：{'/'.join(card_render.STYLES)}）")

    out_dir = _scope(CARDS_DIR) / f"{content_id}_{style}"
    try:
        results = card_render.render_cards(payload, brand=brand, style_key=style, out_dir=out_dir)
    except Exception as e:  # 渲染失败不写日志库，直接报给前端
        log_usage("/api/render-cards", False, row["merchant_id"], str(e))
        raise HTTPException(502, f"渲染失败：{e}")

    for r in results:
        r["url"] = "/cards/" + out_dir.relative_to(CARDS_DIR).as_posix() + "/" + r["name"]
        del r["path"]
    return {"ok": True, "style": style, "style_name": card_render.STYLES[style]["name"], "images": results}


# ---- 海报生成（模板 + 字段 → PNG，纯 PIL，本地/云端都能出图） ----

@app.get("/api/poster/templates")
def poster_templates():
    """海报模板列表（含字段定义，前端据此渲染表单）。"""
    return {"ok": True, "available": poster_render.available(),
            "templates": poster_render.list_templates() if poster_render.available() else []}


@app.post("/api/poster/render")
async def poster_render_api(template_id: str = Form(...),
                            values: str = Form(default="{}"),
                            qr_image: UploadFile | None = File(None)):
    """按模板渲染海报。values 是 {字段: 值} 的 JSON 字符串；qr_image 可选（店主自己的微信码图）。"""
    if not poster_render.available():
        raise HTTPException(503, "海报组件缺失（需要 Pillow 与项目内置字体），请联系管理员")
    tpl = poster_render.load_template(template_id)
    if not tpl:
        raise HTTPException(404, f"未知海报模板：{template_id}")
    try:
        vals = json.loads(values or "{}")
        if not isinstance(vals, dict):
            raise ValueError
    except Exception:
        raise HTTPException(400, "values 不是合法的 JSON 对象")

    files: dict[str, Path] = {}
    tmp_qr = None
    if qr_image is not None and qr_image.filename:
        data = await qr_image.read()
        if data and len(data) < 8 * 1024 * 1024:
            tmp_qr = _scope(CARDS_DIR) / "_tmp" / f"qr_{secrets.token_hex(6)}"
            tmp_qr.parent.mkdir(parents=True, exist_ok=True)
            suffix = Path(qr_image.filename).suffix or ".png"
            tmp_qr = tmp_qr.with_suffix(suffix)
            tmp_qr.write_bytes(data)
            files["__qr_image__"] = tmp_qr

    out_dir = _scope(CARDS_DIR) / "posters"
    name = f"poster_{template_id}_{int(time.time() * 1000)}.png"
    try:
        r = poster_render.render_poster(tpl, vals, out_dir / name, files=files)
    except Exception as e:
        log_usage("/api/poster/render", False, None, f"tpl={template_id} {e}")
        raise HTTPException(502, f"海报渲染失败：{e}")
    finally:
        if tmp_qr:
            try:
                tmp_qr.unlink()
            except Exception:
                pass
    log_usage("/api/poster/render", True, None, f"tpl={template_id}")
    r["url"] = "/cards/" + (out_dir / name).relative_to(CARDS_DIR).as_posix()
    del r["path"]
    return {"ok": True, **r}


# ---- 发布通道（扫码登录 + 平台代发，Easel 发布脚本封装） ----

def _ensure_card_images(content_id: int, payload: dict, brand: str) -> list[str]:
    """确保卡片 PNG 已渲染，返回绝对路径列表（发布脚本要真文件）。"""
    style = "cream"
    out_dir = _scope(CARDS_DIR) / f"{content_id}_{style}"
    pngs = sorted(out_dir.glob("card_*.png"))
    if not pngs:
        card_render.render_cards(payload, brand=brand, style_key=style, out_dir=out_dir)
        pngs = sorted(out_dir.glob("card_*.png"))
    if not pngs:
        raise HTTPException(502, "配图渲染失败，无法发布图文")
    return [str(p.resolve()) for p in pngs]


def _publish_args(platform: str, payload: dict, skill: str = "") -> dict:
    """把成品映射成对应平台的发布参数。

    boss-create 是单一成品（不分平台各写一版）：小红书/抖音都从 master 取文案，
    标题按平台字数截断，正文直接复用——「一键导入」的适配在发布层做，不在生成层做。
    """
    pf = (payload.get("platforms") or {}).get(platform) or {}
    master = payload.get("master") or {}
    if platform == "xiaohongshu":
        title = (pf.get("title") or master.get("title") or "")[:20]
        body = pf.get("body") or master.get("body") or ""
        tags = pf.get("hashtags") or master.get("hashtags") or []
        return {"title": title, "content": body, "tags": tags}
    if platform == "douyin":
        title = (pf.get("title") or master.get("title") or "")[:55]
        if pf.get("shots") or pf.get("hook"):
            parts = []
            if pf.get("hook"):
                parts.append(pf["hook"])
            for s in (pf.get("shots") or []):
                parts.append(f"{s.get('time','')} {s.get('say','')}")
            body = "\n".join(parts)
        else:
            # 单一成品没有分平台口播稿：正文 + 标签直接带过去
            body = (master.get("body") or "")
        tags = pf.get("hashtags") or master.get("hashtags") or []
        return {"title": title, "content": body, "tags": tags}
    if platform in ("kuaishou", "channels"):
        # 快手/视频号只收视频：图文发布时不走这里（前端已按平台分流），兜底给明确提示
        raise HTTPException(400, f"{'快手' if platform == 'kuaishou' else '微信视频号'}只发视频：先做视频版再发")
    raise HTTPException(400, f"暂不支持 {platform}（朋友圈无发布接口）")


@app.get("/api/channels/status")
def channels_status():
    """各平台绑定状态（whoami 有 5 分钟缓存）。
    没有发布组件（云端）时直接返回「未绑定 + 原因」，不去起浏览器子进程。"""
    ok, why = publisher.available()
    out = {}
    for p, meta in publisher.SUPPORTED.items():
        if not ok:
            out[p] = {"label": meta["label"], "loggedIn": False, "name": "", "error": ""}
            continue
        try:
            w = publisher.whoami_cached(p)
        except Exception:
            w = {}
        out[p] = {"label": meta["label"], "loggedIn": bool(w.get("loggedIn")),
                  "name": w.get("name") or "", "error": w.get("error")}
    return {"ok": True, "channels": out, "publish_available": ok, "publish_reason": why}


@app.post("/api/channels/{platform}/login")
def channel_login(platform: str, body: dict | None = None):
    body = body or {}
    # 抖音登录可能触发短信验证，默认弹窗（headed）让老板能输入；小红书纯扫码 headless 即可
    headed = bool(body.get("headed", platform == "douyin"))
    r = publisher.start_login(platform, LOGIN_DIR, headed=headed)
    if not r.get("ok"):
        raise HTTPException(400, r.get("error", "无法启动登录"))
    return r


@app.get("/api/channels/{platform}/login-status")
def channel_login_status(platform: str):
    return publisher.login_status(platform)


@app.post("/api/publish")
def do_publish(body: dict):
    """把一条成品发到指定平台。默认 dry-run，body.exec=true 才真发。
    成品带 video_url 时走视频通道（抖音/视频号）；图文走卡片图通道。"""
    content_id = body.get("content_id")
    platforms = body.get("platforms") or []
    exec_mode = bool(body.get("exec"))
    if not content_id or not platforms:
        raise HTTPException(400, "缺少 content_id 或 platforms")

    conn = db()
    row = conn.execute("SELECT * FROM contents WHERE id=?", (content_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "内容不存在")
    payload = json.loads(row["payload"])
    brand = ""
    if row["merchant_id"]:
        p = conn.execute("SELECT name FROM merchant_profiles WHERE id=?", (row["merchant_id"],)).fetchone()
        brand = p["name"] if p else ""
    conn.close()

    video_path = None
    if payload.get("video_url"):
        vp = BASE / "storage" / "videos" / f"{content_id}.mp4"
        if not vp.exists():
            raise HTTPException(400, "视频文件不存在，请重新合成")
        video_path = str(vp.resolve())
        master = payload.get("master") or {}
        title = (master.get("title") or payload.get("topic") or "")[:30]
        tags = master.get("hashtags") or []
        results = {}
        for plat in platforms:
            if plat == "moments":
                results[plat] = {"ok": False, "error": "朋友圈视频要手动发：下载视频后用手机发布"}
                continue
            if plat == "xiaohongshu":
                results[plat] = {"ok": False, "error": "小红书发图文版更合适（视频请发抖音/快手/视频号）"}
                continue
            r = publisher.publish(plat, title, content=master.get("body") or "",
                                  tags=tags, exec_mode=exec_mode, video=video_path)
            results[plat] = r
            log_usage("/api/publish" + ("" if exec_mode else ":dry"), bool(r.get("ok")), row["merchant_id"],
                      f"{plat}:video " + (r.get("error") or "")[:200])
        return {"ok": all(r.get("ok") for r in results.values()), "results": results}

    images = _ensure_card_images(content_id, payload, brand)
    results = {}
    for plat in platforms:
        try:
            spec = _publish_args(plat, payload, row["skill"])
        except HTTPException as e:
            results[plat] = {"ok": False, "error": e.detail}
            continue
        r = publisher.publish(plat, spec["title"], spec["content"],
                              images=images, tags=spec["tags"], exec_mode=exec_mode)
        results[plat] = r
        log_usage("/api/publish" + ("" if exec_mode else ":dry"), bool(r.get("ok")), row["merchant_id"],
                  f"{plat}: " + (r.get("error") or "")[:200])
    ok_all = all(r.get("ok") for r in results.values())
    return {"ok": ok_all, "results": results}


# ---- 内容列表 / 质检历史 ----

@app.get("/api/contents")
def list_contents(merchant_id: int, limit: int = 20):
    conn = db()
    _get_profile_or_404(conn, merchant_id, _owner())      # 归属校验
    rows = conn.execute(
        "SELECT id, skill, topic, status, created_at, payload FROM contents WHERE merchant_id=? ORDER BY created_at DESC LIMIT ?",
        (merchant_id, min(limit, 100)),
    ).fetchall()
    conn.close()
    return {"ok": True, "contents": [dict(r) for r in rows]}


@app.get("/api/quality-checks")
def list_checks(content_id: int | None = None, limit: int = 20):
    conn = db()
    if content_id:
        _content_master(conn, content_id)          # 归属校验
        rows = conn.execute(
            "SELECT * FROM quality_checks WHERE content_id=? ORDER BY created_at DESC LIMIT ?",
            (content_id, min(limit, 100)),
        ).fetchall()
    elif _owner() == "local":
        rows = conn.execute(
            "SELECT * FROM quality_checks ORDER BY created_at DESC LIMIT ?", (min(limit, 100),)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT q.* FROM quality_checks q "
            "JOIN contents c ON c.id = q.content_id "
            "JOIN merchant_profiles p ON p.id = c.merchant_id "
            "WHERE p.owner=? ORDER BY q.created_at DESC LIMIT ?",
            (_owner(), min(limit, 100)),
        ).fetchall()
    conn.close()
    return {"ok": True, "checks": [dict(r) for r in rows]}


@app.get("/api/usage")
def usage_summary():
    conn = db()
    if _owner() == "local":
        rows = conn.execute(
            "SELECT endpoint, COUNT(*) AS calls, SUM(ok) AS ok_calls FROM usage_log GROUP BY endpoint"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT u.endpoint, COUNT(*) AS calls, SUM(u.ok) AS ok_calls FROM usage_log u "
            "JOIN merchant_profiles p ON p.id = u.merchant_id "
            "WHERE p.owner=? GROUP BY u.endpoint",
            (_owner(),),
        ).fetchall()
    conn.close()
    return {"ok": True, "usage": [dict(r) for r in rows]}


if __name__ == "__main__":
    import uvicorn

    init_db()
    print(f"\n  小商户内容 SaaS · 本地演示  →  http://localhost:{PORT}\n")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")

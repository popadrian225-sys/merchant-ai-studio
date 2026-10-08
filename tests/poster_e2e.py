# -*- coding: utf-8 -*-
"""海报功能 E2E 测试（FORCE_AUTH 云端模式 + 多用户隔离）"""
import io
import json
import sys
import urllib.request
import urllib.error

BASE = "http://localhost:8794"
CFG = "deploy/local/config.json"


def call(path, data=None, token=None, admin=None, raw=None, ctype="application/json"):
    h = {}
    if ctype:
        h["Content-Type"] = ctype
    if token:
        h["X-Auth-Token"] = token
    if admin:
        h["X-Admin-Token"] = admin
    body = raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
    req = urllib.request.Request(BASE + path, body, h)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


cfg = json.load(open(CFG, encoding="utf-8"))

# 1. 管理员发两张码
st, r = call("/api/admin/licenses", {"action": "new", "count": 2, "note": "poster-e2e"}, admin=cfg["admin_token"])
codes = r["codes"]
print("1) 发码:", st, codes)

# 2. 用户A 登录
st, tokA = call("/api/auth/login", {"code": codes[0], "phone": "13900000001"})
assert st == 200, tokA
tA = tokA["token"]
print("2) 用户A登录:", st, "owner =", tokA["owner"])

# 3. 模板列表
st, tpls = call("/api/poster/templates", token=tA)
print("3) 模板列表:", st, "数量 =", len(tpls.get("templates", [])),
      [t["id"] for t in tpls.get("templates", [])][:8])

# 4. 渲染营销海报（multipart 表单，与前端一致）
import secrets
tpl_id = "promo-red"
vals = {"shop": "测试面馆", "title": "新品上市", "price": "12.8",
        "address": "测试路 1 号", "phone": "13800000000", "qr_link": "https://example.com/a"}
boundary = "----pb" + secrets.token_hex(8)
parts = []
for k, v in (("template_id", tpl_id), ("values", json.dumps(vals, ensure_ascii=False))):
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
parts.append(f'--{boundary}--\r\n'.encode())
body = b"".join(parts)
st, rr = call("/api/poster/render", token=tA, raw=body,
              ctype=f"multipart/form-data; boundary={boundary}")
print("4) 渲染海报:", st, rr.get("url"), "缺字段:", rr.get("missing"))

# 5. 用户B 登录并确认看不到 A 的海报文件
st, tokB = call("/api/auth/login", {"code": codes[1], "phone": "13900000002"})
tB = tokB["token"]
url = rr.get("url", "")
req = urllib.request.Request(BASE + url, headers={"X-Auth-Token": tB})
try:
    urllib.request.urlopen(req, timeout=10)
    print("5) 跨用户访问海报文件: ❌ 竟然成功了（严重问题）")
except urllib.error.HTTPError as e:
    print("5) 跨用户访问海报文件: ✅ 被拦住 HTTP", e.code)
except Exception as e:
    print("5) 跨用户访问海报文件: ✅ 被拦住", type(e).__name__)

# 6. 用户A 自己能访问
req = urllib.request.Request(BASE + url, headers={"X-Auth-Token": tA})
try:
    resp = urllib.request.urlopen(req, timeout=10)
    print("6) 本人访问海报文件: ✅ HTTP", resp.status, len(resp.read()) // 1024, "KB")
except Exception as e:
    print("6) 本人访问海报文件: ❌", e)

# 7. 未知模板
st, r7 = call("/api/poster/render", {"template_id": "nope", "values": {}}, token=tA)
print("7) 未知模板:", st, r7.get("detail", ""))

# 8. 收尾：停用测试码
for c in codes:
    call("/api/admin/licenses", {"action": "disable", "code": c}, admin=cfg["admin_token"])
print("8) 测试码已停用")

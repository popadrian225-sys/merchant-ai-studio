"""admin_license.py — 激活码管理（只在你自己的电脑上跑，不外发）

本地发码（本地版数据库）：
  python admin_license.py new --count 5 --note "10月团购-张老板"
  python admin_license.py list

云端发码（线上版数据库，与本地独立）：
  python admin_license.py --remote https://你的链接 new --count 5 --note "张老板"
  python admin_license.py --remote https://你的链接 list
  python admin_license.py --remote https://你的链接 disable XS-XXXX-XXXX-XXXX

激活码备份（防止云端重部署丢码；导出的文件随部署包一起上传）：
  python admin_license.py --remote https://你的链接 export --out licenses_seed.json

其它：
  bind <码> <手机号>     提前登记手机号（可选）
  reset-phone <码>       客户换手机时解绑
  disable / enable       停用（退款即踢出）/ 重新启用

--token 不填时自动读同目录 config.json 里的 admin_token。
安全模型：一码一人——首次「激活码+手机号」登录即绑定，之后码被转发别人也用不了。
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _load_server():
    """惰性导入 server.py（依赖 fastapi 等本地依赖）；--remote 模式零依赖直接走 HTTP。"""
    g = globals()
    from server import db, gen_code, init_db, pretty_code, _norm_code, _norm_phone  # noqa: E402
    g.update(db=db, gen_code=gen_code, init_db=init_db,
             pretty_code=pretty_code, _norm_code=_norm_code, _norm_phone=_norm_phone)

DEPLOY_CONFIG = Path(__file__).resolve().parent / "config.json"


def default_token() -> str:
    try:
        return json.loads(DEPLOY_CONFIG.read_text(encoding="utf-8")).get("admin_token") or ""
    except Exception:
        return ""


def api_call(remote: str, token: str, payload: dict) -> dict:
    req = urllib.request.Request(remote.rstrip("/") + "/api/admin/licenses",
                                 data=json.dumps(payload).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Admin-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read().decode())
        except Exception:
            d = {}
        print(f"云端返回错误 {e.code}：{d.get('detail') or d.get('error') or '未知'}")
        if e.code == 404:
            print("（密钥不对或云端还没部署管理接口；--token 要填部署实例 config.json 里的 admin_token）")
        sys.exit(1)
    except Exception as e:
        print("连不上云端：", e)
        sys.exit(1)


def print_licenses(rows):
    label = {"unused": "未激活", "active": "使用中", "disabled": "已停用"}
    print(f"\n{'激活码':<22}{'状态':<8}{'绑定手机':<14}{'套餐':<7}{'店铺上限':<9}备注")
    print("-" * 88)
    for r in rows:
        print(f"{r['code']:<22}{label.get(r['status'], r['status']):<8}"
              f"{(r['phone'] or '—'):<14}{r['plan']:<7}{str(r['max_profiles']):<9}{r['note'] or ''}")
    print(f"\n共 {len(rows)} 个；未激活 {sum(1 for r in rows if r['status'] == 'unused')}，"
          f"使用中 {sum(1 for r in rows if r['status'] == 'active')}，"
          f"已停用 {sum(1 for r in rows if r['status'] == 'disabled')}\n")


# ---------------- 本地模式 ----------------

def local_new(a):
    conn = db()
    made = []
    for _ in range(a.count):
        for _try in range(50):
            code = gen_code()
            try:
                conn.execute(
                    "INSERT INTO licenses (code, phone, status, plan, note, max_profiles) VALUES (?,?,?,?,?,?)",
                    (code, _norm_phone(a.phone or "") or None,
                     "active" if a.phone else "unused", a.plan, a.note or "", a.max_profiles))
                break
            except Exception:
                continue
        else:
            print("生成失败：连续撞号，请重试"); sys.exit(1)
        made.append(code)
    conn.commit(); conn.close()
    print(f"\n新生成 {len(made)} 个激活码（{a.plan} 套餐，可建 {a.max_profiles} 个店铺）:\n")
    for c in made:
        print("   ", pretty_code(c))


def local_list(a):
    conn = db()
    sql, params = "SELECT * FROM licenses", []
    if a.status:
        sql += " WHERE status=?"; params.append(a.status)
    sql += " ORDER BY id DESC"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    if not rows:
        print("还没有激活码。用 `python admin_license.py new --count 5` 生成。"); return
    for r in rows:
        if r["phone"]:
            r["phone"] = r["phone"][:3] + "****" + r["phone"][-4:]
        r["code"] = pretty_code(r["code"])
    print_licenses(rows)


def _local_find(conn, raw):
    row = conn.execute("SELECT * FROM licenses WHERE code=?", (_norm_code(raw),)).fetchone()
    if not row:
        print(f"找不到激活码 {pretty_code(raw)}"); sys.exit(1)
    return dict(row)


def local_set(a, sql, params):
    conn = db()
    lic = _local_find(conn, a.code)
    conn.execute(sql, params + [lic["id"]])
    conn.commit(); conn.close()
    print(f"{pretty_code(lic['code'])} → 完成")


def local_export(a):
    conn = db()
    rows = [dict(r) for r in conn.execute(
        "SELECT code, phone, status, plan, note, max_profiles FROM licenses ORDER BY id").fetchall()]
    conn.close()
    Path(a.out).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已导出 {len(rows)} 个激活码 → {a.out}（把这个文件随部署包一起上传，云端就不怕重部署丢码）")


# ---------------- 远程模式 ----------------

def remote_run(a):
    token = a.token or default_token()
    if not token:
        print("没有管理密钥：--token 或 config.json 里的 admin_token"); sys.exit(1)
    if a.cmd == "new":
        d = api_call(a.remote, token, {"action": "new", "count": a.count, "note": a.note,
                                       "phone": a.phone, "plan": a.plan,
                                       "max_profiles": a.max_profiles})
        print(f"\n云端新生成 {len(d['codes'])} 个激活码（{a.plan} 套餐）:\n")
        for c in d["codes"]:
            print("   ", c)
    elif a.cmd == "list":
        d = api_call(a.remote, token, {"action": "list", "status": a.status})
        print_licenses(d["licenses"])
    elif a.cmd in ("bind", "reset-phone", "disable", "enable"):
        payload = {"action": a.cmd, "code": a.code}
        if a.cmd == "bind":
            payload["phone"] = a.phone
        d = api_call(a.remote, token, payload)
        print(f"{d.get('code')} → {a.cmd} 完成")
    elif a.cmd == "export":
        d = api_call(a.remote, token, {"action": "export"})
        Path(a.out).write_text(json.dumps(d["licenses"], ensure_ascii=False, indent=2),
                               encoding="utf-8")
        print(f"已从云端导出 {len(d['licenses'])} 个激活码 → {a.out}")


def main():
    ap = argparse.ArgumentParser(description="激活码管理（一码一人）")
    ap.add_argument("--remote", default="", help="云端地址（如 https://你的域名），不填则操作本地库")
    ap.add_argument("--token", default="", help="云端管理密钥（默认读 config.json 的 admin_token）")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("new", help="生成新激活码")
    p.add_argument("--count", type=int, default=1)
    p.add_argument("--note", default="")
    p.add_argument("--phone", default="")
    p.add_argument("--plan", default="basic", choices=["basic", "pro"])
    p.add_argument("--max-profiles", type=int, default=3, dest="max_profiles")
    p.set_defaults(kind="local")

    p = sub.add_parser("list", help="查看激活码")
    p.add_argument("--status", choices=["unused", "active", "disabled"])

    for name, extra in (("bind", "phone"), ("reset-phone", None), ("disable", None), ("enable", None)):
        p = sub.add_parser(name)
        p.add_argument("code")
        if extra:
            p.add_argument("phone")

    p = sub.add_parser("export", help="导出全部激活码（做种子备份）")
    p.add_argument("--out", default="licenses_seed.json")

    args = ap.parse_args()
    if not getattr(args, "cmd", None):
        ap.print_help(); return
    if args.remote:
        remote_run(args); return
    _load_server()
    init_db()
    if args.cmd == "new":
        local_new(args)
    elif args.cmd == "list":
        local_list(args)
    elif args.cmd == "export":
        local_export(args)
    elif args.cmd == "bind":
        local_set(args, "UPDATE licenses SET phone=?, status='active', activated_at=? WHERE id=?",
                  [_norm_phone(args.phone), datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
    elif args.cmd == "reset-phone":
        local_set(args, "UPDATE licenses SET phone=NULL, status='unused', activated_at=NULL WHERE id=?", [])
    elif args.cmd == "disable":
        local_set(args, "UPDATE licenses SET status='disabled' WHERE id=?", [])
    elif args.cmd == "enable":
        conn = db(); lic = _local_find(conn, args.code)
        conn.execute("UPDATE licenses SET status=? WHERE id=?",
                     ("active" if lic["phone"] else "unused", lic["id"]))
        conn.commit(); conn.close()
        print(f"{pretty_code(lic['code'])} → 完成")


if __name__ == "__main__":
    main()

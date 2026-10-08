"""check_cloud.py — 线上链接体检（发布后跑一次，确认云端真的能用）

用法：
  python app/check_cloud.py --remote https://你的链接
  python app/check_cloud.py --remote https://你的链接 --token 管理密钥   # 不填则读同目录 config.json

检查项：
  1. 链接能不能打开、登录页在不在
  2. 云端能力：视频合成（ffmpeg）、配图出图、自动代发（云端必为不可用，属正常）
  3. 管理密钥对不对（能不能远程发码）
  4. 发一个临时激活码 → 登录激活 → 验证数据隔离 → 停用该码收尾
每项给出结论和下一步建议；有失败项会在结尾汇总。
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEPLOY_CONFIG = ROOT / "deploy" / "local" / "config.json"
LOCAL_CONFIG = ROOT / "local" / "config.json"

results = []


def ok(name, detail=""):
    results.append((True, name, detail))
    print(f"  ✅ {name}" + (f" —— {detail}" if detail else ""))


def bad(name, detail="", hint=""):
    results.append((False, name, detail))
    print(f"  ❌ {name}" + (f" —— {detail}" if detail else ""))
    if hint:
        print(f"     建议：{hint}")


def req(base, path, *, data=None, token=None, admin=None, timeout=60, method=None):
    r = urllib.request.Request(base.rstrip("/") + path,
                               method=method or ("POST" if data is not None else "GET"))
    if data is not None:
        r.add_header("Content-Type", "application/json")
        r.data = json.dumps(data).encode()
    if token:
        r.add_header("Authorization", "Bearer " + token)
        r.add_header("X-Auth-Token", token)   # 双头兼容：某些代理会替换 Authorization 头
    if admin:
        r.add_header("X-Admin-Token", admin)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(body)
            except Exception:
                return resp.status, body
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"error": str(e)}


def main():
    ap = argparse.ArgumentParser(description="线上链接体检")
    ap.add_argument("--remote", required=True, help="线上链接")
    ap.add_argument("--token", default="", help="管理密钥（默认读同目录 config.json）")
    ap.add_argument("--phone", default="13800000000", help="体检用的手机号（只是走流程，不会真的发短信）")
    args = ap.parse_args()

    base = args.remote
    token = args.token
    if not token:
        for f in (DEPLOY_CONFIG, LOCAL_CONFIG):
            try:
                token = json.loads(f.read_text(encoding="utf-8")).get("admin_token") or ""
            except Exception:
                token = ""
            if token:
                break

    print(f"\n体检目标：{base}\n")

    # 1. 页面
    st, html = req(base, "/")
    if st == 200 and isinstance(html, str) and "loginGate" in html:
        ok("链接可访问，登录页正常")
    elif st == 200:
        bad("链接能打开，但页面不是最新版（没看到登录页）", f"HTTP {st}",
            "可能还在跑旧版本，重新发布一次")
    else:
        bad("链接打不开", f"HTTP {st} {html if isinstance(html, str) else ''}".strip(),
            "确认链接拼写、等几秒重试；云端首次启动要装依赖，通常 1 分钟内可用")
        return finish()

    # 2. 能力
    st, d = req(base, "/api/capabilities")
    caps = (d or {}).get("capabilities") or {}
    if st == 200 and caps:
        if caps.get("video"):
            ok("视频合成组件可用（云端 ffmpeg 装上了）")
        else:
            bad("视频合成不可用", "云端缺 ffmpeg 组件",
                "确认 deploy/requirements.txt 里有 imageio-ffmpeg，然后重新发布")
        if caps.get("cards"):
            ok("配图出图可用")
        else:
            bad("配图出图不可用", "缺 Pillow 或内置字体",
                "确认 assets/fonts/ 随部署包一起上传了")
        if caps.get("publish"):
            ok("自动代发可用（说明这是本地版）")
        else:
            print("  ℹ️  自动代发不可用属正常：云端不做扫码代发，用「一键导出成品」发布")
    else:
        bad("能力接口没返回", f"HTTP {st}", "确认服务起来了")

    # 3. 管理密钥 + 发临时码
    if not token:
        bad("没有管理密钥", "没找到 admin_token",
            "在 config.json 里配置 admin_token，或 --token 传进来")
        return finish()
    st, d = req(base, "/api/admin/licenses", data={"action": "new", "count": 1, "note": "体检临时码"},
                admin=token)
    if st == 200 and (d or {}).get("ok"):
        code = d["codes"][0]
        ok("远程发码正常", f"临时码 {code}")
    elif st == 404:
        bad("管理密钥不对（发码被拒）", "HTTP 404",
            "密钥要跟你发布时用的 config.json 里的 admin_token 一致")
        return finish()
    else:
        bad("发码失败", f"HTTP {st} {d}")
        return finish()

    # 4. 登录激活
    st, d = req(base, "/api/auth/login", data={"code": code, "phone": args.phone})
    if st == 200 and (d or {}).get("token"):
        tk = d["token"]
        ok("激活码+手机号登录成功", f"绑定到 {d['license']['phone']}")
    else:
        bad("登录失败", f"HTTP {st} {(d or {}).get('detail') or (d or {}).get('error') or d}")
        return finish()

    # 5. 数据隔离
    st_ok, _ = req(base, "/api/profiles", token=tk)
    st_no, _ = req(base, "/api/profiles")
    if st_ok == 200:
        ok("带登录态能读自己的数据")
    else:
        bad("带登录态读数据失败", f"HTTP {st_ok}")
    if st_no == 401:
        ok("未登录被正确拦住（数据不会裸奔）")
    else:
        bad("未登录居然能读数据", f"HTTP {st_no}", "这是严重问题，请联系技术支持")

    # 6. 收尾：停用临时码，并确认立即生效
    st, d = req(base, "/api/admin/licenses", data={"action": "disable", "code": code}, admin=token)
    st2, d2 = req(base, "/api/auth/login", data={"code": code, "phone": args.phone})
    if st == 200 and st2 == 403:
        ok("停用能力正常（退款踢人即时生效），临时码已清理")
    else:
        bad("停用没立刻生效", f"停用 HTTP {st} / 再登录 HTTP {st2}",
            "检查云端是否是最新版代码")

    finish()


def finish():
    fails = [r for r in results if not r[0]]
    print("\n" + "=" * 56)
    if not fails:
        print("  全部通过：云端可以正常收客了 🎉")
        print("  下一步：python app/admin_license.py --remote <链接> new --count N 发码")
    else:
        print(f"  有 {len(fails)} 项没过，按上面建议处理后再跑一次体检：")
        for _, name, detail in fails:
            print(f"   · {name}" + (f"（{detail}）" if detail else ""))
    print("=" * 56 + "\n")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

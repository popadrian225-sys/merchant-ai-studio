#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""screenshot.py — 用演示数据跑一遍真实界面，截一套 README 用图。

做三件事：
1. 起本地服务（若 8787 已有服务则直接复用），并用 Playwright 逐页截图 → docs/screenshots/
2. 调 /api/poster/render 出 3 张海报样张 → docs/samples/
3. 调 /api/render-cards 出 3 张配图卡片样张 → docs/samples/

前置：先跑 `python tools/seed_demo.py`（否则界面是空的）。
用法：python tools/screenshot.py [--port 8787] [--url http://localhost:8787]
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
SHOTS = ROOT / "docs" / "screenshots"
SAMPLES = ROOT / "docs" / "samples"

errs: list[str] = []


def get(url: str, timeout: int = 30, data=None, ctype="application/json"):
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    if data and ctype:
        req.add_header("Content-Type", ctype)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def post_multipart(url: str, fields: dict):
    boundary = "----screenshotboundary"
    body = b""
    for k, v in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode()
    body += f"--{boundary}--\r\n".encode()
    return get(url, data=body, ctype=f"multipart/form-data; boundary={boundary}")


def download(base: str, rel: str, dest: Path):
    with urllib.request.urlopen(base + rel, timeout=60) as r:
        dest.write_bytes(r.read())
    return dest.stat().st_size


def wait_up(base: str, secs: int = 40) -> bool:
    for _ in range(secs * 2):
        try:
            get(base + "/api/capabilities", timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8790,
                    help="截图专用端口（默认 8790，故意避开常用的 8787，免得连到别的实例）")
    ap.add_argument("--url", default="", help="复用已有服务（仅在你确认它就是本项目实例时才用）")
    a = ap.parse_args()
    base = a.url or f"http://127.0.0.1:{a.port}"
    DEMO_SHOP = "阿珍肠粉店"

    proc = None
    if a.url:
        if not wait_up(base, secs=3):
            print(f"❌ {base} 上没有在跑的服务"); return 1
    else:
        # 端口被占用时直接报错：绝不能连到「别的实例」上（可能连着真实客户数据）
        if wait_up(base, secs=1):
            print(f"❌ 端口 {a.port} 已被占用，可能是另一个服务实例。"
                  f"截图必须用自己的实例，换个 --port 再跑。")
            return 1
        env = dict(os.environ, PORT=str(a.port), PYTHONIOENCODING="utf-8")
        print(f"启动截图专用服务 {base} …")
        proc = subprocess.Popen([sys.executable, "server.py"], cwd=str(APP), env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not wait_up(base):
            print("❌ 服务没起来"); return 1

    try:
        prof = get(base + "/api/profiles")
        names = [p["name"] for p in prof.get("profiles", [])]
        if not names:
            print("❌ 库里没有数据，先跑：python tools/seed_demo.py --reset")
            return 1
        # 双保险：必须连到演示数据，否则立即停手（真实客户数据一律不进仓库）
        if names != [DEMO_SHOP]:
            print(f"❌ 连到的库里是 {names}，不是演示数据 {DEMO_SHOP}。"
                  f"停手，避免把真实数据截进仓库。先跑 tools/seed_demo.py --reset。")
            return 1
        pid = prof["profiles"][0]["id"]
        print(f"用店铺 #{pid} {names[0]} 截图（演示数据 ✓）")

        SHOTS.mkdir(parents=True, exist_ok=True)
        SAMPLES.mkdir(parents=True, exist_ok=True)

        # ---- 先出样张（海报 / 卡片），截图里也能用上 ----
        tpls = {t["id"]: t for t in get(base + "/api/poster/templates")["templates"]}
        poster_jobs = [
            ("promo-red", "poster_promo", {"shop": "阿珍肠粉店", "address": "广州荔湾区老街菜市场旁",
                                           "phone": "020-8888 6666", "qr_link": "https://example.com/azhen"}),
            ("festival-red", "poster_festival", {"shop": "阿珍肠粉店",
                                                 "address": "广州荔湾区老街菜市场旁"}),
            ("cover-bigword", "poster_cover", {"shop": "阿珍肠粉店"}),
        ]
        for tid, name, extra in poster_jobs:
            t = tpls.get(tid)
            if not t:
                continue
            vals = {f["key"]: (f.get("default") or "") for f in t["fields"]}
            vals.update(extra)
            d = post_multipart(base + "/api/poster/render",
                               {"template_id": tid, "values": json.dumps(vals, ensure_ascii=False)})
            if d.get("ok"):
                size = download(base, d["url"], SAMPLES / f"{name}.png")
                print(f"  🎨 {name}.png  {size/1024:.0f} KB")
            else:
                errs.append(f"海报 {tid}: {d}")

        con = get(base + f"/api/contents?merchant_id={pid}&limit=20")
        hero = next((c for c in con["contents"] if "凌晨四点" in c["payload"]), con["contents"][0])
        d = get(base + "/api/render-cards", timeout=180, data=json.dumps(
            {"content_id": hero["id"], "style": "cream"}).encode())
        if d.get("ok"):
            for i, im in enumerate(d["images"][:3]):
                size = download(base, im["url"], SAMPLES / f"card_{i+1}.png")
                print(f"  🖼️ card_{i+1}.png  {size/1024:.0f} KB")
        else:
            errs.append(f"配图: {d}")

        # ---- Playwright 截图 ----
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 960})
            console_errs: list[str] = []
            page.on("console", lambda m: console_errs.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: console_errs.append(str(e)))

            def shot(name: str, full: bool = False):
                page.wait_for_timeout(700)
                page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=full)
                print(f"  📸 {name}.png")

            def nav(v: str):
                page.locator(f'aside nav button[data-v={v}]').click()
                page.wait_for_timeout(900)

            page.goto(base, wait_until="networkidle")
            page.wait_for_timeout(1200)
            shot("01_workbench")

            nav("profile")
            page.wait_for_timeout(500)
            shot("02_profile")

            nav("points")
            page.wait_for_selector("#pointsList .pt-row", timeout=15000)
            shot("03_selling_points")

            nav("topics")
            page.wait_for_selector("#topicList .topic-card", timeout=15000)
            shot("04_topic_pool")

            # 内容创作：把演示成品注入界面（不调 AI，省额度）
            nav("create")
            cid = page.evaluate(
                """async () => {
                    const r = await fetch('/api/contents?merchant_id=' + profile.id + '&limit=20');
                    const j = await r.json();
                    const c = j.contents.find(x => x.payload.includes('凌晨四点')) || j.contents[0];
                    afterCreate({content_id: c.id, result: JSON.parse(c.payload)});
                    return c.id;
                }""")
            page.wait_for_selector("#cardsRow img", timeout=180000)
            page.locator("#createResult").scroll_into_view_if_needed()
            page.set_viewport_size({"width": 1440, "height": 1720})
            page.evaluate("window.scrollTo({top: 0})")
            shot("05_content")
            page.set_viewport_size({"width": 1440, "height": 960})
            print(f"     （注入内容 #{cid}）")

            nav("poster")
            page.wait_for_selector("#posterTplGrid [data-pt]", timeout=15000)
            page.screenshot(path=str(SHOTS / "06_poster_templates.png"))
            print("  📸 06_poster_templates.png")
            page.locator('#posterTplGrid [data-pt="promo-red"]').click()
            page.wait_for_selector("#posterFields input", timeout=10000)
            page.evaluate(
                """() => {
                    const t = POSTER_TPLS.find(x => x.id === POSTER_CUR.id);
                    (t.fields || []).forEach(f => {
                      const el = document.getElementById('pf_' + f.key);
                      if (!el) return;
                      if (!el.value) el.value = f.default || '';
                    });
                    document.getElementById('pf_qr_link').value = 'https://example.com/azhen';
                }""")
            page.click("#btnPosterGo")
            page.wait_for_selector("#posterResult img", timeout=60000)
            shot("07_poster_render")

            nav("materials")
            page.wait_for_selector("#matGrid .mat", timeout=15000)
            shot("08_materials")

            nav("history")
            page.wait_for_selector("#historyList .topic-card", timeout=15000)
            shot("09_history")

            nav("account")
            page.wait_for_timeout(900)
            shot("10_account")

            browser.close()
            if console_errs:
                errs.append(f"控制台错误 {len(console_errs)} 条：{console_errs[:2]}")

        print(f"\n截图 {len(list(SHOTS.glob('*.png')))} 张 → {SHOTS}")
        print(f"样张 {len(list(SAMPLES.glob('*.png')))} 张 → {SAMPLES}")
        if errs:
            print("⚠️ 有问题：", *errs, sep="\n  - ")
            return 1
        print("全部完成 ✅")
        return 0
    finally:
        if proc:
            proc.terminate()


if __name__ == "__main__":
    sys.exit(main())

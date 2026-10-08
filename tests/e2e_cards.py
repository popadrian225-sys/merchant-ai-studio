"""ui_test_cards.py — 聚焦测试：从历史载入笔记 → 渲染卡片图 → 验证图片真实加载。"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8787"
SHOTS = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
SHOTS.mkdir(parents=True, exist_ok=True)

errors: list[str] = []

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--no-proxy-server"])
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.on("console", lambda m: errors.append(f"[console.error] {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"[pageerror] {e}"))

    print("① 打开页面（自动选中已有档案）…")
    page.goto(BASE, wait_until="networkidle")
    page.wait_for_selector("#profileView:not(.hide)", timeout=15000)

    print("② 从历史载入一条笔记…")
    page.click('.tabs button[data-tab="hist"]')
    page.wait_for_timeout(1200)
    # 点最近的一条笔记类历史（带「笔记」标签的那条）
    clicked = page.eval_on_selector_all(
        "#histOut .hist",
        """els => { for (const el of els) { if (el.textContent.includes('笔记')) { el.click(); return el.textContent.slice(0, 50); } } return null; }""",
    )
    if not clicked:
        print("!! 历史里没有笔记类记录")
        sys.exit(1)
    print("   载入:", clicked.replace("\n", " ")[:50])
    page.wait_for_selector("#genOut .title-opt", timeout=15000)

    print("③ 渲染卡片图（本地秒出）…")
    page.click("#btnRender")
    page.wait_for_selector("#renderOut img", timeout=60000)
    imgs = page.eval_on_selector_all(
        "#renderOut img",
        "els => els.map(e => ({src: e.getAttribute('src'), ok: e.naturalWidth > 0, w: e.naturalWidth, h: e.naturalHeight}))",
    )
    print(f"   卡片图 {len(imgs)} 张，全部加载成功: {all(i['ok'] for i in imgs)}，尺寸: {imgs[0]['w']}×{imgs[0]['h']}")
    for i in imgs:
        print("   ·", i["src"])
    page.wait_for_timeout(800)
    page.screenshot(path=str(SHOTS / "9_卡片出图.png"), full_page=True)

    browser.close()

print("\n控制台错误:", errors or "无")
sys.exit(1 if errors or not all(i["ok"] for i in imgs) else 0)

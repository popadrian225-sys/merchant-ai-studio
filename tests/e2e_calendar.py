"""ui_test_cal.py — 聚焦测试：月度排期表 + 历史（前序功能已验证过）。"""

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
    print("   额度栏:", page.inner_text("#quota"))

    print("② 月度排期表（约 1-3 分钟，429 会自动重试）…")
    page.click('.tabs button[data-tab="cal"]')
    page.fill("#calContext", "下周六新批次虾仁到货，五一假期备货")
    page.click("#btnCal")
    page.wait_for_selector("#calOut table tbody tr", timeout=600000)
    rows = page.eval_on_selector_all("#calOut table tbody tr", "els => els.length")
    first_row = page.eval_on_selector("#calOut table tbody tr", "el => el.textContent")
    print("   排期条目:", rows)
    print("   首条:", first_row[:80])
    page.wait_for_timeout(1000)
    page.screenshot(path=str(SHOTS / "7_排期表.png"), full_page=True)

    print("③ 历史…")
    page.click('.tabs button[data-tab="hist"]')
    page.wait_for_timeout(1500)
    hist = page.eval_on_selector_all("#histOut .hist", "els => els.map(e => e.textContent)")
    print("   历史记录:", len(hist), "条")
    for h in hist[:4]:
        print("   ·", h[:60])
    page.screenshot(path=str(SHOTS / "8_历史记录.png"), full_page=True)

    browser.close()

print("\n控制台错误:", errors or "无")
sys.exit(1 if errors else 0)

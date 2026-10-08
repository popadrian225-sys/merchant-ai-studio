"""ui_test.py — 用 Playwright 真实走一遍本地演示界面（不是只看 HTTP 200）。

流程：打开页面 → 填入示例 → 保存档案 → 填主题 → 生成笔记 → 切到质检 → 质检
产出：docs/screenshots/*.png 截图 + 控制台错误清单
"""

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
    page.on("console", lambda m: errors.append(f"[console.{m.type}] {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"[pageerror] {e}"))

    print("① 打开页面…")
    page.goto(BASE, wait_until="networkidle")
    page.wait_for_selector("#profileSel")
    page.wait_for_timeout(1200)  # 等 loadProfiles 完成
    page.screenshot(path=str(SHOTS / "1_初始页面.png"), full_page=True)

    # 库里已有画像时页面会自动选中并隐藏新建表单，先切回「新建商户」
    print("② 填入示例并保存档案…")
    if not page.is_visible("#btnDemo"):
        page.click('button:has-text("改用新商户")')
        page.wait_for_selector("#btnDemo", state="visible")
    page.click("#btnDemo")
    page.click("#btnSaveProfile")
    page.wait_for_selector("#profileView:not(.hide)", timeout=15000)
    quota = page.inner_text("#quota")
    print("   额度栏:", quota)
    page.screenshot(path=str(SHOTS / "2_档案已保存.png"), full_page=True)

    print("③ 生成选题池（约 1-2 分钟）…")
    page.click('.tabs button[data-tab="plan"]')
    page.fill("#planContext", "周五到货一批荔枝，周末家庭聚餐多")
    page.click("#btnPlan")
    page.wait_for_selector("#planOut .topic-item", timeout=240000)
    topics = page.eval_on_selector_all("#planOut .topic-item", "els => els.length")
    top_scores = page.eval_on_selector_all("#planOut .topic-item .pill", "els => els.map(e => e.textContent).filter(t => t.includes('分'))")
    print(f"   选题 {topics} 个，前 3 名分数: {top_scores[:3]}")
    page.screenshot(path=str(SHOTS / "3_选题池.png"), full_page=True)

    print("④ 点第一条「用这个选题写笔记」→ 生成（约 30-60 秒）…")
    first_topic = page.eval_on_selector("#planOut button[data-topic]", "el => el.dataset.topic")
    page.click("#planOut button[data-topic]")
    page.wait_for_selector("#tab-gen:not(.hide)")
    assert page.input_value("#topic") == first_topic, "选题未带入选框"
    page.click("#btnGen")
    page.wait_for_selector("#genOut .title-opt", timeout=180000)
    titles = page.eval_on_selector_all("#genOut .title-opt .txt", "els => els.map(e => e.textContent)")
    cards = page.eval_on_selector_all("#genOut .mini-card .body", "els => els.map(e => e.textContent)")
    tags = page.eval_on_selector_all("#genOut .tags span", "els => els.map(e => e.textContent)")
    print(f"   选题已带入: {first_topic[:30]}…")
    print(f"   标题 {len(titles)} 个 / 卡片 {len(cards)} 张 / 标签 {len(tags)} 个")
    print("   额度栏:", page.inner_text("#quota"))
    page.screenshot(path=str(SHOTS / "4_生成结果.png"), full_page=True)

    print("⑤ 切到质检并执行（约 1-3 分钟）…")
    page.click('.tabs button[data-tab="check"]')
    page.click("#btnCheck")
    page.wait_for_selector("#checkOut .verdict", timeout=300000)
    verdict = page.inner_text("#checkOut .verdict")
    print("   判定:", " / ".join(verdict.split("\n")[:2]))
    issues = page.eval_on_selector_all("#checkOut .issue", "els => els.length")
    print("   合规问题条目:", issues)
    page.wait_for_timeout(1500)
    page.screenshot(path=str(SHOTS / "5_质检结果.png"), full_page=True)

    print("⑥ 一稿多发（抖音+公众号+微博，约 1-2 分钟）…")
    page.click('.tabs button[data-tab="rep"]')
    page.click("#btnRep")
    page.wait_for_selector("#repOut .pill", timeout=240000)
    pieces = page.eval_on_selector_all("#repOut .pill", "els => els.map(e => e.textContent)")
    print("   改编产出:", [p for p in pieces if p in ("抖音口播稿", "公众号短文", "微博短博")])
    page.screenshot(path=str(SHOTS / "6_一稿多发.png"), full_page=True)

    print("⑦ 月度排期表（约 1-2 分钟）…")
    page.click('.tabs button[data-tab="cal"]')
    page.fill("#calContext", "下周六新批次虾仁到货")
    page.click("#btnCal")
    page.wait_for_selector("#calOut table tbody tr", timeout=240000)
    rows = page.eval_on_selector_all("#calOut table tbody tr", "els => els.length")
    print("   排期条目:", rows)
    page.screenshot(path=str(SHOTS / "7_排期表.png"), full_page=True)

    print("⑧ 切到历史…")
    page.click('.tabs button[data-tab="hist"]')
    page.wait_for_timeout(1500)
    hist = page.eval_on_selector_all("#histOut .hist", "els => els.length")
    print("   历史记录:", hist)
    page.screenshot(path=str(SHOTS / "8_历史记录.png"), full_page=True)

    browser.close()

print("\n控制台错误:", errors or "无")
sys.exit(1 if errors else 0)

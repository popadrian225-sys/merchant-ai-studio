"""ui_test_context.py — 「由头输入」链路 E2E（Playwright 真机）

验的是本轮新增：老板能自己写由头（例：今天中秋节），选题和文案都围着它走。
流程：档案填招牌产品 → 选题页写由头/点快捷由头 → 生成选题（Kimi）→ 选一条
     → 创作页确认由头带过来 → 补一句要求 → 生成文案（Kimi）→ 查文案是否体现由头

跑之前服务必须在 8787。用法：python ui_test_context.py
"""

import re
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8787"
SHOT = Path(__file__).parent.parent / "docs" / "screenshots"
SHOT.mkdir(parents=True, exist_ok=True)

errors = []
console_errors = []
MOON = re.compile(r"中秋|月饼|团圆|赏月|月|礼|回家|思念")   # 中秋由头关键词


def shot(page, name):
    page.screenshot(path=str(SHOT / f"{name}.png"), full_page=True)
    print(f"  📸 {name}")


def check(page, cond, msg):
    if cond:
        print(f"  ✅ {msg}")
    else:
        errors.append(msg)
        print(f"  ❌ {msg}")


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: console_errors.append(str(e)))
    page.goto(BASE, wait_until="networkidle")

    print("== 1. 商家档案：主要卖什么 ==")
    page.locator("aside nav button[data-v=profile]").click()
    check(page, page.locator("#pPillars").count() == 1, "档案页有「主要卖什么」输入框")
    page.fill("#pPillars", "招牌手工豆腐\n素菜自助 38 元/位\n现磨豆浆")
    page.click("#btnSave")
    page.wait_for_selector("#pLog:has-text('已保存')", timeout=20000)
    check(page, True, "招牌产品保存成功")
    shot(page, "ctx_profile")

    print("== 2. 内容选题：由头输入 ==")
    page.locator("aside nav button[data-v=topics]").click()
    check(page, page.locator("#tContext").count() == 1, "选题页有由头输入框")
    page.locator("#ctxQuick button").first.click()          # 点「🎑 中秋节」
    val = page.input_value("#tContext")
    check(page, "中秋" in val, f"点快捷由头即填入：{val}")
    page.fill("#tContext", "今天是中秋节，想做几条跟中秋有关的")
    shot(page, "ctx_input")

    print("== 3. 生成选题（Kimi，约 1-3 分钟）==")
    page.click("#btnGenTopics")
    try:
        page.wait_for_selector("#topicList .topic-card", timeout=360000)
    except Exception:
        shot(page, "ctx_topics_timeout")
        print("  页面提示：", (page.text_content("#tLog") or "")[:200])
        raise
    n = page.locator("#topicList .topic-card").count()
    check(page, n >= 3, f"生成 {n} 条选题")
    titles = page.locator("#topicList .topic-card .t").all_inner_texts()
    hit = sum(1 for t in titles if MOON.search(t))
    check(page, hit >= 2, f"其中 {hit}/{len(titles)} 条带中秋元素")
    print("  选题内容：", " ／ ".join(x.replace("\n", " ") for x in titles[:5]))
    check(page, page.locator("#topicList .topic-card .badge").count() >= 3, "选题卡显示由头标签 🎯")
    shot(page, "ctx_topics")

    print("== 4. 选一条 → 创作页应带住由头 ==")
    page.locator("#topicList .topic-card button", has_text="就写这条").first.click()
    page.wait_for_selector("#createRun:visible", timeout=10000)
    ct = (page.text_content("#curTopic") or "").strip()
    check(page, "由头" in ct, "创作页显示由头：" + ct[:50])
    check(page, page.locator("#cExtra").count() == 1, "创作页有「还想交代一句」输入框")
    page.fill("#cExtra", "用街坊聊天的口气，别提价格")
    shot(page, "ctx_create")

    print("== 5. 生成文案（Kimi，约 1-2 分钟）==")
    page.click("#btnGen")
    page.wait_for_selector("#createResult", state="visible", timeout=420000)
    title = (page.text_content("#mTitle") or "").strip()
    body = (page.text_content("#mBody") or "").strip()
    check(page, len(title) > 0, f"标题：{title[:24]}")
    check(page, MOON.search(title + body) is not None, "文案里带住了中秋这个由头")
    check(page, len(body) > 80, f"正文 {len(body)} 字")
    shot(page, "ctx_result")

    print("== 6. 控制台 ==")
    for e in console_errors[:5]:
        print("   ⚠️", e[:160])
    check(page, len(console_errors) == 0, f"控制台零错误（实际 {len(console_errors)} 条）")

    browser.close()

print("\n===== 结果 =====")
if errors:
    print(f"❌ {len(errors)} 项未通过：")
    for e in errors:
        print("   -", e)
else:
    print("✅ 全部通过：由头输入 → 选题围绕由头 → 创作页带住由头 → 文案体现由头")

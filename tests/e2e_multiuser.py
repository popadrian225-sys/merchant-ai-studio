"""ui_test_multi.py — 本轮改动的前端 E2E（Playwright 真机）

验四件事：
  1. 能力探测不报错、页面正常加载
  2. 账号页出现四个平台（小红书/抖音/快手/微信视频号）
  3. 使用码卡片在本地版隐藏（只在云端出现）
  4. 素材库显示空间用量
用法：python ui_test_multi.py（服务须在 8787）
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8787"
SHOT = Path(__file__).parent.parent / "docs" / "screenshots"
SHOT.mkdir(parents=True, exist_ok=True)

errors, console_errors = [], []


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

    print("== 1. 首页与能力探测 ==")
    page.goto(BASE, wait_until="networkidle")
    caps = page.evaluate("() => CAPS")
    check(page, caps.get("publish") is True and caps.get("video") is True,
          f"本地能力：publish={caps.get('publish')} video={caps.get('video')} cards={caps.get('cards')}")

    print("== 2. 素材库：空间用量 ==")
    page.locator("aside nav button[data-v=materials]").click()
    page.wait_for_selector("#matUsage", timeout=15000)
    usage = (page.text_content("#matUsage") or "").strip()
    check(page, "空间用量" in usage, "素材用量行：" + usage[:60])
    shot(page, "m_usage")

    print("== 3. 我的账号：四个平台 ==")
    page.locator("aside nav button[data-v=account]").click()
    try:
        page.wait_for_selector("#chList .pick", timeout=150000)   # 首次要起浏览器校验
    except Exception:
        shot(page, "m_account_timeout")
        print("  页面提示：", (page.text_content("#chList") or "")[:200])
        raise
    text = page.text_content("#chList") or ""
    for name in ["小红书", "抖音", "快手", "微信视频号"]:
        check(page, name in text, f"账号页含「{name}」")
    n_bind = page.locator("#chList [data-bind]").count()
    check(page, n_bind >= 1, f"未绑定平台有「去绑定」按钮（{n_bind} 个）")
    check(page, page.locator("#capsNote").is_visible() is False, "本地版不显示云端降级提示")
    check(page, page.locator("#cidCard").is_visible() is False, "本地版不显示使用码卡片")
    shot(page, "m_account")

    print("== 4. 选题页仍正常 ==")
    page.locator("aside nav button[data-v=topics]").click()
    check(page, page.locator("#tContext").is_visible(), "选题页由头输入框仍在")

    print("== 5. 控制台 ==")
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
    print("✅ 全部通过")

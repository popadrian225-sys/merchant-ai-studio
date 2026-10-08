"""ui_test_boss2.py — 老板模式重构版 E2E（Playwright 真机）

流程：档案填特色 → 卖点提炼(Kimi) → 选题生成(Kimi) → 选选题 → 生成成品(Kimi)
     → 图文卡片出图 → 上传素材 → 勾素材 → 配音分镜(Kimi) → 合成视频(TTS+ffmpeg)
跑之前服务必须在 8787。用法：
  python ui_test_boss2.py            # 全流程
  python ui_test_boss2.py --from=5   # 从第 5 步开始（前几步已验过，省时间省额度）
"""

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8787"
SHOT = Path(__file__).parent.parent / "docs" / "screenshots"
SHOT.mkdir(parents=True, exist_ok=True)

errors = []
console_errors = []
FROM = 1
for a in sys.argv[1:]:
    if a.startswith("--from="):
        FROM = int(a.split("=")[1])


def skips(step: int) -> bool:
    return step < FROM


def shot(page, name):
    page.screenshot(path=str(SHOT / f"{name}.png"), full_page=True)
    print(f"  📸 {name}")


def check(page, cond, msg):
    if cond:
        print(f"  ✅ {msg}")
    else:
        errors.append(msg)
        print(f"  ❌ {msg}")


# 先造一张测试素材图并传到素材库
def seed_material(merchant_id: int) -> int:
    import urllib.request
    import json as _json
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (900, 1200), (240, 230, 210))
    d = ImageDraw.Draw(img)
    d.rectangle([60, 480, 840, 720], fill=(210, 120, 90))
    d.text((320, 580), "TEST 招牌豆腐", fill=(60, 50, 40))
    p = Path(__file__).parent.parent / "docs" / "screenshots" / "_seed_material.jpg"
    img.save(p, quality=88)
    boundary = "----e2eboundary"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"seed.jpg\"\r\n"
            f"Content-Type: image/jpeg\r\n\r\n").encode() + p.read_bytes() + \
           (f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"notes\"\r\n\r\n招牌手工豆腐出锅\r\n--{boundary}--\r\n").encode()
    req = urllib.request.Request(
        f"{BASE}/api/materials?merchant_id={merchant_id}&notes=%E6%8B%9B%E7%89%8C%E6%89%8B%E5%B7%A5%E8%B1%86%E8%85%90%E5%87%BA%E9%94%85",
        data=body, method="POST",
        headers={"content-type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = _json.loads(r.read().decode("utf-8"))
    return data["materials"][0]["id"]


def run():
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 960})
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(str(e)))

        print("== 1. 首页与导航 ==")
        page.goto(BASE, wait_until="networkidle")
        if not skips(1):
            check(page, page.locator("aside nav button").count() >= 8, "侧边栏 8 个导航项")
            check(page, page.locator("#homeSteps .steps").count() >= 4, "首页四步引导")
            shot(page, "b2_home")

        if not skips(2):
            print("== 2. 商家档案 ==")
            page.locator("aside nav button[data-v=profile]").click()
            page.fill("#pFeatures", "手工豆腐每天凌晨四点现做，一天只做两板，中午前卖完；素菜自助38元一位；开了八年老街坊都来")
            page.click("#btnSave")
            page.wait_for_selector("#pLog:has-text('已保存')", timeout=10000)
            check(page, True, "档案保存")
            shot(page, "b2_profile")

        if not skips(3):
            print("== 3. 卖点提炼（Kimi，约 30-60s）==")
            page.locator("aside nav button[data-v=points]").click()
            page.click("#btnReExtract")
            page.wait_for_selector("#pointsList .pt-row", timeout=180000)
            n_points = page.locator("#pointsList .pt-row").count()
            check(page, n_points >= 3, f"提炼出 {n_points} 条卖点")
            # 停用第二条，测试勾选
            page.locator("#pointsList .pt-row").nth(1).locator("[data-toggle]").click()
            page.click("#btnSavePoints")
            page.wait_for_selector("#ptLog:has-text('已保存')", timeout=10000)
            check(page, True, "卖点勾选保存")
            shot(page, "b2_points")

        if not skips(4):
            print("== 4. 内容选题（Kimi，约 30-150s）==")
            page.locator("aside nav button[data-v=topics]").click()
            page.locator("#chipRow button[data-c=no_time]").click()   # 勾「没时间拍」
            page.click("#btnGenTopics")
            try:
                page.wait_for_selector("#topicList .topic-card", timeout=330000)
            except Exception:
                page.screenshot(path=str(SHOT / "b2_topics_timeout.png"), full_page=True)
                print("  ⚠️ 选题超时，页面提示：", (page.text_content("#tLog") or "")[:300])
                raise
            n_topics = page.locator("#topicList .topic-card").count()
            check(page, n_topics >= 3, f"生成 {n_topics} 条选题")
            shot(page, "b2_topics")

        if not skips(5):
            print("== 5. 选选题 → 生成成品（Kimi，约 1-2min）==")
            if skips(4):
                page.locator("aside nav button[data-v=topics]").click()
                page.wait_for_selector("#topicList .topic-card", timeout=20000)
            page.locator("#topicList .topic-card button", has_text="就写这条").first.click()
            page.wait_for_selector("#createRun:visible", timeout=8000)
            page.click("#btnGen")
            page.wait_for_selector("#createResult", state="visible", timeout=420000)
            check(page, len((page.text_content("#mTitle") or "").strip()) > 0, "成品标题生成")
            check(page, len(page.text_content("#mBody") or "") > 80, "成品正文非空")
            shot(page, "b2_create")

        if not skips(6):
            print("== 6. 图文版配图 ==")
            page.wait_for_selector("#cardsRow img", timeout=120000)
            n_cards = page.locator("#cardsRow img").count()
            check(page, n_cards >= 3, f"配图 {n_cards} 张")
            shot(page, "b2_cards")

        if not skips(7):
            print("== 7. 视频版：素材+分镜（Kimi）==")
            mat_id = seed_material(profile_id())
            print(f"  （预置素材 id={mat_id}）")
            page.click("#tabVideo")
            page.wait_for_selector(f"#videoMats .mat[data-pick='{mat_id}']", timeout=15000)
            page.locator(f"#videoMats .mat[data-pick='{mat_id}']").click()
            page.click("#btnScript")
            page.wait_for_selector("#scriptOut .shots .r", timeout=240000)
            n_scenes = page.locator("#scriptOut .shots .r").count()
            check(page, 3 <= n_scenes <= 7, f"分镜 {n_scenes} 段")
            shot(page, "b2_script")

        if not skips(8):
            print("== 8. 合成视频（TTS+ffmpeg，约 1-3min）==")
            page.click("#btnRender")
            page.wait_for_selector("#videoOut video.result", state="visible", timeout=420000)
            src = page.get_attribute("#videoPlayer", "src")
            check(page, bool(src), f"视频地址 {src}")
            # 验证视频文件真的可下载且非空
            import urllib.request
            with urllib.request.urlopen(BASE + src, timeout=60) as r:
                size = len(r.read())
            check(page, size > 100_000, f"视频文件 {size/1024:.0f} KB")
            shot(page, "b2_video")

        if not skips(9):
            print("== 9. 内容历史 ==")
            page.locator("aside nav button[data-v=history]").click()
            page.wait_for_selector("#historyList .topic-card", timeout=10000)
            check(page, True, "历史列表有记录")

        page.wait_for_timeout(800)
        browser.close()

    print("\n========== 结果 ==========")
    if console_errors:
        print("控制台错误：")
        for e in console_errors[:10]:
            print("  -", e[:200])
    if errors:
        print("失败项：", *errors, sep="\n  - ")
        sys.exit(1)
    print("全部通过 ✅  控制台错误:", len(console_errors))


_profile_id_cache = None


def profile_id() -> int:
    global _profile_id_cache
    if _profile_id_cache is None:
        import urllib.request, json as _json
        with urllib.request.urlopen(f"{BASE}/api/profiles", timeout=15) as r:
            d = _json.loads(r.read().decode("utf-8"))
        _profile_id_cache = d["profiles"][0]["id"]
    return _profile_id_cache


if __name__ == "__main__":
    run()

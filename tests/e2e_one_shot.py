"""ui_test_boss.py — 老板模式（简单模式）端到端测试。

流程：打开 → 改店铺信息页 → 回主界面 → 一句话生成 → 四平台切换 → 配图 → 定稿 → 发布指引
产出：docs/screenshots/*.png + 控制台错误清单
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
    page = browser.new_page(viewport={"width": 900, "height": 1000})
    page.on("console", lambda m: errors.append(f"[console.{m.type}] {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"[pageerror] {e}"))

    print("① 打开老板模式首页…")
    page.goto(BASE, wait_until="networkidle")
    page.wait_for_timeout(1500)
    if page.is_visible("#setup"):
        print("   当前是首次使用（企业定位表单）")
        page.screenshot(path=str(SHOTS / "boss_1_企业定位.png"), full_page=True)
        page.click("#btnSave")
        page.wait_for_selector("#main:not(.hide)", timeout=20000)
    else:
        print("   已有店铺，直接进主界面")
        print("   店铺：", page.inner_text("#shopName"), "|", page.inner_text("#quota"))
    page.screenshot(path=str(SHOTS / "boss_1_主界面.png"), full_page=True)

    print("② 打开「改店铺信息」验证企业定位页…")
    page.click("#btnEdit")
    page.wait_for_selector("#setup:not(.hide)", timeout=10000)
    name = page.input_value("#pName")
    loc = page.input_value("#pLocation")
    print(f"   回填店名：{name} | 位置：{loc}")
    page.screenshot(path=str(SHOTS / "boss_2_企业定位.png"), full_page=True)
    page.click("#btnSave")
    page.wait_for_selector("#main:not(.hide)", timeout=20000)
    print("   保存成功，回到主界面")

    print("③ 输入一句话并生成（约 1-3 分钟）…")
    page.fill("#demand", "今天店里新到一批本地当季的新鲜蔬菜，想发一条吸引附近街坊来吃饭")
    page.click("#btnGo")
    page.wait_for_selector("#progress:not(.hide)", timeout=10000)
    page.wait_for_timeout(2500)
    page.screenshot(path=str(SHOTS / "boss_3_生成中.png"), full_page=True)

    page.wait_for_selector("#result:not(.hide)", timeout=600000)
    print("   内容已生成")
    page.wait_for_timeout(4000)  # 等配图渲染
    page.screenshot(path=str(SHOTS / "boss_4_生成结果.png"), full_page=True)

    # 四平台切换
    print("④ 切换四个平台…")
    for key, label in (("xiaohongshu", "小红书"), ("douyin", "抖音"), ("channels", "视频号"), ("moments", "朋友圈")):
        page.click(f'#ptabs button[data-p="{key}"]')
        page.wait_for_timeout(700)
        body = page.inner_text("#pbody")
        print(f"   {label}：{len(body)} 字 | 有复制按钮：{page.is_visible('#pbody [data-copy]')}")
        if key == "douyin":
            page.screenshot(path=str(SHOTS / "boss_5_抖音分镜.png"), full_page=True)
        if key == "moments":
            page.screenshot(path=str(SHOTS / "boss_6_朋友圈.png"), full_page=True)

    # 配图检查
    imgs = page.eval_on_selector_all("#cardsRow img", "els => els.map(e => ({src:e.src, w:e.naturalWidth, h:e.naturalHeight}))")
    print(f"⑤ 配图：{len(imgs)} 张")
    for im in imgs[:3]:
        print(f"   {im['w']}×{im['h']} {im['src'].split('/')[-1]}")

    print("⑥ 点「就用这条」→ 发布确认页…")
    page.click("#btnUse")
    page.wait_for_selector("#shipping:not(.hide)", timeout=15000)
    print("   勾选平台数：", page.eval_on_selector_all("#shipPicks .pick.on", "els => els.length"))
    page.screenshot(path=str(SHOTS / "boss_7_发布确认.png"), full_page=True)

    print("⑦ 点「OK，我发完了」…")
    page.click("#btnNext")
    page.wait_for_selector("#doneWrap:not(.hide)", timeout=10000)
    page.screenshot(path=str(SHOTS / "boss_8_完成.png"), full_page=True)

    print("⑧ 点「再写一条」回到主界面…")
    page.click("#btnAgain")
    page.wait_for_selector("#main:not(.hide)", timeout=10000)
    print("   回到主界面，额度：", page.inner_text("#quota"))

    browser.close()

print()
print("控制台错误：", len(errors))
for e in errors[:10]:
    print("  ", e)
print("截图目录：", SHOTS)

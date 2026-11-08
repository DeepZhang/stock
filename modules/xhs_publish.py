"""小红书自动发布图文笔记 (服务器版, headless)"""
import sys, datetime, json
from pathlib import Path
from playwright.sync_api import sync_playwright
from openai import OpenAI

URL = "https://creator.xiaohongshu.com/publish/publish"
AUTH = "/data/stock/xhs_auth.json"
SHOT_DIR = "/data/stock/shots"
CFG = json.load(open("/data/stock/config.json"))

def get_fund_data():
    """读取当日资金流向数据"""
    import glob, os
    files = sorted(glob.glob("/data/stock/data/funds/*_fund.json"), key=os.path.getmtime, reverse=True)
    if not files:
        return ""
    d = json.load(open(files[0]))
    sectors = d.get("sectors", {})
    result = []
    for name, vals in sectors.items():
        if vals:
            result.append((name, vals[-1]))
    result.sort(key=lambda x: -x[1])
    lines = ["今日各板块主力资金净流入（亿元）："]
    for n, v in result:
        lines.append(f"  {n}: {v:+.1f}亿")
    return "\n".join(lines)

def generate_summary():
    """调用大模型生成盘面总结"""
    client = OpenAI(
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key=CFG["ark_api_key"],
    )
    fund_data = get_fund_data()
    prompt = f"""你是A股盘面分析助手。根据以下真实的板块资金流向数据，生成一段小红书风格的笔记正文（100-200字），要求：
1. 口语化，适当带emoji
2. 必须基于真实数据，指出资金净流入最多和净流出最多的板块
3. 不要编造数据
4. 结尾带3-5个话题标签如#A股 #资金流向 #投资理财

{fund_data}

直接输出正文内容，不要解释。"""
    try:
        r = client.chat.completions.create(
            model=CFG["ark_model"],
            messages=[
                {"role": "system", "content": "你是人工智能助手"},
                {"role": "user", "content": prompt}
            ],
            max_tokens=500,
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        print("生成文案失败:", e)
        return "今日A股板块资金流向数据已更新。#A股 #资金流向 #投资理财"

def publish(image, title, desc=""):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(storage_state=AUTH, viewport={"width": 1280, "height": 720})
        page = ctx.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)

        page.evaluate("""() => {
            [...document.querySelectorAll('span')].find(e => e.textContent.trim() === '上传图文').click()
        }""")
        page.wait_for_timeout(3000)

        page.locator('input[type="file"]').first.set_input_files(image)
        page.wait_for_timeout(10000)

        page.locator('input[placeholder*="标题"]').fill(title)
        page.wait_for_timeout(500)

        page.locator('div[contenteditable="true"]').click(force=True)
        page.keyboard.press("Control+a")
        page.keyboard.press("Delete")
        page.keyboard.type(desc, delay=30)
        page.wait_for_timeout(2000)

        page.mouse.click(720, 670)
        page.wait_for_timeout(8000)
        print("URL:", page.url)
        if "success" in page.url or "published=true" in page.url:
            print("✅ 发布成功")
        else:
            print("⚠️ 未确认发布")
        browser.close()

if __name__ == "__main__":
    now = datetime.datetime.now()
    label = "午盘" if now.hour < 13 else "收盘"
    title = f"{now.month}月{now.day}日{label}主力资金流向"
    
    print("生成盘面总结...")
    desc = generate_summary()
    print("标题:", title)
    print("正文:", desc)
    
    shots = sorted(Path(SHOT_DIR).glob("*.png"))
    shots = [s for s in shots if "xhs" not in s.name]
    if not shots:
        print("没找到截图")
        sys.exit(1)
    img = str(shots[-1])
    publish(img, title, desc)

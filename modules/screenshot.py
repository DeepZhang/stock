"""定时截图资金流向页面，推飞书+发小红书"""
import datetime, os, json, sys, time
from playwright.sync_api import sync_playwright
import requests

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, ".."))

# ===== 交易日判断：非交易日直接退出 =====
from modules.stock_data import is_trading_day
now = datetime.datetime.now()
if not is_trading_day(now):
    print(f"[{now.strftime('%Y-%m-%d')}] 非交易日，跳过截图和推送")
    sys.exit(0)

CFG = json.load(open(os.path.join(BASE, "..", "config.json")))
WEBHOOK = CFG["feishu_webhook"]
URL = "http://127.0.0.1:5000/"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOT_DIR = os.path.join(BASE_DIR, "shots")
AUTH_FILE = os.path.join(BASE_DIR, "xhs_auth.json")
os.makedirs(SHOT_DIR, exist_ok=True)

def cleanup_old_shots():
    import glob
    now = time.time()
    for f in glob.glob(f"{SHOT_DIR}/*.png"):
        if now - os.path.getmtime(f) > 7 * 86400:
            os.remove(f)

cleanup_old_shots()

# ===== 截图 =====
today = datetime.datetime.now().strftime("%Y-%m-%d")
now_str = datetime.datetime.now().strftime("%H-%M")
fname = f"{today}_{now_str}.png"
path = f"{SHOT_DIR}/{fname}"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
    page = browser.new_page(viewport={"width":1600,"height":1000}, device_scale_factor=2)
    page.goto(URL, wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(5000)
    page.screenshot(path=path, full_page=False)
    browser.close()

# ===== 推飞书 =====
img_url = f"http://123.56.76.245:5000/shots/{fname}"
msg = {
    "msg_type": "interactive",
    "card": {
        "header": {"title": {"tag":"plain_text","content":f"📊 {today} {now_str.replace('-',':')} 资金流向"}},
        "elements": [{"tag":"action","actions":[{"tag":"button","text":{"tag":"plain_text","content":"📷 查看截图"},"url":img_url,"type":"primary"}]}]
    }
}
requests.post(WEBHOOK, json=msg, timeout=10)
print(f"截图: {path}")

# ===== 大模型生成文案 =====
def get_fund_data():
    import glob
    files = sorted(glob.glob(os.path.join(BASE_DIR, "data/funds/*_fund.json")), key=os.path.getmtime, reverse=True)
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
    for n, v in result[:20]:
        lines.append(f"  {n}: {v:+.1f}亿")
    return "\n".join(lines)

def generate_xhs_text():
    from openai import OpenAI
    client = OpenAI(
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key=CFG["ark_api_key"],
    )
    fund_data = get_fund_data()
    now = datetime.datetime.now()
    label = "午盘" if now.hour < 13 else "收盘"
    prompt = f"""你是A股盘面分析助手。根据以下真实的板块资金流向数据，生成一段小红书笔记，要求：
1. 标题15字以内，吸引人
2. 正文100-200字，口语化，适当带emoji
3. 必须基于真实数据，指出资金净流入最多和净流出最多的板块
4. 不要编造数据
5. 结尾带3-5个话题标签如#A股 #资金流向

{fund_data}

输出格式：第一行是标题，第二行空一行，后面是正文。"""
    try:
        r = client.chat.completions.create(
            model=CFG["ark_model"],
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
        )
        text = r.choices[0].message.content.strip()
        lines = text.split("\n")
        title = lines[0].replace("标题:", "").replace("Title:", "").strip()
        desc = "\n".join(lines[1:]).strip()
        if not title:
            title = f"{now.month}月{now.day}日{label}主力资金流向"
        return title, desc
    except Exception as e:
        print(f"大模型生成失败: {e}")
        title = f"{now.month}月{now.day}日{label}主力资金流向"
        desc = f"{now.month}月{now.day}日 A股{label}，板块资金流向如图。#A股 #资金流向"
        return title, desc

# ===== 发小红书 =====
print("大模型生成文案...")
title, desc = generate_xhs_text()
print(f"标题: {title}")
print(f"正文: {desc}")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
    context = browser.new_context(storage_state=AUTH_FILE, viewport={"width":1440,"height":900})
    page = context.new_page()
    try:
        page.goto("https://creator.xiaohongshu.com/publish/publish", wait_until="domcontentloaded", timeout=60000)
        if "login" in page.url:
            print("小红书登录态失效")
            sys.exit(1)
        page.wait_for_timeout(5000)

        # 切到上传图文tab
        print("切tab...")
        for attempt in range(5):
            clicked = page.evaluate("""() => {
                for (const el of document.querySelectorAll('.creator-tab')) {
                    const t = (el.innerText||'').trim();
                    const r = el.getBoundingClientRect();
                    if (t==='上传图文' && r.top>0 && r.top<200 && r.left>0) {
                        el.click();
                        return true;
                    }
                } return false;
            }""")
            print(f"  JS点击结果: {clicked}")
            page.wait_for_timeout(4000)
            has = page.evaluate("() => document.body.innerText.includes('上传图片')")
            print(f"  试{attempt+1} has_upload={has}")
            if has:
                break

        # 上传图片
        print("上传图片...")
        with page.expect_file_chooser() as fc:
            page.locator("text=上传图片").first.click(force=True, timeout=10000)
        fc.value.set_files(path)
        page.wait_for_timeout(8000)
        try:
            page.get_by_text("我知道了", exact=False).first.click(timeout=3000)
        except: pass

        # 填标题
        for sel in ['[placeholder*="标题"]','#title','.d-title']:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=3000):
                    el.click(); el.fill(title); break
            except: pass

        # 填正文
        for sel in ['div[contenteditable="true"]','.ql-editor','[placeholder*="正文"]']:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=3000):
                    el.click(); page.keyboard.type(desc, delay=30); break
            except: pass
        page.wait_for_timeout(2000)

        # CDP穿透shadow DOM点发布
        print("点发布...")
        cdp = context.new_cdp_session(page)
        doc = cdp.send("DOM.getDocument", {"depth":-1, "pierce":True})
        host = [None]
        def fh(n):
            if n.get("nodeName","").lower()=="xhs-publish-btn": host[0]=n; return
            for c in n.get("children",[]): fh(c)
        fh(doc["root"])
        if host[0]:
            dn = cdp.send("DOM.describeNode", {"nodeId":host[0]["nodeId"], "pierce":True})
            srs = dn.get("node",{}).get("shadowRoots",[])
            if srs:
                sd = cdp.send("DOM.describeNode", {"nodeId":srs[0]["nodeId"], "depth":-1, "pierce":True})
                bp = [None]
                def ws(n, d=0):
                    if d>30: return
                    if (n.get("nodeValue","") or "").strip()=="发布":
                        nid = n.get("parentId") or n.get("nodeId")
                        try:
                            bm = cdp.send("DOM.getBoxModel", {"nodeId":nid})
                            c = bm["model"]["content"]
                            bp[0] = ((c[0]+c[4])/2, (c[1]+c[5])/2)
                        except: pass
                    for c in n.get("children",[]): ws(c, d+1)
                ws(sd.get("node",{}))
                if bp[0]:
                    cx,cy = bp[0]
                    print(f"  点击({cx:.0f},{cy:.0f})")
                    cdp.send("Input.dispatchMouseEvent", {"type":"mouseMoved","x":cx,"y":cy})
                    page.wait_for_timeout(300)
                    cdp.send("Input.dispatchMouseEvent", {"type":"mousePressed","x":cx,"y":cy,"button":"left","clickCount":1})
                    cdp.send("Input.dispatchMouseEvent", {"type":"mouseReleased","x":cx,"y":cy,"button":"left","clickCount":1})
        page.wait_for_timeout(5000)
        print("完成")
    except Exception as e:
        print(f"发布失败: {e}")
        import traceback; traceback.print_exc()
    finally:
        browser.close()

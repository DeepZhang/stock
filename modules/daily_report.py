"""每日复盘 - 10个核心问题"""
import requests, json, os, datetime, glob

def _f(val, default=0.0):
    """Safe float conversion: '-' / '' / None -> default."""
    try:
        return float(val)
    except (ValueError, TypeError):
        return default

API_BASE = "https://push2test.eastmoney.com"
HEADERS = {"User-Agent":"Mozilla/5.0","Referer":"https://data.eastmoney.com/"}
import os
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data/daily_reports")
os.makedirs(DATA_DIR, exist_ok=True)

def _get(url, params):
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=10)
        return r.json()
    except Exception as e:
        return {}

def get_indices():
    j = _get(f"{API_BASE}/api/qt/ulist.np/get", {
        "fltt":"2","invt":"2","fields":"f2,f3,f4,f12,f14,f104,f105,f106,f6",
        "secids":"1.000001,0.399006,1.000688"
    })
    out = []
    for it in j.get("data",{}).get("diff",[]):
        out.append({"name":it.get("f14"),"price":_f(it.get("f2")),
            "change_pct":_f(it.get("f3")),"amount":round(_f(it.get("f6"))/1e8,0),
            "up":it.get("f104",0),"down":it.get("f105",0)})
    return out

def get_breadth():
    all_pcts = []
    for page in range(1,15):
        j = _get(f"{API_BASE}/api/qt/clist/get", {
            "pn":str(page),"pz":"500","po":"1","np":"1","fltt":"2","invt":"2",
            "fid":"f3","fs":"m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23","fields":"f3"})
        if not j: break
        diff = (j.get("data") or {}).get("diff") or []
        if not diff: break
        for it in diff:
            try: all_pcts.append(float(it.get("f3",0)))
            except: pass
    up = sum(1 for p in all_pcts if p>0)
    down = sum(1 for p in all_pcts if p<0)
    return {"total":len(all_pcts),"up":up,"down":down,
            "big_up":sum(1 for p in all_pcts if p>=5),"big_down":sum(1 for p in all_pcts if p<=-5)}

def get_limit():
    today = datetime.datetime.now().strftime("%Y%m%d")
    j = _get("https://push2ex.eastmoney.com/getTopicZTPool", {
        "ut":"7eea3edcaed734bea9cbfc24409ed989","dpt":"wz.ztzt",
        "Pageindex":"0","pagesize":"200","sort":"zttj:desc","date":today})
    pool = (j.get("data") or {}).get("pool") or []
    stocks = []
    max_s = 0
    for p in pool:
        s = p.get("zttj",{}).get("days",1) if isinstance(p.get("zttj"),dict) else 1
        max_s = max(max_s, s)
        stocks.append({"code":p.get("c"),"name":p.get("n"),"streak":s,
            "reason":p.get("hybk",""),"amount":round(p.get("lbc",0)/1e8,2) if p.get("lbc") else 0})
    j2 = _get("https://push2ex.eastmoney.com/getTopicDTPool", {
        "ut":"7eea3edcaed734bea9cbfc24409ed989","dpt":"wz.ztzt",
        "Pageindex":"0","pagesize":"200","sort":"fund:asc","date":today})
    dt = (j2.get("data") or {}).get("pool") or []
    return {"limit_up":len(pool),"limit_down":len(dt),"max_streak":max_s,"stocks":stocks[:30]}

def get_sectors():
    j = _get(f"{API_BASE}/api/qt/clist/get", {
        "pn":"1","pz":"100","po":"1","np":"1","fltt":"2","invt":"2",
        "fid":"f3","fs":"m:90+t:2,m:90+t:3","fields":"f12,f14,f3,f62,f104,f105"})
    diff = (j.get("data") or {}).get("diff") or []
    sectors = []
    for x in diff:
        up = x.get("f104",0); down = x.get("f105",0)
        sectors.append({"code":x["f12"],"name":x["f14"],"pct":_f(x.get("f3")),
            "flow":round(_f(x.get("f62"))/1e8,2),"up":up,"down":down,
            "breadth":round(up/(up+down)*100,0) if (up+down)>0 else 0})
    sectors.sort(key=lambda x:-x["pct"])
    return sectors

def get_sector_stocks(bk_code, limit=15):
    j = _get(f"{API_BASE}/api/qt/clist/get", {
        "pn":"1","pz":str(limit),"po":"1","np":"1","fltt":"2","invt":"2",
        "fid":"f3","fs":f"b:{bk_code}","fields":"f12,f14,f3,f62"})
    out = []
    for it in (j.get("data") or {}).get("diff") or []:
        out.append({"code":it.get("f12"),"name":it.get("f14"),
            "pct":_f(it.get("f3")),"flow":round(_f(it.get("f62"))/1e8,2)})
    return out

def get_sector_history_flow(sector_name, days=5):
    """从历史fund json看板块连续资金流入"""
    files = sorted(glob.glob(os.path.join(BASE_DIR, "data/funds/*_fund.json")))[-days:]
    flows = []
    for f in files:
        try:
            d = json.load(open(f))
            if sector_name in d.get("sectors",{}):
                v = d["sectors"][sector_name]
                if v: flows.append(round(v[-1],2))
        except: pass
    return flows

def generate(date_str=None):
    if not date_str:
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    try:
        import sys; sys.path.insert(0, os.path.join(BASE_DIR, "modules"))
        import stock_data
        # 如果请求的不是今天，且已有报告，直接返回
        if date_str != today:
            existing = get_report(date_str)
            if existing: return existing
        # 非交易日：返回最近报告，没有则继续生成
        if not stock_data.is_trading_day():
            existing = list_dates()
            if existing: return get_report(existing[0])
    except: pass
    r = {"date":date_str,"gen_time":datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}
    
    r["indices"] = get_indices()
    r["breadth"] = get_breadth()
    r["limit"] = get_limit()
    all_sectors = get_sectors()
    r["sectors_all"] = all_sectors
    r["sectors_top"] = all_sectors[:8]
    r["sectors_bottom"] = all_sectors[-5:][::-1]
    
    sh = next((x for x in r["indices"] if "上证" in x["name"]),{})
    chg = sh.get("change_pct",0)
    lu = r["limit"]["limit_up"]; ld = r["limit"]["limit_down"]
    b = r["breadth"]
    up_ratio = b["up"]/(b["up"]+b["down"])*100 if (b["up"]+b["down"])>0 else 50
    
    # Q1: 进攻还是防守
    if chg>0.5 and lu>50 and up_ratio>60:
        r["q1"] = "进攻"
        r["q1_detail"] = f"指数{chg}%+涨停{lu}家+上涨占比{up_ratio:.0f}%，适合进攻"
    elif chg<-1 or lu<20 or up_ratio<35:
        r["q1"] = "防守"
        r["q1_detail"] = f"指数{chg}%+涨停{lu}家+上涨占比{up_ratio:.0f}%，控制仓位"
    elif lu>30 and up_ratio>50:
        r["q1"] = "结构性进攻"
        r["q1_detail"] = f"指数一般但局部强，聚焦主线"
    else:
        r["q1"] = "混沌"
        r["q1_detail"] = "热点散乱，快进快出"
    
    # Q2: 赚钱效应
    r["q2"] = f"上涨{b['up']}/下跌{b['down']}，涨停{lu}/跌停{ld}，最高{r['limit']['max_streak']}板"
    r["q2_good"] = up_ratio>55 and lu>ld
    
    # Q3-Q5: 板块方向+持续性
    for s in r["sectors_top"][:5]:
        s["hist_flow"] = get_sector_history_flow(s["name"])
        # 持续性：连续2天以上流入
        s["persistent"] = len([x for x in s["hist_flow"] if x>0]) >= 2
        s["stocks"] = get_sector_stocks(s["code"], 10)
        # Q7: Alpha分析
        for st in s["stocks"]:
            st["alpha"] = round(st["pct"] - s["pct"], 2)
        # Q8: 阶段判断
        max_streak_in_sector = max((x["streak"] for x in r["limit"]["stocks"] if x["code"] in [ss["code"] for ss in s["stocks"]]), default=0)
        s["max_streak"] = max_streak_in_sector
    
    path = f"{DATA_DIR}/{date_str}.json"
    with open(path,"w",encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=2)
    return r

def get_report(date_str):
    p = f"{DATA_DIR}/{date_str}.json"
    if os.path.exists(p): return json.load(open(p))
    return None
def list_dates():
    return [os.path.basename(f).replace(".json","") for f in sorted(glob.glob(f"{DATA_DIR}/*.json"),reverse=True)]

if __name__ == "__main__":
    print(json.dumps(generate(), ensure_ascii=False, indent=2)[:1500])

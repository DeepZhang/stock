"""单个板块涨幅前10个股（带缓存+并发）"""
import requests
import time
from concurrent.futures import ThreadPoolExecutor

HEADERS = {"User-Agent":"Mozilla/5.0","Referer":"https://data.eastmoney.com/"}

_cache = {}
_cache_time = {}
CACHE_TTL = 300  # 5分钟缓存

_sector_pct_cache = None
_sector_pct_time = 0

def get_sector_pct_all():
    """一次性获取所有板块涨幅，缓存5分钟"""
    global _sector_pct_cache, _sector_pct_time
    if _sector_pct_cache and time.time() - _sector_pct_time < 300:
        return _sector_pct_cache
    try:
        r = requests.get("https://push2test.eastmoney.com/api/qt/clist/get", params={
            "pn":"1","pz":"1000","po":"1","np":"1","fltt":"2","invt":"2",
            "fid":"f3","fs":"m:90+t:2,m:90+t:3","fields":"f12,f14,f3"
        }, headers=HEADERS, timeout=10)
        m = {}
        for x in (r.json().get("data") or {}).get("diff") or []:
            m[x.get("f12")] = float(x.get("f3",0))
        _sector_pct_cache = m
        _sector_pct_time = time.time()
        return m
    except:
        return _sector_pct_cache or {}

def get_3d(code):
    try:
        secid = ("1." if code.startswith("6") else "0.") + code
        r = requests.get("https://push2test.eastmoney.com/api/qt/stock/kline/get", params={
            "secid":secid,"fields1":"f1","fields2":"f51,f53","klt":"101","fqt":"1","end":"20500101","lmt":"5"
        }, headers=HEADERS, timeout=5)
        kl = (r.json().get("data") or {}).get("klines") or []
        if len(kl)<4: return code, 0
        closes=[float(k.split(",")[1]) for k in kl]
        return code, round((closes[-1]/closes[-4]-1)*100,2)
    except: return code, 0

def get_sector_top(bk):
    if bk in _cache and time.time() - _cache_time.get(bk,0) < CACHE_TTL:
        return _cache[bk]

    sr = requests.get("https://push2test.eastmoney.com/api/qt/clist/get", params={
        "pn":"1","pz":"50","po":"1","np":"1","fltt":"2","invt":"2",
        "fid":"f3","fs":f"b:{bk}","fields":"f12,f14,f3,f62"}, headers=HEADERS, timeout=10)
    items = []
    for it in (sr.json().get("data") or {}).get("diff") or []:
        try:
            items.append({"code":it.get("f12"),"name":it.get("f14"),
                "pct":float(it.get("f3",0)),"flow":round(float(it.get("f62",0))/1e8,2)})
        except: continue

    codes = [it["code"] for it in items]
    pct3_map = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        for code, pct3 in ex.map(get_3d, codes):
            pct3_map[code] = pct3
    for it in items:
        it["pct3d"] = pct3_map.get(it["code"], 0)

    sector_pct = get_sector_pct_all().get(bk, 0)
    items.sort(key=lambda s:-s["pct"])
    for s in items:
        s["alpha"] = round(s["pct"] - sector_pct, 2)
    result = {"pct":sector_pct,"stocks":items[:10]}
    _cache[bk] = result
    _cache_time[bk] = time.time()
    return result

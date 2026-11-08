"""指定板块每个板块涨幅前10个股，带缓存"""
import requests, time

HEADERS = {"User-Agent":"Mozilla/5.0","Referer":"https://data.eastmoney.com/"}

TARGETS = [
    ("BK1036", "半导体"),
    ("BK1128", "CPO概念"),
    ("BK0877", "PCB"),
    ("BK0478", "有色金属"),
    ("BK1138", "液冷服务器"),
    ("BK1160", "光纤概念"),
    ("BK1408", "机器人"),
    ("BK0963", "商业航天"),
    ("BK1106", "创新药"),
    ("BK1134", "算力概念"),
    ("BK0428", "电力"),
]

_cache = {"data": None, "ts": 0}

def get_alpha_top():
    if _cache["data"] and time.time() - _cache["ts"] < 60:
        return _cache["data"]
    result = []
    for bk, name in TARGETS:
        r = requests.get("https://push2test.eastmoney.com/api/qt/clist/get", params={
            "pn":"1","pz":"1","po":"1","np":"1","fltt":"2","invt":"2",
            "fid":"f3","fs":f"b:{bk}","fields":"f3"
        }, headers=HEADERS, timeout=10)
        diff = (r.json().get("data") or {}).get("diff") or []
        pct = float(diff[0]["f3"]) if diff else 0
        sr = requests.get("https://push2test.eastmoney.com/api/qt/clist/get", params={
            "pn":"1","pz":"50","po":"1","np":"1","fltt":"2","invt":"2",
            "fid":"f3","fs":f"b:{bk}","fields":"f12,f14,f3,f62"}, headers=HEADERS, timeout=10)
        stocks = []
        for it in (sr.json().get("data") or {}).get("diff") or []:
            try:
                spct = float(it.get("f3",0))
                sflow = round(float(it.get("f62",0))/1e8,2)
            except: continue
            code = it.get("f12")
            alpha = round(spct - pct, 2)
            stocks.append({
                "code": code, "name": it.get("f14"),
                "pct": spct, "flow": sflow,
                "alpha": alpha, "pct3d": 0
            })
        stocks.sort(key=lambda s:-s["pct"])
        result.append({
            "sector": name, "bk": bk, "pct": pct,
            "stocks": stocks[:10]
        })
    _cache["data"] = result
    _cache["ts"] = time.time()
    return result

if __name__ == "__main__":
    import json
    print(json.dumps(get_alpha_top(), ensure_ascii=False, indent=2))

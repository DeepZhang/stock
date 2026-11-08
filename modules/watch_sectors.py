"""关注板块资金流入监控"""
import requests

HEADERS = {"User-Agent":"Mozilla/5.0","Referer":"https://data.eastmoney.com/"}

WATCH = ["半导体","元件","CPO","液冷","光纤","玻璃基板","创新药",
         "有色","小金属","工业金属","能源金属","机器人","商业航天",
         "航天航空","光模块","PCB","光刻"]

def get_watch_sectors():
    r = requests.get("https://push2test.eastmoney.com/api/qt/clist/get", params={
        "pn":"1","pz":"1000","po":"1","np":"1","fltt":"2","invt":"2",
        "fid":"f62","fs":"m:90+t:2,m:90+t:3",
        "fields":"f12,f14,f3,f62,f104,f105,f184,f66"}, headers=HEADERS, timeout=10)
    all_sectors = (r.json().get("data") or {}).get("diff") or []
    result = []
    for x in all_sectors:
        name = x["f14"]
        if not any(w in name for w in WATCH):
            continue
        try:
            pct = float(x.get("f3",0))
            flow = round(float(x.get("f62",0))/1e8,2)
        except: continue
        result.append({
            "name":name, "bk":x["f12"], "pct":pct, "flow":flow,
            "up":x.get("f104",0), "down":x.get("f105",0),
            "main_ratio": x.get("f184",0),
            "alpha_stocks": []
        })
    result.sort(key=lambda x:-x["flow"])
    return result

if __name__ == "__main__":
    import json
    print(json.dumps(get_watch_sectors(), ensure_ascii=False, indent=2))

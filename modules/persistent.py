"""直接从东财获取板块资金流排行"""
import requests

HEADERS = {"User-Agent":"Mozilla/5.0","Referer":"https://data.eastmoney.com/"}

def get_persistent_sectors(days=5):
    # 当日板块资金流排行
    r = requests.get("https://push2test.eastmoney.com/api/qt/clist/get", params={
        "pn":"1","pz":"50","po":"1","np":"1","fltt":"2","invt":"2",
        "fid":"f62","fs":"m:90+t:2,m:90+t:3",
        "fields":"f12,f14,f3,f62,f104,f105"}, headers=HEADERS, timeout=10)
    out = []
    for x in (r.json().get("data") or {}).get("diff") or []:
        flow = round(float(x.get("f62",0))/1e8,2)
        out.append({
            "name":x["f14"], "bk":x["f12"],
            "pct":float(x.get("f3",0)),
            "flow":flow,
            "up":x.get("f104",0), "down":x.get("f105",0)
        })
    out.sort(key=lambda x:-x["flow"])
    return out[:15]

if __name__ == "__main__":
    import json
    print(json.dumps(get_persistent_sectors(), ensure_ascii=False, indent=2))

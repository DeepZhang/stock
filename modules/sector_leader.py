"""板块名跳转龙头K线 - 模糊匹配"""
import requests
HEADERS = {"User-Agent":"Mozilla/5.0","Referer":"https://data.eastmoney.com/"}

def get_sector_leader(name):
    r = requests.get("https://push2test.eastmoney.com/api/qt/clist/get", params={
        "pn":"1","pz":"500","po":"1","np":"1","fltt":"2","invt":"2",
        "fid":"f3","fs":"m:90+t:2,m:90+t:3","fields":"f12,f14"}, headers=HEADERS, timeout=10)
    diff = (r.json().get("data") or {}).get("diff") or []
    # 模糊匹配
    target = None
    for x in diff:
        if name in x.get("f14","") or x.get("f14","") in name:
            target = x; break
    if not target:
        return None, None
    bk = target["f12"]
    r2 = requests.get("https://push2test.eastmoney.com/api/qt/clist/get", params={
        "pn":"1","pz":"1","po":"1","np":"1","fltt":"2","invt":"2",
        "fid":"f3","fs":f"b:{bk}","fields":"f12,f14"}, headers=HEADERS, timeout=10)
    d2 = (r2.json().get("data") or {}).get("diff") or []
    if d2:
        c = d2[0]["f12"]
        return ("sh"+c if c.startswith("6") else "sz"+c, d2[0]["f14"])
    return None, None

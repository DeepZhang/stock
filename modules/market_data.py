import requests
import json
import time
import os
from datetime import datetime, timedelta

# ===================== 配置 =====================
API_BASE = "https://push2test.eastmoney.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://data.eastmoney.com/",
}

# 简单缓存
_cache = {}
CACHE_TTL = 30  # 秒


def _get_cached(key):
    if key in _cache:
        val, ts = _cache[key]
        if time.time() - ts < CACHE_TTL:
            return val
    return None


def _set_cached(key, val):
    _cache[key] = (val, time.time())


# ===================== 大盘指数数据 =====================
def get_index_data():
    cached = _get_cached("index_data")
    if cached is not None:
        return cached

    url = f"{API_BASE}/api/qt/ulist.np/get"
    params = {
        "fltt": "2",
        "invt": "2",
        "fields": "f2,f3,f4,f12,f14,f104,f105,f106",
        "secids": "1.000001,0.399006,1.000688",
    }
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=8)
        j = r.json()
        diff = j.get("data", {}).get("diff", [])
        result = []
        for item in diff:
            result.append({
                "name": item.get("f14"),
                "code": item.get("f12"),
                "price": float(item.get("f2", 0)),
                "change_pct": float(item.get("f3", 0)),
                "change": float(item.get("f4", 0)),
                "up_count": item.get("f104", 0),
                "down_count": item.get("f105", 0),
                "flat_count": item.get("f106", 0),
            })
        _set_cached("index_data", result)
        return result
    except Exception as e:
        print(f"[指数数据失败] {e}")
        return []


# ===================== 涨跌停统计 =====================
def get_limit_stats():
    cached = _get_cached("limit_stats")
    if cached is not None:
        return cached

    url = f"{API_BASE}/api/qt/clist/get"
    all_pcts = []
    page = 1

    while True:
        params = {
            "pn": str(page),
            "pz": "500",
            "po": "1",
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
            "fields": "f3",
        }
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=8)
            j = r.json()
            diff = j.get("data", {}).get("diff", [])
            if not diff:
                break
            for item in diff:
                try:
                    all_pcts.append(float(item.get("f3", 0)))
                except:
                    pass
            total = j.get("data", {}).get("total", 0)
            if len(all_pcts) >= total:
                break
            page += 1
            if page > 15:
                break
        except Exception as e:
            print(f"[涨跌停统计失败] {e}")
            break

    up = sum(1 for p in all_pcts if p > 0)
    down = sum(1 for p in all_pcts if p < 0)
    flat = sum(1 for p in all_pcts if p == 0)
    
    # 涨停跌停数：用东方财富官方涨停池/跌停池接口（更准确）
    from datetime import datetime
    today = datetime.now().strftime("%Y%m%d")
    
    limit_up = 0
    limit_down = 0
    
    # 涨停池
    try:
        zt_url = "https://push2ex.eastmoney.com/getTopicZTPool"
        zt_params = {
            "ut": "7eea3edcaed734bea9cbfc24409ed989",
            "dpt": "wz.ztzt",
            "Pageindex": "0",
            "pagesize": "500",
            "sort": "fbt:asc",
            "date": today,
        }
        r_zt = requests.get(zt_url, params=zt_params, headers=HEADERS, timeout=8)
        j_zt = r_zt.json()
        limit_up = len(j_zt.get("data", {}).get("pool", []))
    except Exception as e:
        print(f"[涨停池失败] {e}")
        #  fallback：用原来的方法
        limit_up = sum(1 for p in all_pcts if p >= 9.9)
    
    # 跌停池
    try:
        dt_url = "https://push2ex.eastmoney.com/getTopicDTPool"
        dt_params = {
            "ut": "7eea3edcaed734bea9cbfc24409ed989",
            "dpt": "wz.ztzt",
            "Pageindex": "0",
            "pagesize": "500",
            "sort": "fund:asc",
            "date": today,
        }
        r_dt = requests.get(dt_url, params=dt_params, headers=HEADERS, timeout=8)
        j_dt = r_dt.json()
        limit_down = len(j_dt.get("data", {}).get("pool", []))
    except Exception as e:
        print(f"[跌停池失败] {e}")
        limit_down = sum(1 for p in all_pcts if p <= -9.9)

    result = {
        "limit_up": limit_up,
        "limit_down": limit_down,
        "up_count": up,
        "down_count": down,
        "flat_count": flat,
        "total": len(all_pcts),
    }
    _set_cached("limit_stats", result)
    return result


# ===================== 板块排行 =====================
def get_sector_rank(sort_by="change_pct", order="desc", limit=10):
    cache_key = f"sector_rank_{sort_by}_{order}_{limit}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    url = f"{API_BASE}/api/qt/clist/get"
    fid_map = {"change_pct": "f3", "fund_flow": "f62"}
    fid = fid_map.get(sort_by, "f3")

    params = {
        "pn": "1",
        "pz": str(limit),
        "po": "1" if order == "desc" else "0",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "fid": fid,
        "fs": "m:90+t:2,m:90+t:3",
        "fields": "f12,f14,f3,f62",
    }
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=8)
        j = r.json()
        diff = j.get("data", {}).get("diff", [])
        result = []
        for item in diff:
            result.append({
                "code": item.get("f12"),
                "name": item.get("f14"),
                "change_pct": float(item.get("f3", 0)),
                "fund_flow": round(float(item.get("f62", 0)) / 1e8, 2),
            })
        _set_cached(cache_key, result)
        return result
    except Exception as e:
        print(f"[板块排行失败] {e}")
        return []


# ===================== 个股资金流排行 =====================
def get_stock_fund_rank(order="desc", limit=10):
    """获取个股资金流入流出 TOP10"""
    cache_key = f"stock_fund_rank_{order}_{limit}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    url = f"{API_BASE}/api/qt/clist/get"
    params = {
        "pn": "1",
        "pz": str(limit),
        "po": "1" if order == "desc" else "0",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "fid": "f62",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
        "fields": "f12,f14,f62",
    }
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=8)
        j = r.json()
        diff = j.get("data", {}).get("diff", [])
        result = []
        for item in diff:
            result.append({
                "code": item.get("f12"),
                "name": item.get("f14"),
                "fund_flow": round(float(item.get("f62", 0)) / 1e8, 2),
            })
        _set_cached(cache_key, result)
        return result
    except Exception as e:
        print(f"[个股资金流排行失败] {e}")
        return []


# ===================== 板块资金流历史对比 =====================
def _get_sector_summary_today():
    today_str = datetime.now().strftime("%Y-%m-%d")
    data_file = f"{today_str}_fund.json"
    if not os.path.exists(data_file):
        return {}
    try:
        with open(data_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        sectors = data.get("sectors", {})
        summary = {}
        for name, values in sectors.items():
            if values:
                summary[name] = values[-1]
        return summary
    except:
        return {}


def get_sector_flow_analysis():
    today_flow = _get_sector_summary_today()

    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    yesterday_file = f"{yesterday}_fund.json"
    yesterday_flow = {}
    if os.path.exists(yesterday_file):
        try:
            with open(yesterday_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            sectors = data.get("sectors", {})
            for name, values in sectors.items():
                if values:
                    yesterday_flow[name] = values[-1]
        except:
            pass

    consecutive_inflow = []
    surge_inflow = []
    consecutive_outflow = []

    if today_flow:
        sorted_today = sorted(today_flow.items(), key=lambda x: x[1], reverse=True)
        surge_inflow = sorted_today[:5]

        for name, today_val in today_flow.items():
            yesterday_val = yesterday_flow.get(name, 0)
            if today_val > 0 and yesterday_val > 0:
                consecutive_inflow.append((name, today_val, yesterday_val))
        consecutive_inflow.sort(key=lambda x: x[1], reverse=True)
        consecutive_inflow = consecutive_inflow[:5]

        for name, today_val in today_flow.items():
            yesterday_val = yesterday_flow.get(name, 0)
            if today_val < 0 and yesterday_val < 0:
                consecutive_outflow.append((name, today_val, yesterday_val))
        consecutive_outflow.sort(key=lambda x: x[1])
        consecutive_outflow = consecutive_outflow[:5]

    return {
        "consecutive_inflow": consecutive_inflow,
        "surge_inflow": surge_inflow,
        "consecutive_outflow": consecutive_outflow,
        "has_yesterday_data": len(yesterday_flow) > 0,
    }


# ===================== 汇总：今日行情总览 =====================
def get_market_fund_flow():
    """获取市场整体资金流向（超大单/大单/中单/小单）"""
    import requests
    url = "https://push2test.eastmoney.com/api/qt/stock/fflow/daykline/get"
    params = {
        "lmt": "1",
        "klt": "101",
        "secid": "1.000001",
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://data.eastmoney.com/",
    }
    try:
        r = requests.get(url, params=params, headers=headers, timeout=8)
        j = r.json()
        klines = j.get("data", {}).get("klines", [])
        if klines:
            parts = klines[-1].split(",")
            return {
                "main_net": float(parts[1]) / 1e8,
                "small_net": float(parts[2]) / 1e8,
                "medium_net": float(parts[3]) / 1e8,
                "large_net": float(parts[4]) / 1e8,
                "super_net": float(parts[5]) / 1e8,
            }
    except Exception as e:
        print(f"[市场资金流向失败] {e}")
    return None

def get_market_overview():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    indices = get_index_data()
    limit_stats = get_limit_stats()
    flow_analysis = get_sector_flow_analysis()

    fund_flow = get_market_fund_flow()

    return {
        "update_time": now,
        "indices": indices,
        "limit_stats": limit_stats,
        "flow_analysis": flow_analysis,
        "fund_flow": fund_flow,
    }

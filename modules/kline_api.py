# -*- coding: utf-8 -*-
"""
个股K线数据模块
- 搜索：东方财富搜索建议接口
- K线：新浪日K接口，周K/月K从日K聚合
- 实时行情：腾讯qt.gtimg.cn
"""
import requests
import time
from datetime import datetime

SEARCH_URL = "https://searchapi.eastmoney.com/api/suggest/get"
SEARCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.eastmoney.com/",
}

SINA_KLINE_URL = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
SINA_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://finance.sina.com.cn/",
}

_cache = {}
CACHE_TTL = 60


def _get_cached(key):
    if key in _cache:
        val, ts = _cache[key]
        if time.time() - ts < CACHE_TTL:
            return val
    return None


def _set_cached(key, val):
    _cache[key] = (val, time.time())


def search_stock(keyword):
    keyword = (keyword or "").strip()
    if not keyword:
        return []
    cache_key = f"search_{keyword}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    # 如果输入bk开头，直接搜板块
    if keyword.upper().startswith("BK"):
        try:
            r = requests.get("https://push2test.eastmoney.com/api/qt/clist/get", params={
                "pn":"1","pz":"500","po":"1","np":"1","fltt":"2","invt":"2",
                "fid":"f3","fs":"m:90+t:2,m:90+t:3","fields":"f12,f14"},
                headers={"User-Agent":"Mozilla/5.0","Referer":"https://data.eastmoney.com/"}, timeout=8)
            result = []
            target = keyword.upper()
            for x in (r.json().get("data") or {}).get("diff") or []:
                if x["f12"].upper() == target or target in x["f12"].upper():
                    result.append({"code":x["f12"],"name":x["f14"],"market":"bk","qtmk":x["f12"]})
            return result[:5]
        except: return []

    params = {
        "input": keyword, "type": "14",
        "token": "D43BF722C8E33BDC906FB84D85E326E8",
        "count": "15",
    }
    try:
        r = requests.get(SEARCH_URL, params=params, headers=SEARCH_HEADERS, timeout=8)
        j = r.json()
        data = (j.get("QuotationCodeTable") or {}).get("Data") or []
        result = []
        for item in data:
            quote_id = item.get("QuoteID", "")
            code = item.get("Code", "")
            name = item.get("Name", "")
            if not quote_id or "." not in quote_id:
                continue
            prefix, _, pure_code = quote_id.partition(".")
            if prefix == "1":
                market = "sh"
            elif prefix == "0":
                market = "sz"
            else:
                continue
            if not pure_code or len(pure_code) != 6 or not pure_code.isdigit():
                continue
            result.append({
                "code": pure_code, "name": name,
                "market": market, "qtmk": market + pure_code,
            })
        _set_cached(cache_key, result)
        return result
    except Exception as e:
        print(f"[搜索失败] {e}")
        return []


def _fetch_daily_bars(qtmk, count=320):
    """从新浪拉日K，返回 bars list (旧->新)，volume单位=手"""
    # 板块代码 BK开头
    if qtmk.startswith("BK"):
        return _fetch_sector_bars(qtmk, count)
    # 期货代码（不以sh/sz/bj开头）
    if not qtmk.startswith(("sh", "sz", "bj")):
        return _fetch_futures_bars(qtmk, count)

    r = requests.get(SINA_KLINE_URL, params={
        "symbol": qtmk, "scale": "240", "ma": "no", "datalen": str(count),
    }, headers=SINA_HEADERS, timeout=10)
    data = r.json()
    bars = []
    for b in data:
        try:
            bars.append({
                "date": b["day"],
                "open": float(b["open"]),
                "close": float(b["close"]),
                "high": float(b["high"]),
                "low": float(b["low"]),
                "volume": float(b["volume"]) / 100.0,
            })
        except (ValueError, KeyError):
            continue
    return bars


def _fetch_sector_bars(bk_code, count=320):
    """东财板块日K"""
    url = "https://push2test.eastmoney.com/api/qt/stock/kline/get"
    r = requests.get(url, params={
        "secid": f"90.{bk_code}",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "klt": "101", "fqt": "1", "end": "20500101", "lmt": str(count),
    }, headers={"User-Agent":"Mozilla/5.0","Referer":"https://quote.eastmoney.com/"}, timeout=10)
    d = r.json().get("data") or {}
    bars = []
    for row in d.get("klines",[]):
        parts = row.split(",")
        try:
            bars.append({
                "date": parts[0], "open": float(parts[1]),
                "close": float(parts[2]), "high": float(parts[3]),
                "low": float(parts[4]), "volume": float(parts[5])/100.0,
            })
        except: continue
    return bars

def _fetch_futures_bars(symbol, count=320):
    """期货日K，区分国内和外盘"""
    # 外盘代码映射
    global_map = {"GC00Y": "GC", "B00Y": "OIL"}
    if symbol in global_map:
        url = "https://stock2.finance.sina.com.cn/futures/api/json.php/GlobalFuturesService.getGlobalFuturesDailyKLine"
        r = requests.get(url, params={"symbol": global_map[symbol]}, headers=SINA_HEADERS, timeout=10)
        data = r.json()
        bars = []
        for row in data[-count:]:
            try:
                bars.append({
                    "date": row["date"],
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row.get("volume", 0)),
                })
            except (ValueError, KeyError):
                continue
        return bars

    # 国内期货
    url = "https://stock2.finance.sina.com.cn/futures/api/json.php/InnerFuturesNewService.getDailyKLine"
    r = requests.get(url, params={"symbol": symbol}, headers=SINA_HEADERS, timeout=10)
    data = r.json()
    bars = []
    for row in data[-count:]:
        try:
            bars.append({
                "date": row["d"],
                "open": float(row["o"]),
                "high": float(row["h"]),
                "low": float(row["l"]),
                "close": float(row["c"]),
                "volume": float(row["v"]),
            })
        except (ValueError, KeyError):
            continue
    return bars


def _aggregate_bars(daily_bars, period):
    """从日K聚合周K或月K"""
    if period == "day":
        return daily_bars

    grouped = []
    cur_key = None
    cur = None

    for b in daily_bars:
        dt = datetime.strptime(b["date"], "%Y-%m-%d")
        if period == "week":
            iso = dt.isocalendar()
            key = (iso[0], iso[1])
        else:  # month
            key = (dt.year, dt.month)

        if key != cur_key:
            if cur:
                grouped.append(cur)
            cur_key = key
            cur = {
                "date": b["date"],
                "open": b["open"],
                "close": b["close"],
                "high": b["high"],
                "low": b["low"],
                "volume": b["volume"],
            }
        else:
            cur["close"] = b["close"]
            cur["high"] = max(cur["high"], b["high"])
            cur["low"] = min(cur["low"], b["low"])
            cur["volume"] += b["volume"]

    if cur:
        grouped.append(cur)
    return grouped


def get_kline(qtmk, period="day", count=320):
    """获取K线数据"""
    if period not in ("day", "week", "month"):
        period = "day"

    cache_key = f"kline_{qtmk}_{period}_{count}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    try:
        # 拉足够多的日K来聚合周/月
        daily_count = count if period == "day" else count * 6
        daily_bars = _fetch_daily_bars(qtmk, daily_count)
        bars = _aggregate_bars(daily_bars, period)
        bars = bars[-int(count):]

        result = {
            "code": qtmk,
            "name": "",
            "period": period,
            "bars": bars,
        }
        _set_cached(cache_key, result)
        return result
    except Exception as e:
        print(f"[K线获取失败] {e}")
        return {"code": qtmk, "name": "", "period": period, "bars": []}


# ---------- 实时行情（腾讯） ----------
QUOTE_URL = "https://qt.gtimg.cn/q={code}"
QUOTE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://gu.qq.com/",
}


def get_realtime_quote(qtmk):
    cache_key = f"quote_{qtmk}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    try:
        url = QUOTE_URL.format(code=qtmk)
        r = requests.get(url, headers=QUOTE_HEADERS, timeout=8)
        r.encoding = "gbk"
        text = r.text
        if "=" not in text:
            return {}
        raw = text.split("=", 1)[1].strip().strip('";')
        p = raw.split("~")
        if len(p) < 50:
            return {}

        def _f(idx):
            try:
                return float(p[idx])
            except (ValueError, IndexError):
                return 0.0

        def _buy(price_idx, vol_idx):
            return {"price": _f(price_idx), "vol": _f(vol_idx)}
        result = {
            "name": p[1], "code": p[2],
            "price": _f(3), "prev_close": _f(4), "open": _f(5),
            "volume": _f(6), "change": _f(31), "change_pct": _f(32),
            "high": _f(33), "low": _f(34), "amount": _f(37),
            "turnover": _f(38), "pe": _f(39), "amplitude": _f(43),
            "circ_mv": _f(44), "total_mv": _f(45), "pb": _f(46),
            "limit_up": _f(47), "limit_down": _f(48),
            "bid1": _buy(9, 10), "bid2": _buy(11, 12),
            "bid3": _buy(13, 14), "bid4": _buy(15, 16),
            "bid5": _buy(17, 18),
            "ask1": _buy(19, 20), "ask2": _buy(21, 22),
            "ask3": _buy(23, 24), "ask4": _buy(25, 26),
            "ask5": _buy(27, 28),
        }
        _set_cached(cache_key, result)
        return result
    except Exception as e:
        print(f"[实时行情失败] {e}")
        return {}


def get_fundamental(qtmk):
    cache_key = f"fund2_{qtmk}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached
    try:
        em_code = qtmk.upper()
        result = {"report": "", "operate": {}, "company": {}}

        # 1. 操盘必读 - 主要指标
        try:
            url1 = f"https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/ZYZBAjaxNew?type=0&code={em_code}"
            r1 = requests.get(url1, headers={"Referer":"https://emweb.securities.eastmoney.com/"}, timeout=10)
            arr = r1.json().get("data", [])
            if arr:
                d = arr[0]
                def _b(v):
                    try: return round(v/1e8, 2)
                    except: return None
                result["report"] = d.get("REPORT_DATE_NAME", "")
                result["operate"] = {
                    "eps": d.get("EPSJB"),
                    "bps": round(d.get("BPS",0),2) if d.get("BPS") else None,
                    "revenue": _b(d.get("TOTALOPERATEREVE")),
                    "revenue_yoy": round(d.get("TOTALOPERATEREVETZ",0),2) if d.get("TOTALOPERATEREVETZ") is not None else None,
                    "net_profit": _b(d.get("PARENTNETPROFIT")),
                    "np_yoy": round(d.get("PARENTNETPROFITTZ",0),2) if d.get("PARENTNETPROFITTZ") is not None else None,
                    "gross_margin": round(d.get("XSMLL",0),2) if d.get("XSMLL") is not None else None,
                    "net_margin": round(d.get("XSJLL",0),2) if d.get("XSJLL") is not None else None,
                    "roe": round(d.get("ROEJQ",0),2) if d.get("ROEJQ") is not None else None,
                    "debt_ratio": round(d.get("ZCFZL",0),2) if d.get("ZCFZL") is not None else None,
                    "current_ratio": round(d.get("LD",0),2) if d.get("LD") is not None else None,
                    "quick_ratio": round(d.get("SD",0),2) if d.get("SD") is not None else None,
                    "asset_turnover": round(d.get("TOAZZL",0),2) if d.get("TOAZZL") is not None else None,
                    "inventory_days": round(d.get("CHZZTS",0),0) if d.get("CHZZTS") is not None else None,
                    "roic": round(d.get("ROIC",0),2) if d.get("ROIC") is not None else None,
                    "total_asset": _b(d.get("TOTALOPERATEREVE")),
                }
        except Exception as e:
            print(f"[操盘必读失败] {e}")

        # 2. 公司概况
        try:
            url2 = f"https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/CompanySurveyAjax?code={em_code}"
            r2 = requests.get(url2, headers={"Referer":"https://emweb.securities.eastmoney.com/"}, timeout=10)
            jb = r2.json().get("jbzl", {})
            fxxg = r2.json().get("fxxg", {})
            result["company"] = {
                "name": jb.get("gsmc",""),
                "industry": jb.get("sshy",""),
                "exchange": jb.get("ssjys",""),
                "chairman": jb.get("dsz",""),
                "employees": jb.get("gyrs",""),
                "reg_capital": jb.get("zczb",""),
                "founded": fxxg.get("clrq",""),
                "listed": fxxg.get("ssrq",""),
                "main_biz": jb.get("jyfw","")[:200],
                "intro": jb.get("gsjj","")[:300],
                "website": jb.get("gswz",""),
                "address": jb.get("bgdz",""),
            }
        except Exception as e:
            print(f"[公司概况失败] {e}")

        _set_cached(cache_key, result)
        return result
    except Exception as e:
        print(f"[基本面失败] {e}")
        return {}

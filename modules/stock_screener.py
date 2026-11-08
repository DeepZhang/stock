# -*- coding: utf-8 -*-
"""选股器：横盘放量突破策略（新浪日K数据源，板块来自资金流向页）"""
import requests
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# 从资金流向模块导入板块列表
try:
    from stock_data import SECTOR_MAP as BLOCKS
except Exception:
    BLOCKS = {}

CLIST_URL = "https://push2test.eastmoney.com/api/qt/clist/get"
SINA_KLINE_URL = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://data.eastmoney.com/",
}

N = 30
VOL_NUM = 1.8


def _market_prefix(code):
    return "sh" if code.startswith("6") else "sz"


def get_block_stocks():
    result = {}  # code -> (name, sector)
    for bk_name, bk_code in BLOCKS.items():
        page = 1
        while True:
            params = {
                "pn": str(page), "pz": "200", "po": "1", "np": "1",
                "fltt": "2", "invt": "2", "fid": "f12",
                "fs": f"b:{bk_code}", "fields": "f12,f14",
            }
            try:
                r = requests.get(CLIST_URL, params=params, headers=HEADERS, timeout=10)
                j = r.json()
                diff = j.get("data", {}).get("diff", []) or []
                total = j.get("data", {}).get("total", 0)
                for item in diff:
                    code = item.get("f12", "")
                    name = item.get("f14", "")
                    if len(code) == 6 and code.isdigit():
                        if code.startswith("30") or code.startswith("688"):
                            continue
                        if code not in result:
                            result[code] = (name, bk_name)
                if page * 200 >= total:
                    break
                page += 1
            except Exception as e:
                print(f"[板块{bk_name}] 失败: {e}")
                break
        time.sleep(0.15)
    return result


def fetch_kline_bars(qtmk, count=120):
    try:
        r = requests.get(SINA_KLINE_URL, params={
            "symbol": qtmk, "scale": "240", "ma": "no", "datalen": str(count),
        }, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}, timeout=8)
        data = r.json()
        bars = []
        for b in data:
            try:
                bars.append({
                    "date": b["day"], "open": float(b["open"]),
                    "close": float(b["close"]), "high": float(b["high"]),
                    "low": float(b["low"]), "volume": float(b["volume"]),
                })
            except (ValueError, KeyError):
                continue
        return bars
    except Exception:
        return []


def ema_series(arr, n):
    k = 2 / (n + 1)
    out = [arr[0]]
    for i in range(1, len(arr)):
        out.append(arr[i] * k + out[-1] * (1 - k))
    return out


def check_stock(code, name):
    qtmk = _market_prefix(code) + code
    bars = fetch_kline_bars(qtmk, 120)
    if len(bars) < N + 30:
        return None

    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    vols = [b["volume"] for b in bars]

    if len(vols) < 26:
        return None
    vol_base = sum(vols[-25:-5]) / 20
    v1, v2, v3 = vols[-1], vols[-2], vols[-3]
    if not (v1 > vol_base * VOL_NUM and v2 > vol_base * VOL_NUM and v3 > vol_base * VOL_NUM):
        return None

    ema12 = ema_series(closes, 12)
    ema26 = ema_series(closes, 26)
    dif = [ema12[i] - ema26[i] for i in range(len(closes))]
    dea = ema_series(dif, 9)
    macd = [(dif[i] - dea[i]) * 2 for i in range(len(closes))]

    type_a = abs(macd[-1]) < 0.5 and abs(dif[-1] - dea[-1]) < 0.3
    type_b = dif[-1] > dea[-1] and macd[-1] > 0 and macd[-1] > macd[-2]
    type_c = dif[-1] > 0 and dea[-1] > 0 and macd[-1] > 0 and macd[-1] > macd[-2]
    if not (type_a or type_b or type_c):
        return None

    hh = max(highs[-N:])
    ma20 = sum(closes[-20:]) / 20
    if not (closes[-1] > hh * 0.95 and closes[-1] > ma20):
        return None

    return {
        "code": code, "name": name, "qtmk": qtmk,
        "price": closes[-1],
        "change_pct": round((closes[-1] - closes[-2]) / closes[-2] * 100, 2),
        "vol_ratio": round(v1 / vol_base, 2),
        "macd_type": "A" if type_a else ("B" if type_b else "C"),
        "macd_val": round(macd[-1], 3),
        "dif": round(dif[-1], 3),
        "dea": round(dea[-1], 3),
        "above_ma20": round((closes[-1] - ma20) / ma20 * 100, 2),
        "date": bars[-1]["date"],
    }


def run_screen():
    print("[选股] 获取板块成分股...")
    stocks = get_block_stocks()
    total = len(stocks)
    print(f"[选股] 候选 {total} 只（{len(BLOCKS)}个板块）")

    results = []
    done = 0
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(check_stock, c, n[0]): (c, n[1]) for c, n in stocks.items()}
        for future in as_completed(futures):
            done += 1
            try:
                r = future.result()
                if r:
                    r["sector"] = futures[future][1]
                    results.append(r)
                    print(f"  [{done}/{total}] 命中: {r['name']}({r['code']}) "
                          f"价:{r['price']} 放量:{r['vol_ratio']}x MACD:{r['macd_type']}")
            except Exception:
                pass
            if done % 100 == 0:
                print(f"[选股] 进度 {done}/{total}, 命中 {len(results)}")
            time.sleep(0.05)

    results.sort(key=lambda x: x["vol_ratio"], reverse=True)
    print(f"[选股] 完成，命中 {len(results)} 只")
    return results

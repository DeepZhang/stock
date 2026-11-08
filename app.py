import sys, os
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE_DIR, 'modules'))
from flask import Flask, jsonify, send_file, request, send_from_directory
import json
import datetime
import os
import glob
import logging
import threading
import time
import tempfile

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)
log.disabled = True

import stock_data
import market_data
import kline_api

app = Flask(__name__)

# ===================== 数据接口 =====================

def _read_latest_data(target_date=None):
    """读取数据：可指定日期；默认读当天，当天无数据则实时拉取"""
    base_dir = BASE_DIR
    now = datetime.datetime.now()
    today_str = now.strftime("%Y-%m-%d")

    weekday = now.weekday()  # 0=Mon, 6=Sun

    # 工作日 9:00-9:15：新的一天已开始，清空昨日数据，等集合竞价后再拉实时
    if target_date is None and weekday < 5 and now.hour == 9 and now.minute < 15:
        return {"times": [], "sectors": {}}, ""

    # 周末或非交易时间（15:00后/9:30前），读最近交易日
    if target_date is None:
        is_trading_time = (weekday < 5) and ((now.hour == 9 and now.minute >= 15) or (10 <= now.hour < 15))
        if weekday >= 5 or not is_trading_time:
            # 找最近有数据的文件
            import glob
            files = sorted(glob.glob(os.path.join(BASE_DIR, "data/funds/*_fund.json")))
            if files:
                latest = files[-1]
                try:
                    with open(latest, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if data.get("times") and data.get("sectors") and len(data["times"]) > 0:
                        return data, latest.split("/")[-1].replace("_fund.json","")
                except:
                    pass


    if target_date:
        # 指定日期：先读本地文件
        target_file = os.path.join(base_dir, "data/funds", f"{target_date}_fund.json")
        if os.path.exists(target_file):
            try:
                with open(target_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("times") and data.get("sectors") and len(data["times"]) > 0:
                    return data, target_date
            except:
                pass

        # 如果指定的是今天但文件不存在/为空，且是交易日，才实时拉取收盘数据
        import stock_data as _sd
        if target_date == today_str and _sd.is_trading_day():
            print(f"[当天快照] {target_date} 无本地数据，实时从东方财富拉取收盘数据...")
            for retry in range(3):
                try:
                    import stock_data
                    sectors_data = stock_data.fetch_all_sectors_fund()
                    if sectors_data:
                        now_str = datetime.datetime.now().strftime("%H:%M")
                        sectors_arr = {name: [val] for name, val in sectors_data.items()}
                        data = {
                            "times": [now_str],
                            "sectors": sectors_arr,
                            "meta": {
                                "date": today_str,
                                "sector_count": len(sectors_data),
                                "sample_count": 1,
                                "start_time": now_str,
                                "end_time": now_str,
                            }
                        }
                        # 保存为当天文件
                        try:
                            with open(target_file, "w", encoding="utf-8") as f:
                                json.dump(data, f, ensure_ascii=False)
                            print(f"[当天快照] 已保存到 {target_file}")
                        except Exception as save_err:
                            print(f"[当天快照] 保存失败: {save_err}")
                        return data, today_str
                    else:
                        print(f"[当天快照] 第 {retry+1} 次拉取返回空，重试中...")
                except Exception as e:
                    print(f"[当天快照] 第 {retry+1} 次拉取失败: {e}")
                    import time
                    time.sleep(1)

        # 其他历史日期：没有就返回空
        return {"times": [], "sectors": {}}, target_date

    # 默认：读当天
    now = datetime.datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    today_file = os.path.join(base_dir, "data/funds", f"{today_str}_fund.json")

    # 判断是否实时模式（9:15之后就开始实时拉取）
    hour = now.hour
    minute = now.minute
    is_realtime = (
        (hour == 9 and minute >= 15) or
        (hour == 10) or
        (hour == 11) or
        (hour == 13) or
        (hour == 14) or
        (hour == 15 and minute <= 0)
    )

    # 非实时模式：读本地文件（9:15之前）
    if not is_realtime:
        if os.path.exists(today_file):
            try:
                with open(today_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("times") and data.get("sectors") and len(data["times"]) > 0:
                    return data, today_str
            except:
                pass

    # 交易时间内，实时拉取最新数据，追加到历史数据后面
    for retry in range(3):
        try:
            import stock_data
            sectors_data = stock_data.fetch_all_sectors_fund()
            if sectors_data:
                now_str = datetime.datetime.now().strftime("%H:%M")
                
                # 先读本地历史数据
                history = {"times": [], "sectors": {}}
                if os.path.exists(today_file):
                    try:
                        with open(today_file, "r", encoding="utf-8") as f:
                            history = json.load(f)
                    except:
                        pass
                
                # 如果最后一个时间点就是当前时间，不重复添加
                last_time = history["times"][-1] if history["times"] else None
                if last_time != now_str:
                    # 追加新数据点
                    history["times"].append(now_str)
                    for name, val in sectors_data.items():
                        if name not in history["sectors"]:
                            history["sectors"][name] = []
                        history["sectors"][name].append(val)
                    
                    # 保存回本地文件
                    try:
                        with open(today_file, "w", encoding="utf-8") as f:
                            json.dump(history, f, ensure_ascii=False)
                    except Exception as save_err:
                        print(f"[快照] 保存失败: {save_err}")
                
                return history, today_str
            else:
                print(f"[快照] 第 {retry+1} 次拉取返回空，重试中...")
        except Exception as e:
            print(f"[快照] 第 {retry+1} 次拉取失败: {e}")
            import time
            time.sleep(1)

    # 3. 都失败了，返回空
    return {"times": [], "sectors": {}}, ""


@app.route("/api/data")
def api_data():
    target_date = request.args.get("date", None)
    data, date_str = _read_latest_data(target_date)
    return jsonify({
        "data": data,
        "date": date_str
    })


@app.route("/api/dates")
def api_dates():
    """返回所有有数据的日期列表（倒序）"""
    base_dir = BASE_DIR
    pattern = os.path.join(base_dir, "data", "funds", "*_fund.json")
    files = glob.glob(pattern)
    dates = []
    for f in files:
        fname = os.path.basename(f)
        # 提取日期部分：YYYY-MM-DD_fund.json
        if fname.endswith("_fund.json"):
            d = fname[:-len("_fund.json")]
            # 简单校验格式
            if len(d) == 10 and d[4] == "-" and d[7] == "-":
                dates.append(d)
    dates.sort(reverse=True)
    return jsonify({"dates": dates})


@app.route("/api/market-overview")
def api_market_overview():
    data = market_data.get_market_overview()
    return jsonify(data)


@app.route("/api/sector-rank")
def api_sector_rank():
    sort_by = request.args.get("sort_by", "change_pct")
    order = request.args.get("order", "desc")
    limit = int(request.args.get("limit", "10"))
    data = market_data.get_sector_rank(sort_by=sort_by, order=order, limit=limit)
    return jsonify({"list": data})


@app.route("/api/sector-change-pct")
def api_sector_change_pct():
    """获取各板块的涨跌幅（用 SECTOR_MAP 的代码匹配）"""
    try:
        import requests
        import stock_data
        API_BASE = "https://push2test.eastmoney.com"
        HEADERS = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://data.eastmoney.com/",
        }
        url = f"{API_BASE}/api/qt/clist/get"
        params = {
            "pn": "1",
            "pz": "1000",
            "po": "1",
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": "m:90+t:2,m:90+t:3",
            "fields": "f12,f14,f3",  # f12=代码, f14=名称, f3=涨跌幅
        }
        r = requests.get(url, params=params, headers=HEADERS, timeout=10)
        j = r.json()
        diff = j.get("data", {}).get("diff", [])
        
        # 按代码建索引
        code_map = {}
        for item in diff:
            code_map[item["f12"]] = float(item.get("f3", 0))
        
        # 用 SECTOR_MAP 的代码匹配，返回 {板块名: 涨跌幅}
        result = {}
        for name, code in stock_data.SECTOR_MAP.items():
            pct = code_map.get(code)
            if pct is not None:
                result[name] = pct
        
        return jsonify({"data": result})
    except Exception as e:
        print(f"涨跌幅接口错误: {e}")
        return jsonify({"data": {}})


@app.route("/api/stock-fund-rank")
def api_stock_fund_rank():
    order = request.args.get("order", "desc")
    limit = int(request.args.get("limit", "10"))
    data = market_data.get_stock_fund_rank(order=order, limit=limit)
    return jsonify({"list": data})




# ===================== 个股K线接口 =====================

@app.route("/api/search")
def api_search():
    keyword = request.args.get("keyword", "").strip()
    result = kline_api.search_stock(keyword)
    return jsonify({"list": result})


@app.route("/api/kline")
def api_kline():
    code = request.args.get("code", "").strip()
    period = request.args.get("period", "day").strip()
    count = int(request.args.get("count", "320"))
    if not code:
        return jsonify({"error": "missing code"}), 400
    result = kline_api.get_kline(code, period=period, count=count)
    return jsonify(result)


@app.route("/api/quote")
def api_quote():
    code = request.args.get("code", "").strip()
    if not code:
        return jsonify({}), 400
    return jsonify(kline_api.get_realtime_quote(code))

@app.route("/api/fundamental")
def api_fundamental():
    code = request.args.get("code", "").strip()
    if not code:
        return jsonify({}), 400
    return jsonify(kline_api.get_fundamental(code))

@app.route("/api/review")
def api_review():
    import review
    return jsonify(review.review())

@app.route("/api/daily_report")
def api_daily_report():
    import daily_report
    date = request.args.get("date","").strip()
    if not date:
        return jsonify({"dates":daily_report.list_dates()})
    r = daily_report.get_report(date)
    if not r:
        r = daily_report.generate(date)
    return jsonify(r)

@app.route("/api/daily_report/gen")
def api_daily_gen():
    import daily_report
    return jsonify(daily_report.generate())

@app.route("/api/persistent")
def api_persistent():
    from persistent import get_persistent_sectors
    return jsonify(get_persistent_sectors())

@app.route("/api/sector_top")
def api_sector_top():
    from sector_api import get_sector_top
    bk = request.args.get("bk","")
    return jsonify(get_sector_top(bk))

@app.route("/api/alpha_top")
def api_alpha_top():
    from alpha_top import get_alpha_top
    return jsonify(get_alpha_top())

@app.route("/api/watch_sectors")
def api_watch():
    from watch_sectors import get_watch_sectors
    return jsonify(get_watch_sectors())

@app.route("/api/sector_leader")
def api_sector_leader():
    from sector_leader import get_sector_leader
    name = request.args.get("name","")
    code, sname = get_sector_leader(name)
    return jsonify({"code":code,"name":sname})

@app.route("/api/sector_stocks")
def api_sector_stocks():
    import requests
    bk = request.args.get("bk","")
    r = requests.get("https://push2test.eastmoney.com/api/qt/clist/get", params={
        "pn":"1","pz":"50","po":"1","np":"1","fltt":"2","invt":"2",
        "fid":"f3","fs":f"b:{bk}","fields":"f12,f14,f3,f62"},
        headers={"User-Agent":"Mozilla/5.0","Referer":"https://data.eastmoney.com/"}, timeout=10)
    out = []
    for it in (r.json().get("data") or {}).get("diff") or []:
        out.append({"code":it.get("f12"),"name":it.get("f14"),
            "pct":float(it.get("f3",0)),"flow":round(float(it.get("f62",0))/1e8,2)})
    return jsonify(out)


SCREEN_CACHE = os.path.join(BASE_DIR, "data/stock_picks.json")

# 单进程 Flask 的所有入口（手动、启动、定时）共享同一把锁。
_screen_lock = threading.Lock()
_screen_scanning = False
SCREEN_CACHE_MAX_AGE = 24 * 60 * 60


def _read_screen_cache():
    try:
        with open(SCREEN_CACHE, encoding="utf-8") as f:
            cached = json.load(f)
        if not isinstance(cached.get("list"), list):
            raise ValueError("screen cache list required")
        return cached
    except (OSError, ValueError, AttributeError):
        return {"time": "", "list": []}


def _screen_worker():
    global _screen_scanning
    tmp = None
    try:
        import stock_screener
        results = stock_screener.run_screen()
        payload = {"time": datetime.datetime.now().isoformat(timespec="microseconds"), "list": results}
        os.makedirs(os.path.dirname(SCREEN_CACHE), exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=os.path.dirname(SCREEN_CACHE),
                                         delete=False) as f:
            tmp = f.name
            json.dump(payload, f, ensure_ascii=False, allow_nan=False)
        os.replace(tmp, SCREEN_CACHE)
    except Exception:
        app.logger.exception("后台选股失败，保留原缓存")
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
        with _screen_lock:
            _screen_scanning = False


def _start_screen(force=False):
    global _screen_scanning
    with _screen_lock:
        if _screen_scanning:
            return True
        cached = _read_screen_cache()
        try:
            age = time.time() - os.path.getmtime(SCREEN_CACHE)
        except OSError:
            age = SCREEN_CACHE_MAX_AGE
        if not force and cached.get("time") and age < SCREEN_CACHE_MAX_AGE:
            return False
        _screen_scanning = True
        try:
            threading.Thread(target=_screen_worker, name="stock-screen", daemon=True).start()
        except Exception:
            _screen_scanning = False
            raise
        return True


@app.route("/api/screen")
def api_screen():
    cached = _read_screen_cache()
    scanning = _start_screen(force=request.args.get("force") == "1")
    return jsonify({"list": cached["list"], "count": len(cached["list"]),
                    "time": cached.get("time", ""), "scanning": scanning})

@app.route("/api/screen_cached")
def api_screen_cached():
    import os
    if os.path.exists(SCREEN_CACHE):
        with open(SCREEN_CACHE, "r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    return jsonify({"time": "", "list": []})

@app.route("/api/commodities")
def api_commodities():
    import requests, re
    result = {}
    # 腾讯外盘
    try:
        r = requests.get("https://qt.gtimg.cn/q=hf_GC,hf_OIL", timeout=8)
        t = r.content.decode("gbk", errors="ignore")
        for code, name in [("hf_GC","COMEX黄金"),("hf_OIL","布伦特原油")]:
            m = re.search(code + '="([^"]+)"', t)
            if m:
                p = m.group(1).split(",")
                result[name] = {"price": p[0], "chg_pct": float(p[1])}
    except Exception as e:
        print(f"商品行情腾讯失败: {e}")
    # 新浪国内期货（主力连续 nf_前缀）
    try:
        r2 = requests.get("https://hq.sinajs.cn/list=nf_AU0,nf_AG0,nf_CU0,nf_LC0",
                          headers={"Referer":"https://finance.sina.com.cn"}, timeout=8)
        t2 = r2.content.decode("gbk", errors="ignore")
        for code, name in [("nf_AU0","沪金"),("nf_AG0","沪银"),("nf_CU0","沪铜"),("nf_LC0","碳酸锂")]:
            m = re.search(code + '="([^"]*)"', t2)
            if m and m.group(1):
                p = m.group(1).split(",")
                if len(p) > 9 and p[7]:
                    price = p[7]  # 最新价
                    prev = p[9]   # 昨结算
                    chg = (float(price)-float(prev))/float(prev)*100 if float(prev)>0 else 0
                    result[name] = {"price": price, "chg_pct": round(chg,2)}
    except Exception as e:
        print(f"商品行情新浪失败: {e}")
    return jsonify(result)

@app.route("/api/quote_batch")
def api_quote_batch():
    import requests
    codes = request.args.get("codes", "")
    if not codes:
        return jsonify({})
    try:
        r = requests.get(f"https://qt.gtimg.cn/q={codes}", timeout=8)
        t = r.content.decode("gbk", errors="ignore")
        result = {}
        for line in t.strip().split(";"):
            line = line.strip()
            if not line or "=" not in line: continue
            var = line.split("=")[0].replace("v_","")
            data = line.split('"')[1] if '"' in line else ""
            if not data: continue
            p = data.split("~")
            if len(p) > 45:
                result[var] = {
                    "name": p[1], "price": float(p[3]) if p[3] else 0,
                    "change": float(p[31]) if p[31] else 0,
                    "change_pct": float(p[32]) if p[32] else 0,
                    "high": float(p[33]) if p[33] else 0,
                    "low": float(p[34]) if p[34] else 0,
                    "volume": float(p[36]) if p[36] else 0,
                    "amount": float(p[37]) if p[37] else 0,
                    "turnover": float(p[38]) if p[38] else 0,
                    "pe": float(p[39]) if p[39] else 0,
                    "amplitude": float(p[43]) if p[43] else 0,
                }
        return jsonify(result)
    except Exception as e:
        print(f"批量行情失败: {e}")
        return jsonify({})

@app.route("/precious")
def precious_page():
    return send_file("static/precious.html")

def _auto_screen():
    _start_screen()


def _daily_screen():
    """每天15:30自动跑选股更新缓存，非交易日不跑"""
    import stock_data
    while True:
        now = datetime.datetime.now()
        # 计算今天15:30
        target = now.replace(hour=15, minute=30, second=0, microsecond=0)
        if now > target:
            target += datetime.timedelta(days=1)
        wait_sec = (target - now).total_seconds()
        print(f"[定时选股] 下次运行: {target.strftime('%Y-%m-%d %H:%M')}, 等待{wait_sec/3600:.1f}小时")
        time.sleep(wait_sec)
        # 非交易日不跑
        if not stock_data.is_trading_day(target):
            print(f"[定时选股] {target.strftime('%Y-%m-%d')} 非交易日，跳过")
            continue
        print(f"[定时选股] 开始运行...")
        _start_screen(force=True)



@app.route("/screener")
def screener_page():
    return send_file("static/screener.html")

# ===================== 页面路由 =====================

@app.route("/")
def index():
    return send_file("static/index.html")


@app.route("/market")
def market_page():
    return send_file("static/market.html")


@app.route("/rank")
def rank_page():
    return send_file("static/rank.html")


@app.route("/sentiment")
def sentiment_page():
    return send_file("static/sentiment.html")


@app.route("/js/chart.js")
def js_chart():
    return send_file("static/js/chart.js")
@app.route("/js/kline.js")
def js_kline():
    return send_file("static/js/kline.js")
@app.route("/js/market.js")
def js_market():
    return send_file("static/js/market.js")
@app.route("/js/rank.js")
def js_rank():
    return send_file("static/js/rank.js")
@app.route("/js/sentiment.js")
def js_sentiment():
    return send_file("static/js/sentiment.js")


@app.route("/market.js")
def market_js():
    return send_file("static/js/market.js")


@app.route("/rank.js")
def rank_js():
    return send_file("static/js/rank.js")


@app.route("/sentiment.js")
def sentiment_js():
    return send_file("static/js/sentiment.js")

@app.route("/daily")
def daily_page():
    return send_from_directory("static", "daily.html")

@app.route("/review")
def review_page():
    return send_from_directory("static", "review.html")

@app.route("/kline")
def kline_page():
    return send_file("static/kline.html")


import requests as _req

@app.route("/api/holders")
def api_holders():
    code = request.args.get("code", "")
    pure = code.replace("sh","").replace("sz","").replace("bj","")
    # 确定市场前缀
    if pure.startswith("6"):
        sec = "SH"+pure
    elif pure.startswith(("0","3")):
        sec = "SZ"+pure
    else:
        sec = "BJ"+pure
    try:
        r = _req.get("https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax",
            params={"code":sec}, headers={"User-Agent":"Mozilla/5.0"}, timeout=10)
        data = r.json().get("gdrs",[])
        result = []
        for d in reversed(data):
            result.append({
                "date": d.get("END_DATE","")[:10],
                "holders": d.get("HOLDER_TOTAL_NUM",0),
                "change": d.get("TOTAL_NUM_RATIO",0),
                "price": d.get("PRICE",0),
            })
        return jsonify(result)
    except Exception as e:
        return jsonify([])

@app.route("/kline.js")
def kline_js():
    return send_file("static/js/kline.js")



@app.route("/shots/<path:filename>")
def serve_shot(filename):
    return send_from_directory(os.path.join(BASE_DIR, "shots"), filename)

if __name__ == "__main__":
    threading.Thread(target=_auto_screen, daemon=True).start()
    threading.Thread(target=_daily_screen, daemon=True).start()
    stock_data.start_collector()
    app.run(host="0.0.0.0", port=5000, debug=False)

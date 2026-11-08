import requests
import json
import time
import threading
import os
import random
from datetime import datetime, timedelta

# ===================== 配置 =====================
# 数据源：东方财富测试环境（正式环境 push2.eastmoney.com 被服务器 IP 封禁）
API_BASE = "https://push2test.eastmoney.com"

# 板块名称 -> 板块代码映射
SECTOR_MAP = {
    "半导体":     "BK1036",
    "CPO概念":   "BK1128",
    "PCB":       "BK0877",
    "有色金属":   "BK0478",
    "微盘股":     "BK1158",
    "液冷服务器": "BK1138",
    "光纤概念":   "BK1660",
    "机器人":     "BK1408",
    "商业航天":   "BK0963",
    "创新药":     "BK1106",
    "培育钻石":   "BK1023",
    "先进封装":   "BK1101",
    "玻璃基板":   "BK1175",
    "锂矿概念":   "BK1173",
    "算力概念":   "BK1134",
    "化工原料":   "BK0512",
    "电力":       "BK0428",
    "电网概念":   "BK1647",
    "军工":       "BK0490",
    "电池":       "BK1033",
    "储能概念":   "BK0989",
    "光伏概念":   "BK0588",
    "固态电池":   "BK0968",
}

# 采集间隔（秒）
NORMAL_INTERVAL = 60
HIGH_FREQ_INTERVAL = 30
RETRY_TIMES = 2

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://data.eastmoney.com/bkzj/hy.html",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# ===================== 节假日列表（A股休市日，周末代码自动判断不用列） =====================
HOLIDAYS = {
    "2026-01-01",
    "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",
    "2026-04-06",
    "2026-05-01",
    "2026-06-19",
    "2026-09-25",  # 中秋
    "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07",
}

# ===================== 全局状态 =====================
today_data = {
    "times": [],
    "sectors": {name: [] for name in SECTOR_MAP}
}

_data_lock = threading.Lock()
_current_date = datetime.now().strftime("%Y-%m-%d")

# 资金快照目录不存在时采集与读取都会静默失败，先建出来
os.makedirs("data/funds", exist_ok=True)


def is_trading_day(d=None):
    """判断是否A股交易日：排除周末和节假日"""
    if d is None:
        d = datetime.now()
    if d.weekday() >= 5:
        return False
    if d.strftime("%Y-%m-%d") in HOLIDAYS:
        return False
    return True


def _data_file():
    return os.path.join("data/funds", f"{_current_date}_fund.json")


def _load_today_data():
    """启动时加载当天已有数据；非交易日则加载最近交易日数据"""
    global today_data, _current_date
    now = datetime.now()

    if not is_trading_day(now):
        # 非交易日：找最近一个有数据的文件
        import glob
        files = sorted(glob.glob("data/funds/*_fund.json"))
        if files:
            latest = files[-1]
            try:
                with open(latest, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if loaded.get("times") and loaded.get("sectors"):
                    today_data = loaded
                    # 从文件名恢复日期
                    fname = os.path.basename(latest)
                    _current_date = fname.replace("_fund.json", "")
                    print(f"[非交易日] 加载最近交易日 {_current_date} 数据，共 {len(today_data['times'])} 条")
                    return
            except Exception as e:
                print(f"[加载] 读取 {latest} 失败: {e}")
        print("[非交易日] 无历史数据，从空开始")
        return

    fname = _data_file()
    if os.path.exists(fname):
        try:
            with open(fname, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if "times" in loaded and "sectors" in loaded:
                today_data = loaded
                print(f"[加载] 已读取 {fname}，共 {len(today_data['times'])} 条数据")
        except Exception as e:
            print(f"[加载] 读取 {fname} 失败: {e}，从空数据开始")
            today_data = {"times": [], "sectors": {name: [] for name in SECTOR_MAP}}


def _check_new_day():
    """跨天时检查是否需要切换日期；非交易日不重置数据"""
    global _current_date, today_data
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d")

    if now_str == _current_date:
        return

    # 新的一天
    if not is_trading_day(now):
        # 周末/非交易日：不切换，继续保留上一个交易日数据
        return

    # 是交易日，检查当天是否已有数据文件
    new_file = os.path.join("data/funds", f"{now_str}_fund.json")
    if os.path.exists(new_file):
        with _data_lock:
            _current_date = now_str
            try:
                with open(new_file, "r", encoding="utf-8") as f:
                    today_data = json.load(f)
                print(f"[跨天] 加载 {now_str} 已有数据，共 {len(today_data['times'])} 条")
            except:
                today_data = {"times": [], "sectors": {name: [] for name in SECTOR_MAP}}
    else:
        # 新交易日，从空开始
        with _data_lock:
            _current_date = now_str
            today_data = {"times": [], "sectors": {name: [] for name in SECTOR_MAP}}
            print(f"[跨天] 新交易日 {_current_date}，数据已重置")


# ===================== 一次性拉取所有板块资金流 =====================
def fetch_all_sectors_fund():
    url = f"{API_BASE}/api/qt/clist/get"
    params = {
        "pn": "1", "pz": "1000", "po": "1", "np": "1", "fltt": "2", "invt": "2",
        "fid": "f62", "fs": "m:90+t:2,m:90+t:3",
        "fields": "f12,f14,f62",
    }
    for attempt in range(RETRY_TIMES + 1):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=10)
            r.raise_for_status()
            j = r.json()
            diff = j.get("data", {}).get("diff", [])
            if not diff:
                if attempt < RETRY_TIMES:
                    time.sleep(1)
                    continue
                return None
            code_map = {item["f12"]: item["f62"] for item in diff}
            result = {}
            for name, code in SECTOR_MAP.items():
                val = code_map.get(code)
                try:
                    num = float(val)
                except (ValueError, TypeError):
                    num = 0.0
                result[name] = round(num / 1e8, 2)
            return result
        except Exception as e:
            if attempt < RETRY_TIMES:
                time.sleep(1 * (attempt + 1))
                continue
            print(f"[采集失败] {e}")
            return None


def is_trading_time():
    """A股交易时间：9:30-11:30, 13:00-15:00"""
    now = datetime.now()
    if not is_trading_day(now):
        return False
    total = now.hour * 60 + now.minute
    return (570 <= total <= 690) or (780 <= total <= 900)


def current_interval():
    now = datetime.now()
    total = now.hour * 60 + now.minute
    if (570 <= total <= 590) or (870 <= total <= 900):
        return HIGH_FREQ_INTERVAL
    return NORMAL_INTERVAL


def collect_loop():
    _load_today_data()
    print("=" * 50)
    print("数据采集线程已启动")
    print(f"数据源: {API_BASE}")
    print(f"板块数: {len(SECTOR_MAP)}")
    print(f"普通间隔: {NORMAL_INTERVAL}s | 活跃间隔: {HIGH_FREQ_INTERVAL}s")
    print("=" * 50)

    while True:
        try:
            _check_new_day()

            if not is_trading_time():
                time.sleep(60)
                continue

            now_str = datetime.now().strftime("%H:%M")
            fund_data = fetch_all_sectors_fund()

            if fund_data is None:
                print(f"[{now_str}] 采集失败，等待下一轮")
                time.sleep(30)
                continue

            with _data_lock:
                today_data["times"].append(now_str)
                for name in SECTOR_MAP:
                    today_data["sectors"][name].append(fund_data.get(name, 0.0))
                today_data["meta"] = {
                    "date": _current_date,
                    "sector_count": len(SECTOR_MAP),
                    "sample_count": len(today_data["times"]),
                    "start_time": today_data["times"][0] if today_data["times"] else "",
                    "end_time": now_str,
                }
                fname = _data_file()
                with open(fname, "w", encoding="utf-8") as f:
                    json.dump(today_data, f, ensure_ascii=False)

            top3 = sorted(fund_data.items(), key=lambda x: x[1], reverse=True)[:3]
            bottom3 = sorted(fund_data.items(), key=lambda x: x[1])[:3]
            print(f"[{now_str}] 采集成功 | TOP3: " +
                  " | ".join(f"{n}:{v:+.2f}亿" for n, v in top3))

            interval = current_interval()
            time.sleep(interval)

        except Exception as e:
            print(f"[主循环异常] {e}")
            time.sleep(30)


def start_collector():
    t = threading.Thread(target=collect_loop, daemon=True)
    t.start()
    return t

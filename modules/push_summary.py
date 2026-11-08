#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日收盘总结 - 飞书推送"""

import sys
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from datetime import datetime
import market_data
import stock_data

WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/0f361ea9-6b19-4d14-9d46-f9ea5cb71c65"

def get_north_fund_flow():
    """获取北向资金流入"""
    url = "https://push2test.eastmoney.com/api/qt/kamt.rtmin/get"
    params = {
        "fields1": "f1,f2,f3,f4",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
    }
    try:
        r = requests.get(url, params=params, headers={"Referer": "https://data.eastmoney.com/"}, timeout=8)
        j = r.json()
        data = j.get("data", {})
        s2n = data.get("s2n", [])
        if s2n:
            # 从后往前找，找到第一个有效的数据
            for i in range(len(s2n)-1, -1, -1):
                parts = s2n[i].split(",")
                try:
                    # f52 是沪股通+深股通净流入
                    val = float(parts[1])
                    if val != 0:
                        return val / 1e4  # 单位：亿
                except:
                    continue
    except Exception as e:
        print(f"北向资金获取失败: {e}")
    return None

def get_market_fund_flow():
    """获取市场整体资金流向（超大单/大单/中单/小单）"""
    url = "https://push2test.eastmoney.com/api/qt/stock/fflow/daykline/get"
    params = {
        "lmt": "1",
        "klt": "101",
        "secid": "1.000001",
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
    }
    try:
        r = requests.get(url, params=params, headers={"Referer": "https://data.eastmoney.com/"}, timeout=8)
        j = r.json()
        klines = j.get("data", {}).get("klines", [])
        if klines:
            parts = klines[-1].split(",")
            return {
                "main_net": float(parts[1]) / 1e8,  # 主力净流入
                "small_net": float(parts[2]) / 1e8,  # 小单净流入（散户）
                "medium_net": float(parts[3]) / 1e8,  # 中单净流入（游资）
                "large_net": float(parts[4]) / 1e8,  # 大单净流入
                "super_net": float(parts[5]) / 1e8,  # 超大单净流入（机构）
            }
    except Exception as e:
        print(f"市场资金流向获取失败: {e}")
    return None

def generate_daily_summary():
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    
    indices = market_data.get_index_data()
    limit_stats = market_data.get_limit_stats()
    sector_inflow = stock_data.fetch_all_sectors_fund()
    
    sorted_sectors = sorted(sector_inflow.items(), key=lambda x: x[1], reverse=True)
    top_inflow = sorted_sectors[:5]
    top_outflow = sorted_sectors[-5:][::-1]
    
    up_ratio = limit_stats['up_count'] / limit_stats['total'] if limit_stats['total'] > 0 else 0
    if up_ratio > 0.6:
        mood = "🔥 情绪高涨"
    elif up_ratio > 0.5:
        mood = "😊 情绪偏暖"
    elif up_ratio > 0.35:
        mood = "😐 情绪中性"
    elif up_ratio > 0.25:
        mood = "😔 情绪偏弱"
    else:
        mood = "🥶 情绪低迷"
    
    text = f"""📊 每日收盘总结（{today_str}）

【大盘指数】
"""
    for idx in indices:
        emoji = "🟢" if idx['change_pct'] >= 0 else "🔴"
        text += f"{emoji} {idx['name']}：{idx['price']:.2f} ({idx['change_pct']:+.2f}%)\n"
    
    text += f"""
【涨跌家数】
📈 上涨：{limit_stats['up_count']} 家
📉 下跌：{limit_stats['down_count']} 家
📊 市场情绪：{mood}

【涨跌停】
🚀 涨停：{limit_stats['limit_up']} 家
💥 跌停：{limit_stats['limit_down']} 家

【板块资金流入 TOP5】
"""
    for i, (name, val) in enumerate(top_inflow, 1):
        text += f"{i}. {name}：+{val:.2f}亿\n"
    
    text += f"""
【板块资金流出 TOP5】
"""
    for i, (name, val) in enumerate(top_outflow, 1):
        text += f"{i}. {name}：{val:.2f}亿\n"
    
    # 北向资金
    north = get_north_fund_flow()
    if north is not None:
        emoji = "🟢" if north >= 0 else "🔴"
        text += f"""
【北向资金】
{emoji} 今日净流入：{north:+.2f}亿
"""
    
    # 市场资金分类
    fund = get_market_fund_flow()
    if fund:
        text += f"""
【市场资金分类】
🏦 机构（超大单+大单）：{(fund['super_net'] + fund['large_net']):+.2f}亿
🎯 游资（中单）：{fund['medium_net']:+.2f}亿
👥 散户（小单）：{fund['small_net']:+.2f}亿
"""
    
    return text

def send_to_feishu(text):
    data = {
        "msg_type": "text",
        "content": {
            "text": text
        }
    }
    r = requests.post(WEBHOOK_URL, json=data, timeout=10)
    print(f"推送状态: {r.status_code}")
    print(f"返回: {r.text}")

if __name__ == "__main__":
    now = datetime.now()
    if not stock_data.is_trading_day(now):
        print(f"[{now.strftime('%Y-%m-%d')}] 非交易日，跳过推送")
        exit(0)
    
    summary = generate_daily_summary()
    print(summary)
    print()
    send_to_feishu(summary)

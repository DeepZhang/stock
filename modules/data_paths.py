# -*- coding: utf-8 -*-
"""数据路径解析：本地开发与线上部署的数据位置不同，这里统一找。

本地（开发）：
    data/history/*.parquet      2704 只（23 板块成分股）
    data/sector_index/*.json    23 个板块 BK 指数
    data/sector_members.json

线上（阿里云 /data/stock）：
    历史行情在 /data/stock-analysis/history/（5478 只，全市场）
    板块指数与成分表也在 /data/stock-analysis/ 下

本模块只做**只读**路径查找，不创建、不写入、不删除任何数据文件。
找不到时返回 None，由调用方决定降级行为。
"""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 候选根目录：本地 data/ 优先，之后是线上共享数据目录
_DATA_ROOTS = [
    os.path.join(BASE_DIR, "data"),
    "/data/stock-analysis",
    "/data/stock/data",
]


def _first_existing(*candidates):
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return None


def history_dir():
    """返回含 *.parquet 个股日线的目录。"""
    return _first_existing(*[os.path.join(r, "history") for r in _DATA_ROOTS])


def index_dir():
    """返回含板块 BK 指数 json 的目录。"""
    return _first_existing(
        os.path.join(BASE_DIR, "data", "sector_index"),
        "/data/stock-analysis/sector_index",
        os.path.join(BASE_DIR, "stock-analysis", "sector_index"),
    )


def members_file():
    """返回板块成分表 json（本地 data/ 与仓库内 stock-analysis/ 同源）。"""
    return _first_existing(
        os.path.join(BASE_DIR, "data", "sector_members.json"),
        "/data/stock-analysis/sector_members.json",
        os.path.join(BASE_DIR, "stock-analysis", "sector_members.json"),
    )


def cache_dir():
    """可写缓存目录（用于落盘中间结果），不存在则创建。"""
    p = os.path.join(BASE_DIR, "data", "panel_cache")
    try:
        os.makedirs(p, exist_ok=True)
        return p
    except OSError:
        return None

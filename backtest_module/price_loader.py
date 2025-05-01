# price_loader.py
"""
集中管理 ETH 價格下載與快取
"""
import os, json, time, requests, pandas as pd
from functools import lru_cache
from datetime import date, timedelta

# ──────────────── 設定區 ────────────────
CACHE_DIR = ".price_cache"
TTL_SECONDS = 12 * 60 * 60  # 超過 12 小時才重新抓
API_URL = "https://api.coingecko.com/api/v3/coins/ethereum/market_chart"
# ───────────────────────────────────────

os.makedirs(CACHE_DIR, exist_ok=True)


def _cache_path(key: str) -> str:
    return f"{CACHE_DIR}/{key}.parquet"


def _is_cache_fresh(meta_path: str) -> bool:
    if not os.path.exists(meta_path):
        return False
    with open(meta_path, "r") as fp:
        meta = json.load(fp)
    return (time.time() - meta["timestamp"]) < TTL_SECONDS


@lru_cache(maxsize=32)  # in-memory
def load_eth_prices(total_days: int) -> pd.DataFrame:
    """
    下載「today-total_days + 1」的日線價格。
    先檢查本機快取，若檔案過舊才重新 call API。
    """
    key = f"eth_{total_days}d"
    data_path = _cache_path(key)
    meta_path = _cache_path(key + "_meta")

    # ---------- 1) 讀本機快取 ----------
    if os.path.exists(data_path) and _is_cache_fresh(meta_path):
        return pd.read_parquet(data_path)

    # ---------- 2) call API ----------
    resp = requests.get(
        API_URL,
        params={"vs_currency": "usd", "days": total_days, "interval": "daily"},
        timeout=10,
    )
    resp.raise_for_status()

    df = (
        pd.DataFrame(resp.json()["prices"], columns=["ts_ms", "price_usd"])
        .assign(date=lambda d: pd.to_datetime(d.ts_ms, unit="ms").dt.date)
        .loc[:, ["date", "price_usd"]]
        .drop_duplicates("date")
        .reset_index(drop=True)
    )

    # ---------- 3) 寫快取 ----------
    df.to_parquet(data_path, index=False)
    with open(meta_path, "w") as fp:
        json.dump({"timestamp": time.time()}, fp)

    return df


if __name__ == "__main__":
    df = load_eth_prices(30)
    print(df.head())

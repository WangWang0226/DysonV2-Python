# price_loader.py
import requests
import pandas as pd


def load_eth_prices(days: int = 30) -> pd.DataFrame:
    """
    回傳日線 DataFrame: [date, price_usd]
    """
    url = "https://api.coingecko.com/api/v3/coins/ethereum/market_chart"
    r = requests.get(
        url,
        params={"vs_currency": "usd", "days": days, "interval": "daily"},
        timeout=10,
    )
    r.raise_for_status()

    df = (
        pd.DataFrame(r.json()["prices"], columns=["ts_ms", "price_usd"])
        .assign(date=lambda d: pd.to_datetime(d.ts_ms, unit="ms").dt.date)
        .loc[:, ["date", "price_usd"]]
    )
    return df

if __name__ == "__main__":
    df = load_eth_prices(30)
    print(df.head())
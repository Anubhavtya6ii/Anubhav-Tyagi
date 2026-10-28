"""Download daily OHLC history from Yahoo Finance into data/*.csv."""
import os
from datetime import date

import pandas as pd
import yfinance as yf

SYMBOLS = {
    "NIFTY50": "^NSEI",
    "SENSEX": "^BSESN",
    "BANKNIFTY": "^NSEBANK",
    "BITCOIN": "BTC-USD",
    "ETHEREUM": "ETH-USD",
    "USDINR": "USDINR=X",
}


def main():
    os.makedirs("data", exist_ok=True)
    today = pd.Timestamp(date.today())
    for name, sym in SYMBOLS.items():
        d = yf.download(sym, period="max", interval="1d", auto_adjust=False, progress=False)
        if isinstance(d.columns, pd.MultiIndex):
            d.columns = d.columns.get_level_values(0)
        d = d[["Open", "High", "Low", "Close"]].dropna(subset=["Close"])
        d.index = pd.to_datetime(d.index).tz_localize(None)
        d = d[(d["Close"] > 0) & (d.index < today)]  # drop today's unfinished bar
        d.to_csv(f"data/{name}.csv", index_label="Date")
        print(f"{name:10s} {sym:10s} {len(d):6d} rows  {d.index.min().date()} -> {d.index.max().date()}")


if __name__ == "__main__":
    main()

"""Verify every downloaded NIFTY dataset and write the inventory: rows, coverage, missing candles, sanity checks."""
import glob
import os

import numpy as np
import pandas as pd

SESSION_MINUTES = 375  # 09:15 - 15:29 one-minute candles on a normal NSE day


def trading_days():
    """NSE trading days from the bhavcopy archive already in the repo (2001+)."""
    d = pd.read_csv("../data/nifty_daily_candles_oi.csv", usecols=["Date"], parse_dates=["Date"]).Date
    return pd.DatetimeIndex(sorted(set(d)))


def check(path, nse_days):
    kind = path.split("/")[1]
    sid, interval = os.path.basename(path).replace(".csv.gz", "").split("_")
    try:
        f = pd.read_csv(path, parse_dates=["time_ist"])
    except pd.errors.EmptyDataError:
        f = pd.DataFrame()
    row = {"dataset": kind, "security_id": int(sid), "interval": interval, "rows": len(f)}
    if f.empty:
        return row
    day = f.time_ist.dt.normalize()
    first, last = day.min(), day.max()
    expected = nse_days[(nse_days >= first) & (nse_days <= last)]
    have = set(day.unique())
    missing_days = [d for d in expected if d not in have]
    bad_ohlc = ((f.low > f[["open", "close"]].min(axis=1) + 1e-9) | (f.high < f[["open", "close"]].max(axis=1) - 1e-9)).sum()
    row.update({
        "first": f.time_ist.min(), "last": f.time_ist.max(), "trading_days_with_data": len(have),
        "nse_trading_days_in_range": len(expected), "missing_trading_days": len(missing_days),
        "duplicate_timestamps": int(f.timestamp.duplicated().sum()), "bad_ohlc_rows": int(bad_ohlc),
        "non_positive_prices": int((f[["open", "high", "low", "close"]] <= 0).any(axis=1).sum()),
        "has_oi": "open_interest" in f, "zero_volume_rows": int((f.volume == 0).sum()) if "volume" in f else None,
    })
    if interval == "1":
        per_day = f.groupby(day).size()
        row["avg_candles_per_day"] = round(per_day.mean(), 1)
        row["full_days (375 candles)"] = int((per_day >= SESSION_MINUTES).sum())
        row["missing_1min_candles"] = int((SESSION_MINUTES - per_day.clip(upper=SESSION_MINUTES)).sum())
    return row


def cross_checks():
    out = []
    dd = pd.read_csv("data/index/13_D.csv.gz", parse_dates=["time_ist"])
    dd["date"] = dd.time_ist.dt.normalize()
    ref = pd.read_csv("../data/nifty_daily_candles_oi.csv", parse_dates=["Date"])
    ref = ref[ref["Candle source"] == "Nifty spot"].set_index("Date").Close
    j = dd.set_index("date").close.to_frame("dhan").join(ref.rename("yahoo"), how="inner")
    diff = (j.dhan / j.yahoo - 1).abs()
    out.append({"check": "Index daily close: Dhan vs Yahoo", "days compared": len(j), "median abs diff %": diff.median() * 100,
                "days off by >0.1%": int((diff > 0.001).sum())})
    m1 = pd.read_csv("data/index/13_1.csv.gz", parse_dates=["time_ist"]).set_index("time_ist")
    m5 = pd.read_csv("data/index/13_5.csv.gz", parse_dates=["time_ist"]).set_index("time_ist")
    agg = m1.resample("5min", origin="start_day", offset="15min").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    k = agg.join(m5[["open", "high", "low", "close"]], rsuffix="_api", how="inner")
    mism = (k[["open", "high", "low", "close"]].to_numpy() != k[["open_api", "high_api", "low_api", "close_api"]].to_numpy()).any(axis=1)
    out.append({"check": "Index 5-min from API vs built from 1-min", "days compared": k.index.normalize().nunique(),
                "median abs diff %": float(np.nan), "days off by >0.1%": int(mism.sum()), "note": f"{mism.mean():.2%} of {len(k)} candles differ"})
    last_daily = dd.groupby("date").close.last()
    last_1m = m1.groupby(m1.index.normalize()).close.last()
    z = pd.concat([last_daily.rename("daily"), last_1m.rename("1min")], axis=1, join="inner")
    zd = (z["1min"] / z.daily - 1).abs()
    out.append({"check": "Index daily close vs last 1-min close", "days compared": len(z), "median abs diff %": zd.median() * 100,
                "days off by >0.1%": int((zd > 0.001).sum())})
    return pd.DataFrame(out)


def main():
    nse = trading_days()
    rows = [check(p, nse) for p in sorted(glob.glob("data/*/*.csv.gz"))]
    inv = pd.DataFrame(rows)
    inst = pd.read_csv("raw/nifty_instruments.csv")[["SECURITY_ID", "DISPLAY_NAME", "INSTRUMENT", "expiry", "STRIKE_PRICE", "OPTION_TYPE", "EXPIRY_FLAG"]]
    inv = inv.merge(inst, left_on="security_id", right_on="SECURITY_ID", how="left").drop(columns="SECURITY_ID")
    inv.to_csv("inventory_datasets.csv", index=False)
    cc = cross_checks()
    cc.to_csv("inventory_cross_checks.csv", index=False)
    return inv, cc


if __name__ == "__main__":
    pd.set_option("display.width", 250, "display.max_columns", 30)
    inv, cc = main()
    print(inv[inv.dataset != "options"].drop(columns=["STRIKE_PRICE", "OPTION_TYPE"]).to_string(index=False))
    print(cc.to_string(index=False))

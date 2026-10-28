"""Download NSE F&O bhavcopies and keep a compact slice of NIFTY weekly options.

Saves data/nifty_options.csv.gz (nearest 2 expiries, strikes within +-15% of spot)
and data/INDIAVIX.csv. Re-running only downloads days that are missing.
"""
import io
import os
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests
import yfinance as yf

START = pd.Timestamp("2019-02-01")  # Nifty weekly options launched Feb 2019
NEW_FORMAT_FROM = pd.Timestamp("2024-07-08")  # NSE switched to the UDiFF bhavcopy
CACHE = "data/nifty_opt_days"
OUT = "data/nifty_options.csv.gz"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
BASE = "https://nsearchives.nseindia.com/content"


def urls(day):
    mon = f"{day:%b}".upper()
    old = f"{BASE}/historical/DERIVATIVES/{day:%Y}/{mon}/fo{day:%d}{mon}{day:%Y}bhav.csv.zip"
    new = f"{BASE}/fo/BhavCopy_NSE_FO_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"
    return [new, old] if day >= NEW_FORMAT_FROM else [old, new]


def parse(raw, spot):
    z = zipfile.ZipFile(io.BytesIO(raw))
    d = pd.read_csv(z.open(z.namelist()[0]))
    if "TckrSymb" in d:  # new format
        d = d[(d.TckrSymb == "NIFTY") & (d.FinInstrmTp == "IDO")]
        d = pd.DataFrame({
            "date": pd.to_datetime(d.TradDt), "expiry": pd.to_datetime(d.XpryDt),
            "strike": d.StrkPric, "type": d.OptnTp, "close": d.ClsPric, "settle": d.SttlmPric,
            "volume": d.TtlTradgVol, "oi": d.OpnIntrst, "lot": d.NewBrdLotQty,
        })
    else:  # old format
        d = d[(d.SYMBOL == "NIFTY") & (d.INSTRUMENT == "OPTIDX")]
        d = pd.DataFrame({
            "date": pd.to_datetime(d.TIMESTAMP, format="%d-%b-%Y"),
            "expiry": pd.to_datetime(d.EXPIRY_DT, format="%d-%b-%Y"),
            "strike": d.STRIKE_PR, "type": d.OPTION_TYP, "close": d.CLOSE, "settle": d.SETTLE_PR,
            "volume": d.CONTRACTS, "oi": d.OPEN_INT, "lot": pd.NA,
        })
    near = sorted(e for e in d.expiry.unique() if e >= d.date.iloc[0])[:2]
    d = d[d.expiry.isin(near) & (d.strike.between(spot * 0.85, spot * 1.15))]
    d["spot"] = spot
    return d


def fetch_day(day, spot):
    path = f"{CACHE}/{day:%Y%m%d}.csv"
    if os.path.exists(path) or os.path.exists(path + ".missing"):
        return "cached"
    for url in urls(day):
        for attempt in range(4):
            try:
                r = requests.get(url, headers=UA, timeout=30)
            except requests.RequestException:
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 200 and r.content[:2] == b"PK":
                parse(r.content, spot).to_csv(path, index=False)
                return "ok"
            if r.status_code == 404:
                break
            time.sleep(2 ** attempt)
    open(path + ".missing", "w").close()
    return "missing"


def main():
    os.makedirs(CACHE, exist_ok=True)
    spot = pd.read_csv("data/NIFTY50.csv", index_col="Date", parse_dates=True)["Close"]
    days = spot[spot.index >= START]
    with ThreadPoolExecutor(max_workers=4) as ex:
        status = list(ex.map(lambda kv: fetch_day(*kv), days.items()))
    print({s: status.count(s) for s in set(status)})

    parts = [pd.read_csv(f"{CACHE}/{f}", parse_dates=["date", "expiry"])
             for f in sorted(os.listdir(CACHE)) if f.endswith(".csv")]
    pd.concat(parts).to_csv(OUT, index=False)
    print("saved", OUT, sum(len(p) for p in parts), "rows")

    vix = yf.download("^INDIAVIX", period="max", interval="1d", auto_adjust=False, progress=False)
    if isinstance(vix.columns, pd.MultiIndex):
        vix.columns = vix.columns.get_level_values(0)
    vix[["Close"]].rename(columns={"Close": "VIX"}).to_csv("data/INDIAVIX.csv", index_label="Date")


if __name__ == "__main__":
    main()

"""Download the FULL Nifty option chain (every expiry, every strike) and Nifty futures, every day since June 2001.

Source: NSE F&O bhavcopies. Per-day files are cached in data/nifty_chain_days/ and combined into one
gzip CSV per year in data/nifty_chain/. Re-running only downloads days that are missing.
"""
import io
import os
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

from fetch_nse_options import UA, urls

START = pd.Timestamp("2001-06-01")  # Nifty index options began trading in June 2001
CACHE = "data/nifty_chain_days"
OUT_DIR = "data/nifty_chain"
COLS = ["date", "instrument", "expiry", "strike", "type", "open", "high", "low", "close", "settle",
        "contracts", "oi", "chg_oi", "underlying"]


def to_date(s):
    """Old bhavcopies use 14-MAY-2012, but a few use a two-digit year (14-May-12)."""
    s = s.astype(str).str.strip()
    return pd.to_datetime(s, format="%d-%b-%Y", errors="coerce").fillna(pd.to_datetime(s, format="%d-%b-%y", errors="coerce"))


def parse(raw):
    z = zipfile.ZipFile(io.BytesIO(raw))
    d = pd.read_csv(z.open(z.namelist()[0]), on_bad_lines="skip")  # a few NSE files have broken lines
    d.columns = d.columns.str.strip()
    d = d.rename(columns={"OPTIONTYPE": "OPTION_TYP"})  # some years (e.g. 2005) use this header
    if "TckrSymb" in d:  # UDiFF format (from July 2024)
        d = d[(d.TckrSymb == "NIFTY") & d.FinInstrmTp.isin(["IDO", "IDF"])]
        return pd.DataFrame({
            "date": pd.to_datetime(d.TradDt), "instrument": d.FinInstrmTp.map({"IDO": "OPT", "IDF": "FUT"}),
            "expiry": pd.to_datetime(d.XpryDt), "strike": d.StrkPric, "type": d.OptnTp.fillna("FUT"),
            "open": d.OpnPric, "high": d.HghPric, "low": d.LwPric, "close": d.ClsPric, "settle": d.SttlmPric,
            "contracts": d.TtlTradgVol, "oi": d.OpnIntrst, "chg_oi": d.ChngInOpnIntrst, "underlying": d.UndrlygPric,
        })[COLS]
    d = d[(d.SYMBOL == "NIFTY") & d.INSTRUMENT.isin(["OPTIDX", "FUTIDX"])]
    return pd.DataFrame({
        "date": to_date(d.TIMESTAMP), "instrument": d.INSTRUMENT.map({"OPTIDX": "OPT", "FUTIDX": "FUT"}),
        "expiry": to_date(d.EXPIRY_DT), "strike": d.STRIKE_PR,
        "type": d.OPTION_TYP.where(d.INSTRUMENT == "OPTIDX", "FUT"),
        "open": d.OPEN, "high": d.HIGH, "low": d.LOW, "close": d.CLOSE, "settle": d.SETTLE_PR,
        "contracts": d.CONTRACTS, "oi": d.OPEN_INT, "chg_oi": d.CHG_IN_OI, "underlying": pd.NA,
    })[COLS]


def fetch_day(day):
    path = f"{CACHE}/{day:%Y%m%d}.csv"
    if any(os.path.exists(path + x) for x in ("", ".missing", ".bad")):
        return "cached"
    for url in urls(day):
        for attempt in range(4):
            try:
                r = requests.get(url, headers=UA, timeout=30)
            except requests.RequestException:
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 200 and r.content[:2] == b"PK":
                try:
                    parse(r.content).to_csv(path, index=False)
                except Exception as e:  # keep going; the day is listed as bad
                    with open(path + ".bad", "w") as f:
                        f.write(repr(e))
                    return "bad"
                return "ok"
            if r.status_code == 404:
                break
            time.sleep(2 ** attempt)
    open(path + ".missing", "w").close()  # weekend-free calendar, so most of these are market holidays
    return "missing"


def main():
    os.makedirs(CACHE, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    days = pd.bdate_range(START, pd.Timestamp.today().normalize() - pd.Timedelta(days=1))
    with ThreadPoolExecutor(max_workers=4) as ex:
        status = list(ex.map(fetch_day, days))
    print({s: status.count(s) for s in set(status)})
    files = sorted(f for f in os.listdir(CACHE) if f.endswith(".csv"))
    for year in sorted({f[:4] for f in files}):
        parts = [pd.read_csv(f"{CACHE}/{f}") for f in files if f.startswith(year) and os.path.getsize(f"{CACHE}/{f}") > 100]
        if parts:
            pd.concat(parts).to_csv(f"{OUT_DIR}/{year}.csv.gz", index=False)
            print(year, len(parts), "days", sum(len(p) for p in parts), "rows")


if __name__ == "__main__":
    main()

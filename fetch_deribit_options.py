"""Daily snapshot of Bitcoin / Ethereum option prices from Deribit's public trade history.

For every day we take all option trades between 08:00 and 10:00 UTC (Deribit expires options at 08:00)
and keep the last mark price, implied vol and index price of each instrument.
Saves data/deribit_options.csv.gz. Re-running only downloads days that are missing.
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

START = pd.Timestamp("2019-01-01")
CURRENCIES = ["BTC", "ETH"]
CACHE = "data/deribit_days"
OUT = "data/deribit_options.csv.gz"
API = "https://history.deribit.com/api/v2/public/get_last_trades_by_currency"


def get(params):
    for attempt in range(5):
        try:
            r = requests.get(API, params=params, timeout=30)
            if r.status_code == 200:
                return r.json()["result"]
        except (requests.RequestException, ValueError, KeyError):
            pass
        time.sleep(2 ** attempt)
    raise RuntimeError(f"Deribit request failed: {params}")


def fetch_day(cur, day):
    path = f"{CACHE}/{cur}_{day:%Y%m%d}.csv"
    if os.path.exists(path):
        return "cached"
    start = int((day + pd.Timedelta(hours=8)).timestamp() * 1000)
    end = int((day + pd.Timedelta(hours=10)).timestamp() * 1000)
    trades = []
    while True:
        res = get({"currency": cur, "kind": "option", "start_timestamp": start, "end_timestamp": end,
                   "count": 1000, "sorting": "asc"})
        trades += res["trades"]
        if not res.get("has_more") or not res["trades"]:
            break
        start = res["trades"][-1]["timestamp"] + 1
    if not trades:
        pd.DataFrame().to_csv(path, index=False)
        return "empty"
    t = pd.DataFrame(trades).sort_values("timestamp")
    g = t.groupby("instrument_name")
    d = pd.DataFrame({"mark": g.mark_price.last(), "iv": g.iv.last(), "index": g.index_price.last(),
                      "trades": g.size(), "amount": g.amount.sum()}).reset_index()
    parts = d.instrument_name.str.split("-", expand=True)
    d["currency"], d["date"] = cur, day
    d["expiry"] = pd.to_datetime(parts[1], format="%d%b%y")
    d["strike"], d["type"] = parts[2].astype(float), parts[3].map({"C": "CE", "P": "PE"})
    d["mark_usd"] = d["mark"] * d["index"]  # Deribit quotes option prices in BTC / ETH
    d.to_csv(path, index=False)
    return "ok"


def main():
    os.makedirs(CACHE, exist_ok=True)
    days = pd.date_range(START, pd.Timestamp.today().normalize() - pd.Timedelta(days=1), freq="D")
    jobs = [(c, d) for c in CURRENCIES for d in days]
    with ThreadPoolExecutor(max_workers=4) as ex:
        status = list(ex.map(lambda j: fetch_day(*j), jobs))
    print({s: status.count(s) for s in set(status)})
    parts = [pd.read_csv(f"{CACHE}/{f}", parse_dates=["date", "expiry"]) for f in sorted(os.listdir(CACHE))
             if os.path.getsize(f"{CACHE}/{f}") > 10]
    pd.concat(parts).to_csv(OUT, index=False)
    print("saved", OUT, sum(len(p) for p in parts), "rows")
    fetch_settlement()



def fetch_settlement():
    """08:00 UTC settlement estimate for every day: mean of the 07:30-08:00 perpetual candle (Deribit settles on a
    30-minute TWAP of the index before 08:00). Saves data/deribit_settlement.csv."""
    url = "https://www.deribit.com/api/v2/public/get_tradingview_chart_data"
    out = {}
    for cur in CURRENCIES:
        rows, t = [], START
        end = pd.Timestamp.today().normalize()
        while t < end:
            t2 = min(t + pd.Timedelta(days=50), end)
            r = requests.get(url, params={"instrument_name": f"{cur}-PERPETUAL", "resolution": "30",
                                          "start_timestamp": int(t.timestamp() * 1000), "end_timestamp": int(t2.timestamp() * 1000)},
                             timeout=30).json()["result"]
            rows.append(pd.DataFrame({"ts": pd.to_datetime(r["ticks"], unit="ms"), "open": r["open"], "close": r["close"]}))
            t = t2
        c = pd.concat(rows).drop_duplicates("ts")
        c = c[(c.ts.dt.hour == 7) & (c.ts.dt.minute == 30)]
        out[cur] = pd.Series(((c.open + c.close) / 2).to_numpy(), index=c.ts.dt.normalize())
    pd.DataFrame(out).to_csv("data/deribit_settlement.csv", index_label="Date")
    print("saved data/deribit_settlement.csv")


if __name__ == "__main__":
    main()

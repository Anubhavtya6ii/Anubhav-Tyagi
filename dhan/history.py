"""Download all historical NIFTY candles Dhan serves: index, every listed future and every listed option.

Raw responses are kept as gzipped JSON (one file per instrument x interval, list of per-call responses);
parsed candles go to CSV.gz next to them. Re-running skips files that already exist.
Layout: raw/history/{index|futures|options}/{security_id}_{interval}.json.gz and data/.../{security_id}_{interval}.csv.gz
"""
import gzip
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from client import Dhan

INTRADAY_START = pd.Timestamp("2015-01-01")  # earlier than Dhan serves, so the true start is discovered
DAILY_START = "2000-01-01"
CHUNK_DAYS = 90  # Dhan limit per intraday call
TODAY = pd.Timestamp.today().normalize()
LOG = "raw/history/download_log.csv"

d = Dhan(per_second=4.0)


def chunks(start):
    t = pd.Timestamp(start).normalize()
    while t <= TODAY:
        t2 = min(t + pd.Timedelta(days=CHUNK_DAYS - 1), TODAY)
        yield f"{t:%Y-%m-%d} 09:00:00", f"{t2:%Y-%m-%d} 15:45:00"
        t = t2 + pd.Timedelta(days=1)


def to_frame(responses):
    parts = []
    for r in responses:
        j = r["response"]
        if isinstance(j, dict) and j.get("timestamp"):
            parts.append(pd.DataFrame({k: v for k, v in j.items() if isinstance(v, list) and len(v) == len(j["timestamp"])}))
    if not parts:
        return pd.DataFrame()
    f = pd.concat(parts).drop_duplicates("timestamp").sort_values("timestamp")
    f.insert(0, "time_ist", pd.to_datetime(f.timestamp, unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None))
    return f


def fetch(kind, sid, seg, ins, interval, start=None):
    """interval: 'D' for daily or 1/5/15/25/60 minutes."""
    raw = f"raw/history/{kind}/{sid}_{interval}.json.gz"
    out = f"data/{kind}/{sid}_{interval}.csv.gz"
    if os.path.exists(raw):
        return None
    os.makedirs(os.path.dirname(raw), exist_ok=True)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    responses = []
    if interval == "D":
        body = {"securityId": str(sid), "exchangeSegment": seg, "instrument": ins, "expiryCode": 0,
                "oi": seg != "IDX_I", "fromDate": DAILY_START, "toDate": f"{TODAY + pd.Timedelta(days=1):%Y-%m-%d}"}
        st, j = d.post("/charts/historical", body)
        responses.append({"request": body, "status": st, "response": j})
    else:
        for fr, to in chunks(start or INTRADAY_START):
            body = {"securityId": str(sid), "exchangeSegment": seg, "instrument": ins, "interval": str(interval),
                    "oi": seg != "IDX_I", "fromDate": fr, "toDate": to}
            st, j = d.post("/charts/intraday", body)
            responses.append({"request": body, "status": st, "response": j})
    with gzip.open(raw, "wt") as fh:
        json.dump(responses, fh)
    f = to_frame(responses)
    f.to_csv(out, index=False)
    errors = sum(1 for r in responses if r["status"] != 200)
    return {"kind": kind, "security_id": sid, "interval": interval, "calls": len(responses), "errors": errors,
            "rows": len(f), "first": f.time_ist.min() if len(f) else None, "last": f.time_ist.max() if len(f) else None}


def log(rows):
    rows = [r for r in rows if r]
    if rows:
        pd.DataFrame(rows).to_csv(LOG, mode="a", header=not os.path.exists(LOG), index=False)


def listing_start(kind, sid):
    """First trading day of a contract, from its daily candles (saves empty intraday calls)."""
    p = f"data/{kind}/{sid}_D.csv.gz"
    try:
        f = pd.read_csv(p)
        return pd.Timestamp(f.time_ist.min()) if len(f) else None
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return None


def main(stage):
    os.makedirs("raw/history", exist_ok=True)
    inst = pd.read_csv("raw/nifty_instruments.csv")
    fut = inst[inst.INSTRUMENT == "FUTIDX"].SECURITY_ID.tolist()
    opt = inst[inst.INSTRUMENT == "OPTIDX"].sort_values(["expiry", "STRIKE_PRICE"]).SECURITY_ID.tolist()
    if stage == "index":
        log([fetch("index", 13, "IDX_I", "INDEX", iv) for iv in ["D", 1, 5, 15, 25, 60]])
    elif stage == "futures":
        log([fetch("futures", s, "NSE_FNO", "FUTIDX", "D") for s in fut])
        log([fetch("futures", s, "NSE_FNO", "FUTIDX", iv, listing_start("futures", s)) for s in fut for iv in [1, 5, 15, 25, 60]])
    elif stage == "options_daily":
        with ThreadPoolExecutor(4) as ex:
            for i in range(0, len(opt), 200):
                log(list(ex.map(lambda s: fetch("options", s, "NSE_FNO", "OPTIDX", "D"), opt[i:i + 200])))
                print(f"options daily {min(i + 200, len(opt))}/{len(opt)}", flush=True)
    elif stage == "options_1min":
        def one(s):
            st = listing_start("options", s)
            return fetch("options", s, "NSE_FNO", "OPTIDX", 1, st) if st is not None else None
        with ThreadPoolExecutor(4) as ex:
            for i in range(0, len(opt), 100):
                log(list(ex.map(one, opt[i:i + 100])))
                print(f"options 1-min {min(i + 100, len(opt))}/{len(opt)}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])

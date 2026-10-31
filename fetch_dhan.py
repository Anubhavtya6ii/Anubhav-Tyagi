"""Download Nifty 5-minute index candles and EXPIRED Nifty options (rolling ATM +/- N strikes) from the DhanHQ API.

Needs the DhanHQ "Data APIs" plan. Credentials come from the environment (never written to disk):
    DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN

    python fetch_dhan.py            # both datasets
    python fetch_dhan.py 5min       # only data/nifty_5min.csv.gz
    python fetch_dhan.py options    # only data/dhan_expired_week1/<year>.csv.gz

Behaviour of the API that this script is built around (all checked against the live API, Oct 2026):
  * /charts/intraday takes at most 90 days per call, and fromDate must be BEFORE 09:15 or the first candle is dropped.
  * /charts/rollingoption is documented as 30 days per call, but in practice only a span of 1 day works
    (fromDate=d, toDate=d+1 returns both days; anything longer is rejected with DH-905). So options are fetched in
    2-day windows, one request per (window, call/put, strike offset).
  * Holidays/weekends/dates before the data starts come back as empty arrays, not errors.
  * The `strike` string must be exactly "ATM", "ATM+3", "ATM-3". A stray space or lower case silently returns ATM,
    so the strings are only ever built by strike_name().
  * Rolling options carry no expiry date; expiry_flag/expiry_code say which contract is tracked
    (WEEK/1 = nearest weekly expiry on that day).
Timestamps are converted to naive IST.
"""
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pandas as pd
import requests

BASE = "https://api.dhan.co/v2"
NIFTY_ID = 13  # Dhan security id of the NIFTY 50 index

# ---- 5-minute index candles
INTRADAY_START = date(2017, 1, 1)  # API returns empty before Jan-Jul 2017
INTRADAY_CHUNK_DAYS = 80           # API limit is 90 days per call
OUT_5MIN = "data/nifty_5min.csv.gz"

# ---- expired options
OPT_START = date(2020, 7, 1)       # data starts between 7 Jul and 4 Aug 2020
EXPIRY_FLAG = "WEEK"               # "WEEK" or "MONTH"
EXPIRY_CODE = 1                    # 1 = nearest expiry, 2 = next, 3 = far
STRIKES = 5                        # fetch ATM-5 .. ATM+5 (API supports up to 10 for near index expiries)
REQUIRED = ["open", "high", "low", "close", "volume", "oi", "iv", "spot", "strike"]
TAG = f"{EXPIRY_FLAG.lower()}{EXPIRY_CODE}"
CACHE = f"data/dhan_expired_{TAG}_days"   # one csv per 2-day window, all strikes (resumable, gitignored)
OUT_DIR = f"data/dhan_expired_{TAG}"      # one csv.gz per year
OPT_COLS = ["datetime", "expiry_flag", "expiry_code", "option_type", "offset", "strike", "spot",
            "open", "high", "low", "close", "volume", "oi", "iv"]

WORKERS = 4
MIN_GAP = 0.3  # seconds between any two requests overall (~3.3/s). The documented limit is 5/s, but a sustained 4.5/s
               # got HTTP 429 / DH-904 after ~35 minutes; throttled windows are simply fetched again on the next run.


class RateLimiter:
    def __init__(self, gap):
        self.gap, self.next, self.lock = gap, 0.0, threading.Lock()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            delay = max(0.0, self.next - now)
            self.next = max(now, self.next) + self.gap
        if delay:
            time.sleep(delay)


LIMITER = RateLimiter(MIN_GAP)
SESSION = requests.Session()


def headers():
    try:
        return {"access-token": os.environ["DHAN_ACCESS_TOKEN"], "client-id": os.environ["DHAN_CLIENT_ID"],
                "Content-Type": "application/json", "Accept": "application/json"}
    except KeyError as e:
        sys.exit(f"Set the {e.args[0]} environment variable first.")


def post(path, body, retries=6):
    """POST with rate limiting and retries. Returns the parsed JSON, or {"errorCode": ...} for a non-retryable error."""
    last = ""
    for attempt in range(retries):
        LIMITER.wait()
        try:
            r = SESSION.post(f"{BASE}{path}", headers=headers(), json=body, timeout=60)
            j = r.json()
        except (requests.RequestException, ValueError) as e:
            last = type(e).__name__
            time.sleep(min(2 ** attempt, 30))
            continue
        code = j.get("errorCode") if isinstance(j, dict) else None
        if code in ("DH-901", "DH-902", "DH-903"):  # bad/expired token, no Data plan, no permission: retrying is pointless
            sys.exit(f"Dhan refused the request: {code} {j.get('errorMessage')}")
        if r.status_code == 429 or code == "DH-904" or r.status_code >= 500:  # rate limit / server trouble
            last = f"HTTP {r.status_code} {code or ''}".strip()
            time.sleep(min(2 ** attempt, 30))
            continue
        return j
    return {"errorCode": "RETRIES_EXHAUSTED", "errorMessage": f"{path} failed {retries} times, last cause: {last}"}


def to_ist(epoch):
    return pd.to_datetime(epoch, unit="s", utc=True).tz_convert("Asia/Kolkata").tz_localize(None)


def strike_name(offset):
    return "ATM" if offset == 0 else f"ATM{offset:+d}"  # exactly "ATM", "ATM+3", "ATM-3"


# ----------------------------------------------------------------------------- 5-minute index candles
def fetch_daily():
    """Daily Nifty candles from Dhan: the list of real trading days (incl. Budget Saturdays and Diwali Muhurat sessions)."""
    j = post("/charts/historical", {"securityId": str(NIFTY_ID), "exchangeSegment": "IDX_I", "instrument": "INDEX",
                                    "expiryCode": 0, "fromDate": str(INTRADAY_START), "toDate": str(date.today())})
    if "errorCode" in j:
        sys.exit(f"daily candles: {j['errorCode']} {j.get('errorMessage')}")
    d = pd.DataFrame({"date": to_ist(j["timestamp"]).normalize(), **{k: j[k] for k in ("open", "high", "low", "close")}})
    return d.set_index("date")


def clean_5min(d, cal):
    """Remove what the raw feed contains besides real index candles (all of it measured on the Oct 2026 download):
    weekend mock sessions (not in the daily calendar), Aug-Sep 2021 flat zero-volume bars stamped 05:37-22:00,
    and one-off bars after 15:30. A stray 09:16 print is snapped onto its 5-min bar. Diwali Muhurat evening sessions stay."""
    d = d[d.datetime.dt.normalize().isin(cal.index)]
    t, day = d.datetime.dt.strftime("%H:%M"), d.datetime.dt.normalize()
    in_session = (t >= "09:15") & (t <= "15:30")
    d = d[in_session | ~day.isin(day[in_session])]  # days with no daytime bar at all are evening Muhurat sessions
    return d.assign(datetime=d.datetime.dt.floor("5min")).drop_duplicates("datetime")


def qa_5min(d, cal):
    """Print what is still wrong in the data, so it is known rather than silently trusted."""
    day = d.datetime.dt.normalize()
    n = d.groupby(day).size()
    missing = sorted(set(cal.index[cal.index >= d.datetime.min().normalize()]) - set(n.index))
    short = n[(n < 75) & (n.index.dayofweek < 5)]
    print(f"candles per day: {n.value_counts().sort_index().to_dict()}  (75 = 09:15-15:25, 76 = plus a 15:30 closing print)")
    print(f"trading days with no 5-min data at all ({len(missing)}): {[str(x.date()) for x in missing]}")
    print(f"weekday sessions with fewer than 75 bars ({len(short)}): {dict((str(k.date()), int(v)) for k, v in short.items())}")
    r = d[(d.datetime.dt.strftime("%H:%M") >= "09:15") & (d.datetime.dt.strftime("%H:%M") <= "15:30")]
    a = r.groupby(r.datetime.dt.normalize()).agg(h=("high", "max"), l=("low", "min")).join(cal, how="inner")
    off = a[((a.h - a.high).abs() / a.high > 0.002) | ((a.l - a.low).abs() / a.low > 0.002)]
    print(f"days where the 5-min high/low differs from the daily candle by >0.2% ({len(off)}): {[str(x.date()) for x in off.index]}")


def fetch_5min():
    parts, start, end = [], INTRADAY_START, date.today() - timedelta(days=1)
    while start <= end:
        stop = min(start + timedelta(days=INTRADAY_CHUNK_DAYS - 1), end)
        j = post("/charts/intraday", {
            "securityId": str(NIFTY_ID), "exchangeSegment": "IDX_I", "instrument": "INDEX", "interval": "5",
            "fromDate": f"{start} 09:00:00", "toDate": f"{stop} 16:00:00"})  # 09:00, not 09:15, or candle 1 is lost
        if "errorCode" in j:
            sys.exit(f"intraday {start}..{stop}: {j['errorCode']} {j.get('errorMessage')}")
        if j.get("timestamp"):
            parts.append(pd.DataFrame({"datetime": to_ist(j["timestamp"]), **{k: j[k] for k in ("open", "high", "low", "close", "volume")}}))
        print(f"5min {start} -> {stop}: {len(j.get('timestamp', []))} candles", flush=True)
        start = stop + timedelta(days=1)
    raw = pd.concat(parts).sort_values("datetime", kind="stable")
    cal = fetch_daily()
    d = clean_5min(raw, cal).reset_index(drop=True)
    os.makedirs("data", exist_ok=True)
    d.to_csv(OUT_5MIN, index=False)
    print(f"\n{OUT_5MIN}: {len(d)} candles kept of {len(raw)} downloaded, {d.datetime.dt.normalize().nunique()} days, "
          f"{d.datetime.min()} -> {d.datetime.max()}")
    qa_5min(d, cal)


# ----------------------------------------------------------------------------- expired options
def fetch_series(offset, option_type, d0, d1):
    """One rolling series (e.g. CALL ATM+2) for the window d0..d1 (d1 <= d0 + 1 day). Returns a DataFrame or an error string."""
    j = post("/charts/rollingoption", {
        "exchangeSegment": "NSE_FNO", "interval": "5", "securityId": NIFTY_ID, "instrument": "OPTIDX",
        "expiryFlag": EXPIRY_FLAG, "expiryCode": EXPIRY_CODE, "strike": strike_name(offset),
        "drvOptionType": option_type, "requiredData": REQUIRED, "fromDate": str(d0), "toDate": str(d1)})
    if "errorCode" in j:
        return f"{j['errorCode']} {j.get('errorMessage')}"
    side = (j.get("data") or {}).get("ce" if option_type == "CALL" else "pe") or {}
    if not side.get("timestamp"):
        return pd.DataFrame(columns=OPT_COLS)
    d = pd.DataFrame({"datetime": to_ist(side["timestamp"]), "expiry_flag": EXPIRY_FLAG, "expiry_code": EXPIRY_CODE,
                      "option_type": "CE" if option_type == "CALL" else "PE", "offset": offset})
    for k in ("strike", "spot", "open", "high", "low", "close", "volume", "oi", "iv"):
        d[k] = side[k]
    return d


def fetch_window(d0):
    path = f"{CACHE}/{d0:%Y%m%d}.csv"
    if any(os.path.exists(path + x) for x in ("", ".empty", ".bad")):
        return "cached"
    d1 = d0 + timedelta(days=1)
    # ATM call first: if it is empty the window is a holiday/weekend/pre-history and the other series would be empty too
    first = fetch_series(0, "CALL", d0, d1)
    if isinstance(first, str):
        if first.startswith("RETRIES_EXHAUSTED"):  # transient: leave the window uncached so the next run retries it
            print(f"  {d0} ATM CALL: {first}", flush=True)
            return "retry"
        open(path + ".bad", "w").write(first)  # a real API rejection: do not ask again
        return "bad"
    if first.empty:
        open(path + ".empty", "w").close()
        return "empty"
    parts = [first]
    for opt in ("CALL", "PUT"):
        for off in range(-STRIKES, STRIKES + 1):
            if opt == "CALL" and off == 0:
                continue
            r = fetch_series(off, opt, d0, d1)
            if isinstance(r, str):  # keep the window uncached so the next run retries it
                print(f"  {d0} {opt} {strike_name(off)}: {r}", flush=True)
                return "retry"
            parts.append(r)
    pd.concat(parts).to_csv(path + ".tmp", index=False)
    os.replace(path + ".tmp", path)  # atomic: a half-written window is never mistaken for a finished one
    return "ok"


def fetch_options():
    os.makedirs(CACHE, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    end = date.today() - timedelta(days=1)
    starts = [OPT_START + timedelta(days=i) for i in range(0, (end - OPT_START).days + 1, 2)]
    starts = [s for s in starts if not all((s + timedelta(days=k)).weekday() >= 5 for k in (0, 1))]  # skip Sat+Sun pairs
    print(f"{len(starts)} windows, up to {len(starts) * (2 * STRIKES + 1) * 2} requests (cached ones are skipped)", flush=True)
    status, t0 = [], time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for i, s in enumerate(ex.map(fetch_window, starts), 1):
            status.append(s)
            if i % 25 == 0 or i == len(starts):
                print(f"{i}/{len(starts)} windows  {time.time() - t0:5.0f}s  {dict((k, status.count(k)) for k in set(status))}", flush=True)
    files = sorted(f for f in os.listdir(CACHE) if f.endswith(".csv"))
    if not files:
        sys.exit("No option data downloaded.")
    d = pd.concat(pd.read_csv(f"{CACHE}/{f}", parse_dates=["datetime"]) for f in files)
    d = d.drop_duplicates(["datetime", "option_type", "offset"]).sort_values(["datetime", "option_type", "offset"])
    # A strike more than one step from where spot and offset put it is a Dhan glitch (seen once: 2025-10-16 15:25, ATM call at
    # 26300 with Nifty at 25566). One step of slack is normal: CE and PE are sampled at slightly different instants, so near a
    # strike midpoint the two sides can sit on neighbouring strikes.
    glitch = (d.strike - (50 * (d.spot / 50).round() + 50 * d.offset)).abs() > 50
    if glitch.any():
        print(f"dropping {int(glitch.sum())} rows whose strike is inconsistent with spot: {sorted(set(d[glitch].datetime.astype(str)))}")
        d = d[~glitch]
    for year, g in d.groupby(d.datetime.dt.year):
        g.to_csv(f"{OUT_DIR}/{year}.csv.gz", index=False)
        print(year, len(g), "rows")
    retry, bad = status.count("retry"), status.count("bad")
    if retry:
        print(f"\n{retry} windows hit temporary errors and are NOT in the output yet: run the script again to fetch them.")
    if bad:
        print(f"{bad} windows were rejected by the API (reason in the *.bad files in {CACHE}); they are skipped on later runs.")


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what not in ("all", "5min", "options"):
        sys.exit(__doc__)
    if what in ("all", "5min"):
        fetch_5min()
    if what in ("all", "options"):
        fetch_options()

"""Black-Scholes pricing for NIFTY options plus a daily at-the-money implied-vol table built from real NSE closing prices.

IV for a trading day is taken from the PREVIOUS day's real closes of the same expiry, so nothing in a day's model
price uses information from that day. Intraday premium = BS(spot at that minute, strike, time left, that IV).
"""
import glob

import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.stats import norm

R, Q = 0.065, 0.013  # risk-free rate and dividend yield (small effect for weekly contracts)
STRIKE_STEP = 50


def bs(spot, k, t, iv, is_call):
    """Vectorised Black-Scholes price. t in years (>0), iv in decimals."""
    spot, k, t, iv = np.broadcast_arrays(np.asarray(spot, float), np.asarray(k, float), np.asarray(t, float), np.asarray(iv, float))
    t = np.maximum(t, 1e-9)
    f = spot * np.exp((R - Q) * t)
    sd = iv * np.sqrt(t)
    d1 = (np.log(f / k) + 0.5 * sd ** 2) / sd
    d2 = d1 - sd
    df = np.exp(-R * t)
    call = df * (f * norm.cdf(d1) - k * norm.cdf(d2))
    put = df * (k * norm.cdf(-d2) - f * norm.cdf(-d1))
    return np.where(is_call, call, put)


def delta(spot, k, t, iv, is_call):
    t = np.maximum(np.asarray(t, float), 1e-9)
    f = np.asarray(spot, float) * np.exp((R - Q) * t)
    d1 = (np.log(f / k) + 0.5 * (iv ** 2) * t) / (iv * np.sqrt(t))
    nd = norm.cdf(d1) * np.exp(-Q * t)
    return np.where(is_call, nd, nd - np.exp(-Q * t))


def implied_vol(price, spot, k, t, is_call):
    intrinsic = max((spot - k) if is_call else (k - spot), 0.0)
    if not (price > intrinsic + 0.05 and t > 0):
        return np.nan
    try:
        return brentq(lambda v: float(bs(spot, k, t, v, is_call)) - price, 0.02, 3.0, xtol=1e-5)
    except ValueError:
        return np.nan


# Trading-time clock. Variance does not accrue evenly over calendar time: about 70% of a day's variance is realised
# in the 375 trading minutes and about 30% in the overnight gap. Using calendar time makes a model option decay far too
# slowly during the session (it only loses ~26% of a day's theta in market hours), which flatters option buyers.
W_INTRA, W_ON, SESSION_MIN, ANNUAL_UNITS = 0.7, 0.3, 375.0, 252.0
_tdays = pd.read_csv("data/nifty_daily_candles_oi.csv", usecols=["Date"], parse_dates=["Date"]).Date
_all = pd.date_range(_tdays.min(), _tdays.max(), freq="B")
HOLIDAYS = np.array(sorted(set(_all) - set(_tdays)), dtype="datetime64[D]")  # weekdays with no session (beyond the data: none assumed)


def sessions_after(day, expiry):
    """Trading sessions in (day, expiry]."""
    return int(np.busday_count(np.datetime64(pd.Timestamp(day).date(), "D") + 1, np.datetime64(pd.Timestamp(expiry).date(), "D") + 1, holidays=HOLIDAYS))


def t_eff(day, expiry, minute_since_open):
    """Years (in trading-time units) left from `minute_since_open` (0 = 09:15, 375 = 15:30) on `day` to the expiry's close."""
    m = np.clip(np.asarray(minute_since_open, float), 0, SESSION_MIN)
    return np.maximum(W_INTRA * (SESSION_MIN - m) / SESSION_MIN + sessions_after(day, expiry), 1e-3) / ANNUAL_UNITS


def build_iv_table(max_dte_days=40):
    """ATM implied vol per (date, expiry) from real closing prices; `spot` = that day's index close."""
    close = pd.read_csv("data/nifty_daily_candles_oi.csv", parse_dates=["Date"]).set_index("Date")["Close"]
    rows = []
    for path in sorted(glob.glob("data/nifty_chain/*.csv.gz")):
        y = pd.read_csv(path, parse_dates=["date", "expiry"])
        y = y[(y.instrument == "OPT") & (y.close > 0) & (y.date >= "2016-01-01")]
        for (day, exp), g in y.groupby(["date", "expiry"]):
            dte = (exp - day).days
            if dte > max_dte_days or dte <= 0:  # 0DTE closing prices are pure intrinsic value: no vol to read off
                continue
            s = close.get(day, np.nan)
            if s != s:
                continue
            ce, pe = g[g.type == "CE"].set_index("strike").close, g[g.type == "PE"].set_index("strike").close
            both = ce.index.intersection(pe.index)
            both = both[(both >= s - 250) & (both <= s + 250)]
            if len(both) == 0:
                continue
            k = min(both, key=lambda x: abs(x - s))
            t = float(t_eff(day, exp, SESSION_MIN))  # at the close: sessions left / 252
            ivs = [implied_vol(float(ce[k]), s, k, t, True), implied_vol(float(pe[k]), s, k, t, False)]
            ivs = [v for v in ivs if v == v]
            if ivs:
                rows.append({"date": day, "expiry": exp, "dte": dte, "iv": float(np.mean(ivs)), "atm_strike": k, "spot": s,
                             "straddle": float(ce[k] + pe[k])})
        print(path, "done", flush=True)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    t = build_iv_table()
    t.to_csv("data/atm_iv_table.csv", index=False)
    print(len(t), "rows;", t.date.min().date(), "->", t.date.max().date())
    print(t.groupby(t.date.dt.year).iv.describe()[["count", "mean", "50%", "min", "max"]].round(3))

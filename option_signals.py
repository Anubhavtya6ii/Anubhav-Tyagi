"""Bars, daily features and entry signals for the NIFTY option-buying lab.

A signal looks only at bars that have CLOSED, then the trade enters at the next bar's open.
Every signal gives at most one trade per day: the first valid one between 09:30 and 14:30.
"""
import numpy as np
import pandas as pd

OPEN_MIN = 9 * 60 + 15
LO_MIN, HI_MIN = 15, 315  # first signal bar must close >= 09:30 ; the entry bar must open <= 14:30


# ------------------------------------------------------------------ data
class Bars:
    """OHLCV matrices (days x bars-per-day) for one bar size, full sessions only."""

    def __init__(self, minutes):
        self.bm = minutes
        self.nb = 375 // minutes
        f = pd.read_csv(f"dhan/data/index/13_{minutes}.csv.gz", parse_dates=["time_ist"])
        f["d"] = f.time_ist.dt.normalize()
        f["slot"] = ((f.time_ist.dt.hour * 60 + f.time_ist.dt.minute) - OPEN_MIN) // minutes
        f = f[(f.slot >= 0) & (f.slot < self.nb) & (f.d.dt.dayofweek < 5)]
        n = f.groupby("d").size()
        f = f[f.d.isin(n[n == self.nb].index)]
        self.days = pd.DatetimeIndex(sorted(f.d.unique()))
        pos = (self.days.get_indexer(f.d), f.slot.to_numpy())
        for c in ("open", "high", "low", "close", "volume"):
            m = np.full((len(self.days), self.nb), np.nan)
            m[pos] = f[c].to_numpy()
            setattr(self, c[0].upper() if c != "volume" else "V", m)
        self.last = (15 * 60 + 15 - OPEN_MIN) // minutes - 1  # bar index whose close is 15:15
        self.lo = -(-LO_MIN // minutes) - 1  # first allowed signal bar
        self.hi = HI_MIN // minutes - 1  # last allowed signal bar (entry bar opens <= 14:30)


def daily_features(days):
    dd = pd.read_csv("dhan/data/index/13_D.csv.gz", parse_dates=["time_ist"]).set_index("time_ist")
    dd.index = dd.index.normalize()
    out = pd.DataFrame(index=days)
    out["pdh"], out["pdl"], out["pdc"] = dd.high.shift().reindex(days), dd.low.shift().reindex(days), dd.close.shift().reindex(days)
    tr = pd.concat([dd.high - dd.low, (dd.high - dd.close.shift()).abs(), (dd.low - dd.close.shift()).abs()], axis=1).max(axis=1)
    out["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean().shift().reindex(days)

    ch = pd.read_csv("data/nifty_daily_candles_oi.csv", parse_dates=["Date", "Fut expiry"]).set_index("Date")
    px, oi = ch.Close, ch["Fut OI"]
    chg_p, chg_o = px.pct_change(), oi.diff()
    state = pd.Series("", index=ch.index)
    state[(chg_p > 0) & (chg_o > 0)] = "long buildup"
    state[(chg_p > 0) & (chg_o < 0)] = "short covering"
    state[(chg_p < 0) & (chg_o > 0)] = "short buildup"
    state[(chg_p < 0) & (chg_o < 0)] = "long unwinding"
    state[ch.index == ch["Fut expiry"]] = ""  # futures expiry day distorts OI
    out["oi_state"] = state.shift().reindex(days).fillna("")  # state of the PREVIOUS day

    fut = pd.read_csv("dhan/data/futures/61471_D.csv.gz", parse_dates=["time_ist"]).set_index("time_ist")
    fut.index = fut.index.normalize()
    fe = fut.close.ewm(span=20, adjust=False).mean()
    out["fut_trend"] = np.sign(fut.close - fe).shift().reindex(days).fillna(0)

    iv = pd.read_csv("data/atm_iv_table.csv", parse_dates=["date", "expiry"])
    out["is_expiry"] = days.isin(set(iv.expiry))
    return out


# ------------------------------------------------------------------ indicators on the flattened series
def ema(x, n):
    return pd.Series(x).ewm(span=n, adjust=False).mean().to_numpy()


def rsi(x, n=14):
    s = pd.Series(x)
    ch = s.diff()
    up = ch.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-ch.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return (100 - 100 / (1 + up / dn)).to_numpy()


def supertrend(h, l, c, n=10, mult=3.0):
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    atr = pd.Series(tr).ewm(alpha=1 / n, adjust=False).mean().to_numpy()
    hl2 = (h + l) / 2
    ub, lb = hl2 + mult * atr, hl2 - mult * atr
    fub, flb, tr_ = ub.copy(), lb.copy(), np.zeros(len(c))
    for i in range(1, len(c)):
        fub[i] = ub[i] if (ub[i] < fub[i - 1] or c[i - 1] > fub[i - 1]) else fub[i - 1]
        flb[i] = lb[i] if (lb[i] > flb[i - 1] or c[i - 1] < flb[i - 1]) else flb[i - 1]
        if tr_[i - 1] <= 0 and c[i] > fub[i - 1]:
            tr_[i] = 1
        elif tr_[i - 1] >= 0 and c[i] < flb[i - 1]:
            tr_[i] = -1
        else:
            tr_[i] = tr_[i - 1]
    return tr_


def cross_up(a, b):
    return (a > b) & (np.r_[False, a[:-1] <= b[:-1]])


def cross_dn(a, b):
    return (a < b) & (np.r_[False, a[:-1] >= b[:-1]])


def first_events(long_m, short_m, B, day_ok=None):
    """First long/short signal bar per day inside the allowed window -> (bar index, direction), -1 where none."""
    shape = (len(B.days), B.nb)
    lm, sm = long_m.reshape(shape).copy(), short_m.reshape(shape).copy()
    win = np.zeros(shape, bool)
    win[:, B.lo:B.hi + 1] = True
    lm &= win
    sm &= win
    if day_ok is not None:
        lm &= day_ok[:, None]
        sm &= day_ok[:, None]
    il = np.where(lm.any(1), lm.argmax(1), 10 ** 6)
    is_ = np.where(sm.any(1), sm.argmax(1), 10 ** 6)
    idx = np.minimum(il, is_)
    d = np.where(il < is_, 1, -1)
    idx = np.where(idx >= 10 ** 6, -1, idx)
    return idx, np.where(idx >= 0, d, 0)


# ------------------------------------------------------------------ signals
def build_signals(B, F, iv_prev_change=None):
    """Return {name: (bar_idx[D], direction[D])}. Works for any bar size; indicator lengths are in bars."""
    D, nb = B.O.shape
    O, H, L, C, V = (getattr(B, k).ravel() for k in ("O", "H", "L", "C", "V"))
    out = {}
    pdh = np.repeat(F.pdh.to_numpy(), nb)
    pdl = np.repeat(F.pdl.to_numpy(), nb)
    c_prev = np.r_[np.nan, C[:-1]]

    # --- price action
    out["Prev-day high/low breakout"] = first_events((C > pdh) & (c_prev <= pdh), (C < pdl) & (c_prev >= pdl), B)
    n30 = max(30 // B.bm, 1)
    rng_hi = np.repeat(B.H[:, :n30].max(1), nb)
    rng_lo = np.repeat(B.L[:, :n30].min(1), nb)
    pos_in_day = np.tile(np.arange(nb), D)
    after = pos_in_day >= n30
    orb_l, orb_s = (C > rng_hi) & after & (c_prev <= rng_hi), (C < rng_lo) & after & (c_prev >= rng_lo)
    out["30-min opening range breakout"] = first_events(orb_l, orb_s, B)
    # failed breakout reversal: closes beyond prev-day high/low, then back inside within 3 bars
    ab, be = C > pdh, C < pdl
    back_dn = np.zeros_like(ab)
    back_up = np.zeros_like(be)
    for k in range(1, 4):
        sh_ab = np.r_[np.zeros(k, bool), ab[:-k]]
        sh_be = np.r_[np.zeros(k, bool), be[:-k]]
        back_dn |= sh_ab & (C <= pdh)
        back_up |= sh_be & (C >= pdl)
    out["Failed breakout reversal"] = first_events(back_up & ~be, back_dn & ~ab, B)

    # --- VWAP (index volume used as the weight; resets daily)
    tp = (H + L + C) / 3
    cv = np.cumsum((tp * V).reshape(D, nb), 1)
    cvol = np.cumsum(V.reshape(D, nb), 1)
    vwap = (cv / np.maximum(cvol, 1)).ravel()
    out["VWAP crossover"] = first_events(cross_up(C, vwap) & (pos_in_day >= 3), cross_dn(C, vwap) & (pos_in_day >= 3), B)
    above = (C > vwap).astype(int).reshape(D, nb)
    run_up = np.zeros_like(above)
    run_dn = np.zeros_like(above)
    for j in range(1, nb):
        run_up[:, j] = np.where(above[:, j - 1] == 1, run_up[:, j - 1] + 1, 0)
        run_dn[:, j] = np.where(above[:, j - 1] == 0, run_dn[:, j - 1] + 1, 0)
    ru, rd = run_up.ravel(), run_dn.ravel()
    need = max(30 // B.bm, 2)
    tol = 0.0003
    out["VWAP retest"] = first_events((ru >= need) & (L <= vwap * (1 + tol)) & (C > vwap), (rd >= need) & (H >= vwap * (1 - tol)) & (C < vwap), B)

    # --- moving averages / momentum (continuous series across days)
    e9, e20, e50, e21 = ema(C, 9), ema(C, 20), ema(C, 50), ema(C, 21)
    out["EMA 9/20 cross"] = first_events(cross_up(e9, e20), cross_dn(e9, e20), B)
    out["EMA 20/50 cross"] = first_events(cross_up(e20, e50), cross_dn(e20, e50), B)
    r = rsi(C, 14)
    out["RSI momentum (60/40)"] = first_events(cross_up(r, np.full_like(r, 60)), cross_dn(r, np.full_like(r, 40)), B)
    out["RSI reversal (30/70)"] = first_events(cross_up(r, np.full_like(r, 30)), cross_dn(r, np.full_like(r, 70)), B)
    macd = ema(C, 12) - ema(C, 26)
    sig = ema(macd, 9)
    out["MACD signal cross"] = first_events(cross_up(macd, sig), cross_dn(macd, sig), B)
    st = supertrend(H, L, C)
    out["Supertrend flip"] = first_events((st > 0) & (np.r_[0, st[:-1]] <= 0), (st < 0) & (np.r_[0, st[:-1]] >= 0), B)

    # --- OI analysis (previous day's futures price/OI state, daily data)
    s = F.oi_state.to_numpy()
    first_bar = max(15 // B.bm, 1)  # enter ~09:30
    d_idx = np.where(np.isin(s, ["long buildup", "short buildup"]), first_bar, -1)
    d_dir = np.where(s == "long buildup", 1, np.where(s == "short buildup", -1, 0))
    out["OI buildup (long->CE, short->PE)"] = (np.where(d_dir != 0, d_idx, -1), d_dir)
    d_idx = np.where(np.isin(s, ["short covering", "long unwinding"]), first_bar, -1)
    d_dir = np.where(s == "short covering", 1, np.where(s == "long unwinding", -1, 0))
    out["OI covering/unwinding (cover->CE, unwind->PE)"] = (np.where(d_dir != 0, d_idx, -1), d_dir)
    # OI direction confirmed by the first 30 minutes of price
    ret30 = B.C[:, n30 - 1] / np.where(np.isnan(F.pdc.to_numpy()), np.nan, F.pdc.to_numpy()) - 1
    oi_dir = np.where(np.isin(s, ["long buildup", "short covering"]), 1, np.where(np.isin(s, ["short buildup", "long unwinding"]), -1, 0))
    conf = (np.sign(ret30) == oi_dir) & (oi_dir != 0)
    out["OI direction + 30-min price confirmation"] = (np.where(conf, n30, -1), np.where(conf, oi_dir, 0))

    # --- futures confirmation (daily futures trend only: intraday futures history is not available)
    ft = F.fut_trend.to_numpy()
    ft_up = np.repeat(ft > 0, nb)
    ft_dn = np.repeat(ft < 0, nb)
    out["Futures trend + EMA 9/20 cross"] = first_events(cross_up(e9, e20) & ft_up, cross_dn(e9, e20) & ft_dn, B)

    # --- multi-timeframe: 15-min trend + entry-timeframe EMA 9/21 cross
    k15 = 15 // B.bm if B.bm <= 15 else 1
    n15 = nb // k15
    c15 = B.C[:, k15 - 1::k15][:, :n15].ravel()
    t15 = np.sign(ema(c15, 9) - ema(c15, 21)).reshape(D, n15)
    j = np.clip((np.arange(nb) + 1) // k15 - 1, -1, n15 - 1)
    trend_bar = np.where(j[None, :] >= 0, t15[:, np.clip(j, 0, n15 - 1)], 0).ravel()
    out["15-min trend + EMA 9/21 entry"] = first_events(cross_up(e9, e21) & (trend_bar > 0), cross_dn(e9, e21) & (trend_bar < 0), B)

    # --- IV expansion (previous days' ATM IV rising) + opening-range breakout direction
    if iv_prev_change is not None:
        ok = np.asarray(iv_prev_change) >= 0.05
        out["IV expansion + opening range breakout"] = first_events(orb_l, orb_s, B, day_ok=ok)
    return out

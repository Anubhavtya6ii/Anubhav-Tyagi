"""NIFTY intraday option-BUYING lab: signal x strike x exit x timeframe x expiry day, on modelled premiums.

Premiums come from Black-Scholes with each contract's implied vol taken from the PREVIOUS day's real NSE closing prices
(see optmodel.py) on a trading-time clock. The model tracks real 15-minute premium changes closely (corr 0.96) but its LEVEL is only good to ~10%,
so results are for comparing set-ups, not for forecasting rupees. Weekly era only (Mar 2019 - Oct 2026), 1 lot = 65.

Run: python option_buying_lab.py   -> Option_Buying_Lab.xlsx
"""
import time

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Font

import build_excel as bx
import optmodel as om
import option_signals as sg

LOT = 65
SPLIT = pd.Timestamp("2022-01-01")
WEEKLY_FROM = pd.Timestamp("2019-03-01")
OUT_FILE = "Option_Buying_Lab.xlsx"
STRIKES = ["ITM 100", "ITM 50", "ATM", "OTM 50", "OTM 100", "Delta 0.30", "Delta 0.40", "Delta 0.50", "Delta 0.60"]
DELTAS = {"Delta 0.30": 0.30, "Delta 0.40": 0.40, "Delta 0.50": 0.50, "Delta 0.60": 0.60}
MIN_PREMIUM = 5.0
STRESS_DECAY_PER_15MIN = 0.0022  # extra premium decay seen in the real-vs-model check (about 0.33 pt on a ~150 pt option)

# exit packages (bars are converted from minutes at run time)
EXITS = {
    "SL 25% + target 50%": dict(sl_pct=.25, tgt_pct=.50),
    "SL 25% + trailing 25%": dict(sl_pct=.25, trail_pct=.25),
    "SL 40% + target 80%": dict(sl_pct=.40, tgt_pct=.80),
    "SL 20pts + target 40pts": dict(sl_pts=20, tgt_pts=40),
    "SL 20pts + trailing 20pts": dict(sl_pts=20, trail_pts=20),
    "SL 25%, hold to 15:15": dict(sl_pct=.25),
    "SL 25%, time exit 30min": dict(sl_pct=.25, minutes=30),
    "SL 25%, time exit 60min": dict(sl_pct=.25, minutes=60),
    "No SL, time exit 30min": dict(minutes=30),
}


# ------------------------------------------------------------------ contracts and IV
class Contracts:
    def __init__(self):
        t = pd.read_csv("data/atm_iv_table.csv", parse_dates=["date", "expiry"])
        t = t[t.date >= "2019-01-01"]
        self.iv = {(r.date, r.expiry): r.iv for r in t.itertuples()}
        self.dates = sorted(t.date.unique())
        self.prev = {d: self.dates[i - 1] for i, d in enumerate(self.dates) if i > 0}
        self.live = {d: sorted([(r.dte, r.expiry) for r in g.itertuples() if 0 <= r.dte <= 14]) for d, g in t.groupby("date")}
        for d in list(self.live):  # the contract expiring today (0DTE) has no IV row of its own; it was priced yesterday
            if (self.prev.get(d), d) in self.iv:
                self.live[d] = [(0, d)] + self.live[d]

    def pick(self, day, which):
        """(expiry, dte_of_nearest, iv) for 'nearest' or 'next' weekly contract; IV is last session's real ATM IV."""
        lst = self.live.get(day)
        pd_ = self.prev.get(day)
        if not lst or pd_ is None:
            return None
        j = 0 if which == "nearest" else 1
        if len(lst) <= j or lst[0][0] > 7:
            return None
        exp = lst[j][1]
        iv = self.iv.get((pd_, exp))
        if iv is None or iv != iv:
            return None
        return exp, lst[0][0], float(np.clip(iv, 0.06, 0.80))


# ------------------------------------------------------------------ costs
def half_spread(p):
    return np.maximum(0.30, 0.005 * p)  # measured ATM bid-ask is ~0.9 pt on ~120 pt options


def charges_rs(entry, exit_, day):
    new = day >= pd.Timestamp("2024-10-01")
    turnover = (entry + exit_) * LOT
    txn = turnover * (0.00035 if new else 0.00053)
    stt = exit_ * LOT * (0.001 if new else 0.000625)
    brok = 40.0
    return brok + txn + stt + 0.18 * (brok + txn) + entry * LOT * 0.00003 + turnover * 0.000001


# ------------------------------------------------------------------ one trade, all strikes x exits
def exit_path(Po, Ph, Pl, Pc, E0, p, bm):
    n = len(Po)
    last = n - 1
    if p.get("minutes"):
        last = min(last, max(p["minutes"] // bm, 1) - 1)
    stop = -np.inf
    if "sl_pct" in p:
        stop = E0 * (1 - p["sl_pct"])
    if "sl_pts" in p:
        stop = E0 - p["sl_pts"]
    tgt = np.inf
    if "tgt_pct" in p:
        tgt = E0 * (1 + p["tgt_pct"])
    if "tgt_pts" in p:
        tgt = E0 + p["tgt_pts"]
    if "trail_pct" in p or "trail_pts" in p:
        runmax = np.maximum.accumulate(np.r_[E0, Ph[:-1]])
        tstop = runmax * (1 - p["trail_pct"]) if "trail_pct" in p else runmax - p["trail_pts"]
        stop_arr = np.maximum(stop, tstop)
    else:
        stop_arr = np.full(n, stop)
    hs, ht = Pl[:last + 1] <= stop_arr[:last + 1], Ph[:last + 1] >= tgt
    i_s = hs.argmax() if hs.any() else 10 ** 6
    i_t = ht.argmax() if ht.any() else 10 ** 6
    if i_s <= last and i_s <= i_t:
        return min(Po[i_s], stop_arr[i_s]), i_s
    if i_t <= last:
        return max(Po[i_t], tgt), i_t
    return Pc[last], last


def simulate(B, F, idx, dirs, C, which="nearest", stress=False, strikes=STRIKES, exits=EXITS):
    rows = []
    bm = B.bm
    for k in np.flatnonzero(idx >= 0):
        day = B.days[k]
        if day < WEEKLY_FROM:
            continue
        i = int(idx[k]) + 1
        if i >= B.last:
            continue
        pk = C.pick(day, which)
        if pk is None:
            continue
        exp, dte0, iv = pk
        d = int(dirs[k])
        call = d > 0
        sl = slice(i, B.last + 1)
        o, h, l, c = B.O[k, sl], B.H[k, sl], B.L[k, sl], B.C[k, sl]
        if np.isnan(o).any():
            continue
        start = (np.arange(i, B.last + 1)) * bm  # minutes since 09:15
        t_o, t_m, t_c = om.t_eff(day, exp, start), om.t_eff(day, exp, start + bm / 2), om.t_eff(day, exp, start + bm)
        spot0 = o[0]
        atm = np.round(spot0 / 50) * 50
        cand = np.arange(atm - 1000, atm + 1001, 50)
        dl = np.abs(om.delta(spot0, cand, t_o[0], iv, call))
        K = []
        for s in strikes:
            if s == "ATM":
                K.append(atm)
            elif s.startswith("ITM"):
                K.append(atm - d * float(s.split()[1]))
            elif s.startswith("OTM"):
                K.append(atm + d * float(s.split()[1]))
            else:
                K.append(cand[np.argmin(np.abs(dl - DELTAS[s]))])
        K = np.array(K)[:, None]
        hi, lo = (h, l) if call else (l, h)
        Po = om.bs(o[None, :], K, t_o[None, :], iv, call)
        Ph = om.bs(hi[None, :], K, t_m[None, :], iv, call)
        Pl = om.bs(lo[None, :], K, t_m[None, :], iv, call)
        Pc = om.bs(c[None, :], K, t_c[None, :], iv, call)
        if stress:
            f = 1 - STRESS_DECAY_PER_15MIN * (np.arange(len(o)) * bm) / 15.0
            Po, Ph, Pl, Pc = Po * f, Ph * f, Pl * f, Pc * f
        for si, s in enumerate(strikes):
            if Po[si, 0] < MIN_PREMIUM:
                continue
            E0 = Po[si, 0] + float(half_spread(Po[si, 0]))
            for ename, p in exits.items():
                px, ib = exit_path(Po[si], Ph[si], Pl[si], Pc[si], E0, p, bm)
                X = max(px - float(half_spread(px)), 0.05)
                net = (X - E0) * LOT - charges_rs(E0, X, day)
                rows.append((day, dte0, d, s, ename, E0, X, ib + 1, net, net / (E0 * LOT)))
    return pd.DataFrame(rows, columns=["day", "dte", "dir", "strike", "exit", "entry", "exit_px", "bars", "rs", "ret"])


# ------------------------------------------------------------------ statistics
def tstat(x):
    x = np.asarray(x, float)
    return x.mean() / x.std(ddof=1) * np.sqrt(len(x)) if len(x) > 2 and x.std(ddof=1) > 0 else np.nan


DAYTYPES = {"All days": lambda d: d.dte >= 0, "Expiry day (0DTE)": lambda d: d.dte == 0, "Day before expiry": lambda d: d.dte == 1,
            "Other days (2+ DTE)": lambda d: d.dte >= 2}


def stats(g):
    if len(g) < 5:
        return None
    is_, oos = g[g.day < SPLIT], g[g.day >= SPLIT]
    wins, losses = g.rs[g.rs > 0].sum(), -g.rs[g.rs < 0].sum()
    cum = g.sort_values("day").rs.cumsum()
    return {"Trades": len(g), "Win rate": (g.rs > 0).mean(), "Avg return %": g.ret.mean(), "Avg Rs (1 lot)": g.rs.mean(),
            "Total Rs (1 lot)": g.rs.sum(), "Profit factor": wins / losses if losses else np.nan,
            "Max drawdown Rs": (cum - cum.cummax().clip(lower=0)).min(), "Avg premium": g.entry.mean(),
            "Avg hold (bars)": g.bars.mean(), "IS trades": len(is_), "IS avg %": is_.ret.mean(), "IS t": tstat(is_.ret),
            "OOS trades": len(oos), "OOS avg %": oos.ret.mean(), "OOS t": tstat(oos.ret)}


def passed(s):
    return bool(s["IS trades"] >= 40 and s["OOS trades"] >= 40 and s["IS t"] >= 2 and s["OOS t"] >= 1 and s["OOS avg %"] > 0)


def summarise(tr, label):
    rows = []
    for (st, ex), g in tr.groupby(["strike", "exit"]):
        for dt, fn in DAYTYPES.items():
            sub = g[fn(g)]
            s = stats(sub)
            if s:
                rows.append({**label, "Strike": st, "Exit": ex, "Day type": dt, **s, "Passed": passed(s)})
    return rows


def random_signal(B, rng):
    idx = rng.integers(B.lo, B.hi + 1, size=len(B.days))
    return idx, rng.choice([-1, 1], size=len(B.days))


# ------------------------------------------------------------------ main
def build():
    t0 = time.time()
    C = Contracts()
    B5 = sg.Bars(5)
    F5 = sg.daily_features(B5.days)
    vix = pd.read_csv("data/INDIAVIX.csv", index_col="Date", parse_dates=True)["VIX"]
    chg = (vix.shift(1) / vix.shift(2) - 1).reindex(B5.days).fillna(0).to_numpy()
    sig5 = sg.build_signals(B5, F5, chg)
    print(f"5-min bars: {len(B5.days)} days | signals: {len(sig5)} | events:", {k: int((v[0] >= 0).sum()) for k, v in sig5.items()}, flush=True)

    all_rows, tr_store = [], {}
    for name, (idx, dirs) in sig5.items():
        tr = simulate(B5, F5, idx, dirs, C)
        tr_store[(name, "5-min", "nearest")] = tr
        all_rows += summarise(tr, {"Signal": name, "Entry timeframe": "5-min", "Contract": "nearest weekly"})
        print(f"  {name}: {len(tr)} sims, {time.time() - t0:.0f}s", flush=True)

    # next-week contract for the ATM / delta-0.40 subset
    for name, (idx, dirs) in sig5.items():
        tr = simulate(B5, F5, idx, dirs, C, which="next", strikes=["ATM", "Delta 0.40"])
        all_rows += summarise(tr, {"Signal": name, "Entry timeframe": "5-min", "Contract": "next weekly"})
    print("next-week contract done", f"{time.time() - t0:.0f}s", flush=True)

    # 1-minute entries for the timeframe-agnostic signals
    B1 = sg.Bars(1)
    F1 = sg.daily_features(B1.days)
    chg1 = (vix.shift(1) / vix.shift(2) - 1).reindex(B1.days).fillna(0).to_numpy()
    sig1 = sg.build_signals(B1, F1, chg1)
    keep = ["Prev-day high/low breakout", "30-min opening range breakout", "VWAP crossover", "EMA 9/20 cross", "EMA 20/50 cross",
            "RSI momentum (60/40)", "MACD signal cross", "Supertrend flip", "15-min trend + EMA 9/21 entry"]
    for name in keep:
        idx, dirs = sig1[name]
        tr = simulate(B1, F1, idx, dirs, C, strikes=["ITM 50", "ATM", "OTM 50", "Delta 0.40"])
        tr_store[(name, "1-min", "nearest")] = tr
        all_rows += summarise(tr, {"Signal": name, "Entry timeframe": "1-min", "Contract": "nearest weekly"})
        print(f"  1-min {name}: {len(tr)} sims, {time.time() - t0:.0f}s", flush=True)

    # random-signal baselines = the pure cost of buying options (no skill) and the luck benchmark
    rng = np.random.default_rng(5)
    base_rows, null_pass = [], []
    for seed in range(12):
        idx, dirs = random_signal(B5, rng)
        tr = simulate(B5, F5, idx, dirs, C)
        rows = summarise(tr, {"Signal": f"RANDOM entry (seed {seed})", "Entry timeframe": "5-min", "Contract": "nearest weekly"})
        base_rows += rows
        null_pass.append(sum(r["Passed"] for r in rows))
    print("random baselines done", f"{time.time() - t0:.0f}s", flush=True)

    res = pd.DataFrame(all_rows)
    base = pd.DataFrame(base_rows)
    real_same = res[(res["Entry timeframe"] == "5-min") & (res.Contract == "nearest weekly")]
    print(f"configs: {len(res)} | passed: {int(res.Passed.sum())} | random-signal passes per run (of {len(real_same) // len(sig5)} configs): {null_pass}", flush=True)

    # stress the winners
    winners = res[res.Passed].copy()
    stress_rows = []
    for _, w in winners.iterrows():
        key = (w.Signal, w["Entry timeframe"], "nearest")
        if key not in tr_store:
            continue
        bars = B5 if w["Entry timeframe"] == "5-min" else B1
        feats = F5 if w["Entry timeframe"] == "5-min" else F1
        sigs = sig5 if w["Entry timeframe"] == "5-min" else sig1
        idx, dirs = sigs[w.Signal]
        tr = simulate(bars, feats, idx, dirs, C, stress=True, strikes=[w.Strike], exits={w.Exit: EXITS[w.Exit]})
        s = stats(tr[DAYTYPES[w["Day type"]](tr)])
        if s:
            stress_rows.append({"Signal": w.Signal, "Strike": w.Strike, "Exit": w.Exit, "Day type": w["Day type"],
                                "Base avg %": w["Avg return %"], "Stress avg %": s["Avg return %"], "Base OOS avg %": w["OOS avg %"],
                                "Stress OOS avg %": s["OOS avg %"], "Stress OOS t": s["OOS t"]})
    stress = pd.DataFrame(stress_rows)
    print("stress done", f"{time.time() - t0:.0f}s", flush=True)
    return res, base, null_pass, stress, tr_store


if __name__ == "__main__":
    pd.set_option("display.width", 250, "display.max_columns", 30)
    res, base, null_pass, stress, store = build()
    res.to_pickle("data/option_buying_results.pkl")
    base.to_pickle("data/option_buying_baseline.pkl")
    stress.to_pickle("data/option_buying_stress.pkl")
    print(res.Passed.sum(), null_pass)

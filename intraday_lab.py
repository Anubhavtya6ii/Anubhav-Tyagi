"""Intraday NIFTY strategy lab on 5-minute Dhan candles (Apr 2017 - Oct 2026).

~100 strategies from five families (opening-range breakout, gap, intraday momentum, late-day momentum,
overnight, expiry-day momentum), traded as NIFTY futures. Rules and parameter grids are fixed up front.
Discovery = trades before 2022-01-01, validation = 2022-01-01 onward. A strategy "passes" only if it is
significant in discovery AND profitable in validation after costs. A sign-flip Monte Carlo shows how many
of the ~100 would pass by luck. Writes Intraday_Strategy_Lab.xlsx.

Run: python intraday_lab.py   (needs dhan/data/index/13_5.csv.gz and 13_D.csv.gz, data/nifty_daily_candles_oi.csv)
"""
import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Font

import build_excel as bx

LOT = 65  # fixed lot so every year is comparable (real lots were 75 / 50 / 25 / 65 over time)
SLIP = 1.0  # index points lost per side
SPLIT = pd.Timestamp("2022-01-01")
EXIT_SLOT = 72  # 15:15 - the last reliable bar of every year (in 2026 the index freezes after 15:15)
MIN_IS, MIN_OOS = 100, 50
LUCK_RUNS = 300
OUT_FILE = "Intraday_Strategy_Lab.xlsx"


# ------------------------------------------------------------------ data
def load():
    f = pd.read_csv("dhan/data/index/13_5.csv.gz", parse_dates=["time_ist"])
    f["d"] = f.time_ist.dt.normalize()
    f["slot"] = ((f.time_ist.dt.hour * 60 + f.time_ist.dt.minute) - (9 * 60 + 15)) // 5
    f = f[(f.slot >= 0) & (f.slot <= 74) & (f.d.dt.dayofweek < 5)]
    n = f.groupby("d").size()
    f = f[f.d.isin(n[n == 75].index)]
    days = pd.DatetimeIndex(sorted(f.d.unique()))
    shape = (len(days), 75)
    idx = (days.get_indexer(f.d), f.slot.to_numpy())
    mats = {}
    for c in ("open", "high", "low", "close"):
        m = np.full(shape, np.nan)
        m[idx] = f[c].to_numpy()
        mats[c] = m

    dd = pd.read_csv("dhan/data/index/13_D.csv.gz", parse_dates=["time_ist"]).set_index("time_ist")
    dd.index = dd.index.normalize()
    tr = pd.concat([dd.high - dd.low, (dd.high - dd.close.shift()).abs(), (dd.low - dd.close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    daily = pd.DataFrame({"close": dd.close, "atr": atr, "ema20": dd.close.ewm(span=20, adjust=False).mean(),
                          "ema50": dd.close.ewm(span=50, adjust=False).mean()})
    prev = daily.shift().reindex(days)  # everything known before today's open
    prev.columns = ["prev_close", "atr", "ema20", "ema50"]
    prev["atr_pct"] = prev.atr / prev.prev_close
    prev["trend_up"] = prev.ema20 > prev.ema50

    ch = pd.read_csv("data/nifty_daily_candles_oi.csv", parse_dates=["Date", "Near expiry"])
    exp = sorted(set(ch["Near expiry"].dropna()) & set(days))
    exp_set = set(exp)
    last_exp_close, last_exp_i, cyc = {}, {}, {}
    pe_i, pe_close = None, None
    for i, d in enumerate(days):
        if pe_i is not None:
            cyc[d] = (pe_close, i - pe_i)
        if d in exp_set:
            pe_i, pe_close = i, daily.close.get(d, np.nan)
    fut = pd.read_csv("dhan/data/futures/61471_D.csv.gz", parse_dates=["time_ist"]).set_index("time_ist")  # continuous near-month futures
    fut.index = fut.index.normalize()
    fexp = set(pd.read_csv("data/nifty_daily_candles_oi.csv", parse_dates=["Fut expiry"])["Fut expiry"].dropna())
    prev["fut_close"] = fut.close.reindex(days).to_numpy()
    prev["fut_open_next"] = fut.open.reindex(days).shift(-1).to_numpy()
    prev["fut_roll"] = [d in fexp for d in days]  # futures expiry day: the continuous series jumps to the next contract overnight
    prev["is_expiry"] = [d in exp_set for d in days]
    prev["cyc_start_close"] = [cyc.get(d, (np.nan, np.nan))[0] for d in days]
    prev["cyc_days"] = [cyc.get(d, (np.nan, np.nan))[1] for d in days]
    prev["cyc_move_z"] = ((prev.prev_close / prev.cyc_start_close - 1) / (prev.atr_pct * np.sqrt(prev.cyc_days))).fillna(0.0)
    return days, mats, prev


def cost_pts(date, price):
    return 2 * SLIP + price * (0.00021 if date < pd.Timestamp("2024-10-01") else 0.00027)


# ------------------------------------------------------------------ trade simulation
def run(O, H, L, C, i0, d, stop=None, tp=None, last=EXIT_SLOT - 1):
    """Enter at the open of bar i0 in direction d (+1/-1), manage stop / target on bars i0..last, else exit at close of `last`."""
    for i in range(i0, last + 1):
        if d > 0:
            if stop is not None and L[i] <= stop:
                return (O[i] if O[i] <= stop else stop), i, "stop"
            if tp is not None and H[i] >= tp:
                return (O[i] if O[i] >= tp else tp), i, "target"
        else:
            if stop is not None and H[i] >= stop:
                return (O[i] if O[i] >= stop else stop), i, "stop"
            if tp is not None and L[i] <= tp:
                return (O[i] if O[i] <= tp else tp), i, "target"
    return C[last], last, "time"


def trade_row(name, family, params, day, i0, d, entry, exit_px, ex_i, why):
    gross = d * (exit_px - entry)
    c = cost_pts(day, entry)
    return {"strategy": name, "family": family, "params": params, "date": day, "direction": "Long" if d > 0 else "Short",
            "entry slot": i0, "entry": entry, "exit": exit_px, "exit slot": ex_i, "exit type": why,
            "gross pts": gross, "cost pts": c, "net pts": gross - c}


# ------------------------------------------------------------------ strategies
def strategies():
    """Yield (name, family, params, function(days, mats, prev) -> list of trade rows)."""
    out = []

    def orb(n, side, filt, stop_kind):
        def f(days, M, P):
            rows = []
            for k, day in enumerate(days):
                O, H, L, C = M["open"][k], M["high"][k], M["low"][k], M["close"][k]
                hi, lo = H[:n].max(), L[:n].min()
                for j in range(n - 1, 60):  # signal on bar j close, enter next bar
                    d = 1 if C[j] > hi else -1 if C[j] < lo else 0
                    if d == 0 or j < n:
                        continue
                    if (d > 0 and side == "short") or (d < 0 and side == "long"):
                        break
                    up = P.trend_up.iloc[k]
                    if filt == "trend" and ((d > 0) != bool(up)):
                        break
                    stop = lo if stop_kind == "opposite side" else (hi + lo) / 2
                    stop = stop if d > 0 else (hi if stop_kind == "opposite side" else (hi + lo) / 2)
                    px, ei, why = run(O, H, L, C, j + 1, d, stop=stop)
                    rows.append(trade_row(name, "Opening range breakout", params, day, j + 1, d, O[j + 1], px, ei, why))
                    break
            return rows
        name = f"ORB {n * 5}min {side} | trend filter {filt} | stop {stop_kind}"
        params = f"range={n * 5}min, side={side}, filter={filt}, stop={stop_kind}"
        out.append((name, "Opening range breakout", params, f))

    for n in (3, 6, 12):
        for side in ("both", "long", "short"):
            for filt in ("none", "trend"):
                for sk in ("opposite side", "midpoint"):
                    orb(n, side, filt, sk)

    def gap(k_atr, mode, stop_atr):
        def f(days, M, P):
            rows = []
            for k, day in enumerate(days):
                pc, ap = P.prev_close.iloc[k], P.atr_pct.iloc[k]
                if not (pc == pc and ap == ap):
                    continue
                O, H, L, C = M["open"][k], M["high"][k], M["low"][k], M["close"][k]
                g = O[0] / pc - 1
                if abs(g) < k_atr * ap:
                    continue
                gd = 1 if g > 0 else -1
                first = 1 if C[0] > O[0] else -1
                if mode == "follow" and first == gd:
                    d = gd
                elif mode == "fade" and first == -gd:
                    d = -gd
                else:
                    continue
                e = O[1]
                stop = None if stop_atr is None else e - d * stop_atr * P.atr.iloc[k]
                px, ei, why = run(O, H, L, C, 1, d, stop=stop)
                rows.append(trade_row(name, "Gap", params, day, 1, d, e, px, ei, why))
            return rows
        sa = "no stop" if stop_atr is None else f"stop {stop_atr} ATR"
        name = f"Gap {mode} | gap >= {k_atr} ATR | {sa}"
        params = f"gap>={k_atr}ATR, mode={mode}, {sa}"
        out.append((name, "Gap", params, f))

    for k_atr in (0.25, 0.5, 1.0):
        for mode in ("follow", "fade"):
            for sa in (None, 1.0):
                gap(k_atr, mode, sa)

    def mom(w, with_gap, thr, stop_atr):
        def f(days, M, P):
            rows = []
            for k, day in enumerate(days):
                pc, ap = P.prev_close.iloc[k], P.atr_pct.iloc[k]
                if not (pc == pc and ap == ap):
                    continue
                O, H, L, C = M["open"][k], M["high"][k], M["low"][k], M["close"][k]
                base = pc if with_gap else O[0]
                r = C[w - 1] / base - 1
                if abs(r) < thr * ap or r == 0:
                    continue
                d = 1 if r > 0 else -1
                e = O[w]
                stop = None if stop_atr is None else e - d * stop_atr * P.atr.iloc[k]
                px, ei, why = run(O, H, L, C, w, d, stop=stop)
                rows.append(trade_row(name, "Intraday momentum", params, day, w, d, e, px, ei, why))
            return rows
        sa = "no stop" if stop_atr is None else f"stop {stop_atr} ATR"
        name = f"Momentum first {w * 5}min {'incl gap' if with_gap else 'excl gap'} | move >= {thr} ATR | {sa}"
        params = f"window={w * 5}min, gap={'in' if with_gap else 'out'}, min move={thr}ATR, {sa}"
        out.append((name, "Intraday momentum", params, f))

    for w in (3, 6, 12):
        for wg in (True, False):
            for thr in (0.0, 0.5):
                for sa in (None, 1.0):
                    mom(w, wg, thr, sa)

    def late(src, thr, mode):
        def f(days, M, P):
            rows = []
            for k, day in enumerate(days):
                pc, ap = P.prev_close.iloc[k], P.atr_pct.iloc[k]
                if not (pc == pc and ap == ap):
                    continue
                O, H, L, C = M["open"][k], M["high"][k], M["low"][k], M["close"][k]
                r = {"first 30min": C[5] / pc - 1, "first 60min": C[11] / pc - 1, "day so far (to 14:40)": C[65] / O[0] - 1}[src]
                if abs(r) < thr * ap or r == 0:
                    continue
                d = (1 if r > 0 else -1) * (1 if mode == "follow" else -1)
                e = O[66]  # 14:45
                px, ei, why = run(O, H, L, C, 66, d)
                rows.append(trade_row(name, "Late-day momentum", params, day, 66, d, e, px, ei, why))
            return rows
        name = f"Late-day {mode} {src} | move >= {thr} ATR | 14:45 to 15:15"
        params = f"signal={src}, mode={mode}, min move={thr}ATR, 14:45-15:15"
        out.append((name, "Late-day momentum", params, f))

    for src in ("first 30min", "first 60min", "day so far (to 14:40)"):
        for thr in (0.0, 0.5):
            for mode in ("follow", "fade"):
                late(src, thr, mode)

    def overnight(kind):
        def f(days, M, P):
            rows = []
            for k in range(len(days) - 1):
                day = days[k]
                O, H, L, C = M["open"][k], M["high"][k], M["low"][k], M["close"][k]
                today = C[EXIT_SLOT - 1] / O[0] - 1
                up = bool(P.trend_up.iloc[k])
                d = {"long always": 1, "long after down day": 1 if today < 0 else 0, "long after up day": 1 if today > 0 else 0,
                     "long in uptrend": 1 if up else 0, "short in downtrend": -1 if not up else 0,
                     "short after up day": -1 if today > 0 else 0}[kind]
                if d == 0:
                    continue
                if (days[k + 1] - day).days > 4 or P.fut_roll.iloc[k]:
                    continue
                e, x = P.fut_close.iloc[k], P.fut_open_next.iloc[k]  # real futures prices: close -> next open
                if not (e == e and x == x):
                    continue
                rows.append(trade_row(name, "Overnight (futures)", params, day, EXIT_SLOT, d, e, x, 0, "next open"))
            return rows
        name = f"Overnight {kind} | 15:15 to next 09:15"
        params = f"rule={kind}"
        out.append((name, "Overnight (futures)", params, f))

    for kind in ("long always", "long after down day", "long after up day", "long in uptrend", "short in downtrend", "short after up day"):
        overnight(kind)

    def expiry(z_thr, slot, mode):
        def f(days, M, P):
            rows = []
            for k, day in enumerate(days):
                if not P.is_expiry.iloc[k]:
                    continue
                z = P.cyc_move_z.iloc[k]
                if abs(z) < z_thr or z == 0:
                    continue
                d = (1 if z > 0 else -1) * (1 if mode == "follow" else -1)
                O, H, L, C = M["open"][k], M["high"][k], M["low"][k], M["close"][k]
                px, ei, why = run(O, H, L, C, slot, d)
                rows.append(trade_row(name, "Expiry-day momentum", params, day, slot, d, O[slot], px, ei, why))
            return rows
        t = {1: "09:20", 36: "12:15"}[slot]
        name = f"Expiry day {mode} cycle momentum | z >= {z_thr} | enter {t}"
        params = f"mode={mode}, cycle move z>={z_thr}, entry={t}"
        out.append((name, "Expiry-day momentum", params, f))

    for z in (0.0, 0.75, 1.5):
        for slot in (1, 36):
            for mode in ("follow", "fade"):
                expiry(z, slot, mode)
    return out


# ------------------------------------------------------------------ statistics
def tstat(x):
    x = np.asarray(x, float)
    return x.mean() / x.std(ddof=1) * np.sqrt(len(x)) if len(x) > 2 and x.std(ddof=1) > 0 else np.nan


def summarise(name, family, params, t):
    if t.empty:
        return None
    is_, oos = t[t.date < SPLIT], t[t.date >= SPLIT]
    net = t["net pts"]
    pnl = net * LOT
    cum = pnl.sort_index().cumsum()
    wins, losses = net[net > 0].sum(), -net[net < 0].sum()
    years = (t.date.max() - t.date.min()).days / 365.25
    return {
        "Strategy": name, "Family": family, "Rules": params, "Trades": len(t), "Win rate": (net > 0).mean(),
        "Avg net pts": net.mean(), "Profit factor": wins / losses if losses else np.nan,
        "Total Rs (1 lot)": pnl.sum(), "Rs per year (1 lot)": pnl.sum() / max(years, 0.5),
        "Max drawdown Rs (1 lot)": (cum - cum.cummax().clip(lower=0)).min(), "Worst trade Rs": pnl.min(),
        "Avg cost pts": t["cost pts"].mean(), "Avg gross pts": t["gross pts"].mean(),
        "IS trades": len(is_), "IS avg net pts": is_["net pts"].mean() if len(is_) else np.nan, "IS t-score": tstat(is_["net pts"]),
        "OOS trades": len(oos), "OOS avg net pts": oos["net pts"].mean() if len(oos) else np.nan, "OOS t-score": tstat(oos["net pts"]),
        "OOS win rate": (oos["net pts"] > 0).mean() if len(oos) else np.nan,
    }


def passed(s):
    return (s["IS trades"] >= MIN_IS) and (s["OOS trades"] >= MIN_OOS) and (s["IS t-score"] >= 2.0) \
        and (s["OOS t-score"] >= 1.0) and (s["OOS avg net pts"] > 0)


def luck_check(books, runs=LUCK_RUNS):
    """Flip the direction of every trade at random (keeps volatility, costs and trade counts; removes any skill)."""
    rng = np.random.default_rng(11)
    data = []
    for t in books.values():
        if len(t) < MIN_IS + MIN_OOS:
            continue
        data.append((np.abs(t["gross pts"].to_numpy()) * np.sign(t["gross pts"].to_numpy()), t["cost pts"].to_numpy(), (t.date < SPLIT).to_numpy()))
    counts = []
    for _ in range(runs):
        n = 0
        for g, c, is_mask in data:
            flip = rng.choice([-1.0, 1.0], size=len(g))
            net = np.abs(g) * flip - c
            a, b = net[is_mask], net[~is_mask]
            if len(a) >= MIN_IS and len(b) >= MIN_OOS and tstat(a) >= 2.0 and tstat(b) >= 1.0 and b.mean() > 0:
                n += 1
        counts.append(n)
    return counts, len(data)


# ------------------------------------------------------------------ workbook
def build():
    days, M, P = load()
    print(f"{len(days)} trading days, {days[0].date()} -> {days[-1].date()}", flush=True)
    books, rows = {}, []
    for name, family, params, fn in strategies():
        t = pd.DataFrame(fn(days, M, P))
        books[name] = t
        s = summarise(name, family, params, t)
        if s:
            s["Passed"] = bool(passed(s))
            rows.append(s)
    res = pd.DataFrame(rows)
    res["Score (weaker t-score)"] = res[["IS t-score", "OOS t-score"]].min(axis=1)
    res = res.sort_values("Score (weaker t-score)", ascending=False).reset_index(drop=True)
    luck, tested = luck_check(books)
    n_pass = int(res.Passed.sum())
    print(f"strategies tested: {len(res)} | passed: {n_pass} | luck (random direction): {min(luck)}-{max(luck)} avg {np.mean(luck):.2f}", flush=True)

    ok = res[res.Passed]
    top = ok.groupby("Family", sort=False).head(1).head(5)  # best of each family, then best 5 families
    if len(top) < 5:
        extra = res[~res.Strategy.isin(top.Strategy)].head(5 - len(top))
        top = pd.concat([top, extra])
    top = top.reset_index(drop=True)

    bx.PCT_COLS.update({"Win rate", "OOS win rate"})
    bx.RS_COLS.update({"Total Rs (1 lot)", "Rs per year (1 lot)", "Max drawdown Rs (1 lot)", "Worst trade Rs", "Rs"})
    bx.NUM_COLS.update({"Avg net pts", "Profit factor", "Avg cost pts", "Avg gross pts", "IS avg net pts", "IS t-score", "OOS avg net pts",
                        "OOS t-score", "Score (weaker t-score)", "net pts", "gross pts", "cost pts", "entry", "exit"})
    wb = Workbook()
    ws = wb.active
    ws.title = "Best_5"
    ws["A1"] = ("Best 5 NIFTY intraday strategies (5-min data, Apr 2017 - Oct 2026, traded as futures)" if n_pass >= 5 else
                f"NO strategy is proven: {n_pass} of {len(res)} passed. Below are the {len(top)} closest, shown for reference only")
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = (f"Found on trades before {SPLIT.date()}, confirmed on trades after it. Costs: {SLIP:g} pt slippage per side + futures charges. "
                f"1 lot = {LOT}. Not financial advice.")
    ws["A2"].font = Font(italic=True, color="7F7F7F")
    ws["A3"] = (f"Luck check: {n_pass} of {len(res)} strategies passed. With RANDOM trade directions the same test passes {min(luck)}-{max(luck)} "
                f"of {tested} (average {np.mean(luck):.1f}) in {len(luck)} simulations.")
    ws["A3"].font = Font(bold=True, color="C00000")
    show = top.drop(columns=["Score (weaker t-score)"])
    bx.write_df(ws, show, row=5)
    ws.row_dimensions[5].height = 45
    for r in ws.iter_rows(min_row=6, min_col=3, max_col=3):
        for c in r:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    bx.finish(ws, freeze="B6", widths={"A": 56, "B": 24, "C": 52})

    ws = wb.create_sheet("All_Strategies")
    ws["A1"] = f"All {len(res)} strategies tested, ranked by the weaker of the discovery and validation t-scores"
    ws["A1"].font = Font(bold=True)
    bx.write_df(ws, res, row=3)
    ws.auto_filter.ref = f"A3:{bx.get_column_letter(res.shape[1])}{3 + len(res)}"
    bx.finish(ws, freeze="B4", widths={"A": 56, "C": 40})

    ws = wb.create_sheet("Best_Yearly")
    ws["A1"] = "Profit / loss per calendar year, 1 lot (Rs), after costs"
    ws["A1"].font = Font(bold=True)
    yr = []
    for name in top.Strategy:
        t = books[name]
        y = (t["net pts"] * LOT).groupby(t.date.dt.year).sum()
        yr.append({"Strategy": name, **y.to_dict()})
    yr = pd.DataFrame(yr)
    yr = yr[["Strategy"] + sorted(c for c in yr.columns if c != "Strategy")]
    yr.columns = [str(c) for c in yr.columns]
    bx.write_df(ws, yr, row=3, rs_all=True)
    for r in ws.iter_rows(min_row=4, min_col=2):
        for c in r:
            c.number_format = bx.RS_FMT
    ws.conditional_formatting.add(f"B4:{bx.get_column_letter(yr.shape[1])}{3 + len(yr)}", ColorScaleRule(
        start_type="num", start_value=-100000, start_color="F8696B", mid_type="num", mid_value=0, mid_color="FFFFFF",
        end_type="num", end_value=100000, end_color="63BE7B"))
    bx.finish(ws, freeze="B4", widths={"A": 56})

    ws = wb.create_sheet("Best_Weekday")
    ws["A1"] = "Average net points per trade by weekday for the best 5"
    ws["A1"].font = Font(bold=True)
    wd = []
    for name in top.Strategy:
        t = books[name]
        g = t.groupby(t.date.dt.dayofweek)["net pts"].mean()
        wd.append({"Strategy": name, **{["Mon", "Tue", "Wed", "Thu", "Fri"][k]: v for k, v in g.items()}})
    bx.write_df(ws, pd.DataFrame(wd), row=3)
    for r in ws.iter_rows(min_row=4, min_col=2):
        for c in r:
            c.number_format = "0.0"
    bx.finish(ws, freeze="B4", widths={"A": 56})

    ws = wb.create_sheet("Best_Trades")
    allt = pd.concat([books[n] for n in top.Strategy])
    allt["Rs"] = allt["net pts"] * LOT
    bx.write_df(ws, allt, row=1)
    ws.auto_filter.ref = ws.dimensions
    bx.finish(ws, freeze="C2", widths={"A": 56, "C": 40})

    ws = wb.create_sheet("Assumptions")
    lines = [
        ("Data", "Dhan 5-minute NIFTY 50 index candles, 3 Apr 2017 - 30 Oct 2026, 2,358 complete sessions (75 bars). Daily ATR / EMA from Dhan daily candles; expiry dates from NSE bhavcopies."),
        ("Instrument", "Intraday strategies are traded on the index as if it were NIFTY futures. Overnight strategies use the REAL continuous near-month futures (close to next open, roll days excluded), because the index open is not tradable: futures basis is about 7-8 points lower at the open than at the close. Real futures need about Rs 1.2-1.5 lakh margin per lot, so Rs 25,000 cannot hold one lot."),
        ("Execution", "Signal on a bar's close, entry at the next bar's open. Stops and targets are checked on bar highs/lows; a stop that gaps through fills at the open. All intraday trades exit at 15:15 (bars after 15:15 are frozen in 2026 data)."),
        ("Costs", f"{SLIP:g} point slippage per side plus futures charges: STT 0.0125% on sell (0.02% from Oct 2024), exchange fees, brokerage, stamp duty and GST, about 0.021% of price round trip (0.027% from Oct 2024)."),
        ("Lot size", f"Fixed at {LOT} so years are comparable. Real lot sizes were 75, 50, 25 and 65 at different times."),
        ("Families", "Opening range breakout (15/30/60-min range, both/long/short, trend filter, two stops); Gap follow/fade (3 sizes, with/without stop); Intraday momentum (3 windows, with/without gap, 2 thresholds, with/without stop); Late-day momentum (3 signals, follow/fade); Overnight (6 rules); Expiry-day momentum (3 thresholds, 2 entry times, follow/fade). All fixed before looking at results."),
        ("Selection", f"Passed = at least {MIN_IS} trades and t-score >= 2 before {SPLIT.date()}, at least {MIN_OOS} trades, t-score >= 1 and positive average after it. The best 5 are the best passing strategy of each family (so they are not copies of each other), ranked by the weaker of the two t-scores."),
        ("Luck check", "Every trade's direction is flipped at random (same volatility, costs, trade counts, no skill) and the whole pass test repeated 300 times. If random strategies pass nearly as often as the real ones, the passes are mostly luck."),
        ("Caveats", "Even a pass is a historical pattern, not a promise. Winners were picked from ~100 candidates, so their validation numbers are slightly optimistic. Paper-trade first. Not financial advice."),
    ]
    ws.append(["Topic", "Detail"])
    for c in ws[1]:
        c.fill, c.font = bx.HEAD_FILL, bx.HEAD_FONT
    for l in lines:
        ws.append(list(l))
    for r in ws.iter_rows(min_row=2):
        r[0].font = Font(bold=True)
        r[1].alignment = Alignment(wrap_text=True, vertical="top")
    ws.column_dimensions["A"].width = 18
    ws.column_dimensions["B"].width = 125
    wb.save(OUT_FILE)
    print("saved", OUT_FILE)
    return res, top, luck, books


if __name__ == "__main__":
    pd.set_option("display.width", 260, "display.max_columns", 30, "display.max_colwidth", 60)
    res, top, luck, books = build()
    print(top[["Strategy", "Trades", "Win rate", "Avg net pts", "Profit factor", "Total Rs (1 lot)", "Max drawdown Rs (1 lot)",
               "IS t-score", "OOS t-score", "OOS avg net pts", "Passed"]].round(3).to_string(index=False))
    print(res.groupby("Family").agg(strategies=("Strategy", "size"), passed=("Passed", "sum"), best_score=("Score (weaker t-score)", "max")).round(2).to_string())

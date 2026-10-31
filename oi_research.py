"""Do the popular option-chain / OI beliefs hold on Nifty, 2001-2026?

Uses data/nifty_daily_candles_oi.csv (python nifty_daily_oi.py). Writes Nifty_OI_Research.xlsx.
Every test has a fair comparison and is split into an early and a late period.
"""
import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

import build_excel as bx

SPLIT_DATE = pd.Timestamp("2016-01-01")
WEEKLY_FROM = pd.Timestamp("2019-02-11")
MIN_STRIKES = 10
HORIZONS = [1, 5, 10]
OUT_FILE = "Nifty_OI_Research.xlsx"


def load():
    t = pd.read_csv("data/nifty_daily_candles_oi.csv", parse_dates=["Date", "Near expiry", "Fut expiry"]).set_index("Date")
    return t[t["Strikes listed"] >= MIN_STRIKES]


def tstat(x):
    x = pd.Series(x).dropna()
    return x.mean() / x.std() * np.sqrt(len(x)) if len(x) > 2 and x.std() > 0 else np.nan


def period(d):
    return np.where(d < SPLIT_DATE, "2001-2015", "2016-2026")


def regime(d):
    return np.where(d < WEEKLY_FROM, "Monthly expiry era", "Weekly expiry era")


# ---------------------------------------------------------------- expiry cycles (questions 1-3)
def cycles(t):
    """One observation per expiry, taken at two moments: when the cycle starts and 1 day before expiry."""
    dates = t.index
    pos = {d: i for i, d in enumerate(dates)}
    exps = sorted(e for e in t["Near expiry"].unique() if e in pos)
    rows = []
    for prev, exp in zip(exps, exps[1:]):
        for when, start in (("Cycle start", prev), ("1 day before expiry", dates[pos[exp] - 1])):
            if start not in pos or t.at[start, "Near expiry"] != exp:
                continue
            r = t.loc[start]
            path = t.iloc[pos[start] + 1:pos[exp] + 1]
            if path.empty or not r["ATM straddle"] > 0:
                continue
            c, s = r.Close, r["ATM straddle"]
            rows.append({
                "When": when, "Start": start, "Expiry": exp, "Trading days": len(path), "Close": c, "Straddle": s,
                "Resistance": r["Max CE OI strike (resistance)"], "Support": r["Max PE OI strike (support)"],
                "Max pain": r["Max pain (near)"], "PCR": r["PCR (near)"],
                "High to expiry": path.High.max(), "Low to expiry": path.Low.min(), "Expiry close": path.Close.iloc[-1],
            })
    c = pd.DataFrame(rows)
    c["Period"], c["Era"] = period(c.Start), regime(c.Start)
    c["R dist (moves)"] = (c.Resistance - c.Close) / c.Straddle
    c["S dist (moves)"] = (c.Close - c.Support) / c.Straddle
    c["Up reach (moves)"] = (c["High to expiry"] - c.Close) / c.Straddle
    c["Down reach (moves)"] = (c.Close - c["Low to expiry"]) / c.Straddle
    c["Expiry move (moves)"] = (c["Expiry close"] - c.Close) / c.Straddle
    c["Max pain pull (moves)"] = (c["Max pain"] - c.Close) / c.Straddle
    return c


def level_test(c, side):
    """Is the max-OI strike touched / closed beyond LESS often than any level at the same distance?"""
    dist, reach = ("R dist (moves)", "Up reach (moves)") if side == "resistance" else ("S dist (moves)", "Down reach (moves)")
    sign = 1 if side == "resistance" else -1
    groups = list(c.groupby(["When", "Period"])) + list(c.groupby(["When", "Era"]))
    return pd.DataFrame([_level_row(g, dist, reach, sign, keys) for keys, g in groups])


def _level_row(g, dist, reach, sign, keys):
    ok = g[g[dist] > 0]  # only levels still ahead of price
    reach_all = g[reach].to_numpy()
    close_all = (sign * g["Expiry move (moves)"]).to_numpy()
    # baseline: chance that ANY level that far away gets touched / closed beyond, from the same cycles
    touch_base = np.array([(reach_all >= d).mean() for d in ok[dist]])
    close_base = np.array([(close_all >= d).mean() for d in ok[dist]])
    touched = (ok[reach] >= ok[dist]).to_numpy()
    closed = (sign * ok["Expiry move (moves)"] >= ok[dist]).to_numpy()
    n = len(ok)
    z_t = (touched.sum() - touch_base.sum()) / np.sqrt((touch_base * (1 - touch_base)).sum()) if n else np.nan
    z_c = (closed.sum() - close_base.sum()) / np.sqrt((close_base * (1 - close_base)).sum()) if n else np.nan
    return {"When": keys[0], "Group": keys[1], "Cycles": n, "Already crossed at start": int((g[dist] <= 0).sum()),
            "Avg distance (moves)": ok[dist].mean(),
            "Touched %": touched.mean(), "Touched % expected (any level)": touch_base.mean(), "Touch z": z_t,
            "Closed beyond at expiry %": closed.mean(), "Closed beyond % expected": close_base.mean(), "Close z": z_c}


def max_pain_test(c):
    rows = []
    for keys, g in list(c.groupby(["When", "Period"])) + list(c.groupby(["When", "Era"])):
        g = g.dropna(subset=["Max pain pull (moves)"])
        x, y = g["Max pain pull (moves)"], g["Expiry move (moves)"]
        far = g[x.abs() >= 0.25]
        hit = (np.sign(far["Max pain pull (moves)"]) == np.sign(far["Expiry move (moves)"])).mean()
        up_rate = (far["Expiry move (moves)"] > 0).mean()
        exp_hit = ((far["Max pain pull (moves)"] > 0) * up_rate + (far["Max pain pull (moves)"] < 0) * (1 - up_rate)).mean()
        slope = np.polyfit(x, y, 1)[0] if len(g) > 5 else np.nan
        resid = y - slope * x - (y.mean() - slope * x.mean())
        se = np.sqrt((resid ** 2).sum() / (len(g) - 2) / ((x - x.mean()) ** 2).sum()) if len(g) > 5 else np.nan
        rows.append({"When": keys[0], "Group": keys[1], "Cycles": len(g), "Corr(pull, move)": x.corr(y),
                     "Slope": slope, "Slope t": slope / se if se else np.nan,
                     "Cycles with max pain >= 0.25 moves away": len(far), "Moved toward max pain %": hit,
                     "Expected by chance %": exp_hit,
                     "Expiry closer to max pain than to start %": ((g["Expiry close"] - g["Max pain"]).abs() < (g["Expiry close"] - g.Close).abs()).mean()})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- daily signals (questions 4-6)
def daily_frame(t):
    d = t.copy()
    for h in HORIZONS:
        d[f"Next {h}d %"] = d.Close.shift(-h) / d.Close - 1
    fut_chg = d["Fut OI"].diff()  # total futures OI across expiries, so month-end rollover mostly nets out
    up, oi_up = d.Close.diff() > 0, fut_chg > 0
    d["Futures OI signal"] = np.select([up & oi_up, up & ~oi_up, ~up & oi_up, ~up & ~oi_up],
                                       ["Long buildup (price up, OI up)", "Short covering (price up, OI down)",
                                        "Short buildup (price down, OI up)", "Long unwinding (price down, OI down)"], "")
    d.loc[d.index == d["Fut expiry"], "Futures OI signal"] = ""  # futures expiry days distort OI
    d["Writing balance"] = (d["PE chg OI (near)"] - d["CE chg OI (near)"]) / (d["CE OI (near)"] + d["PE OI (near)"])
    d["Period"], d["Era"] = period(d.index), regime(d.index)
    return d


def bucket_test(d, col, buckets):
    """Forward returns after each bucket vs all days, non-overlapping samples for the t-score."""
    rows = []
    for name, mask in buckets.items():
        for grp_col in ("Period", "Era"):
            for grp, g in d[mask].groupby(grp_col):
                base = d[d[grp_col] == grp]
                row = {"Signal": name, "Group": grp, "Days": len(g)}
                for h in HORIZONS:
                    k = f"Next {h}d %"
                    sel = g[k].dropna()
                    keep, last = [], None
                    for i, dt in enumerate(sel.index):  # non-overlapping
                        if last is None or base.index.get_loc(dt) - base.index.get_loc(last) >= h:
                            keep.append(dt)
                            last = dt
                    ex = sel.loc[keep] - base[k].mean()
                    row[f"Avg next {h}d %"] = sel.mean()
                    row[f"All days next {h}d %"] = base[k].mean()
                    row[f"Edge {h}d %"] = sel.mean() - base[k].mean()
                    row[f"t {h}d"] = tstat(ex)
                rows.append(row)
    return pd.DataFrame(rows)


def verdict(scores, belief, threshold=2.0):
    """belief = +1 / -1: the sign of the score if the popular belief were true."""
    v = [x for x in scores if x == x]
    strong = [x for x in v if abs(x) >= threshold]
    if v and len(strong) == len(v) and len({np.sign(x) for x in v}) == 1:
        return "YES - the belief holds in both periods" if np.sign(v[0]) == belief else "OPPOSITE - the reverse happens, in both periods"
    if strong:
        side = "as believed" if np.sign(strong[0]) == belief else "the reverse of the belief"
        return f"WEAK - only in one period ({side})"
    return "NO - not different from chance"


# sign of the score if the belief were true (Q1-2 score = crossed more than chance; Q4-6 score = return edge)
BELIEF = {"Long buildup (price up, OI up)": 1, "Short covering (price up, OI down)": 1,
          "Short buildup (price down, OI up)": -1, "Long unwinding (price down, OI down)": -1,
          "Heavy CALL writing day (bottom 10%)": -1, "Heavy PUT writing day (top 10%)": 1}


# ---------------------------------------------------------------- report
def build():
    t = load()
    c = cycles(t)
    res = level_test(c, "resistance")
    sup = level_test(c, "support")
    mp = max_pain_test(c)
    d = daily_frame(t)
    is_ = d.index < SPLIT_DATE
    q = d.loc[is_, "PCR (near)"].quantile([0.1, 0.9]).to_numpy()
    pcr = bucket_test(d, "PCR (near)", {
        f"PCR very low (bottom 10%, < {q[0]:.2f})": d["PCR (near)"] < q[0],
        f"PCR very high (top 10%, > {q[1]:.2f})": d["PCR (near)"] > q[1]})
    fut = bucket_test(d, "Futures OI signal", {s: d["Futures OI signal"] == s for s in sorted(set(d["Futures OI signal"]) - {""})})
    wq = d.loc[is_, "Writing balance"].quantile([0.1, 0.9]).to_numpy()
    wr = bucket_test(d, "Writing balance", {
        "Heavy CALL writing day (bottom 10%)": d["Writing balance"] < wq[0],
        "Heavy PUT writing day (top 10%)": d["Writing balance"] > wq[1]})

    def per(df, col, when=None):
        x = df if when is None else df[df.When == when]
        return x[x.Group.isin(["2001-2015", "2016-2026"])][col].tolist()

    answers = []
    for when in ("Cycle start", "1 day before expiry"):
        r = res[(res.When == when) & res.Group.isin(["2001-2015", "2016-2026"])]
        answers.append({"#": 1, "Question": f"Max CALL OI strike acts as resistance ({when})",
                        "What we found": f"Touched {r['Touched %'].mean():.0%} of the time vs {r['Touched % expected (any level)'].mean():.0%} for any level that far away; "
                                         f"closed above at expiry {r['Closed beyond at expiry %'].mean():.0%} vs {r['Closed beyond % expected'].mean():.0%}.",
                        "Belief": "Price stays below it", "Verdict": verdict(per(res, "Close z", when), -1)})
        s = sup[(sup.When == when) & sup.Group.isin(["2001-2015", "2016-2026"])]
        answers.append({"#": 2, "Question": f"Max PUT OI strike acts as support ({when})",
                        "What we found": f"Touched {s['Touched %'].mean():.0%} vs {s['Touched % expected (any level)'].mean():.0%} expected; "
                                         f"closed below at expiry {s['Closed beyond at expiry %'].mean():.0%} vs {s['Closed beyond % expected'].mean():.0%}.",
                        "Belief": "Price stays above it", "Verdict": verdict(per(sup, "Close z", when), -1)})
        m = mp[(mp.When == when) & mp.Group.isin(["2001-2015", "2016-2026"])]
        answers.append({"#": 3, "Question": f"Price is pulled toward max pain by expiry ({when})",
                        "What we found": f"Moved toward max pain {m['Moved toward max pain %'].mean():.0%} of the time vs {m['Expected by chance %'].mean():.0%} by chance; "
                                         f"correlation {m['Corr(pull, move)'].mean():.2f}.",
                        "Belief": "Expiry close drifts toward max pain", "Verdict": verdict(per(mp, "Slope t", when), 1)})
    for df, num, label in ((pcr, 4, "PCR extreme"), (fut, 5, "Futures OI"), (wr, 6, "Option writing")):
        for sig, g in df.groupby("Signal", sort=False):
            gg = g[g.Group.isin(["2001-2015", "2016-2026"])]
            belief = BELIEF.get(sig, 1 if "high" in sig else -1)  # common reading: high PCR bullish, low PCR bearish
            answers.append({"#": num, "Question": f"{label}: {sig} -> next 5 days",
                            "What we found": "; ".join(f"{r.Group}: {r['Avg next 5d %']:+.2%} vs {r['All days next 5d %']:+.2%} normal" for _, r in gg.iterrows()),
                            "Belief": "Bullish" if belief > 0 else "Bearish", "Verdict": verdict(gg["t 5d"].tolist(), belief)})
    ans = pd.DataFrame(answers)

    pct = [c_ for c_ in set(res.columns) | set(sup.columns) | set(mp.columns) | set(pcr.columns) if "%" in c_]
    bx.PCT_COLS.update(pct)
    bx.NUM_COLS.update({"Touch z", "Close z", "Avg distance (moves)", "Corr(pull, move)", "Slope", "Slope t", "t 1d", "t 5d", "t 10d"})
    wb = Workbook()
    ws = wb.active
    ws.title = "Answers"
    ws["A1"] = "Do option-chain / OI beliefs hold for Nifty? (2001-2026, end-of-day NSE data)"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = ("YES needs a |score| >= 2 in the same direction in BOTH 2001-2015 and 2016-2026. Distances are measured in "
                "'moves' = that day's ATM straddle, so calm and wild markets compare fairly. Not financial advice.")
    ws["A2"].font = Font(italic=True, color="7F7F7F")
    bx.write_df(ws, ans, row=4)
    for r in ws.iter_rows(min_row=5, min_col=3, max_col=3):
        for cell in r:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    bx.finish(ws, freeze="A5", widths={"A": 4, "B": 60, "C": 100, "D": 34, "E": 48})

    for title, df, note in (
        ("Q1_Resistance", res, "Max CALL OI strike: how often it was touched / closed above, vs any level the same distance away (same cycles)."),
        ("Q2_Support", sup, "Max PUT OI strike: how often it was touched / closed below, vs any level the same distance away."),
        ("Q3_MaxPain", mp, "Does the expiry move follow the direction of max pain? Slope / correlation of expiry move on max-pain pull."),
        ("Q4_PCR", pcr, "Forward Nifty returns after extreme put-call ratio days vs all days. t uses non-overlapping samples."),
        ("Q5_Futures_OI", fut, "Forward returns after each futures price/OI combination (futures expiry days excluded)."),
        ("Q6_Writing", wr, "Forward returns after days when much more put OI (or call OI) was added than the other side."),
    ):
        ws = wb.create_sheet(title)
        ws["A1"] = note
        ws["A1"].font = Font(bold=True)
        bx.write_df(ws, df, row=3)
        ws.row_dimensions[3].height = 45
        bx.finish(ws, freeze="C4", widths={"A": 36})

    ws = wb.create_sheet("Cycles_Data")
    bx.write_df(ws, c, row=1)
    ws.auto_filter.ref = ws.dimensions
    bx.finish(ws, freeze="C2")

    ws = wb.create_sheet("Method")
    lines = [
        ("Data", "One row per day from Nifty_Daily_Candles_OI.xlsx (NSE bhavcopy OI, Yahoo / near-month futures candles). Days with fewer than 10 strikes listed (2001-2002) are dropped."),
        ("Cycles", "For every expiry, the chain is read at the start of the cycle (previous expiry day) and 1 trading day before expiry. 'Touched' uses the highs/lows until expiry; 'closed beyond' uses the expiry-day close."),
        ("Fair comparison (Q1-Q2)", "For each cycle, the chance that ANY level at the same distance gets touched is estimated from all cycles in the same group. If max-OI strikes were real walls, they would be crossed less often than that. z < -2 = real resistance/support."),
        ("Max pain (Q3)", "Pull = (max pain - close) in moves. A real pull gives a positive slope / correlation between pull and the move to expiry, and 'moved toward' well above chance."),
        ("Daily signals (Q4-Q6)", "Next 1/5/10-day Nifty return after the signal minus the average for all days in the same period. Cut-offs (10% / 90%) come from 2001-2015 only. t-scores use non-overlapping days."),
        ("Verdict", "YES = the popular belief holds with a score beyond 2 in both halves of history. OPPOSITE = the reverse of the belief holds in both halves. WEAK = only one half. NO = indistinguishable from chance."),
        ("Limits", "End-of-day data. Intraday reactions at OI levels are not visible. Monthly expiries before Feb 2019, weekly after. Not financial advice."),
    ]
    ws.append(["Topic", "Detail"])
    for cell in ws[1]:
        cell.fill, cell.font = bx.HEAD_FILL, bx.HEAD_FONT
    for l in lines:
        ws.append(list(l))
    for r in ws.iter_rows(min_row=2):
        r[0].font = Font(bold=True)
        r[1].alignment = Alignment(wrap_text=True, vertical="top")
    ws.column_dimensions["A"].width = 24
    ws.column_dimensions["B"].width = 120
    wb.save(OUT_FILE)
    print("saved", OUT_FILE)
    return ans, res, sup, mp, pcr, fut, wr


if __name__ == "__main__":
    pd.set_option("display.width", 250, "display.max_columns", 30, "display.max_colwidth", 120)
    ans, res, sup, mp, pcr, fut, wr = build()
    print(ans.to_string(index=False))

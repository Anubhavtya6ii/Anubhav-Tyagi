"""Mine daily candle patterns: after each pattern, what happened next and how far did price go?

Discovery on the first 60% of each asset's history, validation on the last 40%.
The 10 best patterns that survive validation become trade rules, backtested from Rs 25,000.
Run `python fetch_data.py` first, then `python pattern_research.py` -> Candle_Pattern_Strategies.xlsx
"""
import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Font

import build_excel as bx

ASSETS = ["NIFTY50", "SENSEX", "BANKNIFTY", "BITCOIN", "ETHEREUM"]
GROUP = {"NIFTY50": "India", "SENSEX": "India", "BANKNIFTY": "India", "BITCOIN": "Crypto", "ETHEREUM": "Crypto"}
HORIZONS = [1, 3, 5, 10, 20]
FILTERS = ["any trend", "above 200 SMA", "below 200 SMA"]
SPLIT = 0.6
COST = 0.002  # round trip (0.1% per side)
STOP_ATR = 2.0
CAPITAL = 25_000
MIN_EVENTS = 25
OUT_FILE = "Candle_Pattern_Strategies.xlsx"

DESCRIBE = {
    "big green candle": "Body bigger than 0.8x ATR, closes up",
    "big red candle": "Body bigger than 0.8x ATR, closes down",
    "doji": "Body less than 10% of the day's range",
    "hammer": "Lower wick >= 2x body, small upper wick (rejection of lows)",
    "shooting star": "Upper wick >= 2x body, small lower wick (rejection of highs)",
    "bullish engulfing": "Green candle whose body swallows the previous red body",
    "bearish engulfing": "Red candle whose body swallows the previous green body",
    "inside bar": "High and low both inside the previous day's range",
    "outside bar up": "Takes out previous high and low, closes green",
    "outside bar down": "Takes out previous high and low, closes red",
    "NR7 (narrowest range of 7)": "Smallest high-low range of the last 7 days",
    "wide range day": "High-low range more than 2x ATR",
    "gap up": "Opens above the previous day's high",
    "gap down": "Opens below the previous day's low",
    "3 red closes in a row": "Close lower than the day before, 3 days running",
    "3 green closes in a row": "Close higher than the day before, 3 days running",
    "5 red closes in a row": "Close lower than the day before, 5 days running",
    "5 green closes in a row": "Close higher than the day before, 5 days running",
    "20-day closing high": "Highest close of the last 20 days",
    "20-day closing low": "Lowest close of the last 20 days",
    "close near day's low": "Closes in the bottom 15% of the day's range",
    "close near day's high": "Closes in the top 15% of the day's range",
    "crash day (-2 ATR)": "Close falls more than 2x ATR from the previous close",
    "surge day (+2 ATR)": "Close rises more than 2x ATR from the previous close",
    "RSI(2) below 5": "2-day RSI below 5 (deeply oversold)",
    "RSI(2) above 95": "2-day RSI above 95 (deeply overbought)",
    "close below lower Bollinger": "Close under the 20-day, 2-std lower band",
    "close above upper Bollinger": "Close over the 20-day, 2-std upper band",
    "golden cross (50>200)": "50-day SMA crosses above the 200-day SMA",
    "death cross (50<200)": "50-day SMA crosses below the 200-day SMA",
    "cross above 20 EMA": "Close moves from below to above the 20-day EMA",
    "cross below 20 EMA": "Close moves from above to below the 20-day EMA",
    "month-end day": "Last trading day of the month",
    "5-day closing low": "Lowest close of the last 5 days",
}


# ---------------------------------------------------------------- data + patterns
def load(name):
    d = pd.read_csv(f"data/{name}.csv", index_col="Date", parse_dates=True)
    return d[(d.Open > 0) & (d.High >= d.Low)]


def rsi(s, n):
    ch = s.diff()
    up = ch.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-ch.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def patterns(d):
    o, h, l, c = d.Open, d.High, d.Low, d.Close
    po, ph, pl, pc = o.shift(), h.shift(), l.shift(), c.shift()
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    patr = atr.shift()
    body, rng = c - o, (h - l).replace(0, np.nan)
    upper, lower = h - np.maximum(o, c), np.minimum(o, c) - l
    pos = (c - l) / rng
    sma50, sma200, ema20 = c.rolling(50).mean(), c.rolling(200).mean(), c.ewm(span=20, adjust=False).mean()
    mid, sd = c.rolling(20).mean(), c.rolling(20).std()
    r2 = rsi(c, 2)
    up_day, dn_day = c > pc, c < pc
    p = {
        "big green candle": body > 0.8 * patr,
        "big red candle": -body > 0.8 * patr,
        "doji": body.abs() < 0.1 * rng,
        "hammer": (lower >= 2 * body.abs()) & (upper <= 0.3 * rng) & (rng > 0.5 * patr),
        "shooting star": (upper >= 2 * body.abs()) & (lower <= 0.3 * rng) & (rng > 0.5 * patr),
        "bullish engulfing": (c > o) & (pc < po) & (o <= pc) & (c >= po),
        "bearish engulfing": (c < o) & (pc > po) & (o >= pc) & (c <= po),
        "inside bar": (h < ph) & (l > pl),
        "outside bar up": (h > ph) & (l < pl) & (c > o),
        "outside bar down": (h > ph) & (l < pl) & (c < o),
        "NR7 (narrowest range of 7)": (h - l) == (h - l).rolling(7).min(),
        "wide range day": (h - l) > 2 * patr,
        "gap up": o > ph,
        "gap down": o < pl,
        "3 red closes in a row": dn_day.rolling(3).sum() == 3,
        "3 green closes in a row": up_day.rolling(3).sum() == 3,
        "5 red closes in a row": dn_day.rolling(5).sum() == 5,
        "5 green closes in a row": up_day.rolling(5).sum() == 5,
        "20-day closing high": c >= c.rolling(20).max(),
        "20-day closing low": c <= c.rolling(20).min(),
        "close near day's low": pos < 0.15,
        "close near day's high": pos > 0.85,
        "crash day (-2 ATR)": (c - pc) < -2 * patr,
        "surge day (+2 ATR)": (c - pc) > 2 * patr,
        "RSI(2) below 5": r2 < 5,
        "RSI(2) above 95": r2 > 95,
        "close below lower Bollinger": c < mid - 2 * sd,
        "close above upper Bollinger": c > mid + 2 * sd,
        "golden cross (50>200)": (sma50 > sma200) & (sma50.shift() <= sma200.shift()),
        "death cross (50<200)": (sma50 < sma200) & (sma50.shift() >= sma200.shift()),
        "cross above 20 EMA": (c > ema20) & (pc <= ema20.shift()),
        "cross below 20 EMA": (c < ema20) & (pc >= ema20.shift()),
        "month-end day": pd.Series(d.index.month, index=d.index) != pd.Series(d.index.month, index=d.index).shift(-1),
        "5-day closing low": c <= c.rolling(5).min(),
    }
    warm = sma200.notna()
    p = {k: (v.fillna(False).astype(bool) & warm) for k, v in p.items()}
    trend = {"any trend": warm, "above 200 SMA": c > sma200, "below 200 SMA": c < sma200}
    return p, trend, atr


def forward(d, n):
    """Long trade entered at next open, held n days: close return, best high (MFE), worst low (MAE)."""
    entry = d.Open.shift(-1)
    exit_ = d.Close.shift(-n)
    hi = d.High[::-1].rolling(n).max()[::-1].shift(-1)
    lo = d.Low[::-1].rolling(n).min()[::-1].shift(-1)
    return exit_ / entry - 1, hi / entry - 1, lo / entry - 1


def non_overlapping(mask, n):
    idx, last = [], -10**9
    for i in np.flatnonzero(mask.to_numpy()):
        if i - last >= n:
            idx.append(i)
            last = i
    return idx


def tstat(x):
    return x.mean() / x.std() * np.sqrt(len(x)) if len(x) > 2 and x.std() > 0 else np.nan


# ---------------------------------------------------------------- research
def research():
    atlas, cands, ctx = [], [], {}
    for a in ASSETS:
        d = load(a)
        pats, trends, atr = patterns(d)
        fw = {n: forward(d, n) for n in HORIZONS}
        cut = d.index[int(len(d) * SPLIT)]
        ctx[a] = (d, pats, trends, atr, cut)
        for name, m in pats.items():
            for filt, tmask in trends.items():
                mask = m & tmask
                for n in HORIZONS:
                    ret, mfe, mae = fw[n]
                    ok = mask & ret.notna()
                    base = ret[tmask & ret.notna()]
                    row = {"Asset": a, "Pattern": name, "Trend filter": filt, "Hold days": n}
                    for part, sel in (("IS", ok & (d.index < cut)), ("OOS", ok & (d.index >= cut))):
                        ev = ret.iloc[non_overlapping(sel, n)]
                        b = base[(base.index < cut) if part == "IS" else (base.index >= cut)]
                        row[f"{part} events"] = len(ev)
                        row[f"{part} avg %"] = ev.mean()
                        row[f"{part} win %"] = (ev > 0).mean() if len(ev) else np.nan
                        row[f"{part} edge vs random %"] = ev.mean() - b.mean()
                        row[f"{part} t long"] = tstat(ev - b.mean())
                        row[f"{part} t short"] = tstat(-ev)
                    if filt == "any trend":
                        ev_all = non_overlapping(ok, n)
                        atlas.append({
                            "Asset": a, "Pattern": name, "Hold days": n, "Times seen": len(ev_all),
                            "Avg move %": ret.iloc[ev_all].mean(), "Up %": (ret.iloc[ev_all] > 0).mean() if ev_all else np.nan,
                            "Random-day avg %": ret[trends["any trend"]].mean(),
                            "Median best (high) %": mfe.iloc[ev_all].median(), "Median worst (low) %": mae.iloc[ev_all].median(),
                        })
                    cands.append(row)
    return pd.DataFrame(atlas), pd.DataFrame(cands), ctx


def select(c):
    """Pick a direction per candidate on in-sample data, keep the ones that hold up out of sample."""
    c = c[(c["IS events"] >= MIN_EVENTS) & (c["OOS events"] >= 10)].copy()
    c["Direction"] = np.where(c["IS t long"] >= c["IS t short"], "LONG", "SHORT")
    long_ = c.Direction == "LONG"
    c["IS score"] = np.where(long_, c["IS t long"], c["IS t short"])
    c["IS net %"] = np.where(long_, c["IS avg %"], -c["IS avg %"]) - COST
    c["OOS net %"] = np.where(long_, c["OOS avg %"], -c["OOS avg %"]) - COST
    c["OOS beats random"] = np.where(long_, c["OOS edge vs random %"] > 0, c["OOS avg %"] < 0)
    c["Passed"] = (c["IS score"] >= 2) & (c["IS net %"] > 0) & (c["OOS net %"] > 0) & c["OOS beats random"]
    c = c.sort_values("IS score", ascending=False)
    picked, seen = [], set()
    for i, r in c[c.Passed].iterrows():
        key = (GROUP[r.Asset], r.Pattern, r.Direction)
        if key in seen:
            continue
        seen.add(key)
        picked.append(i)
        if len(picked) == 10:
            break
    return c, c.loc[picked]


def null_test(runs=6):
    """Same test with every pattern moved to random dates: how many 'pass' by pure luck?"""
    global patterns
    real, counts = patterns, []
    for seed in range(runs):
        rng = np.random.default_rng(seed)

        def shuffled(d):
            p, trend, atr = real(d)
            return {k: pd.Series(np.roll(v.to_numpy(), rng.integers(300, len(v) - 300)), index=v.index) & trend["any trend"]
                    for k, v in p.items()}, trend, atr

        patterns = shuffled
        counts.append(int(select(research()[1])[0].Passed.sum()))
    patterns = real
    return counts


# ---------------------------------------------------------------- trade the top 10
def trade(ctx, r):
    d, pats, trends, atr, cut = ctx[r.Asset]
    mask = (pats[r.Pattern] & trends[r["Trend filter"]]).to_numpy()
    o, h, l, c, a = d.Open.to_numpy(), d.High.to_numpy(), d.Low.to_numpy(), d.Close.to_numpy(), atr.to_numpy()
    n, side = int(r["Hold days"]), 1 if r.Direction == "LONG" else -1
    rows, i = [], 0
    while i < len(d) - n - 1:
        if not mask[i]:
            i += 1
            continue
        e = o[i + 1]
        stop = e - side * STOP_ATR * a[i]
        exit_px, exit_i, how = c[i + n], i + n, "time"
        for j in range(i + 1, i + n + 1):
            if (side == 1 and l[j] <= stop) or (side == -1 and h[j] >= stop):
                gap_through = (side == 1 and o[j] < stop) or (side == -1 and o[j] > stop)
                exit_px, exit_i, how = (o[j] if gap_through and j > i + 1 else stop), j, "stop"
                break
        ret = side * (exit_px / e - 1) - COST
        far = (h[i + 1:i + n + 1].max() / e - 1) if side == 1 else (1 - l[i + 1:i + n + 1].min() / e)
        rows.append({"Signal day": d.index[i].date(), "Entry": d.index[i + 1].date(), "Exit": d.index[exit_i].date(),
                     "Entry price": e, "Exit price": exit_px, "Exit type": how, "Return": ret,
                     "Best move in favour": far, "Out-of-sample": d.index[i] >= cut})
        i = exit_i + 1
    t = pd.DataFrame(rows)
    t["Equity Rs"] = CAPITAL * (1 + t.Return).cumprod()
    return t


def summarise(name, r, t, d):
    years = (d.index[-1] - d.index[200]).days / 365.25
    eq = t["Equity Rs"]
    o = t[t["Out-of-sample"]]
    hit = {f"Hit {k}% in favour": (t["Best move in favour"] >= k / 100).mean() for k in (1, 2, 3, 5)}
    return {
        "#": name, "Asset": r.Asset, "Direction": r.Direction, "Pattern": r.Pattern, "Trend filter": r["Trend filter"],
        "Hold days": int(r["Hold days"]),
        "Rule": (f"{r.Direction} at next day's open after '{r.Pattern}' ({DESCRIBE[r.Pattern]})"
                 + ("" if r["Trend filter"] == "any trend" else f", only when price is {r['Trend filter']}")
                 + f". Exit at the close after {int(r['Hold days'])} day(s), or stop {STOP_ATR:g}x ATR away."),
        "Trades": len(t), "Win Rate": (t.Return > 0).mean(), "Avg trade %": t.Return.mean(),
        "Profit Factor": t.Return[t.Return > 0].sum() / -t.Return[t.Return < 0].sum() if (t.Return < 0).any() else np.nan,
        "Final Rs": eq.iloc[-1], "CAGR": (eq.iloc[-1] / CAPITAL) ** (1 / years) - 1,
        "Max Drawdown": (eq / eq.cummax().clip(lower=CAPITAL) - 1).min(),
        "OOS trades": len(o), "OOS win rate": (o.Return > 0).mean() if len(o) else np.nan,
        "OOS avg trade %": o.Return.mean() if len(o) else np.nan,
        "Median best move %": t["Best move in favour"].median(), **hit,
        "Stops hit": int((t["Exit type"] == "stop").sum()),
        "In-sample t-score": r["IS score"],
    }


# ---------------------------------------------------------------- excel
def build():
    luck = null_test()
    atlas, cands, ctx = research()
    scored, top = select(cands)
    summ, trades = [], {}
    for k, (_, r) in enumerate(top.iterrows(), 1):
        t = trade(ctx, r)
        trades[k] = (r, t)
        summ.append(summarise(k, r, t, ctx[r.Asset][0]))
    s = pd.DataFrame(summ)

    pct = {"Win Rate", "Avg trade %", "CAGR", "Max Drawdown", "OOS win rate", "OOS avg trade %", "Median best move %",
           "Hit 1% in favour", "Hit 2% in favour", "Hit 3% in favour", "Hit 5% in favour", "Avg move %", "Up %",
           "Random-day avg %", "Median best (high) %", "Median worst (low) %", "Return", "Best move in favour",
           "IS avg %", "IS win %", "IS edge vs random %", "OOS avg %", "OOS win %", "OOS edge vs random %", "IS net %", "OOS net %"}
    bx.PCT_COLS.update(pct)
    bx.RS_COLS.update({"Final Rs", "Equity Rs"})
    bx.NUM_COLS.update({"Profit Factor", "In-sample t-score", "IS t long", "IS t short", "OOS t long", "OOS t short", "IS score",
                        "Entry price", "Exit price"})
    wb = Workbook()

    ws = wb.active
    ws.title = "Top_Strategies"
    ws["A1"] = "Candle-pattern strategies found in the data (discovered on the first 60%, confirmed on the last 40%)"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = ("Rs 25,000 traded with each rule on its own, full capital per trade, 0.2% round-trip cost. "
                "Past results, not a promise; not financial advice. Short trades need futures/perpetuals.")
    ws["A2"].font = Font(italic=True, color="7F7F7F")
    ws["A3"] = (f"Luck check: real patterns -> {int(scored.Passed.sum())} candidates passed. Same test on RANDOM dates -> "
                f"{min(luck)}-{max(luck)} passed (avg {np.mean(luck):.0f}). So these daily patterns are not clearly better than chance; "
                f"only {len(s)} distinct strategies survived. Treat them as ideas to paper-trade, not proven edges.")
    ws["A3"].font = Font(bold=True, color="C00000")
    bx.write_df(ws, s, row=5)
    ws.row_dimensions[5].height = 45
    for row in ws.iter_rows(min_row=6, min_col=7, max_col=7):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    bx.finish(ws, freeze="E6", widths={"D": 26, "G": 70})

    ws = wb.create_sheet("Top10_Yearly")
    ws["A1"] = "Year-by-year return of each of the 10 strategies (sum of trade returns that started that year)"
    ws["A1"].font = Font(bold=True)
    y = []
    for k, (r, t) in trades.items():
        yr = t.groupby(pd.to_datetime(t.Entry).dt.year).Return.apply(lambda x: (1 + x).prod() - 1)
        y.append({"#": k, "Strategy": f"{r.Asset} {r.Direction} {r.Pattern}", **yr.to_dict()})
    y = pd.DataFrame(y)
    y = y[["#", "Strategy"] + sorted(c for c in y.columns if isinstance(c, int))]
    y.columns = [str(c) for c in y.columns]
    bx.write_df(ws, y, row=3, pct_all=True, color=True)
    bx.finish(ws, freeze="C4", widths={"B": 50})

    ws = wb.create_sheet("Top10_Trades")
    allt = pd.concat([t.assign(**{"#": k, "Strategy": f"{r.Asset} {r.Direction} {r.Pattern}"}) for k, (r, t) in trades.items()])
    allt = allt[["#", "Strategy"] + [c for c in allt.columns if c not in ("#", "Strategy")]]
    bx.write_df(ws, allt, row=1)
    ws.auto_filter.ref = ws.dimensions
    bx.finish(ws, freeze="C2", widths={"B": 50})

    ws = wb.create_sheet("Pattern_Atlas")
    ws["A1"] = ("After each candle pattern: how much did price move over the next N days, how often was it up, "
                "and how far did it go (median best high / worst low)? Compare with a random day.")
    ws["A1"].font = Font(bold=True)
    atl = atlas.copy()
    atl.insert(2, "What it means", atl.Pattern.map(DESCRIBE))
    bx.write_df(ws, atl, row=3)
    ws.auto_filter.ref = f"A3:{bx.get_column_letter(atl.shape[1])}{3 + len(atl)}"
    ws.conditional_formatting.add(f"F4:F{3 + len(atl)}", ColorScaleRule(
        start_type="num", start_value=-0.02, start_color="F8696B", mid_type="num", mid_value=0,
        mid_color="FFFFFF", end_type="num", end_value=0.02, end_color="63BE7B"))
    bx.finish(ws, freeze="D4", widths={"B": 28, "C": 50})

    ws = wb.create_sheet("All_Candidates")
    ws["A1"] = f"Every pattern x trend filter x hold period tested ({len(scored)} with enough events). 'Passed' = strong in-sample AND still profitable after costs out-of-sample."
    ws["A1"].font = Font(bold=True)
    bx.write_df(ws, scored, row=3)
    ws.auto_filter.ref = f"A3:{bx.get_column_letter(scored.shape[1])}{3 + len(scored)}"
    bx.finish(ws, freeze="C4", widths={"B": 28})

    ws = wb.create_sheet("Assumptions")
    passed = int(scored.Passed.sum())
    lines = [
        ("Data", "Daily OHLC from Yahoo Finance: Nifty 50 (2007+), Sensex (1997+), Bank Nifty (2007+), Bitcoin (2014+), Ethereum (2017+). Crypto in USD."),
        ("Patterns", f"{len(DESCRIBE)} candle / indicator patterns x 3 trend filters x {len(HORIZONS)} hold periods ({', '.join(map(str, HORIZONS))} days) x 5 assets."),
        ("Discovery vs validation", f"Each asset's history is split at 60%. Direction and ranking come only from the first 60%. A candidate passes only if its in-sample t-score is >= 2, it beats a random entry, and it is STILL profitable after costs in the last 40%. {passed} of {len(scored)} candidates passed."),
        ("Why this matters", "Testing thousands of combinations always finds some that look great by luck. The out-of-sample check removes most of those, but because it was used as a filter, the out-of-sample numbers of the winners are still a little optimistic."),
        ("Luck check", f"Every pattern was also moved to random dates (same frequency) and the whole test re-run {len(luck)} times: {luck} candidates passed by pure luck, vs {passed} for the real patterns."),
        ("Variety", "At most one strategy per pattern + direction per market group (India indices / Crypto), so the 10 are not copies of each other."),
        ("Trade rules", f"Signal on the pattern day's close, enter at the next day's open, exit at the close after the hold period, or at a stop {STOP_ATR:g}x ATR(14) away (at the open if price gaps through it). No new trade while one is open."),
        ("Costs", f"{COST:.1%} round trip per trade (brokerage + slippage). No taxes, funding or futures roll costs."),
        ("Rs 25,000", "Each strategy is run on its own with the full Rs 25,000, compounding. Index shorts assume Nifty/Bank Nifty futures; crypto shorts assume perpetual futures."),
        ("'How far it went'", "Best move in favour = the furthest price moved your way during the hold, measured from the entry price (high for longs, low for shorts)."),
        ("Caveats", "Past patterns can stop working. Use paper trading first. Not financial advice."),
    ]
    ws.append(["Topic", "Detail"])
    for c in ws[1]:
        c.fill, c.font = bx.HEAD_FILL, bx.HEAD_FONT
    for l in lines:
        ws.append(list(l))
    for r in ws.iter_rows(min_row=2):
        r[0].font = Font(bold=True)
        r[1].alignment = Alignment(wrap_text=True, vertical="top")
    ws.column_dimensions["A"].width = 24
    ws.column_dimensions["B"].width = 120

    wb.save(OUT_FILE)
    print("saved", OUT_FILE, "| passed", passed, "of", len(scored), "| random-date passes", luck)
    return s, scored


if __name__ == "__main__":
    pd.set_option("display.width", 250, "display.max_columns", 30, "display.max_colwidth", 30)
    s, scored = build()
    print(s[["#", "Asset", "Direction", "Pattern", "Trend filter", "Hold days", "Trades", "Win Rate", "Avg trade %",
             "Profit Factor", "Final Rs", "CAGR", "Max Drawdown", "OOS trades", "OOS avg trade %", "Median best move %"]].round(3).to_string())

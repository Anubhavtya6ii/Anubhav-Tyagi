"""Options strategy lab: one engine, many defined-risk option strategies, four markets.

Markets: Nifty and Bank Nifty weekly options (NSE bhavcopy), Bitcoin and Ethereum weekly options (Deribit).
Every trade is held to expiry. Results are measured in R = profit / money at risk, so markets compare fairly.
Discovery on the first 60% of each market's weeks, validation on the last 40%, plus a random-week luck check.

Run fetch_nse_options.py and fetch_deribit_options.py first, then `python options_lab.py` -> Options_Strategy_Lab.xlsx
"""
import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Font

import build_excel as bx

SPLIT = 0.6
RISK_PER_TRADE = 0.10  # sizing illustration: risk 10% of the account on each trade
CAPITAL = 25_000
LUCK_RUNS = 20
OUT_FILE = "Options_Strategy_Lab.xlsx"

MARKETS = {
    # name: source, symbol, lot (units per lot, for Rs-per-lot figures), slippage per leg per order (points)
    # weekly contracts: Nifty from Feb 2019, Bank Nifty from May 2016 until NSE dropped them in Nov 2024
    "NIFTY": {"src": "nse", "lot": 65, "slip": 0.5, "from": pd.Timestamp("2019-02-08"), "until": None},
    "BANKNIFTY": {"src": "nse", "lot": 30, "slip": 1.5, "from": pd.Timestamp("2016-05-27"), "until": pd.Timestamp("2024-11-20")},
    "BTC": {"src": "deribit", "lot": 1, "from": None, "until": None},
    "ETH": {"src": "deribit", "lot": 1, "from": None, "until": None},
}

# NSE charges (as fraction of premium unless noted)
NSE_BROKERAGE, NSE_TXN, NSE_GST, NSE_STAMP, NSE_EX_STT = 20, 0.0005, 0.18, 0.00003, 0.00125
# Deribit: taker fee 0.03% of underlying capped at 12.5% of option price; delivery 0.015%; slippage assumption
DB_FEE, DB_DELIVERY, DB_SLIP_PCT, DB_SLIP_IDX = 0.0003, 0.00015, 0.02, 0.0001


def nse_stt(day):
    return 0.001 if day >= pd.Timestamp("2024-10-01") else 0.000625


# ---------------------------------------------------------------- markets
class Market:
    def __init__(self, name):
        cfg = MARKETS[name]
        self.name, self.cfg, self.src = name, cfg, cfg["src"]
        if self.src == "nse":
            d = pd.read_csv("data/nse_options.csv.gz", parse_dates=["date", "expiry"])
            d = d[(d.symbol == name) & (d.close > 0)].rename(columns={"close": "price"})
            d["traded"] = d.volume > 0
            spot = d.groupby("date").spot.first()
            self.daily = self.settle = spot  # index close: NSE settles weekly options on it
        else:
            d = pd.read_csv("data/deribit_options.csv.gz", parse_dates=["date", "expiry"])
            d = d[(d.currency == name) & (d.mark_usd > 0)].rename(columns={"mark_usd": "price"})
            d["traded"] = True
            spot = d.groupby("date")["index"].median()  # index while the snapshot's options traded (for strikes)
            # Deribit settles at 08:00 UTC on a 30-min TWAP; using the snapshot index instead adds noise that
            # flatters option buyers, so expiry payoffs, signals and realized moves use the 07:30-08:00 average.
            self.daily = self.settle = pd.read_csv("data/deribit_settlement.csv", index_col="Date", parse_dates=True)[name].dropna()
            d["spot"] = d.date.map(spot)
        if cfg["from"] is not None:
            d = d[d.date >= cfg["from"]]
        if cfg["until"] is not None:
            d = d[d.date <= cfg["until"]]
        self.px = {(r.date, r.expiry, r.strike, r.type): r.price for r in d[["date", "expiry", "strike", "type", "price"]].itertuples(index=False)}
        self.chains = {k: g for k, g in d.groupby(["date", "expiry"])}
        self.spot = spot[spot.index.isin(d.date.unique())]
        self.dates = list(self.spot.index)
        self.pos = {dt: i for i, dt in enumerate(self.dates)}
        if self.src == "nse":
            near = d[d.expiry >= d.date].groupby("date").expiry.min()
            self.expiries = sorted(e for e in set(near) if e in self.pos)
        else:  # Deribit weekly contracts expire on Fridays
            self.expiries = sorted(e for e in d.expiry.unique()
                                   if pd.Timestamp(e).weekday() == 4 and e in self.pos and e in self.settle.index)
        c = self.daily
        self.trend = np.sign(c.ewm(span=20, adjust=False).mean() - c.ewm(span=50, adjust=False).mean())
        self.mom = np.sign(c / c.shift(20) - 1)
        self.rev = -np.sign(c / c.shift(5) - 1)
        self.periods = 252 if self.src == "nse" else 365

    def n_periods(self, entry, expiry):
        return self.pos[expiry] - self.pos[entry] if self.src == "nse" else (expiry - entry).days

    def realized_move(self, entry, n):
        s = self.daily[self.daily.index <= entry]
        moves = (s - s.shift(n)).abs().dropna()
        return moves.iloc[-40:].mean() if len(moves) >= 40 else np.nan


# ---------------------------------------------------------------- structures
def pick(chain, typ, target, up):
    for traded in (True, False):
        c = chain[chain.type == typ]
        if traded:
            c = c[c.traded]
        c = c[c.strike >= target].sort_values("strike") if up else c[c.strike <= target].sort_values("strike", ascending=False)
        if not c.empty:
            return c.strike.iloc[0], c.price.iloc[0]
    return None


def structures(chain, spot):
    ce, pe = chain[(chain.type == "CE") & chain.traded], chain[(chain.type == "PE") & chain.traded]
    both = sorted(set(ce.strike) & set(pe.strike))
    if not both:
        return None, None, None
    atm = min(both, key=lambda k: abs(k - spot))
    c0, p0 = ce[ce.strike == atm].price.iloc[0], pe[pe.strike == atm].price.iloc[0]
    S = c0 + p0
    L, Sh = 1, -1

    def leg(typ, target, up, side):
        p = pick(chain, typ, target, up)
        return None if p is None else (p[0], typ, side, p[1])

    def condor(m, w):
        sc, sp = leg("CE", spot + m * S, True, Sh), leg("PE", spot - m * S, False, Sh)
        if not sc or not sp:
            return None
        lc, lp = leg("CE", sc[0] + w * S, True, L), leg("PE", sp[0] - w * S, False, L)
        return [sc, sp, lc, lp] if lc and lp else None

    ac, ap = (atm, "CE", L, c0), (atm, "PE", L, p0)
    fly_c, fly_p = leg("CE", atm + S, True, L), leg("PE", atm - S, False, L)
    s = {
        "Iron Condor (sell 1x move, wings +1x)": condor(1.0, 1.0),
        "Wide Iron Condor (sell 1x, wings +2x)": condor(1.0, 2.0),
        "Iron Fly (sell ATM, wings 1x)": [(atm, "CE", Sh, c0), (atm, "PE", Sh, p0), fly_c, fly_p] if fly_c and fly_p else None,
        "Long Straddle (buy ATM)": [ac, ap],
        "Long Strangle (buy 0.5x away)": [leg("CE", spot + 0.5 * S, True, L), leg("PE", spot - 0.5 * S, False, L)],
        "LONG CALL": [ac], "LONG PUT": [ap],
        "BULL CALL SPREAD": [ac, leg("CE", atm + S, True, Sh)],
        "BEAR PUT SPREAD": [ap, leg("PE", atm - S, False, Sh)],
        "SELL PUT SPREAD": None, "SELL CALL SPREAD": None,
    }
    sps = leg("PE", spot - 0.5 * S, False, Sh)
    if sps:
        s["SELL PUT SPREAD"] = [sps, leg("PE", sps[0] - S, False, L)]
    scs = leg("CE", spot + 0.5 * S, True, Sh)
    if scs:
        s["SELL CALL SPREAD"] = [scs, leg("CE", scs[0] + S, True, L)]
    s = {k: v for k, v in s.items() if v and all(v)}
    return s, S, atm


def payoff(legs, s_exp):
    return sum(side * (max(s_exp - k, 0) if t == "CE" else max(k - s_exp, 0)) for k, t, side, _ in legs)


def leg_costs(mk, legs, day, idx, opening=True, prices=None):
    total = 0.0
    for (k, t, side, p0), p in zip(legs, prices or [l[3] for l in legs]):
        if mk.src == "nse":
            sell = (side < 0) == opening
            total += mk.cfg["slip"] + (NSE_BROKERAGE / mk.cfg["lot"] + p * NSE_TXN) * (1 + NSE_GST)
            total += p * nse_stt(day) if sell else p * NSE_STAMP
        else:
            total += min(DB_FEE * idx, 0.125 * p) + DB_SLIP_PCT * p + DB_SLIP_IDX * idx
    return total


def settle_costs(mk, legs, s_exp):
    total = 0.0
    for k, t, side, _ in legs:
        iv = max(s_exp - k, 0) if t == "CE" else max(k - s_exp, 0)
        if iv <= 0:
            continue
        total += iv * NSE_EX_STT if (mk.src == "nse" and side > 0) else 0.0
        total += min(DB_DELIVERY * s_exp, 0.125 * iv) if mk.src == "deribit" else 0.0
    return total


def evaluate(mk, legs, entry, expiry):
    debit = sum(side * p for _, _, side, p in legs)  # >0 paid, <0 received
    s_exp = mk.settle[expiry]
    grid = [0.0] + sorted({k for k, *_ in legs}) + [3 * max(k for k, *_ in legs)]
    max_loss = -min(payoff(legs, x) - debit for x in grid)
    c_in = leg_costs(mk, legs, entry, mk.spot[entry])
    gross = payoff(legs, s_exp) - debit
    cost = c_in + settle_costs(mk, legs, s_exp)
    return {"gross": gross, "cost": cost, "risk": max_loss + c_in}


# ---------------------------------------------------------------- build every weekly trade
def weekly_book(mk):
    rows = []
    for prev, exp in zip(mk.expiries, mk.expiries[1:]):
        before = [d for d in mk.dates[max(0, mk.pos[exp] - 10):mk.pos[exp]] if d < exp]
        for timing, entry in (("Full week", prev), ("1 day before expiry", before[-1] if before else None)):
            if entry is None or (entry, exp) not in mk.chains or entry not in mk.pos:
                continue
            spot = mk.spot[entry]
            s, S, atm = structures(mk.chains[(entry, exp)], spot)
            if not s:
                continue
            n = mk.n_periods(entry, exp)
            if n <= 0:
                continue
            real = mk.realized_move(entry, n)
            base = {"Market": mk.name, "Timing": timing, "Entry": entry, "Expiry": exp, "Spot": spot,
                    "Spot at expiry": mk.settle[exp], "ATM straddle": S, "Implied move %": S / spot,
                    "Realized move %": real / spot if real == real else np.nan,
                    "Implied/Realized": S / real if real and real == real else np.nan,
                    "IV proxy": S / (0.8 * spot * np.sqrt(n / mk.periods)),
                    "trend": mk.trend.get(entry, 0), "mom": mk.mom.get(entry, 0), "rev": mk.rev.get(entry, 0)}
            for name, legs in s.items():
                r = evaluate(mk, legs, entry, exp)
                rows.append({**base, "Structure": name, "Legs": " | ".join(f"{'+' if sd > 0 else '-'}{k:g}{t}@{p:.2f}" for k, t, sd, p in legs), **r})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- strategy definitions over the weekly book
NONDIR = ["Iron Condor (sell 1x move, wings +1x)", "Wide Iron Condor (sell 1x, wings +2x)", "Iron Fly (sell ATM, wings 1x)",
          "Long Straddle (buy ATM)", "Long Strangle (buy 0.5x away)"]
DIRECTIONAL = {"Buy ATM call / put": ("LONG CALL", "LONG PUT"),
               "Debit spread (bull call / bear put)": ("BULL CALL SPREAD", "BEAR PUT SPREAD"),
               "Credit spread (sell put spread / sell call spread)": ("SELL PUT SPREAD", "SELL CALL SPREAD")}
SIGNALS = {"trend": "with the 20/50 EMA trend", "mom": "with the 20-day momentum", "rev": "against the last 5-day move"}
FILTERS = ["All weeks", "Options expensive (implied >= 1.1x realized)", "Options cheap (implied <= 0.9x realized)",
           "Low vol (bottom third)", "Mid vol", "High vol (top third)"]


def filter_mask(g, f, cuts):
    ratio = g["Implied/Realized"]
    return {
        "All weeks": pd.Series(True, index=g.index),
        "Options expensive (implied >= 1.1x realized)": ratio >= 1.1,
        "Options cheap (implied <= 0.9x realized)": ratio <= 0.9,
        "Low vol (bottom third)": g["IV proxy"] <= cuts[0],
        "Mid vol": (g["IV proxy"] > cuts[0]) & (g["IV proxy"] <= cuts[1]),
        "High vol (top third)": g["IV proxy"] > cuts[1],
    }[f]


def strategy_trades(book):
    """Yield (definition, trades DataFrame) for every strategy in the grid."""
    for (mkt, timing), g in book.groupby(["Market", "Timing"]):
        weeks = sorted(g.Expiry.unique())
        cut_date = weeks[int(len(weeks) * SPLIT)]
        wk = g.drop_duplicates("Expiry").set_index("Expiry")
        is_wk = wk[wk.index < cut_date]
        cuts = is_wk["IV proxy"].quantile([1 / 3, 2 / 3]).to_numpy()
        for f in FILTERS:
            fm = filter_mask(wk, f, cuts)
            for st in NONDIR:
                t = g[(g.Structure == st) & g.Expiry.map(fm)]
                yield {"Market": mkt, "Timing": timing, "Strategy": st, "Signal": "-", "Filter": f, "cut": cut_date}, t
            for dname, (up, down) in DIRECTIONAL.items():
                for sig, sdesc in SIGNALS.items():
                    side = wk[sig]
                    want = pd.Series(np.where(side > 0, up, np.where(side < 0, down, "")), index=wk.index)
                    sel = g.Expiry.map(fm) & (g.Structure == g.Expiry.map(want))
                    yield {"Market": mkt, "Timing": timing, "Strategy": dname, "Signal": sdesc, "Filter": f, "cut": cut_date}, g[sel]


def tstat(x):
    return x.mean() / x.std() * np.sqrt(len(x)) if len(x) > 2 and x.std() > 0 else np.nan


def stats_for(t, cut, cost_mult=1.0):
    R = (t.gross - cost_mult * t.cost) / t.risk
    is_, oos = R[t.Expiry < cut], R[t.Expiry >= cut]
    eq = CAPITAL * (1 + RISK_PER_TRADE * R).cumprod()
    years = max((t.Expiry.max() - t.Entry.min()).days / 365.25, 0.5) if len(t) else np.nan
    return {
        "Trades": len(R), "Win Rate": (R > 0).mean() if len(R) else np.nan, "Avg R": R.mean(),
        "Profit Factor": R[R > 0].sum() / -R[R < 0].sum() if (R < 0).any() else np.nan,
        "Worst R": R.min() if len(R) else np.nan, "IS trades": len(is_), "IS avg R": is_.mean(), "IS t-score": tstat(is_),
        "OOS trades": len(oos), "OOS avg R": oos.mean(), "OOS t-score": tstat(oos),
        "Final Rs": eq.iloc[-1] if len(eq) else np.nan,
        "CAGR": (eq.iloc[-1] / CAPITAL) ** (1 / years) - 1 if len(eq) and eq.iloc[-1] > 0 else np.nan,
        "Max Drawdown": (eq / eq.cummax().clip(lower=CAPITAL) - 1).min() if len(eq) else np.nan,
    }


def passed(s):
    return (s["IS trades"] >= 30) & (s["OOS trades"] >= 15) & (s["IS t-score"] >= 2) & (s["OOS avg R"] > 0)


def run_lab(book):
    rows, trades = [], {}
    for k, (defn, t) in enumerate(strategy_trades(book)):
        st = stats_for(t, defn["cut"])
        st2 = stats_for(t, defn["cut"], cost_mult=2.0)
        rows.append({"id": k, **{x: v for x, v in defn.items() if x != "cut"}, "OOS starts": defn["cut"].date(),
                     **st, "Avg R at 2x costs": st2["Avg R"], "OOS avg R at 2x costs": st2["OOS avg R"]})
        trades[k] = t
    res = pd.DataFrame(rows)
    res["Passed"] = passed(res)
    return res, trades


def luck_check(book, runs=LUCK_RUNS):
    """Replace every filter / signal by random weeks (same count, random side): how many strategies 'pass'?"""
    counts = []
    rng = np.random.default_rng(7)
    for _ in range(runs):
        n = 0
        for defn, t in strategy_trades(book):
            if defn["Filter"] == "All weeks" and defn["Signal"] == "-":
                continue
            g = book[(book.Market == defn["Market"]) & (book.Timing == defn["Timing"])]
            k = t.Expiry.nunique()
            if k < 10:
                continue
            weeks = rng.choice(sorted(g.Expiry.unique()), size=k, replace=False)
            if defn["Signal"] == "-":
                rt = g[(g.Structure == defn["Strategy"]) & g.Expiry.isin(weeks)]
            else:
                up, down = DIRECTIONAL[defn["Strategy"]]
                side = dict(zip(weeks, rng.choice([up, down], size=k)))
                rt = g[g.Expiry.isin(weeks) & (g.Structure == g.Expiry.map(side))]
            s = stats_for(rt.sort_values("Expiry"), defn["cut"])
            n += bool((s["IS trades"] >= 30) and (s["OOS trades"] >= 15) and (s["IS t-score"] >= 2) and (s["OOS avg R"] > 0))
        counts.append(n)
    return counts


# ---------------------------------------------------------------- report
def build():
    books = []
    for m in MARKETS:
        mk = Market(m)
        b = weekly_book(mk)
        print(f"{m}: {b.Expiry.nunique()} weeks, {len(b)} structure-trades, {b.Expiry.min().date()} -> {b.Expiry.max().date()}", flush=True)
        books.append(b)
    book = pd.concat(books, ignore_index=True)
    res, trades = run_lab(book)
    non_trivial = res[~((res.Filter == "All weeks") & (res.Signal == "-"))]
    luck = luck_check(book)
    real_pass = int(non_trivial.Passed.sum())
    print("passed:", int(res.Passed.sum()), "| filtered/signal strategies passed:", real_pass, "| random:", luck, flush=True)

    # rank by the weaker of the two periods: a strategy must be good both before and after the split
    res["Consistency score"] = res[["IS t-score", "OOS t-score"]].min(axis=1)
    best = res[res.Passed].sort_values("Consistency score", ascending=False)
    best = best.groupby("Market", group_keys=False).head(4).sort_values("Consistency score", ascending=False).head(10)

    bx.PCT_COLS.update({"Win Rate", "CAGR", "Max Drawdown", "Implied move %", "Realized move %"})
    bx.RS_COLS.update({"Final Rs"})
    bx.NUM_COLS.update({"Avg R", "Worst R", "IS avg R", "IS t-score", "OOS avg R", "OOS t-score", "Profit Factor",
                        "Avg R at 2x costs", "OOS avg R at 2x costs", "Implied/Realized", "R", "IV proxy", "Consistency score"})
    wb = Workbook()
    ws = wb.active
    ws.title = "Best_Strategies"
    ws["A1"] = "Best option strategies found (Nifty, Bank Nifty, Bitcoin, Ethereum weekly options, held to expiry)"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = (f"R = profit / money at risk per trade. Rs 25,000 column assumes {RISK_PER_TRADE:.0%} of the account is risked per trade. "
                "Passed = in-sample t-score >= 2 AND still profitable in the out-of-sample period. Not financial advice.")
    ws["A2"].font = Font(italic=True, color="7F7F7F")
    ws["A3"] = (f"Luck check: {real_pass} filtered/signal strategies passed. With RANDOM weeks instead of the real filters/signals: "
                f"{min(luck)}-{max(luck)} passed (avg {np.mean(luck):.1f}) over {len(luck)} runs.")
    ws["A3"].font = Font(bold=True, color="C00000")
    show = best.drop(columns=["id", "Passed"])
    bx.write_df(ws, show, row=5)
    ws.row_dimensions[5].height = 45
    bx.finish(ws, freeze="E6", widths={"C": 40, "D": 30, "E": 38})

    ws = wb.create_sheet("All_Strategies")
    ws["A1"] = f"Every strategy tested ({len(res)}): 4 markets x 2 entry timings x structures/signals x filters"
    ws["A1"].font = Font(bold=True)
    bx.write_df(ws, res.drop(columns=["id"]), row=3)
    ws.auto_filter.ref = f"A3:{bx.get_column_letter(res.shape[1] - 1)}{3 + len(res)}"
    bx.finish(ws, freeze="E4", widths={"C": 40, "D": 30, "E": 38})

    ws = wb.create_sheet("Implied_vs_Realized")
    ws["A1"] = "Are options over- or under-priced? Average implied move (ATM straddle) vs the move that actually happened, by year"
    ws["A1"].font = Font(bold=True)
    wk = book.drop_duplicates(["Market", "Timing", "Expiry"]).copy()
    wk["Actual move %"] = (wk["Spot at expiry"] - wk.Spot).abs() / wk.Spot
    wk["Year"] = wk.Expiry.dt.year
    ov = wk.groupby(["Market", "Timing", "Year"]).agg(**{"Weeks": ("Expiry", "size"), "Implied move %": ("Implied move %", "mean"),
                                                        "Actual move %": ("Actual move %", "mean")}).reset_index()
    ov["Actual / Implied"] = ov["Actual move %"] / ov["Implied move %"]
    bx.PCT_COLS.update({"Actual move %"})
    bx.NUM_COLS.update({"Actual / Implied"})
    bx.write_df(ws, ov, row=3)
    ws.conditional_formatting.add(f"G4:G{3 + len(ov)}", ColorScaleRule(
        start_type="num", start_value=0.8, start_color="63BE7B", mid_type="num", mid_value=1.0,
        mid_color="FFFFFF", end_type="num", end_value=1.2, end_color="F8696B"))
    bx.finish(ws, freeze="D4")

    ws = wb.create_sheet("Best_Yearly_R")
    ws["A1"] = "Sum of R per calendar year for the best strategies"
    ws["A1"].font = Font(bold=True)
    yr = []
    for _, r in best.iterrows():
        t = trades[r.id]
        R = (t.gross - t.cost) / t.risk
        y = R.groupby(t.Expiry.dt.year).sum()
        yr.append({"Market": r.Market, "Strategy": f"{r.Timing} | {r.Strategy} {'' if r.Signal == '-' else r.Signal} | {r.Filter}", **y.to_dict()})
    yr = pd.DataFrame(yr)
    if len(yr):
        yr = yr[["Market", "Strategy"] + sorted(c for c in yr.columns if isinstance(c, int))]
        yr.columns = [str(c) for c in yr.columns]
        bx.write_df(ws, yr, row=3)
        for row in ws.iter_rows(min_row=4, min_col=3):
            for c in row:
                c.number_format = "0.00"
        ws.conditional_formatting.add(f"C4:{bx.get_column_letter(yr.shape[1])}{3 + len(yr)}", ColorScaleRule(
            start_type="num", start_value=-3, start_color="F8696B", mid_type="num", mid_value=0,
            mid_color="FFFFFF", end_type="num", end_value=3, end_color="63BE7B"))
    bx.finish(ws, freeze="C4", widths={"B": 90})

    ws = wb.create_sheet("Best_Trades")
    bt_rows = []
    for _, r in best.iterrows():
        t = trades[r.id].copy()
        t["R"] = (t.gross - t.cost) / t.risk
        t["P&L per lot"] = (t.gross - t.cost) * MARKETS[r.Market]["lot"]
        t.insert(0, "Strategy", f"{r.Market} | {r.Timing} | {r.Strategy} {'' if r.Signal == '-' else r.Signal} | {r.Filter}")
        bt_rows.append(t)
    if bt_rows:
        bt = pd.concat(bt_rows)
        bt = bt[["Strategy", "Entry", "Expiry", "Structure", "Legs", "Spot", "Spot at expiry", "ATM straddle", "Implied/Realized",
                 "gross", "cost", "risk", "R", "P&L per lot"]]
        bx.write_df(ws, bt, row=1)
        ws.auto_filter.ref = ws.dimensions
    bx.finish(ws, freeze="C2", widths={"A": 70, "E": 60})

    ws = wb.create_sheet("Assumptions")
    lines = [
        ("Data", "NSE F&O bhavcopy end-of-day closes (Nifty weekly from 2019, Bank Nifty weekly 2016 - Nov 2024). Deribit trade history: last mark price of every option traded 08:00-10:00 UTC each day (Bitcoin, Ethereum)."),
        ("Entry", "'Full week' = enter when the previous weekly contract expires and hold the next one to expiry. '1 day before expiry' = enter the day before expiry. NSE at the close, Deribit at ~08:00-10:00 UTC."),
        ("Strikes", "ATM = traded strike nearest spot. 'x' distances are multiples of the ATM straddle price (the market's expected move). Every structure has limited risk; no naked selling."),
        ("Directional signals", "Trend = 20 EMA above/below 50 EMA of spot. Momentum = sign of the 20-day change. Reversal = opposite of the last 5-day change. All known before entry."),
        ("Filters", "Implied/Realized = ATM straddle / average actual move over the same number of days in the last 40 periods. Vol thirds use cut-offs from the in-sample period only."),
        ("R", "Profit after costs divided by the most you could lose (incl. entry costs). +1R = made as much as you risked."),
        ("Costs", "NSE: Rs 20/order brokerage, exchange charge, GST, STT (0.0625%, 0.1% from Oct 2024), stamp duty, 0.5 pt (Nifty) / 1.5 pt (Bank Nifty) slippage per leg; STT on in-the-money long legs at expiry. Deribit: 0.03% of underlying fee (capped 12.5% of price), 2% of price + 0.01% of underlying slippage per leg, 0.015% delivery fee. '2x costs' columns double all of it."),
        ("Validation", "Each market's weeks are split 60% / 40% by time. 'Passed' needs t-score >= 2 on the first 60% and a positive average on the last 40% (at least 30 / 15 trades). Best strategies are ranked by the weaker of the two t-scores, because option selling can show a huge t-score simply by having no big loss yet."),
        ("Luck check", "Every filter and signal is replaced by randomly chosen weeks (same number, random direction) and the whole test re-run. If random weeks pass as often as the real rules, the rules are probably luck."),
        ("Sizing", f"Rs 25,000 / CAGR / drawdown columns risk {RISK_PER_TRADE:.0%} of the account per trade (fractional size). Real NSE lots are whole numbers and need more capital."),
        ("Limits", "End-of-day data, no intraday exits, no stop-losses, positions always held to expiry. Bid-ask spreads can be wider than assumed in fast markets. Taxes not included."),
        ("Caveats", "Past results are no guarantee. Option payoffs are skewed: many small wins can hide a rare large loss. Paper-trade first. Not financial advice."),
    ]
    ws.append(["Topic", "Detail"])
    for c in ws[1]:
        c.fill, c.font = bx.HEAD_FILL, bx.HEAD_FONT
    for l in lines:
        ws.append(list(l))
    for r in ws.iter_rows(min_row=2):
        r[0].font = Font(bold=True)
        r[1].alignment = Alignment(wrap_text=True, vertical="top")
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 120
    wb.save(OUT_FILE)
    print("saved", OUT_FILE)
    return book, res, best, luck


if __name__ == "__main__":
    pd.set_option("display.width", 250, "display.max_columns", 30, "display.max_colwidth", 45)
    book, res, best, luck = build()
    print(best[["Market", "Timing", "Strategy", "Signal", "Filter", "Trades", "Win Rate", "Avg R", "IS t-score", "OOS avg R",
                "Avg R at 2x costs", "Final Rs", "Max Drawdown"]].round(3).to_string())

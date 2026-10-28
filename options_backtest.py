"""End-of-day backtest of hedged Nifty weekly option-selling strategies (iron condor / iron fly).

Run `python fetch_nse_options.py` first, then `python options_backtest.py` -> Nifty_Options_Backtest.xlsx
"""
import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Font

import build_excel as bx

QTY = 65  # current Nifty lot size, held fixed so every year is comparable
CAPITAL = 25_000
SLIP = 0.5  # points lost to bid-ask per leg per order
BROKERAGE = 20  # Rs per order (one order per leg)
TXN = 0.0005  # exchange transaction charge on premium
GST = 0.18  # on brokerage + transaction charge
STAMP = 0.00003  # on buy premium
EXERCISE_STT = 0.00125  # on intrinsic value of long legs that expire in the money
OUT_FILE = "Nifty_Options_Backtest.xlsx"

# name: (short distance in ATM-straddles, wing distance beyond shorts in straddles, stop-loss x credit, entry)
# entry "week" = at the close of the previous expiry (~5 trading days); "1dte" = close of the day before expiry
STRATEGIES = {
    "Iron Condor 0.5x": (0.5, 1.0, None, "week"),
    "Iron Condor 1.0x": (1.0, 1.0, None, "week"),
    "Iron Condor 1.5x": (1.5, 1.0, None, "week"),
    "Iron Fly (ATM)": (0.0, 1.0, None, "week"),
    "Iron Condor 0.5x + SL": (0.5, 1.0, 1.0, "week"),
    "Iron Condor 1.0x + SL": (1.0, 1.0, 1.0, "week"),
    "Iron Condor 1.5x + SL": (1.5, 1.0, 1.0, "week"),
    "Iron Fly (ATM) + SL": (0.0, 1.0, 1.0, "week"),
    "Iron Condor 1.0x, 1 day to expiry": (1.0, 1.0, None, "1dte"),
    "Iron Fly (ATM), 1 day to expiry": (0.0, 1.0, None, "1dte"),
}


def stt_rate(day):
    return 0.001 if day >= pd.Timestamp("2024-10-01") else 0.000625  # STT on option sell premium


# ---------------------------------------------------------------- data
class Market:
    def __init__(self):
        d = pd.read_csv("data/nse_options.csv.gz", parse_dates=["date", "expiry"])
        d = d[(d.symbol == "NIFTY") & (d.date >= pd.Timestamp("2019-02-01")) & (d.close > 0)]
        self.px = {(r.date, r.expiry, r.strike, r.type): r.close for r in d.itertuples()}
        self.chains = {k: g for k, g in d.groupby(["date", "expiry"])}
        self.spot = d.groupby("date").spot.first()
        self.dates = list(self.spot.index)
        near = d[d.expiry >= d.date].groupby("date").expiry.min()
        self.expiries = sorted(set(near))
        vix = pd.read_csv("data/INDIAVIX.csv", index_col="Date", parse_dates=True)["VIX"]
        self.vix = vix.reindex(self.spot.index, method="ffill")


def pick(chain, typ, target, up, traded=True):
    c = chain[chain.type == typ]
    if traded:
        c = c[c.volume > 0]
    c = c[c.strike >= target].sort_values("strike") if up else c[c.strike <= target].sort_values("strike", ascending=False)
    return None if c.empty else (c.strike.iloc[0], c.close.iloc[0])


def build_legs(chain, spot, m, w):
    ce, pe = chain[chain.type == "CE"], chain[chain.type == "PE"]
    both = sorted(set(ce[ce.volume > 0].strike) & set(pe[pe.volume > 0].strike))
    if not both:
        return None
    atm = min(both, key=lambda k: abs(k - spot))
    straddle = ce[ce.strike == atm].close.iloc[0] + pe[pe.strike == atm].close.iloc[0]
    if m == 0:
        sc, sp = (atm, ce[ce.strike == atm].close.iloc[0]), (atm, pe[pe.strike == atm].close.iloc[0])
    else:
        sc, sp = pick(chain, "CE", spot + m * straddle, True), pick(chain, "PE", spot - m * straddle, False)
    if not sc or not sp:
        return None
    lc = pick(chain, "CE", sc[0] + w * straddle, True) or pick(chain, "CE", sc[0] + w * straddle, True, traded=False)
    lp = pick(chain, "PE", sp[0] - w * straddle, False) or pick(chain, "PE", sp[0] - w * straddle, False, traded=False)
    if not lc or not lp:
        return None
    legs = [(sc[0], "CE", -1, sc[1]), (sp[0], "PE", -1, sp[1]), (lc[0], "CE", 1, lc[1]), (lp[0], "PE", 1, lp[1])]
    return legs, straddle, atm


def cost(legs, prices, day, lots, opening):
    """Rs charges for placing one order on every leg (prices = fill prices)."""
    total = 0.0
    for (_, _, sign, _), p in zip(legs, prices):
        sell = (sign < 0) == opening
        prem = p * QTY * lots
        total += (BROKERAGE + prem * TXN) * (1 + GST) + SLIP * QTY * lots
        total += prem * stt_rate(day) if sell else prem * STAMP
    return total


def simulate(mk, name, entry, expiry):
    m, w, sl, _ = STRATEGIES[name]
    chain = mk.chains.get((entry, expiry))
    if chain is None or expiry not in mk.spot.index:
        return None
    built = build_legs(chain, mk.spot[entry], m, w)
    if not built:
        return None
    legs, straddle, atm = built
    credit = -sum(sign * p for _, _, sign, p in legs)
    if credit <= 0:
        return None
    width = max(legs[2][0] - legs[0][0], legs[1][0] - legs[3][0])
    stop = None if sl is None else min(sl * credit, 0.5 * (width - credit))
    last = [p for *_, p in legs]
    exit_day, exit_kind, exit_prices = expiry, "expiry", None
    for day in mk.dates[mk.dates.index(entry) + 1:]:
        if day >= expiry:
            break
        last = [mk.px.get((day, expiry, k, t), prev) for (k, t, _, _), prev in zip(legs, last)]
        pnl = credit + sum(sign * p for (_, _, sign, _), p in zip(legs, last))
        if stop is not None and pnl <= -stop:
            exit_day, exit_kind, exit_prices = day, "stop-loss", last
            break
    if exit_prices is None:
        s = mk.spot[expiry]
        exit_prices = [max(s - k, 0) if t == "CE" else max(k - s, 0) for k, t, _, _ in legs]
    pnl_pts = credit + sum(sign * p for (_, _, sign, _), p in zip(legs, exit_prices))
    return {
        "Strategy": name, "Entry": entry, "Expiry": expiry, "Exit": exit_day, "Exit type": exit_kind,
        "Spot at entry": mk.spot[entry], "Spot at expiry": mk.spot[expiry], "VIX at entry": mk.vix.get(entry),
        "ATM straddle": straddle, "Short CE": legs[0][0], "Short PE": legs[1][0], "Long CE": legs[2][0],
        "Long PE": legs[3][0], "Credit pts": credit, "Width pts": width, "P&L pts": pnl_pts,
        "_legs": legs, "_exit_prices": exit_prices,
    }


def rupees(t, lots):
    legs, exit_prices = t["_legs"], t["_exit_prices"]
    c = cost(legs, [p for *_, p in legs], t["Entry"], lots, opening=True)
    if t["Exit type"] == "stop-loss":
        c += cost(legs, exit_prices, t["Exit"], lots, opening=False)
    else:  # cash-settled at expiry: no orders, only STT on long legs that finish in the money
        c += sum(p * QTY * lots * EXERCISE_STT for (_, _, sign, _), p in zip(legs, exit_prices) if sign > 0)
    return t["P&L pts"] * QTY * lots - c


# ---------------------------------------------------------------- run
def run_all():
    mk = Market()
    trades = []
    for name, (_, _, _, when) in STRATEGIES.items():
        for prev_exp, exp in zip(mk.expiries, mk.expiries[1:]):
            if when == "week":
                entry = prev_exp
            else:
                before = [d for d in mk.dates if d < exp]
                entry = before[-1] if before else None
            if entry is None or entry not in mk.spot.index:
                continue
            t = simulate(mk, name, entry, exp)
            if t:
                trades.append(t)
    tr = pd.DataFrame(trades)
    tr["P&L Rs (1 lot)"] = [rupees(t, 1) for t in trades]
    tr["Max loss Rs (1 lot)"] = (tr["Width pts"] - tr["Credit pts"]) * QTY
    tr["Margin Rs (1 lot)"] = tr["Width pts"] * QTY

    # compounding from Rs 25,000: use all capital, lots = equity / margin per lot
    eq_rows = []
    tr["Lots"], tr["P&L Rs (compounding)"], tr["Equity Rs"] = 0, 0.0, np.nan
    for name in STRATEGIES:
        eq = CAPITAL
        for i in tr.index[tr.Strategy == name]:
            lots = int(eq // tr.at[i, "Margin Rs (1 lot)"])
            pnl = rupees(trades[i], lots) if lots else 0.0
            eq += pnl
            tr.loc[i, ["Lots", "P&L Rs (compounding)", "Equity Rs"]] = [lots, pnl, eq]
            eq_rows.append((name, tr.at[i, "Expiry"], eq))
    equity = pd.DataFrame(eq_rows, columns=["Strategy", "Expiry", "Equity"])
    return mk, tr, equity


def stats(tr, mk):
    rows = []
    for name, g in tr.groupby("Strategy", sort=False):
        pnl = g["P&L Rs (1 lot)"]
        cum = pnl.cumsum()
        ret = pnl / g["Margin Rs (1 lot)"]  # return on margin per trade
        cut = int(len(g) * 0.6)
        years = (g.Expiry.iloc[-1] - g.Entry.iloc[0]).days / 365.25
        final = g["Equity Rs"].iloc[-1]
        peak = g["Equity Rs"].cummax().clip(lower=CAPITAL)
        rows.append({
            "Strategy": name, "Trades": len(g), "From": g.Entry.iloc[0].date(), "To": g.Expiry.iloc[-1].date(),
            "Win Rate": (pnl > 0).mean(), "Avg credit pts": g["Credit pts"].mean(),
            "Avg P&L Rs/lot": pnl.mean(), "Total P&L Rs (1 lot)": pnl.sum(),
            "Profit Factor": pnl[pnl > 0].sum() / -pnl[pnl < 0].sum() if (pnl < 0).any() else np.nan,
            "Worst trade Rs": pnl.min(), "Best trade Rs": pnl.max(),
            "Max DD Rs (1 lot)": (cum - cum.cummax().clip(lower=0)).min(),
            "Stop-loss hits": int((g["Exit type"] == "stop-loss").sum()),
            "Sharpe (per trade, ann.)": ret.mean() / ret.std() * np.sqrt(len(g) / years) if ret.std() > 0 else np.nan,
            "Avg Rs/lot 1st 60% (in-sample)": pnl.iloc[:cut].mean(),
            "Avg Rs/lot last 40% (out-of-sample)": pnl.iloc[cut:].mean(),
            "Final Rs": final, "CAGR": (final / CAPITAL) ** (1 / years) - 1 if final > 0 else -1.0,
            "Max Drawdown": ((g["Equity Rs"] - peak) / peak).min(),
            "Weeks skipped (not enough capital)": int((g.Lots == 0).sum()),
        })
    s = pd.DataFrame(rows)
    nifty = mk.spot.iloc[-1] / mk.spot[s.From.map(pd.Timestamp).min()]
    return s, CAPITAL * nifty


def pick_best(s):
    ok = s[(s["Avg Rs/lot last 40% (out-of-sample)"] > 0) & (s["Avg Rs/lot 1st 60% (in-sample)"] > 0)]
    return ok.sort_values("Sharpe (per trade, ann.)", ascending=False).Strategy.iloc[0] if len(ok) else None


# ---------------------------------------------------------------- excel
def build(mk, tr, equity):
    s, bh_final = stats(tr, mk)
    best = pick_best(s)
    bx.PCT_COLS.update({"Win Rate", "CAGR", "Max Drawdown"})
    bx.RS_COLS.update({"Avg P&L Rs/lot", "Total P&L Rs (1 lot)", "Worst trade Rs", "Best trade Rs", "Max DD Rs (1 lot)",
                       "Avg Rs/lot 1st 60% (in-sample)", "Avg Rs/lot last 40% (out-of-sample)", "Final Rs",
                       "P&L Rs (1 lot)", "Max loss Rs (1 lot)", "Margin Rs (1 lot)", "P&L Rs (compounding)", "Equity Rs"})
    bx.NUM_COLS.update({"Avg credit pts", "Sharpe (per trade, ann.)", "Credit pts", "P&L pts", "ATM straddle", "VIX at entry"})
    wb = Workbook()

    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = "Nifty weekly option selling (hedged), end-of-day backtest"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = (f"Best (risk-adjusted, profitable in both halves of the data): {best or 'none'}.   "
                f"For comparison, Rs 25,000 in Nifty itself over the same period -> Rs {bh_final:,.0f}.")
    ws["A2"].font = Font(bold=True, color="1F3864")
    ws["A3"] = ("Past results, not a promise. Not financial advice. EOD data: stop-losses are checked only at the close, "
                "so real losses on gap/intraday moves can be bigger. Lot size fixed at 65.")
    ws["A3"].font = Font(italic=True, color="7F7F7F")
    bx.write_df(ws, s, row=5)
    ws.row_dimensions[5].height = 60
    bx.finish(ws, freeze="B6", widths={"A": 34})

    global SLIP
    ws = wb.create_sheet("Slippage_Test")
    ws["A1"] = "How fragile is the edge? Total P&L, 1 lot, after costs, at different slippage (points lost per leg per order)"
    ws["A1"].font = Font(bold=True)
    base, recs, rows = SLIP, tr.to_dict("records"), []
    for name in STRATEGIES:
        sub = [t for t in recs if t["Strategy"] == name]
        row = {"Strategy": name}
        for SLIP in (0.5, 1.0, 1.5):
            row[f"Total Rs @ {SLIP} pt"] = sum(rupees(t, 1) for t in sub)
        rows.append(row)
    SLIP = base
    bx.write_df(ws, pd.DataFrame(rows), row=3)
    for r in ws.iter_rows(min_row=4, min_col=2):
        for c in r:
            c.number_format = bx.RS_FMT
    bx.finish(ws, freeze="B4", widths={"A": 34})

    tr["Year"] = tr.Expiry.dt.year
    ws = wb.create_sheet("Yearly_Rs_1lot")
    ws["A1"] = "Profit / loss per calendar year, 1 lot, after all costs (Rs)"
    ws["A1"].font = Font(bold=True)
    y = tr.pivot_table(index="Strategy", columns="Year", values="P&L Rs (1 lot)", aggfunc="sum", sort=False).reset_index()
    y.columns = [str(c) for c in y.columns]
    bx.write_df(ws, y, row=3)
    for row in ws.iter_rows(min_row=4, min_col=2):
        for c in row:
            c.number_format = bx.RS_FMT
    ws.conditional_formatting.add(f"B4:{bx.get_column_letter(len(y.columns))}{3 + len(y)}", ColorScaleRule(
        start_type="num", start_value=-30000, start_color="F8696B", mid_type="num", mid_value=0,
        mid_color="FFFFFF", end_type="num", end_value=30000, end_color="63BE7B"))
    bx.finish(ws, freeze="B4", widths={"A": 34})

    ws = wb.create_sheet("Yearly_WinRate")
    ws["A1"] = "Win rate per calendar year"
    ws["A1"].font = Font(bold=True)
    wr = tr.assign(win=tr["P&L Rs (1 lot)"] > 0).pivot_table(index="Strategy", columns="Year", values="win", aggfunc="mean", sort=False).reset_index()
    wr.columns = [str(c) for c in wr.columns]
    bx.write_df(ws, wr, row=3)
    for row in ws.iter_rows(min_row=4, min_col=2):
        for c in row:
            c.number_format = bx.PCT_FMT
    bx.finish(ws, freeze="B4", widths={"A": 34})

    tr["Month"] = tr.Expiry.dt.month
    ws = wb.create_sheet("Month_of_Year")
    ws["A1"] = "Average P&L per trade by calendar month of expiry, 1 lot (Rs)"
    ws["A1"].font = Font(bold=True)
    mo = tr.pivot_table(index="Strategy", columns="Month", values="P&L Rs (1 lot)", aggfunc="mean", sort=False)
    mo.columns = [bx.MONTHS[c - 1] for c in mo.columns]
    mo = mo.reset_index()
    bx.write_df(ws, mo, row=3)
    for row in ws.iter_rows(min_row=4, min_col=2):
        for c in row:
            c.number_format = bx.RS_FMT
    ws.conditional_formatting.add(f"B4:M{3 + len(mo)}", ColorScaleRule(
        start_type="num", start_value=-2000, start_color="F8696B", mid_type="num", mid_value=0,
        mid_color="FFFFFF", end_type="num", end_value=2000, end_color="63BE7B"))
    bx.finish(ws, freeze="B4", widths={"A": 34})

    tr["VIX zone"] = pd.cut(tr["VIX at entry"], [0, 13, 18, 100], labels=["Low (<13)", "Medium (13-18)", "High (>18)"])
    ws = wb.create_sheet("VIX_Regime")
    ws["A1"] = "Does India VIX at entry matter? Average Rs per lot, win rate and trade count by VIX zone"
    ws["A1"].font = Font(bold=True)
    row = 3
    for label, val, fmt in (("Average P&L Rs per trade (1 lot)", "P&L Rs (1 lot)", bx.RS_FMT), ("Win rate", "win", bx.PCT_FMT), ("Trades", "P&L Rs (1 lot)", "0")):
        ws.cell(row=row, column=1, value=label).font = Font(bold=True)
        agg = "count" if label == "Trades" else "mean"
        v = tr.assign(win=tr["P&L Rs (1 lot)"] > 0).pivot_table(index="Strategy", columns="VIX zone", values=val, aggfunc=agg, sort=False, observed=False).reset_index()
        v.columns = [str(c) for c in v.columns]
        nxt = bx.write_df(ws, v, row=row + 1)
        for r in ws.iter_rows(min_row=row + 2, max_row=row + 1 + len(v), min_col=2, max_col=4):
            for c in r:
                c.number_format = fmt
        row = nxt
    bx.finish(ws, freeze="B1", widths={"A": 34})

    if best:
        ws = wb.create_sheet("Best_Monthly")
        g = tr[tr.Strategy == best]
        ws["A1"] = f"{best}: P&L by year and month, 1 lot (Rs)"
        ws["A1"].font = Font(bold=True)
        grid = g.pivot_table(index="Year", columns="Month", values="P&L Rs (1 lot)", aggfunc="sum")
        grid.columns = [bx.MONTHS[c - 1] for c in grid.columns]
        grid = grid.reindex(columns=bx.MONTHS)
        grid["Year total"] = grid.sum(axis=1)
        grid = grid.reset_index()
        bx.write_df(ws, grid, row=3)
        for r in ws.iter_rows(min_row=4, min_col=2):
            for c in r:
                c.number_format = bx.RS_FMT
        ws.conditional_formatting.add(f"B4:M{3 + len(grid)}", ColorScaleRule(
            start_type="num", start_value=-10000, start_color="F8696B", mid_type="num", mid_value=0,
            mid_color="FFFFFF", end_type="num", end_value=10000, end_color="63BE7B"))
        bx.finish(ws, freeze="B4", widths={"A": 10})

    ws = wb.create_sheet("Equity_25k")
    ws["A1"] = "Rs 25,000 compounding: account value after each expiry (all capital used as margin)"
    ws["A1"].font = Font(bold=True)
    e = equity.pivot_table(index="Expiry", columns="Strategy", values="Equity", sort=False).reindex(columns=list(STRATEGIES)).reset_index()
    bx.write_df(ws, e, row=3)
    for r in ws.iter_rows(min_row=4, min_col=2):
        for c in r:
            c.number_format = bx.RS_FMT
    bx.finish(ws, freeze="B4", widths={"A": 12})

    ws = wb.create_sheet("Trades")
    cols = [c for c in tr.columns if not c.startswith("_") and c not in ("Year", "Month")]
    bx.write_df(ws, tr[cols], row=1)
    ws.row_dimensions[1].height = 45
    ws.auto_filter.ref = ws.dimensions
    bx.finish(ws, freeze="C2")

    ws = wb.create_sheet("Assumptions")
    lines = [
        ("Data", "NSE F&O bhavcopy (end-of-day close of every Nifty option), Feb 2019 onward. Spot = Nifty 50 close. India VIX from Yahoo Finance."),
        ("Entry", "'Week' strategies enter at the close of the previous weekly expiry and hold to the next one (~5 trading days). '1 day to expiry' enters at the close of the day before expiry."),
        ("Strikes", "ATM = strike closest to spot. Short strikes sit 0.5x / 1.0x / 1.5x the ATM straddle price away from spot (the market's own expected move); Iron Fly sells ATM. Hedges (long wings) sit a further 1x straddle beyond each short. Only strikes that actually traded that day are used for the shorts."),
        ("Exit", "Held to expiry and cash-settled at the Nifty close, or (SL versions) closed at the first daily close where the loss reaches 1x the credit received or half the maximum possible loss, whichever is smaller (an Iron Fly can never lose 1x its credit)."),
        ("Important limitation", "End-of-day data only. Real stop-losses fire intraday and gaps can make losses bigger or smaller. Treat SL results as approximate."),
        ("Costs", f"Rs {BROKERAGE}/order brokerage, {TXN:.2%} exchange charge, 18% GST, STT on sell premium (0.0625% before Oct 2024, 0.1% after), stamp duty, {SLIP} pt slippage per leg per order, 0.125% STT on long legs that expire in the money."),
        ("Lot size", f"Fixed at {QTY} (current lot) for every year so results are comparable. Actual historical lot sizes differed."),
        ("Rs 25,000 compounding", "Margin per lot assumed = wing width x lot size (the most you can lose, a little above real SPAN). Lots = account value / margin, rounded down. If the account can't afford 1 lot that week is skipped. This uses all capital every week: aggressive, shown to illustrate risk."),
        ("'Best'", "Highest annualised Sharpe on return-on-margin among strategies that were profitable in BOTH the first 60% and the last 40% of trades."),
        ("Not included", "Income tax on F&O profits, interest on idle cash, margin calls, the 2019-2026 rule changes on lot sizes/expiry days beyond what the data itself shows."),
        ("Caveats", "Past performance does not guarantee future results. Option selling earns small steady amounts and occasionally loses large ones. Not financial advice."),
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
    return s, best, bh_final


if __name__ == "__main__":
    pd.set_option("display.width", 250, "display.max_columns", 30)
    s, best, bh = build(*run_all())
    print(s[["Strategy", "Trades", "Win Rate", "Avg P&L Rs/lot", "Total P&L Rs (1 lot)", "Profit Factor", "Worst trade Rs",
             "Max DD Rs (1 lot)", "Avg Rs/lot last 40% (out-of-sample)", "Final Rs", "Max Drawdown"]].round(2).to_string())
    print("best:", best, "| Nifty B&H Rs", round(bh))

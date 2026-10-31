"""Backtest the 'move away from max pain' idea found in oi_research.py, with real Nifty option prices.

One trade per expiry, entered at the close 1 trading day before expiry, held to expiry:
bullish if Nifty closes above max pain, bearish if below, only when it is at least N moves away
(move = ATM straddle). Compared with a plain momentum rule (direction of the move since the cycle began).
Needs data/nifty_chain/*.csv.gz and data/nifty_daily_candles_oi.csv. Writes Max_Pain_Strategy.xlsx.

Note: the pattern was found on this same 2001-2026 data, so these results are in-sample.
"""
import glob

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

import build_excel as bx
import oi_research as oir

LOT = 65  # fixed lot size so every year compares
SLIP = 0.5  # points per leg per order
THRESHOLDS = [0.25, 0.5, 1.0]
OUT_FILE = "Max_Pain_Strategy.xlsx"


def stt_rate(day):
    return 0.001 if day >= pd.Timestamp("2024-10-01") else 0.000625


def entry_cost(legs, day):
    c = 0.0
    for k, typ, side, p in legs:
        c += SLIP + (20 / LOT + p * 0.0005) * 1.18
        c += p * stt_rate(day) if side < 0 else p * 0.00003
    return c


def settle_value(legs, s):
    pay = sum(side * (max(s - k, 0) if typ == "CE" else max(k - s, 0)) for k, typ, side, _ in legs)
    stt = sum(0.00125 * (max(s - k, 0) if typ == "CE" else max(k - s, 0)) for k, typ, side, _ in legs if side > 0)
    return pay, stt


def chains(days):
    out = {}
    for path in sorted(glob.glob("data/nifty_chain/*.csv.gz")):
        y = pd.read_csv(path, parse_dates=["date", "expiry"])
        y = y[(y.instrument == "OPT") & y.date.isin(days) & (y.close > 0)]
        for k, g in y.groupby(["date", "expiry"]):
            out[k] = g
    return out


def pick(ch, typ, target, up):
    c = ch[ch.type == typ]
    for traded in (True, False):
        cc = c[c.contracts > 0] if traded else c
        cc = cc[cc.strike >= target].sort_values("strike") if up else cc[cc.strike <= target].sort_values("strike", ascending=False)
        if len(cc):
            return float(cc.strike.iloc[0]), float(cc.close.iloc[0])
    return None


def structures(ch, spot, direction):
    ce, pe = ch[(ch.type == "CE") & (ch.contracts > 0)], ch[(ch.type == "PE") & (ch.contracts > 0)]
    both = sorted(set(ce.strike) & set(pe.strike))
    if not both:
        return {}
    atm = min(both, key=lambda k: abs(k - spot))
    c0, p0 = float(ce[ce.strike == atm].close.iloc[0]), float(pe[pe.strike == atm].close.iloc[0])
    S = c0 + p0
    out = {}
    if direction > 0:
        out["Buy ATM option"] = [(atm, "CE", 1, c0)]
        w = pick(ch, "CE", atm + S, True)
        if w:
            out["Debit spread"] = [(atm, "CE", 1, c0), (w[0], "CE", -1, w[1])]
        sp = pick(ch, "PE", spot - 0.5 * S, False)
        lp = sp and pick(ch, "PE", sp[0] - S, False)
        if sp and lp:
            out["Credit spread (sell other side)"] = [(sp[0], "PE", -1, sp[1]), (lp[0], "PE", 1, lp[1])]
    else:
        out["Buy ATM option"] = [(atm, "PE", 1, p0)]
        w = pick(ch, "PE", atm - S, False)
        if w:
            out["Debit spread"] = [(atm, "PE", 1, p0), (w[0], "PE", -1, w[1])]
        sc = pick(ch, "CE", spot + 0.5 * S, True)
        lc = sc and pick(ch, "CE", sc[0] + S, True)
        if sc and lc:
            out["Credit spread (sell other side)"] = [(sc[0], "CE", -1, sc[1]), (lc[0], "CE", 1, lc[1])]
    return out


def trade(legs, entry, s_exp):
    debit = sum(side * p for _, _, side, p in legs)
    grid = [0.0] + sorted(k for k, *_ in legs) + [3 * max(k for k, *_ in legs)]
    max_loss = -min(settle_value(legs, x)[0] - debit for x in grid)
    c_in = entry_cost(legs, entry)
    pay, stt = settle_value(legs, s_exp)
    pnl = pay - debit - c_in - stt
    return pnl, max_loss + c_in


def run():
    t = oir.load()
    cyc = oir.cycles(t)
    cyc = cyc[cyc.When == "1 day before expiry"].dropna(subset=["Max pain pull (moves)"]).copy()
    start_close = cyc.Expiry.map(oir.cycles(t).query("When == 'Cycle start'").set_index("Expiry").Close)
    cyc["Max pain direction"] = -np.sign(cyc["Max pain pull (moves)"])  # away from max pain
    cyc["Momentum direction"] = np.sign(cyc.Close - start_close)
    ch = chains(set(cyc.Start))
    rows = []
    for _, r in cyc.iterrows():
        key = (r.Start, r.Expiry)
        if key not in ch:
            continue
        for rule in ("Max pain direction", "Momentum direction"):
            d = r[rule]
            if d == 0 or d != d:
                continue
            fut = d * (r["Expiry close"] - r.Close)
            base = {"Rule": rule, "Entry": r.Start, "Expiry": r.Expiry, "Direction": "Bullish" if d > 0 else "Bearish",
                    "Close": r.Close, "Max pain": r["Max pain"], "Distance (moves)": abs(r["Max pain pull (moves)"]),
                    "Expiry close": r["Expiry close"], "Straddle": r.Straddle, "Period": r.Period, "Era": r.Era,
                    "Agrees with momentum": r["Max pain direction"] == r["Momentum direction"]}
            rows.append({**base, "Structure": "Futures (direction only)", "P&L pts": fut - 2 * SLIP,
                         "Risk pts": r.Straddle, "R": (fut - 2 * SLIP) / r.Straddle, "Legs": "futures"})
            for name, legs in structures(ch[key], r.Close, d).items():
                pnl, risk = trade(legs, r.Start, r["Expiry close"])
                rows.append({**base, "Structure": name, "P&L pts": pnl, "Risk pts": risk, "R": pnl / risk,
                             "Legs": " | ".join(f"{'+' if s > 0 else '-'}{k:g}{ty}@{p:.2f}" for k, ty, s, p in legs)})
    return pd.DataFrame(rows)


def summarise(tr):
    out = []
    for thr in THRESHOLDS:
        x = tr[tr["Distance (moves)"] >= thr]
        for (rule, st), g in x.groupby(["Rule", "Structure"]):
            row = {"Min distance (moves)": thr, "Rule": rule, "Structure": st, "Trades": len(g),
                   "Win Rate": (g.R > 0).mean(), "Avg R": g.R.mean(), "t-score": oir.tstat(g.R),
                   "Total Rs (1 lot)": (g["P&L pts"] * LOT).sum(), "Worst Rs": (g["P&L pts"] * LOT).min()}
            for grp_col in ("Period", "Era"):
                for grp, gg in g.groupby(grp_col):
                    row[f"Avg R {grp}"] = gg.R.mean()
            cum = (g.sort_values("Entry")["P&L pts"] * LOT).cumsum()
            row["Max DD Rs (1 lot)"] = (cum - cum.cummax().clip(lower=0)).min()
            out.append(row)
    return pd.DataFrame(out)


def momentum_strength():
    cyc = oir.cycles(oir.load())
    st = cyc[cyc.When == "Cycle start"].set_index("Expiry").Close
    x = cyc[cyc.When == "1 day before expiry"].copy()
    move = (x.Close - x.Expiry.map(st)) / x.Straddle
    x["R"] = np.sign(move) * (x["Expiry close"] - x.Close) / x.Straddle
    x["Strength"] = pd.qcut(move.abs(), 4, labels=["Weakest 25%", "25-50%", "50-75%", "Strongest 25%"])
    rows = []
    for (grp, strength), g in list(x.groupby(["Period", "Strength"], observed=True)) + [(("All years", k), g) for k, g in x.groupby("Strength", observed=True)]:
        rows.append({"Group": grp, "Move since cycle start": strength, "Cycles": len(g), "Win Rate": (g.R > 0).mean(),
                     "Avg R": g.R.mean(), "Median R": g.R.median(), "t-score": oir.tstat(g.R)})
    return pd.DataFrame(rows)


def build():
    tr = run()
    s = summarise(tr)
    # does max pain add anything beyond momentum? look at cycles where the two disagree
    mp = tr[tr.Rule == "Max pain direction"]
    dis = mp[~mp["Agrees with momentum"]]
    split = []
    for thr in THRESHOLDS:
        for agree, g in mp[mp["Distance (moves)"] >= thr].groupby("Agrees with momentum"):
            for st, gg in g.groupby("Structure"):
                split.append({"Min distance (moves)": thr, "Max pain agrees with momentum": agree, "Structure": st,
                              "Trades": len(gg), "Win Rate": (gg.R > 0).mean(), "Avg R": gg.R.mean(), "t-score": oir.tstat(gg.R)})
    split = pd.DataFrame(split)

    bx.PCT_COLS.update({"Win Rate"})
    bx.RS_COLS.update({"Total Rs (1 lot)", "Worst Rs", "Max DD Rs (1 lot)"})
    bx.NUM_COLS.update({"Avg R", "Median R", "t-score", "R", "P&L pts", "Risk pts", "Distance (moves)", "Straddle"} |
                       {c for c in s.columns if c.startswith("Avg R ")})
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = "Trade AWAY from max pain, 1 day before Nifty expiry (real option prices, held to expiry)"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = ("R = profit / money at risk after costs. 'Momentum direction' = same trades in the direction Nifty moved since the "
                "cycle started, to see if max pain adds anything. IN-SAMPLE: the idea was found on this same data. Not financial advice.")
    ws["A2"].font = Font(italic=True, color="7F7F7F")
    bx.write_df(ws, s, row=4)
    ws.row_dimensions[4].height = 45
    bx.finish(ws, freeze="D5", widths={"B": 22, "C": 30})

    ws = wb.create_sheet("MaxPain_vs_Momentum")
    ws["A1"] = "Max-pain trades split by whether max pain pointed the same way as momentum. If max pain only works when it agrees, it is just momentum."
    ws["A1"].font = Font(bold=True)
    bx.write_df(ws, split, row=3)
    bx.finish(ws, freeze="D4", widths={"C": 30})

    ws = wb.create_sheet("Momentum_Strength")
    ws["A1"] = ("Is the expiry-day follow-through stronger after a bigger move? 1-day-before-expiry cycles split into quarters by how far "
                "Nifty moved since the cycle began (in moves). R = futures P&L in moves, before costs.")
    ws["A1"].font = Font(bold=True)
    bx.write_df(ws, momentum_strength(), row=3)
    bx.finish(ws, freeze="C4")

    ws = wb.create_sheet("Trades")
    bx.write_df(ws, tr.sort_values(["Rule", "Structure", "Entry"]), row=1)
    ws.auto_filter.ref = ws.dimensions
    bx.finish(ws, freeze="C2", widths={"N": 60})

    ws = wb.create_sheet("Method")
    lines = [
        ("Signal", "At the close 1 trading day before each Nifty expiry: if the close is above max pain, go bullish; if below, bearish. Only when the gap is at least 0.25 / 0.5 / 1.0 'moves' (ATM straddle of the expiring contract)."),
        ("Structures", "Futures (direction only, 2 x 0.5 pt slippage); buy ATM call/put; debit spread (ATM vs 1 move away); credit spread on the other side (short 0.5 move from spot, long 1 move further). All use the contract expiring the next day."),
        ("Prices", "NSE bhavcopy closing prices of each option on the entry day; settlement = Nifty close on expiry (near-month futures before Sep 2007)."),
        ("Costs", f"{SLIP} pt slippage per leg, Rs 20/order brokerage per lot of {LOT}, exchange charge, GST, STT on sell premium, stamp duty, STT on in-the-money long legs at expiry."),
        ("Momentum check", "Same trades taken in the direction of the move from the cycle start to the entry day. If max pain does no better, it adds nothing."),
        ("In-sample warning", "The 'away from max pain' effect was found on 2001-2026 data in oi_research.py, so this backtest is NOT an independent test. Paper-trade forward before trusting it."),
    ]
    ws.append(["Topic", "Detail"])
    for c in ws[1]:
        c.fill, c.font = bx.HEAD_FILL, bx.HEAD_FONT
    for l in lines:
        ws.append(list(l))
    for r in ws.iter_rows(min_row=2):
        r[0].font = Font(bold=True)
        r[1].alignment = Alignment(wrap_text=True, vertical="top")
    ws.column_dimensions["A"].width = 20
    ws.column_dimensions["B"].width = 120
    wb.save(OUT_FILE)
    print("saved", OUT_FILE, len(tr), "trade rows")
    return tr, s, split


if __name__ == "__main__":
    pd.set_option("display.width", 250, "display.max_columns", 30)
    tr, s, split = build()
    print(s.round(3).to_string(index=False))
    print(split.round(3).to_string(index=False))

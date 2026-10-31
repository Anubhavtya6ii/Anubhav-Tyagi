"""One row per trading day since June 2001: the Nifty candle plus what the option chain / OI looked like that day.

Needs data/nifty_chain/*.csv.gz (python fetch_nifty_chain.py) and data/NIFTY50.csv (python fetch_data.py).
Writes Nifty_Daily_Candles_OI.xlsx and data/nifty_daily_candles_oi.csv.
"""
import glob

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule, ColorScaleRule
from openpyxl.styles import Alignment, Font, PatternFill

import build_excel as bx

OUT_FILE = "Nifty_Daily_Candles_OI.xlsx"
OUT_CSV = "data/nifty_daily_candles_oi.csv"


def max_pain(opt):
    """Strike at which option writers pay out the least at expiry."""
    ce, pe = opt[opt.type == "CE"], opt[opt.type == "PE"]
    strikes = np.sort(opt.strike.unique())
    pain = [(ce.oi * np.clip(k - ce.strike, 0, None)).sum() + (pe.oi * np.clip(pe.strike - k, 0, None)).sum() for k in strikes]
    return strikes[int(np.argmin(pain))] if len(strikes) else np.nan


def summarise_day(day, g, spot_close):
    fut = g[g.instrument == "FUT"].sort_values("expiry")
    opt = g[(g.instrument == "OPT") & g.type.isin(["CE", "PE"])]
    row = {"Date": day}
    if len(fut):
        f = fut.iloc[0]
        row.update({"Fut expiry": f.expiry, "Fut open": f.open, "Fut high": f.high, "Fut low": f.low, "Fut close": f.close,
                    "Fut OI": fut.oi.sum(), "Fut chg OI": fut.chg_oi.sum()})
    if opt.empty:
        return row
    ref = spot_close if spot_close == spot_close else row.get("Fut close", np.nan)
    # on an expiry day the expiring series is settling, so "near" = the next expiry after today
    near_exp = opt.expiry[opt.expiry > day].min()
    n = opt[opt.expiry == near_exp]
    ce, pe = n[n.type == "CE"], n[n.type == "PE"]
    both = sorted(set(ce[ce.close > 0].strike) & set(pe[pe.close > 0].strike))
    atm = min(both, key=lambda k: abs(k - ref)) if both and ref == ref else np.nan
    straddle = (ce[ce.strike == atm].close.sum() + pe[pe.strike == atm].close.sum()) if atm == atm else np.nan
    allce, allpe = opt[opt.type == "CE"], opt[opt.type == "PE"]
    row.update({
        "Near expiry": near_exp, "Days to expiry": (near_exp - day).days,
        "CE OI (near)": ce.oi.sum(), "PE OI (near)": pe.oi.sum(),
        "PCR (near)": pe.oi.sum() / ce.oi.sum() if ce.oi.sum() else np.nan,
        "CE chg OI (near)": ce.chg_oi.sum(), "PE chg OI (near)": pe.chg_oi.sum(),
        "Max CE OI strike (resistance)": ce.loc[ce.oi.idxmax(), "strike"] if ce.oi.sum() else np.nan,
        "Max PE OI strike (support)": pe.loc[pe.oi.idxmax(), "strike"] if pe.oi.sum() else np.nan,
        "Max pain (near)": max_pain(n) if n.oi.sum() else np.nan,
        "ATM strike": atm, "ATM straddle": straddle,
        "Implied move %": straddle / ref if ref == ref and straddle == straddle else np.nan,
        "CE OI (all expiries)": allce.oi.sum(), "PE OI (all expiries)": allpe.oi.sum(),
        "PCR (all)": allpe.oi.sum() / allce.oi.sum() if allce.oi.sum() else np.nan,
        "CE contracts traded": allce.contracts.sum(), "PE contracts traded": allpe.contracts.sum(),
        "Strikes listed": opt.strike.nunique(), "Expiries listed": opt.expiry.nunique(),
    })
    return row


def build_table():
    spot = pd.read_csv("data/NIFTY50.csv", index_col="Date", parse_dates=True)
    rows = []
    for path in sorted(glob.glob("data/nifty_chain/*.csv.gz")):
        y = pd.read_csv(path, parse_dates=["date", "expiry"])
        for day, g in y.groupby("date"):
            rows.append(summarise_day(day, g, spot.Close.get(day, np.nan)))
        print(path, "done", flush=True)
    t = pd.DataFrame(rows).set_index("Date").sort_index()

    # candle: Nifty spot from Yahoo where it exists (Sep 2007+), near-month futures before that
    has = t.index.isin(spot.index)
    s = spot.reindex(t.index)
    for c in ("Open", "High", "Low", "Close"):
        t[c] = s[c].where(has, t[f"Fut {c.lower()}"])
    t.insert(0, "Candle source", np.where(has, "Nifty spot", "Near-month futures"))
    t = t[["Candle source", "Open", "High", "Low", "Close"] + [c for c in t.columns if c not in ("Candle source", "Open", "High", "Low", "Close")]]
    t.insert(5, "Change %", t.Close.pct_change())
    t.insert(6, "Candle", np.where(t.Close >= t.Open, "Green", "Red"))
    t.insert(7, "Range %", (t.High - t.Low) / t.Close.shift())
    t.insert(8, "Gap %", t.Open / t.Close.shift() - 1)
    t["Fut basis"] = t["Fut close"] - t.Close
    return t.reset_index()


def build():
    t = build_table()
    t.to_csv(OUT_CSV, index=False)
    bx.PCT_COLS.update({"Change %", "Range %", "Gap %", "Implied move %"})
    bx.NUM_COLS.update({"PCR (near)", "PCR (all)"})
    wb = Workbook()
    ws = wb.active
    ws.title = "Daily"
    bx.write_df(ws, t, row=1)
    ws.row_dimensions[1].height = 45
    n = len(t) + 1
    col = {c: bx.get_column_letter(i + 1) for i, c in enumerate(t.columns)}
    for c in ("Open", "High", "Low", "Close", "Fut open", "Fut high", "Fut low", "Fut close", "ATM straddle", "Fut basis"):
        for cell in ws[col[c]][1:]:
            cell.number_format = "#,##0.00"
    for c in [c for c in t.columns if "OI" in c or "contracts" in c]:
        for cell in ws[col[c]][1:]:
            cell.number_format = "#,##0"
    green, red = PatternFill("solid", fgColor="C6EFCE"), PatternFill("solid", fgColor="FFC7CE")
    ws.conditional_formatting.add(f"{col['Candle']}2:{col['Candle']}{n}", CellIsRule(operator="equal", formula=['"Green"'], fill=green))
    ws.conditional_formatting.add(f"{col['Candle']}2:{col['Candle']}{n}", CellIsRule(operator="equal", formula=['"Red"'], fill=red))
    ws.conditional_formatting.add(f"{col['Change %']}2:{col['Change %']}{n}", ColorScaleRule(
        start_type="num", start_value=-0.03, start_color="F8696B", mid_type="num", mid_value=0, mid_color="FFFFFF",
        end_type="num", end_value=0.03, end_color="63BE7B"))
    ws.conditional_formatting.add(f"{col['PCR (near)']}2:{col['PCR (near)']}{n}", ColorScaleRule(
        start_type="num", start_value=0.6, start_color="F8696B", mid_type="num", mid_value=1.0, mid_color="FFFFFF",
        end_type="num", end_value=1.4, end_color="63BE7B"))
    ws.auto_filter.ref = ws.dimensions
    bx.finish(ws, freeze="C2", widths={"A": 12, "B": 18})

    ws = wb.create_sheet("Column_Guide")
    guide = [
        ("Candle source", "'Nifty spot' = actual index (Yahoo, from Sep 2007). Before that the index is not freely downloadable here, so the near-month Nifty future is used; it trades within a few points of the index."),
        ("Open / High / Low / Close", "The day's candle."),
        ("Change % / Gap % / Range %", "Close vs previous close; open vs previous close; high-low range as % of previous close."),
        ("Fut ...", "Near-month Nifty futures contract: OHLC, total futures open interest and its daily change."),
        ("Near expiry", "Nearest option expiry AFTER that day (weekly from Feb 2019, monthly before). On an expiry day this is the next contract, since the expiring one is settling."),
        ("CE / PE OI (near)", "Total open interest of calls / puts in the nearest expiry. 'chg OI' = change from the previous day."),
        ("PCR", "Put-call ratio = put OI / call OI. Above 1 = more puts written (often read as support), below 1 = more calls written."),
        ("Max CE OI strike", "Call strike with the most open interest: the level option writers expect to hold as resistance."),
        ("Max PE OI strike", "Put strike with the most open interest: the level option writers expect to hold as support."),
        ("Max pain", "Strike where option buyers as a group would lose the most (writers pay the least) if the market expired there."),
        ("ATM strike / straddle", "Strike nearest the close, and the price of buying its call + put = the market's expected move to expiry."),
        ("All expiries", "Same OI totals across every listed expiry."),
        ("Units", "OI and volumes are as NSE reports them. Lot sizes and reporting changed over the years, so compare ratios (PCR) and changes rather than raw OI across decades."),
        ("Source", "NSE F&O bhavcopies (end-of-day), June 2001 onward. Intraday candles are not available for free over this history."),
    ]
    ws.append(["Column", "Meaning"])
    for c in ws[1]:
        c.fill, c.font = bx.HEAD_FILL, bx.HEAD_FONT
    for g in guide:
        ws.append(list(g))
    for r in ws.iter_rows(min_row=2):
        r[0].font = Font(bold=True)
        r[1].alignment = Alignment(wrap_text=True, vertical="top")
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 120
    wb.save(OUT_FILE)
    print("saved", OUT_FILE, len(t), "days", t.Date.min().date(), "->", t.Date.max().date())
    return t


if __name__ == "__main__":
    build()

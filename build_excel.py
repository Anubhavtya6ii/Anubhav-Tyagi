"""Turn backtest results into a formatted Excel workbook."""
import datetime

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

import backtest as bt

HEAD_FILL = PatternFill("solid", fgColor="1F3864")
HEAD_FONT = Font(bold=True, color="FFFFFF")
PCT_COLS = {"Total Return", "CAGR", "Max Drawdown", "Win Rate", "Time in Market"}
RS_COLS = {"Start Rs", "Final Rs", "B&H Final Rs", "Best Final Rs", "Top Final Rs", "Best Final Rs (since 2018)"}
NUM_COLS = {"Sharpe", "Profit Factor", "Sharpe 1st 60% (in-sample)", "Sharpe last 40% (out-of-sample)",
            "Best Sharpe", "Best Out-of-sample Sharpe"}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
RS_FMT, PCT_FMT = '"₹"#,##0', '0.0%'


def write_df(ws, df, row=1, pct_all=False, rs_all=False, color=False):
    """Write df at `row`, style header, apply number formats. Returns next free row."""
    for j, col in enumerate(df.columns, 1):
        c = ws.cell(row=row, column=j, value=str(col))
        c.fill, c.font = HEAD_FILL, HEAD_FONT
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for i, rec in enumerate(df.itertuples(index=False), row + 1):
        for j, (col, v) in enumerate(zip(df.columns, rec), 1):
            if isinstance(v, (float, np.floating)) and (np.isnan(v) or np.isinf(v)):
                v = None
            elif isinstance(v, np.generic):
                v = v.item()
            c = ws.cell(row=i, column=j, value=v)
            if isinstance(v, datetime.date):
                c.number_format = "yyyy-mm-dd"
            elif col in PCT_COLS or (pct_all and j > 2):
                c.number_format = PCT_FMT
            elif col in RS_COLS or (rs_all and j > 2):
                c.number_format = RS_FMT
            elif col in NUM_COLS:
                c.number_format = "0.00"
    if color and len(df):
        rng = f"{get_column_letter(3)}{row + 1}:{get_column_letter(len(df.columns))}{row + len(df)}"
        ws.conditional_formatting.add(rng, ColorScaleRule(
            start_type="num", start_value=-0.3, start_color="F8696B",
            mid_type="num", mid_value=0, mid_color="FFFFFF",
            end_type="num", end_value=0.3, end_color="63BE7B"))
    return row + len(df) + 2


def finish(ws, freeze="A2", widths=None):
    ws.freeze_panes = freeze
    for j in range(1, ws.max_column + 1):
        letter = get_column_letter(j)
        longest = max((len(str(c.value)) for c in ws[letter][:60] if c.value is not None), default=8)
        ws.column_dimensions[letter].width = min(max(10, longest + 2), 38)
    for k, w in (widths or {}).items():
        ws.column_dimensions[k].width = w


def month_returns(net):
    return (1 + net).groupby([net.index.year, net.index.month]).prod() - 1


def month_grid(net):
    m = month_returns(net).unstack()
    m.columns = [MONTHS[i - 1] for i in m.columns]
    m = m.reindex(columns=MONTHS)
    m["Year total"] = (1 + net).groupby(net.index.year).prod() - 1
    m.insert(0, "Year", m.index)
    return m.reset_index(drop=True)


def build(res, series):
    best = bt.pick_best(res)
    full, since = "Full history", f"Since {bt.COMMON_START.year}"
    wb = Workbook()

    # ------------------------------------------------ Summary
    ws = wb.active
    ws.title = "Summary"
    rows = []
    for a in bt.ASSETS:
        r_full = res[(res.Asset == a) & (res.Window == full)].set_index("Strategy")
        r_since = res[(res.Asset == a) & (res.Window == since)].set_index("Strategy")
        b, b2 = best[(a, full)], best[(a, since)]
        bh, top = r_full.loc[bt.BENCH], r_full.drop(bt.BENCH)["Final Rs"].idxmax()
        row = {"Asset": a, "Data from": bh.Start, "Years": bh.Years, "Start Rs": bt.CAPITAL,
               "B&H Final Rs": bh["Final Rs"], "B&H CAGR": bh.CAGR, "B&H Max Drawdown": bh["Max Drawdown"],
               "Best strategy (risk-adjusted)": b or "none passed filter"}
        if b:
            x = r_full.loc[b]
            row.update({"Best Final Rs": x["Final Rs"], "CAGR": x.CAGR, "Max Drawdown": x["Max Drawdown"],
                        "Best Sharpe": x.Sharpe, "Best Out-of-sample Sharpe": x["Sharpe last 40% (out-of-sample)"]})
        row.update({"Highest-Rs strategy": top, "Top Final Rs": r_full.loc[top, "Final Rs"],
                    f"Best strategy since {bt.COMMON_START.year}": b2 or "none passed filter"})
        if b2:
            row["Best Final Rs (since 2018)"] = r_since.loc[b2, "Final Rs"]
        rows.append(row)
    summ = pd.DataFrame(rows)
    ws["A1"] = f"Rs 25,000 invested at the start of each asset's history: what is it worth today?"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = ("Past backtest results, not a promise of future returns. Not financial advice. "
                "'Best' = highest Sharpe among active strategies with >=10 trades that also had positive Sharpe on the last 40% of data.")
    ws["A2"].font = Font(italic=True, color="7F7F7F")
    pct = {"B&H CAGR", "B&H Max Drawdown", "CAGR", "Max Drawdown"}
    PCT_COLS.update(pct)
    write_df(ws, summ, row=4)
    ws.row_dimensions[4].height = 45
    finish(ws, freeze="B5")

    # ------------------------------------------------ Matrix sheets (strategy x asset, final Rs)
    for window, title in ((full, "Matrix_Full"), (since, f"Matrix_Since{bt.COMMON_START.year}")):
        ws = wb.create_sheet(title)
        m = res[res.Window == window].pivot(index="Strategy", columns="Asset", values="Final Rs")
        m = m.reindex(index=list(bt.STRATEGIES), columns=bt.ASSETS).reset_index()
        ws["A1"] = (f"Rs 25,000 -> final value today ({window}). Each asset starts on its own first date."
                    if window == full else f"Rs 25,000 -> final value today, all assets starting {bt.COMMON_START.date()}.")
        ws["A1"].font = Font(bold=True)
        write_df(ws, m, row=3, rs_all=False)
        for row in ws.iter_rows(min_row=4, min_col=2, max_col=1 + len(bt.ASSETS)):
            for c in row:
                c.number_format = RS_FMT
        for j in range(2, 2 + len(bt.ASSETS)):
            col = get_column_letter(j)
            ws.conditional_formatting.add(f"{col}4:{col}{3 + len(m)}", ColorScaleRule(
                start_type="min", start_color="F8696B", mid_type="percentile", mid_value=50,
                mid_color="FFEB84", end_type="max", end_color="63BE7B"))
        finish(ws, freeze="B4", widths={"A": 34})

    # ------------------------------------------------ All results
    for window, title in ((full, "All_Results_Full"), (since, f"All_Results_Since{bt.COMMON_START.year}")):
        ws = wb.create_sheet(title)
        d = res[res.Window == window].drop(columns="Window")
        write_df(ws, d, row=1)
        ws.row_dimensions[1].height = 45
        ws.auto_filter.ref = ws.dimensions
        finish(ws, freeze="C2")

    # ------------------------------------------------ Yearly (full history)
    yr, bal = [], []
    for a in bt.ASSETS:
        for s in bt.STRATEGIES:
            net, eq = series[(a, s, full)]
            y = (1 + net).groupby(net.index.year).prod() - 1
            yr.append({"Asset": a, "Strategy": s, **y.to_dict()})
            bal.append({"Asset": a, "Strategy": s, **eq.groupby(eq.index.year).last().to_dict()})
    ws = wb.create_sheet("Yearly_Return")
    ws["A1"] = "Calendar-year return of each strategy (first and last year may be partial)"
    ws["A1"].font = Font(bold=True)
    write_df(ws, pd.DataFrame(yr).sort_index(axis=1, key=lambda c: c.map(lambda v: (isinstance(v, int), v if isinstance(v, int) else 0))),
             row=3, pct_all=True, color=True)
    finish(ws, freeze="C4", widths={"B": 32})
    ws = wb.create_sheet("Yearly_Balance")
    ws["A1"] = "What Rs 25,000 had grown to at the end of each year"
    ws["A1"].font = Font(bold=True)
    write_df(ws, pd.DataFrame(bal).sort_index(axis=1, key=lambda c: c.map(lambda v: (isinstance(v, int), v if isinstance(v, int) else 0))),
             row=3, rs_all=True)
    finish(ws, freeze="C4", widths={"B": 32})

    # ------------------------------------------------ Month of year + weekday
    mo, wd = [], []
    for a in bt.ASSETS:
        for s in bt.STRATEGIES:
            net, _ = series[(a, s, full)]
            m = month_returns(net).groupby(level=1).mean()
            mo.append({"Asset": a, "Strategy": s, **{MONTHS[k - 1]: v for k, v in m.items()}})
            d = net.groupby(net.index.dayofweek).mean()
            wd.append({"Asset": a, "Strategy": s, **{DAYS[k]: v for k, v in d.items()}})
    ws = wb.create_sheet("Month_of_Year")
    ws["A1"] = "Average return in each calendar month (e.g. average of all Januaries)"
    ws["A1"].font = Font(bold=True)
    write_df(ws, pd.DataFrame(mo), row=3, pct_all=True, color=True)
    finish(ws, freeze="C4", widths={"B": 32})
    ws = wb.create_sheet("Weekday")
    ws["A1"] = "Average daily return by weekday (Sat/Sun only exist for crypto)"
    ws["A1"].font = Font(bold=True)
    write_df(ws, pd.DataFrame(wd), row=3, pct_all=True)
    for row in ws.iter_rows(min_row=4, min_col=3):
        for c in row:
            c.number_format = "0.000%"
    ws.conditional_formatting.add(f"C4:I{3 + len(wd)}", ColorScaleRule(
        start_type="num", start_value=-0.002, start_color="F8696B", mid_type="num", mid_value=0,
        mid_color="FFFFFF", end_type="num", end_value=0.002, end_color="63BE7B"))
    finish(ws, freeze="C4", widths={"B": 32})

    # ------------------------------------------------ Per-asset year x month grids
    for a in bt.ASSETS:
        ws = wb.create_sheet(f"{a}_Monthly")
        row = 1
        picks = [best[(a, full)], bt.BENCH]
        for s in [p for p in picks if p]:
            net, eq = series[(a, s, full)]
            ws.cell(row=row, column=1, value=f"{a} - {s}: monthly returns").font = Font(bold=True, size=12)
            ws.cell(row=row, column=6, value=f"Rs 25,000 -> Rs {eq.iloc[-1]:,.0f}").font = Font(bold=True)
            g = month_grid(net)
            ws.conditional_formatting.add(f"B{row + 2}:N{row + 1 + len(g)}", ColorScaleRule(
                start_type="num", start_value=-0.1, start_color="F8696B", mid_type="num", mid_value=0,
                mid_color="FFFFFF", end_type="num", end_value=0.1, end_color="63BE7B"))
            row = write_df(ws, g, row=row + 1, pct_all=True)
        finish(ws, freeze="B1", widths={"A": 10})

    # ------------------------------------------------ Assumptions
    ws = wb.create_sheet("Assumptions")
    lines = [
        ("What this is", "Backtest of standard, un-optimised technical strategies. Every strategy uses its textbook parameters; nothing was tuned to maximise the result."),
        ("Capital", f"Rs {bt.CAPITAL:,} at the first date of each asset (Full history) or on {bt.COMMON_START.date()} (Since {bt.COMMON_START.year}). Profits are reinvested (compounding)."),
        ("Instruments", "Nifty 50 (^NSEI), Sensex (^BSESN), Bank Nifty (^NSEBANK) as index levels; Bitcoin and Ethereum in USD converted to INR at the daily USD/INR rate. Data: Yahoo Finance, daily."),
        ("Execution", "Signal is read at the close; the trade happens at the NEXT day's open (no look-ahead). Long-only; cash earns 0%; no leverage, no shorting."),
        ("Costs", f"{bt.COST:.2%} per side (brokerage + slippage) on every entry/exit. Taxes, STT, impact cost, funding and futures roll costs are NOT included."),
        ("Indices", "Indices cannot be bought directly; assume an index fund/ETF/futures tracking them closely. Dividends are not included (price index)."),
        ("Max Drawdown", "Biggest fall from a previous peak of the account value."),
        ("Sharpe", "Return per unit of volatility (annualised, risk-free rate 0). Higher is better."),
        ("In/Out-of-sample", "Sharpe is also shown for the first 60% and last 40% of each history. A strategy that only works in the first part is likely curve-fit."),
        ("'Best strategy'", "Highest full-period Sharpe among active strategies with at least 10 trades and a positive out-of-sample Sharpe. 'Highest-Rs strategy' is shown separately because it can carry far bigger drawdowns."),
        ("Trade stats", "Win rate and profit factor are per completed trade (an open trade is marked to market at the last close)."),
        ("Caveats", "Past performance does not guarantee future results. Crypto history is short and dominated by a few huge bull runs. This is not financial advice."),
    ]
    ws.append(["Topic", "Detail"])
    for c in ws[1]:
        c.fill, c.font = HEAD_FILL, HEAD_FONT
    for l in lines:
        ws.append(list(l))
    for row in ws.iter_rows(min_row=2):
        row[0].font = Font(bold=True)
        row[1].alignment = Alignment(wrap_text=True, vertical="top")
    ws.column_dimensions["A"].width = 20
    ws.column_dimensions["B"].width = 120

    wb.save(bt.OUT_FILE)
    print("saved", bt.OUT_FILE)

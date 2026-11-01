"""Turn the option-buying lab results into answers + Option_Buying_Lab.xlsx."""
import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Font

import build_excel as bx

OUT_FILE = "Option_Buying_Lab.xlsx"
GRID = ["Strike", "Exit"]
STRIKE_ORDER = ["ITM 100", "ITM 50", "ATM", "OTM 50", "OTM 100", "Delta 0.30", "Delta 0.40", "Delta 0.50", "Delta 0.60"]


def pct(x):
    return f"{x * 100:+.2f}%"


def load():
    res = pd.read_pickle("data/option_buying_results.pkl")
    base = pd.read_pickle("data/option_buying_baseline.pkl")
    stress = pd.read_pickle("data/option_buying_stress.pkl")
    return res, base, stress


def tables(res, base):
    five = res[(res["Entry timeframe"] == "5-min") & (res.Contract == "nearest weekly")]
    allday = five[five["Day type"] == "All days"]
    t = {}
    t["strike_x_exit"] = allday.pivot_table(index="Strike", columns="Exit", values="Avg return %", aggfunc="mean").reindex(STRIKE_ORDER)
    rb = base[base["Day type"] == "All days"].pivot_table(index="Strike", columns="Exit", values="Avg return %", aggfunc="mean").reindex(STRIKE_ORDER)
    t["random_strike_x_exit"] = rb
    t["by_signal"] = allday.groupby("Signal").agg(**{"Mean return % (all 81 set-ups)": ("Avg return %", "mean"),
                                                    "Share of set-ups profitable": ("Avg return %", lambda x: (x > 0).mean()),
                                                    "Best set-up return %": ("Avg return %", "max"),
                                                    "Mean win rate": ("Win rate", "mean"),
                                                    "Mean OOS return %": ("OOS avg %", "mean"),
                                                    "Set-ups that passed": ("Passed", "sum")}).sort_values("Mean return % (all 81 set-ups)", ascending=False)
    t["day_type"] = five.pivot_table(index="Day type", columns="Strike", values="Avg return %", aggfunc="mean")[STRIKE_ORDER]
    t["day_type_rand"] = base.pivot_table(index="Day type", columns="Strike", values="Avg return %", aggfunc="mean")[STRIKE_ORDER]
    t["delta"] = allday[allday.Strike.str.startswith("Delta")].groupby("Strike").agg(**{"Mean return %": ("Avg return %", "mean"), "Mean win rate": ("Win rate", "mean"),
                                                                                          "Mean avg premium": ("Avg premium", "mean"), "Mean avg Rs/trade": ("Avg Rs (1 lot)", "mean")})
    t["exit"] = allday.groupby("Exit").agg(**{"Mean return %": ("Avg return %", "mean"), "Mean win rate": ("Win rate", "mean"),
                                              "Mean avg Rs/trade": ("Avg Rs (1 lot)", "mean"), "Mean hold (bars)": ("Avg hold (bars)", "mean"),
                                              "Mean profit factor": ("Profit factor", "mean")})
    t["exit_rand"] = base[base["Day type"] == "All days"].groupby("Exit")["Avg return %"].mean().rename("Random-entry return %")
    # 1-min vs 5-min, matched on signal / strike / exit
    one = res[(res["Entry timeframe"] == "1-min") & (res["Day type"] == "All days")]
    m = one.merge(five[five["Day type"] == "All days"], on=["Signal", "Strike", "Exit"], suffixes=(" 1m", " 5m"))
    t["tf_matched"] = m
    t["tf_summary"] = pd.DataFrame({"Mean return % (1-min)": [m["Avg return % 1m"].mean()], "Mean return % (5-min)": [m["Avg return % 5m"].mean()],
                                    "Share where 1-min is better": [(m["Avg return % 1m"] > m["Avg return % 5m"]).mean()],
                                    "Mean trades (1-min)": [m["Trades 1m"].mean()], "Mean trades (5-min)": [m["Trades 5m"].mean()], "Matched set-ups": [len(m)]})
    nxt = res[(res.Contract == "next weekly") & (res["Day type"] == "All days")]
    nm = nxt.merge(allday, on=["Signal", "Strike", "Exit"], suffixes=(" next", " nearest"))
    t["contract"] = pd.DataFrame({"Mean return % (next weekly)": [nm["Avg return % next"].mean()], "Mean return % (nearest weekly)": [nm["Avg return % nearest"].mean()],
                                  "Share where next is better": [(nm["Avg return % next"] > nm["Avg return % nearest"]).mean()], "Matched set-ups": [len(nm)]})
    nm0 = res[(res.Contract == "next weekly") & (res["Day type"] == "Expiry day (0DTE)")].merge(
        five[five["Day type"] == "Expiry day (0DTE)"], on=["Signal", "Strike", "Exit"], suffixes=(" next", " nearest"))
    t["contract_expiry_day"] = pd.DataFrame({"Mean return % (next weekly, on expiry day)": [nm0["Avg return % next"].mean()],
                                             "Mean return % (expiring contract, 0DTE)": [nm0["Avg return % nearest"].mean()], "Matched set-ups": [len(nm0)]})
    return t


def luck(res, base):
    five = res[(res["Entry timeframe"] == "5-min") & (res.Contract == "nearest weekly")]
    n_real, p_real = len(five), int(five.Passed.sum())
    per_seed = base.groupby("Signal").agg(n=("Passed", "size"), passed=("Passed", "sum"))
    rate = per_seed.passed.sum() / per_seed.n.sum()
    return n_real, p_real, rate, per_seed


def build():
    res, base, stress = load()
    t = tables(res, base)
    n_real, p_real, null_rate, per_seed = luck(res, base)
    five = res[(res["Entry timeframe"] == "5-min") & (res.Contract == "nearest weekly")]
    top = res.assign(Score=res[["IS t", "OOS t"]].min(axis=1)).sort_values("Score", ascending=False)
    robust = pd.DataFrame()
    if len(stress):
        robust = stress.copy()
        robust["Still profitable under stress"] = robust["Stress OOS avg %"] > 0
    ans = []

    def add(q, a, verdict):
        ans.append({"Question": q, "What the data says": a, "Verdict": verdict})

    sx = t["strike_x_exit"]
    rx = t["random_strike_x_exit"]
    add("Do ANY of the entry signals make option buying profitable?",
        f"{p_real} of {n_real} set-ups passed (profitable before AND after 2022, t-score thresholds). Random entries pass {null_rate:.2%} of the time, "
        f"so about {null_rate * n_real:.0f} would pass by luck. Mean return of ALL signal set-ups: {pct(five['Avg return %'].mean())} per trade; "
        f"random entries: {pct(base['Avg return %'].mean())}.",
        "No edge beyond luck" if p_real <= null_rate * n_real * 1.5 + 2 else "Some set-ups pass: check the stress sheet")
    bs = t["by_signal"]
    dv = pd.read_pickle("data/direction_value.pkl")
    fprev = pd.read_pickle("data/direction_extra.pkl")
    top_dv = dv.sort_values("Shuffle z (all)", ascending=False).head(2)
    add("Does any signal pick the better side (CE vs PE) at all? (same bar, same strike, same exit, direction flipped / shuffled)",
        f"Of {len(dv)} signals the best shuffle z-scores are {top_dv.iloc[0].Signal} ({top_dv.iloc[0]['Shuffle z (all)']:+.2f}; discovery {top_dv.iloc[0]['Shuffle z (IS)']:+.2f}, validation {top_dv.iloc[0]['Shuffle z (OOS)']:+.2f}) and "
        f"{top_dv.iloc[1].Signal} ({top_dv.iloc[1]['Shuffle z (all)']:+.2f}; {top_dv.iloc[1]['Shuffle z (IS)']:+.2f} / {top_dv.iloc[1]['Shuffle z (OOS)']:+.2f}). Both are just 'follow yesterday's direction' at 09:35: plain "
        f"follow-yesterday gives z {fprev.iloc[0]['Shuffle z']:+.2f} with no OI at all, but only in 2022-23 ({fprev.iloc[5]['Shuffle z']:+.2f}); 2019-21 {fprev.iloc[4]['Shuffle z']:+.2f}, 2024-26 {fprev.iloc[6]['Shuffle z']:+.2f}. "
        f"A best-of-{len(dv)} z of about 2 is what chance alone produces. RSI reversal is significantly WRONG-way (z {dv.set_index('Signal').loc['RSI reversal (30/70)', 'Shuffle z (all)']:+.2f}).",
        "No signal reliably picks the better side. Following yesterday is the only candidate and it is a one-regime effect")
    add("Which signal family is best / worst?",
        f"Best mean return: {bs.index[0]} ({pct(bs.iloc[0, 0])}); worst: {bs.index[-1]} ({pct(bs.iloc[-1, 0])}). "
        f"Spread between best and worst is {(bs.iloc[0, 0] - bs.iloc[-1, 0]) * 100:.1f} percentage points, versus random {pct(base['Avg return %'].mean())}.",
        "See Signal_Ranking")
    best_s = sx.mean(axis=1).idxmax()
    worst_s = sx.mean(axis=1).idxmin()
    add("ITM vs ATM vs OTM, and 0.30 / 0.40 / 0.50 / 0.60 delta",
        f"Mean return across all signals and exits: " + ", ".join(f"{k} {pct(v)}" for k, v in sx.mean(axis=1).items()) +
        f". Same table for random entries: " + ", ".join(f"{k} {pct(v)}" for k, v in rx.mean(axis=1).items()) + f". Best: {best_s}; worst: {worst_s}.",
        "Deeper in-the-money loses least (smaller spread and theta as % of premium); far OTM loses most")
    ex = t["exit"]["Mean return %"]
    add("Fixed vs percentage stop-loss; fixed target vs trailing; time exits",
        "Mean return by exit: " + "; ".join(f"{k}: {pct(v)}" for k, v in ex.sort_values(ascending=False).items()) +
        ". Random-entry returns for the same exits: " + "; ".join(f"{k}: {pct(v)}" for k, v in t["exit_rand"].sort_values(ascending=False).items()) + ".",
        "Exit style changes the loss, but no exit turns a no-edge signal profitable")
    dt = t["day_type"]
    add("Expiry day vs other days",
        "Mean return by day type (all strikes): " + "; ".join(f"{k}: {pct(v)}" for k, v in dt.mean(axis=1).items()) +
        ". Random entries: " + "; ".join(f"{k}: {pct(v)}" for k, v in t["day_type_rand"].mean(axis=1).items()) + ".",
        "Expiry-day buying is the most expensive: premium is small, spread and theta take a larger share")
    tf = t["tf_summary"].iloc[0]
    add("1-minute vs 5-minute entries",
        f"Matched on signal / strike / exit ({int(tf['Matched set-ups'])} set-ups): 1-min {pct(tf['Mean return % (1-min)'])} vs 5-min {pct(tf['Mean return % (5-min)'])} per trade; "
        f"1-min is better in {tf['Share where 1-min is better']:.0%} of them. 1-min makes ~{tf['Mean trades (1-min)'] / max(tf['Mean trades (5-min)'], 1):.1f}x as many signals per day.",
        "Faster entry gives more, noisier signals; it does not create an edge")
    ct = t["contract"].iloc[0]
    ce = t["contract_expiry_day"].iloc[0]
    add("Nearest weekly vs next weekly contract",
        f"All days: next weekly {pct(ct.iloc[0])} vs nearest {pct(ct.iloc[1])} (next is better in {ct.iloc[2]:.0%} of {int(ct.iloc[3])} matched set-ups). "
        f"On expiry day: next weekly {pct(ce.iloc[0])} vs the expiring contract {pct(ce.iloc[1])}.",
        "Avoiding the expiring contract cuts decay, not the missing edge")
    add("Not testable on history: IV expansion + premium momentum, futures trend confirmation, intraday OI",
        "Intraday implied vol, intraday futures prices and intraday OI exist only for contracts listed today (a few weeks for weeklies; expired contracts "
        "are not served by Dhan: rollingoption returns DH-905). Proxies used: previous-day VIX expansion + opening-range breakout, daily futures trend + EMA cross, "
        "previous-day futures OI state.", "Proxies only. Needs forward data capture")

    bx.PCT_COLS.update({"Win rate", "Avg return %", "IS avg %", "OOS avg %", "Share of set-ups profitable", "Mean win rate", "Mean return %",
                        "Mean return % (all 81 set-ups)", "Best set-up return %", "Mean OOS return %", "Mean return % (1-min)", "Mean return % (5-min)",
                        "Share where 1-min is better", "Mean return % (next weekly)", "Mean return % (nearest weekly)", "Share where next is better",
                        "Mean return % (next weekly, on expiry day)", "Mean return % (expiring contract, 0DTE)", "Random-entry return %", "Base avg %",
                        "Stress avg %", "Base OOS avg %", "Stress OOS avg %"})
    bx.RS_COLS.update({"Avg Rs (1 lot)", "Total Rs (1 lot)", "Max drawdown Rs", "Mean avg Rs/trade"})
    bx.PCT_COLS.update({"Signal-direction return %", "Opposite-direction return %", "Long share", "Shuffle z"})
    bx.NUM_COLS.update({"Value of direction (pp)", "Shuffle z", "Shuffle z (all)", "Shuffle z (IS)", "Shuffle z (OOS)", "t-score", "IS value (pp)", "OOS value (pp)"})
    bx.NUM_COLS.update({"Profit factor", "IS t", "OOS t", "Avg premium", "Avg hold (bars)", "Mean avg premium", "Mean hold (bars)", "Mean profit factor",
                        "Stress OOS t", "Mean trades (1-min)", "Mean trades (5-min)"})
    wb = Workbook()
    ws = wb.active
    ws.title = "Answers"
    ws["A1"] = "NIFTY intraday option BUYING lab: answers (modelled premiums, weekly era Mar 2019 - Oct 2026, 1 lot = 65)"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = ("Premiums are Black-Scholes with each contract's IV from the PREVIOUS day's real NSE closes on a trading-time clock; the model matches real "
                "15-minute premium changes (corr 0.96) but its level is only good to ~9%. Read results as comparisons between set-ups. Not financial advice.")
    ws["A2"].font = Font(italic=True, color="7F7F7F")
    bx.write_df(ws, pd.DataFrame(ans), row=4)
    for r in ws.iter_rows(min_row=5, min_col=1, max_col=3):
        for c in r:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    bx.finish(ws, freeze="A5", widths={"A": 46, "B": 120, "C": 52})

    def sheet(name, title, df, fmt=None, color=None, index=True):
        w = wb.create_sheet(name)
        w["A1"] = title
        w["A1"].font = Font(bold=True)
        d = df.reset_index() if index else df
        bx.write_df(w, d, row=3)
        if fmt:
            for r in w.iter_rows(min_row=4, min_col=2):
                for c in r:
                    c.number_format = fmt
        if color:
            rng = f"B4:{bx.get_column_letter(d.shape[1])}{3 + len(d)}"
            w.conditional_formatting.add(rng, ColorScaleRule(start_type="num", start_value=color[0], start_color="F8696B", mid_type="num", mid_value=0,
                                                              mid_color="FFFFFF", end_type="num", end_value=color[1], end_color="63BE7B"))
        bx.finish(w, freeze="B4", widths={"A": 44})
        return w

    sheet("Direction_Value", "Value of each signal's DIRECTION: paired against the opposite side at the same bar/strike/exit; shuffle z removes long/short bias", dv.set_index("Signal"))
    sheet("Follow_Previous_Day", "Is the OI signal just 'follow yesterday'? Plain follow / fade of the previous day's direction (entry ~09:35)", fprev, index=False)
    sheet("Signal_Ranking", "Mean return per trade across the 81 strike x exit set-ups of each signal (5-min, nearest weekly, all days)", t["by_signal"])
    sheet("Strike_x_Exit", "Mean return per trade by strike and exit, averaged over all signals", sx, fmt="0.00%", color=(-0.06, 0.06))
    sheet("Random_Baseline", "Same table for RANDOM entries (no signal): the pure cost of buying options", rx, fmt="0.00%", color=(-0.06, 0.06))
    sheet("Delta_Test", "Delta 0.30 / 0.40 / 0.50 / 0.60 (averages over all signals and exits)", t["delta"])
    sheet("Exit_Compare", "Exit packages (averages over all signals and strikes) with the random-entry benchmark", t["exit"].join(t["exit_rand"]))
    sheet("Expiry_Days", "Mean return by day type and strike (signals)", t["day_type"], fmt="0.00%", color=(-0.08, 0.08))
    sheet("Expiry_Days_Random", "Mean return by day type and strike (random entries)", t["day_type_rand"], fmt="0.00%", color=(-0.08, 0.08))
    sheet("Timeframe_1m_vs_5m", "1-min vs 5-min entries, matched set-ups", t["tf_summary"], index=False)
    sheet("Contract_Choice", "Nearest weekly vs next weekly contract", pd.concat([t["contract"], t["contract_expiry_day"]], axis=1), index=False)

    cols = ["Signal", "Entry timeframe", "Contract", "Strike", "Exit", "Day type", "Trades", "Win rate", "Avg return %", "Avg Rs (1 lot)", "Total Rs (1 lot)",
            "Profit factor", "Max drawdown Rs", "Avg premium", "Avg hold (bars)", "IS trades", "IS avg %", "IS t", "OOS trades", "OOS avg %", "OOS t", "Passed"]
    w = wb.create_sheet("Top_Configs")
    w["A1"] = ("Top 40 set-ups by the weaker of the in-sample and out-of-sample t-scores. With thousands of set-ups tested, the best few are expected by luck; "
               f"random entries pass {null_rate:.2%} of set-ups ({null_rate * n_real:.0f} of {n_real} expected by chance).")
    w["A1"].font = Font(bold=True, color="C00000")
    bx.write_df(w, top[cols].head(40), row=3)
    bx.finish(w, freeze="C4", widths={"A": 46})
    if len(robust):
        sheet("Stress_Test", "Set-ups that passed, re-run with extra premium decay (0.22% of premium per 15 min)", robust, index=False)
    w = wb.create_sheet("All_Configs")
    w["A1"] = f"All {len(res)} set-up summaries"
    w["A1"].font = Font(bold=True)
    bx.write_df(w, res[cols], row=3)
    w.auto_filter.ref = f"A3:{bx.get_column_letter(len(cols))}{3 + len(res)}"
    bx.finish(w, freeze="C4", widths={"A": 46})

    w = wb.create_sheet("Method_and_Limits")
    lines = [
        ("Data", "NIFTY 1-min and 5-min index candles (Dhan, Apr 2017 - Oct 2026); NSE closing option prices for every strike (2001 - Oct 2026) to read each contract's implied vol; India VIX; daily futures and OI."),
        ("Option price model", "Black-Scholes. IV for day D = ATM IV of the same expiry from day D-1's real closes. Time runs on a trading clock (70% of a day's variance in market hours, 30% overnight), checked against real Dhan 1-minute option prices (24 days, 428 contracts): "
                               "15-minute premium changes correlate 0.96 with real, premium level error ~9%, no systematic intraday drift left after the clock fix."),
        ("What the model cannot do", "Intraday IV moves (IV rising on a spike, crushing after), bid-ask widening in fast markets, 0DTE gamma squeezes. Real long-option results could be better or worse than modelled; on 0DTE there was no real data to validate against."),
        ("Entry", "Signal on a closed bar, trade at the next bar's open. One trade per day, first signal between 09:30 and 14:30. Premium below 5 pts is skipped."),
        ("Strikes", "ITM/OTM are 50 or 100 index points from ATM; delta strikes use model delta at entry (nearest 50-point strike)."),
        ("Exits", "Stops/targets/trailing are checked on bar highs and lows of the modelled premium (a CE's premium high occurs at the index high, a PE's at the index low). If stop and target fall in one bar, the stop is assumed first. All trades end by 15:15."),
        ("Costs", "Half-spread max(0.30 pt, 0.5% of premium) each side (real ATM bid-ask ~0.9 pt); brokerage Rs 40 per round trip; STT on sell premium (0.0625% before Oct 2024, 0.1% after); exchange fees, GST, stamp duty."),
        ("Validation", "Trades before 2022-01-01 vs after. Pass = at least 40 trades each side, in-sample t >= 2, out-of-sample t >= 1 and positive. A random-entry run of the same grid gives the luck benchmark (12 runs)."),
        ("Not testable", "IV expansion + premium momentum (no intraday IV history), futures trend confirmation (intraday futures history only for the last weeks), intraday OI buildup. Proxies were used where stated; forward data capture is needed to test them properly."),
        ("Expired weekly options", "Dhan's expired-options endpoint (rollingoption) returned DH-905 for every request, so real intraday prices of expired weekly contracts are not available. That is why premiums are modelled."),
    ]
    w.append(["Topic", "Detail"])
    for c in w[1]:
        c.fill, c.font = bx.HEAD_FILL, bx.HEAD_FONT
    for l in lines:
        w.append(list(l))
    for r in w.iter_rows(min_row=2):
        r[0].font = Font(bold=True)
        r[1].alignment = Alignment(wrap_text=True, vertical="top")
    w.column_dimensions["A"].width = 24
    w.column_dimensions["B"].width = 130
    wb.save(OUT_FILE)
    print("saved", OUT_FILE)
    return res, base, t, pd.DataFrame(ans), robust, (n_real, p_real, null_rate), top


if __name__ == "__main__":
    pd.set_option("display.width", 250, "display.max_columns", 30, "display.max_colwidth", 400)
    res, base, t, ans, robust, lk, top = build()
    for _, r in ans.iterrows():
        print("\n*", r.Question, "\n ", r["What the data says"], "\n  =>", r.Verdict)

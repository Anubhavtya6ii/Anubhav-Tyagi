"""Does a signal's DIRECTION add value? Trade every signal event twice at the same bar, strike distance and exit: once in the
signal's direction and once in the opposite direction (CE <-> PE). The paired difference removes time-of-day, premium level
and theta effects, so what is left is purely 'did the signal pick the better side'."""
import numpy as np
import pandas as pd

import option_buying_lab as ol
import option_signals as sg

KEYS = ["day", "strike", "exit"]


def paired(B, F, idx, dirs, C, strikes=("ATM", "Delta 0.40", "ITM 50")):
    a = ol.simulate(B, F, idx, dirs, C, strikes=list(strikes))
    b = ol.simulate(B, F, idx, -dirs, C, strikes=list(strikes))
    m = a.merge(b, on=KEYS, suffixes=("", "_flip"))
    m["diff"] = m.ret - m.ret_flip
    return m


def perm_z(ev, runs=4000, seed=3):
    """z-score of the real direction choice against the same direction labels shuffled across the event days."""
    if len(ev) < 30:
        return np.nan
    call = np.where(ev.dir > 0, ev.ret, ev.ret_flip)
    put = np.where(ev.dir > 0, ev.ret_flip, ev.ret)
    lab = (ev.dir.to_numpy() > 0)
    real = np.where(lab, call, put).mean()
    rng = np.random.default_rng(seed)
    null = np.array([np.where(rng.permutation(lab), call, put).mean() for _ in range(runs)])
    return (real - null.mean()) / null.std() if null.std() > 0 else np.nan


def main():
    C = ol.Contracts()
    B = sg.Bars(5)
    F = sg.daily_features(B.days)
    vix = pd.read_csv("data/INDIAVIX.csv", index_col="Date", parse_dates=True)["VIX"]
    chg = (vix.shift(1) / vix.shift(2) - 1).reindex(B.days).fillna(0).to_numpy()
    S = sg.build_signals(B, F, chg)
    rows = []
    for name, (idx, dirs) in S.items():
        m = paired(B, F, idx, dirs, C)
        ev = m.groupby("day").agg(diff=("diff", "mean"), ret=("ret", "mean"), ret_flip=("ret_flip", "mean"), dir=("dir", "first"))  # one value per event day
        is_, oos = ev[ev.index < ol.SPLIT], ev[ev.index >= ol.SPLIT]
        rows.append({"Signal": name, "Event days": len(ev), "Signal-direction return %": ev.ret.mean(), "Opposite-direction return %": ev.ret_flip.mean(),
                     "Value of direction (pp)": ev["diff"].mean(), "t-score": ol.tstat(ev["diff"]),
                     "IS days": len(is_), "IS value (pp)": is_["diff"].mean(), "IS t": ol.tstat(is_["diff"]),
                     "OOS days": len(oos), "OOS value (pp)": oos["diff"].mean(), "OOS t": ol.tstat(oos["diff"]),
                     "Long share": (ev.dir > 0).mean(), "Shuffle z (all)": perm_z(ev), "Shuffle z (IS)": perm_z(ev[ev.index < ol.SPLIT]),
                     "Shuffle z (OOS)": perm_z(ev[ev.index >= ol.SPLIT])})
        print(f"{name:48s} n={len(ev):5d} value={ev['diff'].mean() * 100:+.2f}pp t={ol.tstat(ev['diff']):+.2f} | long {(ev.dir > 0).mean():.0%} | shuffle z all {rows[-1]['Shuffle z (all)']:+.2f} IS {rows[-1]['Shuffle z (IS)']:+.2f} OOS {rows[-1]['Shuffle z (OOS)']:+.2f}", flush=True)
    out = pd.DataFrame(rows).sort_values("t-score", ascending=False)
    out.to_pickle("data/direction_value.pkl")
    extra(B, F, C)
    return out


def extra(B, F, C):
    """Is the OI signal just 'follow yesterday'? Plain follow / fade of the previous day's direction, by period, and absolute P&L."""
    pdc = F.pdc.to_numpy()
    prev_dir = np.nan_to_num(np.sign(pdc - pd.Series(pdc).shift(1).to_numpy())).astype(int)
    st = F.oi_state.to_numpy()
    yr = pd.Series(B.days.year, index=B.days)
    cases = [("Follow previous day's direction", prev_dir, np.ones(len(B.days), bool)),
             ("Fade previous day's direction", -prev_dir, np.ones(len(B.days), bool)),
             ("Follow previous day, only OI buildup days", prev_dir, np.isin(st, ["long buildup", "short buildup"])),
             ("Follow previous day, only covering / unwinding days", prev_dir, np.isin(st, ["short covering", "long unwinding"]))]
    cases += [(f"Follow previous day, {a}-{b}", prev_dir, ((yr >= a) & (yr <= b)).to_numpy()) for a, b in ((2019, 2021), (2022, 2023), (2024, 2026))]
    rows = []
    for name, dirs, mask in cases:
        idx = np.where(mask & (dirs != 0), 3, -1)
        m = paired(B, F, idx, np.where(idx >= 0, dirs, 0), C)
        ev = m.groupby("day").agg(diff=("diff", "mean"), ret=("ret", "mean"), ret_flip=("ret_flip", "mean"), dir=("dir", "first"))
        rows.append({"Case": name, "Event days": len(ev), "Signal-direction return %": ev.ret.mean(), "Opposite-direction return %": ev.ret_flip.mean(),
                     "Value of direction (pp)": ev["diff"].mean(), "Shuffle z": perm_z(ev)})
    ex = pd.DataFrame(rows)
    ex.to_pickle("data/direction_extra.pkl")
    return ex


if __name__ == "__main__":
    main()

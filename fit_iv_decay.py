"""Fit IV_t = IV_prev_close * (1 + a - b*x), x = fraction of the session elapsed, on real Dhan 1-min option prices."""
import numpy as np
import pandas as pd

import optmodel as om

iv_t = pd.read_csv("data/atm_iv_table.csv", parse_dates=["date", "expiry"])
inst = pd.read_csv("dhan/raw/nifty_instruments.csv"); inst = inst[inst.INSTRUMENT == "OPTIDX"].copy(); inst["expiry"] = pd.to_datetime(inst.expiry)
idx = pd.read_csv("dhan/data/index/13_1.csv.gz", parse_dates=["time_ist"]).set_index("time_ist").close
days = sorted(set(idx.index.normalize()))
parts = []
for exp in ["2026-11-03", "2026-11-09", "2026-11-17", "2026-12-01"]:
    e = pd.Timestamp(exp)
    for _, r in inst[inst.expiry == e].iterrows():
        try:
            f = pd.read_csv(f"dhan/data/options/{int(r.SECURITY_ID)}_1.csv.gz", parse_dates=["time_ist"]).set_index("time_ist")
        except Exception:
            continue
        f = f[f.volume > 0]
        f = f[(f.index.hour * 60 + f.index.minute >= 9 * 60 + 25) & (f.index.hour * 60 + f.index.minute <= 15 * 60 + 10)]
        for d, g in f.groupby(f.index.normalize()):
            prev = [x for x in days if x < d]
            ivr = iv_t[(iv_t.date == prev[-1]) & (iv_t.expiry == e)] if prev else []
            if len(ivr) == 0:
                continue
            s = idx.reindex(g.index); ok = s.notna() & (g.close > 3)
            mon = (s[ok] - r.STRIKE_PRICE) * (1 if r.OPTION_TYPE == "CE" else -1)
            keep = (mon.abs() < 250).to_numpy()
            if keep.sum() < 20:
                continue
            ix = g.index[ok][keep]
            mins = np.array([t.hour * 60 + t.minute for t in ix])
            parts.append(pd.DataFrame({"date": d, "spot": s[ok][keep].to_numpy(), "k": r.STRIKE_PRICE, "call": r.OPTION_TYPE == "CE",
                                       "actual": g.close[ok][keep].to_numpy(), "iv": float(np.clip(ivr.iv.iloc[0], .06, .8)),
                                       "t": np.array([float(om.t_eff(d, e, m - 555)) for m in mins]), "x": (mins - 555) / 375.0}))
v = pd.concat(parts, ignore_index=True)
dts = sorted(v.date.unique()); print("rows", len(v), "days", len(dts))
def err(df, a, b):
    m = om.bs(df.spot.to_numpy(), df.k.to_numpy(), np.maximum(df.t.to_numpy(), 1e-6), df.iv.to_numpy() * (1 + a - b * df.x.to_numpy()), df.call.to_numpy())
    return float(np.mean(((m - df.actual.to_numpy()) / df.actual.to_numpy()) ** 2))
grid = [(a, b) for a in np.arange(-0.1, 0.41, 0.025) for b in np.arange(-0.1, 0.51, 0.025)]
def fit(df):
    best = min(grid, key=lambda p: err(df, *p)); return best, err(df, *best)
print("no adjustment      rel-rmse = %.4f" % np.sqrt(err(v, 0, 0)))
(a, b), e = fit(v); print("all days     a=%.3f b=%.3f rel-rmse = %.4f" % (a, b, np.sqrt(e)))
h1, h2 = v[v.date.isin(dts[:len(dts) // 2])], v[v.date.isin(dts[len(dts) // 2:])]
(a1, b1), e1 = fit(h1); (a2, b2), e2 = fit(h2)
print("first half   a=%.3f b=%.3f | applied to second half rel-rmse = %.4f (no adj %.4f, own fit %.4f)" % (a1, b1, np.sqrt(err(h2, a1, b1)), np.sqrt(err(h2, 0, 0)), np.sqrt(e2)))
print("second half  a=%.3f b=%.3f | applied to first half  rel-rmse = %.4f (no adj %.4f, own fit %.4f)" % (a2, b2, np.sqrt(err(h1, a2, b2)), np.sqrt(err(h1, 0, 0)), np.sqrt(e1)))
pd.Series({"a": a, "b": b}).to_json("data/iv_decay_fit.json")

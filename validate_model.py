"""Compare the Black-Scholes premium model with REAL Dhan 1-minute option prices (recent weekly contracts)."""
import numpy as np
import pandas as pd

import optmodel as om

iv_t = pd.read_csv("data/atm_iv_table.csv", parse_dates=["date", "expiry"])
inst = pd.read_csv("dhan/raw/nifty_instruments.csv")
inst = inst[inst.INSTRUMENT == "OPTIDX"].copy()
inst["expiry"] = pd.to_datetime(inst.expiry)
idx = pd.read_csv("dhan/data/index/13_1.csv.gz", parse_dates=["time_ist"]).set_index("time_ist").close
days = sorted(set(idx.index.normalize()))
rows = []
for exp in ["2026-11-03", "2026-11-09", "2026-11-17", "2026-12-01"]:
    e = pd.Timestamp(exp)
    for _, r in inst[inst.expiry == e].iterrows():
        try:
            f = pd.read_csv(f"dhan/data/options/{int(r.SECURITY_ID)}_1.csv.gz", parse_dates=["time_ist"])
        except Exception:
            continue
        if f.empty:
            continue
        f = f.set_index("time_ist")
        f = f[(f.volume > 0)]
        for d, g in f.groupby(f.index.normalize()):
            if d.weekday() > 4:
                continue
            prev = [x for x in days if x < d]
            if not prev:
                continue
            ivrow = iv_t[(iv_t.date == prev[-1]) & (iv_t.expiry == e)]
            if ivrow.empty:
                continue
            iv = float(np.clip(ivrow.iv.iloc[0], 0.06, 0.8))
            g = g[(g.index.hour * 60 + g.index.minute >= 9 * 60 + 25) & (g.index.hour * 60 + g.index.minute <= 15 * 60 + 10)]
            s = idx.reindex(g.index)
            ok = s.notna()
            if ok.sum() < 20:
                continue
            tleft = np.array([float(om.t_eff(d, e, t.hour * 60 + t.minute - 555)) for t in g.index[ok]])
            model = om.bs(s[ok].to_numpy(), r.STRIKE_PRICE, np.maximum(tleft, 1e-6), iv, r.OPTION_TYPE == "CE")
            rows.append(pd.DataFrame({"date": d, "expiry": e, "strike": r.STRIKE_PRICE, "type": r.OPTION_TYPE, "dte": (e - d).days,
                                      "spot": s[ok].to_numpy(), "actual": g.close[ok].to_numpy(), "model": model, "time": g.index[ok],
                                      "moneyness": (s[ok].to_numpy() - r.STRIKE_PRICE) * (1 if r.OPTION_TYPE == "CE" else -1)}))
v = pd.concat(rows, ignore_index=True)
v = v[v.actual > 3]
v["err_pts"] = v.model - v.actual
v["err_pct"] = v.err_pts / v.actual
v["band"] = pd.cut(v.moneyness.abs(), [-1, 100, 250, 500, 5000], labels=["ATM (<100)", "near (100-250)", "mid (250-500)", "far (>500)"])
print("rows:", len(v), "| days:", v.date.nunique(), "| contracts:", v.groupby(["expiry", "strike", "type"]).ngroups)
print("\nLEVEL error (model - actual), by moneyness:")
print(v.groupby("band", observed=True).agg(n=("err_pts", "size"), bias_pts=("err_pts", "mean"), median_abs_pts=("err_pts", lambda x: x.abs().median()),
                                           median_abs_pct=("err_pct", lambda x: x.abs().median())).round(3).to_string())
print("\nLEVEL error by days to expiry:")
print(v[v.band.isin(["ATM (<100)", "near (100-250)"])].groupby("dte").agg(n=("err_pts", "size"), bias_pct=("err_pct", "mean"), med_abs_pct=("err_pct", lambda x: x.abs().median())).round(3).head(9).to_string())
# changes over 15 minutes - what matters for a trade
v = v.sort_values(["expiry", "strike", "type", "time"])
out = []
for k, g in v.groupby(["expiry", "strike", "type", "date"]):
    g = g.set_index("time")
    a, m, s = g.actual.resample("15min").last().dropna(), g.model.resample("15min").last().dropna(), g.spot.resample("15min").last().dropna()
    j = pd.concat([a, m, s], axis=1, join="inner").dropna()
    j.columns = ["a", "m", "s"]
    if len(j) < 3:
        continue
    d = j.diff().dropna()
    d["moneyness"] = g.moneyness.iloc[0]; d["dte"] = g.dte.iloc[0]; d["call"] = k[2] == "CE"
    out.append(d)
d = pd.concat(out)
d = d[d.moneyness.abs() < 250]
print("\n15-min premium CHANGES (|moneyness|<250): n =", len(d))
print("  correlation(model, actual): %.3f" % d.m.corr(d.a), "| slope actual on model: %.3f" % np.polyfit(d.m, d.a, 1)[0])
up = d.s * np.where(d.call, 1, -1) > 0
print("  actual/model change ratio when spot moved FOR the position: %.3f | AGAINST: %.3f" % ((d.a[up].abs().sum() / d.m[up].abs().sum()), (d.a[~up].abs().sum() / d.m[~up].abs().sum())))
print("  mean actual change %.3f vs model %.3f pts (drift gap from IV/theta)" % (d.a.mean(), d.m.mean()))

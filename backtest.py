"""Backtest classic long-only strategies on Indian indices and crypto, starting with Rs 25,000.

Run `python fetch_data.py` first, then `python backtest.py` -> Strategy_Backtest_Results.xlsx
"""
import numpy as np
import pandas as pd

CAPITAL = 25_000
COST = 0.001  # 0.1% per side (brokerage + slippage)
COMMON_START = pd.Timestamp("2018-01-01")  # common window: Ethereum history starts Nov 2017
ASSETS = ["NIFTY50", "SENSEX", "BANKNIFTY", "BITCOIN", "ETHEREUM"]
CRYPTO = {"BITCOIN", "ETHEREUM"}
OUT_FILE = "Strategy_Backtest_Results.xlsx"


# ---------------------------------------------------------------- data
def load_asset(name):
    d = pd.read_csv(f"data/{name}.csv", index_col="Date", parse_dates=True)
    if name in CRYPTO:  # USD -> INR so every asset starts with the same Rs 25,000
        fx = pd.read_csv("data/USDINR.csv", index_col="Date", parse_dates=True)["Close"]
        fx = fx.reindex(d.index.union(fx.index)).ffill().bfill().reindex(d.index)
        d = d.mul(fx, axis=0)
    prev_close = d["Close"].shift(1)
    ok = (d["Open"] > 0) & (d["Open"] >= d["Low"] * 0.98) & (d["Open"] <= d["High"] * 1.02)
    d["Open"] = d["Open"].where(ok, prev_close).fillna(d["Close"])
    return d


# ---------------------------------------------------------------- indicators
def sma(s, n):
    return s.rolling(n).mean()


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(s, n):
    ch = s.diff()
    up = ch.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-ch.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def stateful(entry, exit_):
    """Hold from an entry signal until an exit signal."""
    index = entry.index
    entry, exit_ = entry.fillna(False).to_numpy(), exit_.fillna(False).to_numpy()
    pos = np.zeros(len(entry))
    p = 0
    for i in range(len(entry)):
        if p == 0 and entry[i]:
            p = 1
        elif p == 1 and exit_[i]:
            p = 0
        pos[i] = p
    return pd.Series(pos, index=index)


def supertrend_up(df, n=10, mult=3.0):
    h, l, c = df["High"].to_numpy(), df["Low"].to_numpy(), df["Close"].to_numpy()
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    atr = pd.Series(tr).ewm(alpha=1 / n, adjust=False).mean().to_numpy()
    hl2 = (h + l) / 2
    ub, lb = hl2 + mult * atr, hl2 - mult * atr
    fub, flb = ub.copy(), lb.copy()
    trend = np.zeros(len(c))
    for i in range(1, len(c)):
        fub[i] = ub[i] if (ub[i] < fub[i - 1] or c[i - 1] > fub[i - 1]) else fub[i - 1]
        flb[i] = lb[i] if (lb[i] > flb[i - 1] or c[i - 1] < flb[i - 1]) else flb[i - 1]
        if trend[i - 1] <= 0 and c[i] > fub[i - 1]:
            trend[i] = 1
        elif trend[i - 1] == 1 and c[i] < flb[i - 1]:
            trend[i] = 0
        else:
            trend[i] = trend[i - 1]
    trend[:n] = 0
    return pd.Series(trend, index=df.index)


# ---------------------------------------------------------------- strategies
# Each returns the position we WANT after today's close (1 = long, 0 = cash).
# The engine trades it at the next day's open.
def _state(df, entry, exit_):
    return stateful(entry, exit_)


def s_buy_hold(df):
    return pd.Series(1.0, index=df.index)


def s_sma_50_200(df):
    return (sma(df.Close, 50) > sma(df.Close, 200)).astype(float)


def s_sma_20_50(df):
    return (sma(df.Close, 20) > sma(df.Close, 50)).astype(float)


def s_ema_20_50(df):
    return (ema(df.Close, 20) > ema(df.Close, 50)).astype(float)


def s_rsi2(df):
    r = rsi(df.Close, 2)
    return _state(df, (r < 10) & (df.Close > sma(df.Close, 200)), r > 70)


def s_rsi14(df):
    r = rsi(df.Close, 14)
    return _state(df, r < 30, r > 70)


def s_macd(df):
    macd = ema(df.Close, 12) - ema(df.Close, 26)
    return (macd > ema(macd, 9)).astype(float)


def s_supertrend(df):
    return supertrend_up(df, 10, 3.0)


def s_bollinger(df):
    mid, sd = sma(df.Close, 20), df.Close.rolling(20).std()
    return _state(df, df.Close < mid - 2 * sd, df.Close > mid)


def s_donchian(df):
    hi20 = df.High.rolling(20).max().shift(1)
    lo10 = df.Low.rolling(10).min().shift(1)
    return _state(df, df.Close > hi20, df.Close < lo10)


STRATEGIES = {
    "Buy & Hold (benchmark)": s_buy_hold,
    "SMA 50/200 Golden Cross": s_sma_50_200,
    "SMA 20/50 Crossover": s_sma_20_50,
    "EMA 20/50 Trend": s_ema_20_50,
    "RSI(2) Pullback + 200 SMA": s_rsi2,
    "RSI(14) 30/70 Mean Reversion": s_rsi14,
    "MACD (12,26,9)": s_macd,
    "Supertrend (10,3)": s_supertrend,
    "Bollinger Band Reversion (20,2)": s_bollinger,
    "Donchian Breakout (20/10)": s_donchian,
}
BENCH = "Buy & Hold (benchmark)"


# ---------------------------------------------------------------- engine
def evaluate(asset, df, strat, window):
    start = None if window == "Full history" else COMMON_START
    idx = df.index
    want = STRATEGIES[strat](df).astype(float)
    if start is not None:
        want = want.where(idx >= start, 0.0)
    pos = want.shift(1).fillna(0.0)  # decided at yesterday's close, traded at today's open
    prev = pos.shift(1).fillna(0.0)
    gap = (df.Open / df.Close.shift(1) - 1).fillna(0.0)  # close -> open: old position
    intra = df.Close / df.Open - 1  # open -> close: new position
    net = prev * gap + pos * intra - (pos - prev).abs() * COST
    if start is not None:
        keep = idx >= start
        net, pos, prev = net[keep], pos[keep], prev[keep]
    equity = CAPITAL * (1 + net).cumprod()

    years = (net.index[-1] - net.index[0]).days / 365.25
    ppy = len(net) / years
    final = equity.iloc[-1]

    active = (pos == 1) | (prev == 1)
    grp = (active != active.shift()).cumsum()
    trades = [(1 + net[active & (grp == g)]).prod() - 1 for g in grp[active].unique()]
    tr = np.array(trades) if trades else np.array([])
    wins, losses = tr[tr > 0], tr[tr < 0]

    def sharpe(x):
        return x.mean() / x.std() * np.sqrt(ppy) if len(x) > 2 and x.std() > 0 else np.nan

    cut = int(len(net) * 0.6)
    res = {
        "Asset": asset, "Strategy": strat, "Window": window,
        "Start": net.index[0].date(), "End": net.index[-1].date(),
        "Years": round(years, 1), "Start Rs": CAPITAL, "Final Rs": final,
        "Total Return": final / CAPITAL - 1, "CAGR": (final / CAPITAL) ** (1 / years) - 1,
        "Max Drawdown": (equity / equity.cummax() - 1).min(), "Sharpe": sharpe(net),
        "Win Rate": len(wins) / len(tr) if len(tr) else np.nan,
        "Profit Factor": wins.sum() / -losses.sum() if len(losses) else np.nan,
        "Trades": len(tr), "Time in Market": float((pos == 1).mean()),
        "Sharpe 1st 60% (in-sample)": sharpe(net.iloc[:cut]),
        "Sharpe last 40% (out-of-sample)": sharpe(net.iloc[cut:]),
    }
    return res, net, equity


# ---------------------------------------------------------------- run everything
def run_all():
    results, series = [], {}
    for asset in ASSETS:
        df = load_asset(asset)
        for window in ("Full history", f"Since {COMMON_START.year}"):
            for strat in STRATEGIES:
                res, net, eq = evaluate(asset, df, strat, window)
                results.append(res)
                series[(asset, strat, window)] = (net, eq)
    return pd.DataFrame(results), series


def pick_best(res):
    """Best active strategy per asset: highest Sharpe among those that also worked out-of-sample."""
    out = {}
    for (asset, window), g in res.groupby(["Asset", "Window"]):
        g = g[(g.Strategy != BENCH) & (g.Trades >= 10) & (g["Sharpe last 40% (out-of-sample)"] > 0)]
        out[(asset, window)] = g.sort_values("Sharpe", ascending=False).iloc[0].Strategy if len(g) else None
    return out


if __name__ == "__main__":
    import build_excel
    build_excel.build(*run_all())

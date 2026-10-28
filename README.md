# Strategy backtests: Rs 25,000 start

Backtests nine classic long-only strategies (plus Buy & Hold) on Nifty 50, Sensex, Bank Nifty, Bitcoin and Ethereum,
and answers: *if I had started with Rs 25,000, what would it be worth today?*

Output: `Strategy_Backtest_Results.xlsx` (summary, strategy x asset matrix, year-wise, month-wise, weekday, per-asset monthly grids, assumptions).

```bash
pip install -r requirements.txt
python fetch_data.py   # downloads daily data from Yahoo Finance into data/
python backtest.py     # writes Strategy_Backtest_Results.xlsx
```

Signals are read at the close and traded at the next open, long-only, 0.1% cost per side, no taxes, parameters are textbook (not optimised).
Past results do not guarantee future returns. This is not financial advice.

## Nifty weekly options (hedged selling)

End-of-day backtest of iron condors / iron flies on Nifty weekly options from NSE bhavcopies (Feb 2019 onward).
Output: `Nifty_Options_Backtest.xlsx` (summary, slippage test, yearly, monthly, VIX regime, Rs 25,000 equity, every trade).

```bash
python fetch_nse_options.py     # ~2,600 NSE bhavcopies -> data/nse_options.csv.gz (Nifty + Bank Nifty, takes a while)
python options_backtest.py      # writes Nifty_Options_Backtest.xlsx
```

## Options strategy lab (Nifty, Bank Nifty, Bitcoin, Ethereum)

One engine for ~670 defined-risk option strategies (condors, flies, straddles, strangles, directional calls/puts,
debit and credit spreads, with trend / momentum / reversal signals and implied-vs-realized / vol-level filters),
measured in R (profit / money at risk), validated out-of-sample and against random weeks (luck check).
Output: `Options_Strategy_Lab.xlsx`.

```bash
python fetch_nse_options.py      # NSE bhavcopies
python fetch_deribit_options.py  # Deribit trade history snapshots + 08:00 settlement prices
python options_lab.py            # writes Options_Strategy_Lab.xlsx (~10 min, includes 20 luck-check runs)
```

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

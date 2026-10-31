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

## Dhan API: Nifty 5-minute candles and expired options

Needs the DhanHQ *Data APIs* plan. Credentials are read from the environment, never stored: `DHAN_CLIENT_ID`, `DHAN_ACCESS_TOKEN`.

```bash
python fetch_dhan.py            # both
python fetch_dhan.py 5min       # data/nifty_5min.csv.gz        (Nifty 50 index, Apr 2017 onward, IST, OHLCV)
python fetch_dhan.py options    # data/dhan_expired_week1/<year>.csv.gz   (weekly options, ATM-5..ATM+5, CE and PE, Jul 2020 onward)
```

Options columns: `datetime, expiry_flag, expiry_code, option_type, offset, strike, spot, open, high, low, close, volume, oi, iv`.
`offset` is the number of 50-point strikes from ATM at that moment (the "rolling" strike), `expiry_code 1` is the nearest weekly expiry.
Dhan does not return the expiry date itself. The options download is resumable (per 2-day window cache in `data/dhan_expired_week1_days/`).

Things learned from the live API that the script is built around:
- `/charts/rollingoption` is documented as 30 days per call but only a 1-day span works (`DH-905` otherwise), so options are fetched in 2-day windows.
- `/charts/intraday` allows 90 days per call and needs `fromDate` before 09:15, otherwise the first candle is lost.
- Holidays and dates before the data starts return empty arrays, not errors.
- The `strike` string must be exactly `ATM+3` / `ATM-3`: a trailing space or lower case silently returns ATM.
- The raw 5-minute feed contains junk that `fetch_dhan.py` removes: weekend mock sessions, flat zero-volume bars stamped 05:37-22:00 in Aug-Sep 2021, and bars after 15:30.
  It prints a QA report with what is still wrong (Dhan-side gaps such as 2022-03-03, which has only 13 bars).

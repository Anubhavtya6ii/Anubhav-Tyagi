# NIFTY data repository (Dhan API)

Everything Dhan's API served for NIFTY on this account (Data APIs plan), downloaded 31 Oct - 1 Nov 2026.
Market data up to the close of **30 Oct 2026**. No strategy or backtest code lives here.

## Layout

| Path | What |
|---|---|
| `raw/nifty_instruments.csv` | Every listed NIFTY instrument from Dhan's scrip master (index, 3 futures, 3,991 options) |
| `raw/snapshots/20261031_173007/` | Raw JSON + flat CSV of the option chain (all 18 expiries) and full quotes with 5-level depth for every contract |
| `raw/history/{index,futures,options}/{security_id}_{interval}.json.gz` | Raw API responses, one file per instrument x timeframe (list of request/status/response per call) |
| `data/{index,futures,options}/{security_id}_{interval}.csv.gz` | Parsed candles: `time_ist, open, high, low, close, volume, [open_interest], timestamp` |
| `raw/history/download_log.csv` | One line per dataset: calls, errors, rows, first/last candle |
| `inventory_datasets.csv` | Verification of every dataset (coverage, missing days/candles, duplicates, OHLC sanity) |
| `inventory_cross_checks.csv` | Cross-checks against Yahoo / NSE and between timeframes |

Interval codes: `D` daily, `1` `5` `15` `25` `60` minutes. Join `security_id` to `raw/nifty_instruments.csv` for expiry, strike and option type.

## What is in it

| Dataset | Timeframes | Coverage | Fields |
|---|---|---|---|
| NIFTY 50 index | D | 2 Jan 2006 - 30 Oct 2026 (5,163 days) | OHLC, volume |
| NIFTY 50 index | 1, 5, 15, 25, 60 min | 3 Apr 2017 - 30 Oct 2026 (950,952 one-minute candles) | OHLC, volume |
| Futures, continuous near / next / far month | D | 3 Oct 2011 - 30 Oct 2026 (~3,600 days each) | OHLC, volume, OI |
| Futures, the 3 listed contracts | 1, 5, 15, 25, 60 min | from each contract's listing (Aug-Oct 2026) | OHLC, volume, OI |
| Options, all 3,991 listed contracts (18 expiries, strikes 1,500-48,000) | D | from listing, earliest Sep 2023 | OHLC, volume, OI |
| Options, the 1,465 contracts that ever traded | 1 min (+5, 15, 25, 60 min) | from listing; 10.87 M one-minute candles | OHLC, volume, OI |
| Option chain snapshot (30 Oct close) | - | all 18 expiries, 4,010 rows | LTP, IV, delta, gamma, theta, vega, OI, previous OI / close / volume, top bid/ask price & qty |
| Quote snapshot (30 Oct close) | - | 3,987 of 3,995 instruments | LTP, OHLC, volume, OI, OI day high/low, circuit limits, 5-level depth (price, qty, orders) |

## Verification

- Index daily close matches Yahoo on 4,688 / 4,688 overlapping days.
- Index 1-min: 2 NSE trading days missing; 16 extra special-session days (mock / Muhurat); 2,356 days with all 375 candles; 12 candles with open/close outside high/low.
- Index 5-min from the API matches 5-min rebuilt from 1-min on 99.96% of candles.
- Daily close differs from the last 1-min close by a median 0.04%: NSE's official close is a 30-minute average, not the last trade.
- Continuous futures daily series skip 80-217 trading days over 2011-2026.
- 2,526 of 3,991 option contracts never traded (Dhan returns DH-907 "no data"); their daily files are empty.
- No duplicate timestamps and no bad OHLC rows in any option file.

## API limitations found

1. **Expired contracts are not available.** `/charts/rollingoption` (expired options, per strike) returns DH-905 for every parameter combination, including the exact documented example and the payload of Dhan's own Python library. Expired futures/options also have no security IDs in the scrip master. Ask Dhan support whether the endpoint is enabled for this plan.
2. **No history for IV, Greeks or bid/ask.** Those exist only in the live option chain / quote snapshot. Candles carry OHLC, volume and OI only.
3. **Intraday history starts April 2017** for the index and at listing for each contract; daily index goes back to 2006, continuous futures to 2011.
4. **Rate limits.** Published: 5 requests/s for Data APIs. In practice the charts endpoints returned DH-904 (429) for ~2 hours after an early burst at 4 requests/s; downloads then ran at 1 request/s without throttling. `client.py` paces itself adaptively.
5. **Stale rows on re-used security IDs.** e.g. 3 Nov 2026 PE 22150 (53635) has flat zero-volume, zero-OI daily rows from May 2024, before the contract existed. Filter `volume == 0 and open_interest == 0` when it matters.
6. The access token is short-lived (this one expired 1 Nov 2026 22:26). Re-running needs a fresh `DHAN_ACCESS_TOKEN`.

## Re-running

```bash
cd dhan
python3 snapshot.py                      # live option chain + quotes
./run_all.sh                             # index, futures, options daily, options 1-min (skips what exists)
python3 history.py options_other_intervals
python3 verify.py                        # rebuild inventory_*.csv
```
Needs `DHAN_CLIENT_ID` and `DHAN_ACCESS_TOKEN` in the environment.

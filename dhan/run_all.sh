#!/bin/bash
# Full NIFTY download in priority order; every stage skips what is already saved, so this can be re-run.
cd "$(dirname "$0")"
for stage in options_daily index futures options_1min options_1min_rest; do
  python3 history.py $stage >> raw/history/$stage.out 2>&1
done
echo "ALL STAGES DONE" >> raw/history/run_all.out

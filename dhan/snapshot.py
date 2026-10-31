"""Snapshot of everything Dhan only serves 'as of now' for NIFTY: option chain per expiry (IV, Greeks, OI,
bid/ask) and full quotes with 5-level depth for every listed NIFTY contract. Raw JSON is kept as returned."""
import json
import os
import sys
from datetime import datetime

import pandas as pd

from client import Dhan

OUT = "raw/snapshots"


def main():
    d = Dhan()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = f"{OUT}/{stamp}"
    os.makedirs(out, exist_ok=True)
    inst = pd.read_csv("raw/nifty_instruments.csv")

    st, exp = d.post("/optionchain/expirylist", {"UnderlyingScrip": 13, "UnderlyingSeg": "IDX_I"}, gap=3.1)
    json.dump(exp, open(f"{out}/expirylist.json", "w"))
    expiries = exp["data"]
    rows = []
    for e in expiries:
        st, j = d.post("/optionchain", {"UnderlyingScrip": 13, "UnderlyingSeg": "IDX_I", "Expiry": e}, gap=3.1)
        json.dump(j, open(f"{out}/optionchain_{e}.json", "w"))
        data = j.get("data", {}) if isinstance(j, dict) else {}
        for strike, sides in (data.get("oc") or {}).items():
            for side in ("ce", "pe"):
                v = sides.get(side)
                if not v:
                    continue
                flat = {k: x for k, x in v.items() if k != "greeks"}
                flat.update({f"greek_{k}": x for k, x in (v.get("greeks") or {}).items()})
                rows.append({"expiry": e, "strike": float(strike), "type": side.upper(), "underlying_ltp": data.get("last_price"), **flat})
        print(f"option chain {e}: HTTP {st}, strikes {len(data.get('oc') or {})}", flush=True)
    pd.DataFrame(rows).to_csv(f"{out}/optionchain_all.csv", index=False)

    ids = inst[inst.SEGMENT == "D"].SECURITY_ID.astype(int).tolist()
    quotes = []
    batches = [{"IDX_I": [13]}] + [{"NSE_FNO": ids[i:i + 999]} for i in range(0, len(ids), 999)]  # limit 1000 ids per call
    for n, body in enumerate(batches):
        st, j = d.post("/marketfeed/quote", body, gap=1.1)
        json.dump(j, open(f"{out}/quote_{n}.json", "w"))
        for seg, items in (j.get("data") or {}).items():
            if not isinstance(items, dict):
                print("quote error:", seg, items, flush=True)
                continue
            for sid, q in items.items():
                depth = q.pop("depth", {}) or {}
                ohlc = q.pop("ohlc", {}) or {}
                row = {"segment": seg, "security_id": int(sid), **q, **{f"ohlc_{k}": v for k, v in ohlc.items()}}
                for side in ("buy", "sell"):
                    for lvl, x in enumerate(depth.get(side, []), 1):
                        row.update({f"{side}{lvl}_price": x.get("price"), f"{side}{lvl}_qty": x.get("quantity"), f"{side}{lvl}_orders": x.get("orders")})
                quotes.append(row)
        print(f"quotes batch {n}: HTTP {st}", flush=True)
    q = pd.DataFrame(quotes).merge(inst[["SECURITY_ID", "DISPLAY_NAME", "INSTRUMENT", "expiry", "STRIKE_PRICE", "OPTION_TYPE"]],
                                   left_on="security_id", right_on="SECURITY_ID", how="left")
    q.to_csv(f"{out}/quotes_all.csv", index=False)
    print(f"saved {out}: {len(rows)} option-chain rows, {len(q)} quotes of {len(ids) + 1} instruments requested")


if __name__ == "__main__":
    sys.exit(main())

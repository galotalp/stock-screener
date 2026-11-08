"""
Collect earnings-announcement dates per ticker — an INDEPENDENT dataset used only
to CONTROL FOR earnings, not to study them.

Earnings are an exogenous shock: the price gaps on the fundamental report, not on
sentiment. Most reports land in extended hours (e.g. NVDA ~16:00 ET after-hours,
MRK ~06:00 ET pre-market), so they directly confound the "was the move already
priced in after hours?" question. We simply NOTE WHEN each report occurs so the
analysis can hold those windows aside (a regime comparison), keeping the earnings
data completely separate from mentions and prices until analysis time.

Output: data/earnings/<TICKER>.json = {
  "ticker": "NVDA",
  "events": [
    {
      "ts": "2026-05-20T20:00:00Z",     # announcement time, UTC
      "session": "after_hours",          # market session it lands in
      "window_end": "2026-05-21T13:30:00Z",  # next regular open (contaminated window end)
      "eps_estimate": 1.77,
      "reported_eps": 1.87,
      "surprise_pct": 5.54
    }, ...
  ]
}

The "contaminated window" for each event is [ts, window_end): the after-hours /
pre-market gap plus the immediate reaction, up to the next regular open. Analysis
flags any mention/price bar inside such a window as earnings-adjacent.

Usage:
  python3 collect_earnings.py TICKER [TICKER ...]
  python3 collect_earnings.py --from-universe
  python3 collect_earnings.py --from-mentions   (tickers we already track)
"""
import json
import os
import sys
import glob
import time
import math
import warnings

warnings.filterwarnings("ignore")

from market_session import session_of, next_regular_open

DATA = "data"
EDIR = f"{DATA}/earnings"
os.makedirs(EDIR, exist_ok=True)

# how many earnings events to keep per ticker (most recent). 16 quarters ~= 4 years,
# plenty of history to overlap with the sentiment window and any future date.
LIMIT = 16


def _num(x):
    """float or None (yfinance uses NaN for missing estimate/reported/surprise)."""
    try:
        if x is None:
            return None
        f = float(x)
        return None if math.isnan(f) else round(f, 4)
    except (TypeError, ValueError):
        return None


def fetch_events(ticker):
    """List of earnings events (dicts) for a ticker, newest first. [] on failure."""
    try:
        import yfinance as yf
    except Exception:
        return []
    try:
        df = yf.Ticker(ticker).get_earnings_dates(limit=LIMIT)
    except Exception:
        return []
    if df is None or len(df) == 0:
        return []
    events = []
    for ts, row in df.iterrows():
        try:
            u = ts.tz_convert("UTC").to_pydatetime()
        except Exception:
            # tz-naive fallback: assume the index is already UTC-comparable
            try:
                u = ts.to_pydatetime()
            except Exception:
                continue
        iso = u.strftime("%Y-%m-%dT%H:%M:%SZ")
        we = next_regular_open(iso)
        events.append({
            "ts": iso,
            "session": session_of(iso),
            "window_end": we.strftime("%Y-%m-%dT%H:%M:%SZ") if we else None,
            "eps_estimate": _num(row.get("EPS Estimate")),
            "reported_eps": _num(row.get("Reported EPS")),
            "surprise_pct": _num(row.get("Surprise(%)")),
        })
    return events


def collect(ticker):
    base = ticker.upper().replace(".TO", "")
    events = fetch_events(ticker)
    if not events:
        return 0
    out = {"ticker": base, "events": events}
    path = f"{EDIR}/{base}.json"
    tmp = path + ".tmp"
    json.dump(out, open(tmp, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)
    return len(events)


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    if args[0] == "--from-universe":
        u = json.load(open(f"{DATA}/universe_tracked.json"))
        tickers = u["tickers"]
    elif args[0] == "--from-mentions":
        tickers = sorted(os.path.basename(p)[:-6].upper()
                         for p in glob.glob(f"{DATA}/mentions/*.jsonl"))
    else:
        tickers = [a.upper() for a in args]

    total = 0
    for i, tk in enumerate(tickers, 1):
        n = collect(tk)
        total += n
        print(f"[{i}/{len(tickers)}] {tk}: {n} earnings events", flush=True)
        time.sleep(0.3)
    print(f"DONE: {total} earnings events across {len(tickers)} tickers")


if __name__ == "__main__":
    main()

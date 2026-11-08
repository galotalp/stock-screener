"""
Independent price collector — two points per trading day: midday (1pm ET) + close.

Kept DELIBERATELY SEPARATE from the mention/sentiment data. Sentiment and price
are two independent time series; they are aligned only at analysis time, never
coupled at storage time (coupling would bias any correlation we later measure).

Store: data/prices/<TICKER>.json = a flat list of rows:
   [{"date": "YYYY-MM-DD", "midday": <price@1pm ET>, "close": <price>}, ...]
one row per trading day, sorted, accumulating across runs (dedup by date).

Yahoo hourly bars (~60d trailing) give the 1pm-ET midday snapshot; daily bars give
the close and extend history. Each weekly run fills the latest window and merges.

Run: python3 collect_prices.py [TICKER ...]   (default: tickers in data/mentions)
"""
import json
import os
import sys
import glob
import time

import yfinance as yf
from market_session import session_of
from curl_cffi import requests as cffi

DATA = "data"
PDIR = f"{DATA}/prices"
os.makedirs(PDIR, exist_ok=True)
SESSION = cffi.Session(impersonate="chrome")


def load_rows(ticker):
    p = f"{PDIR}/{ticker}.json"
    if os.path.exists(p):
        try:
            return {r["date"]: r for r in json.load(open(p))}
        except Exception:
            pass
    return {}


def save_rows(ticker, by_date):
    rows = [by_date[d] for d in sorted(by_date)]
    json.dump(rows, open(f"{PDIR}/{ticker}.json", "w"))


def load_intraday(ticker):
    p = f"{PDIR}/{ticker}_intraday.json"
    if os.path.exists(p):
        try:
            return {r["ts"]: r for r in json.load(open(p))}
        except Exception:
            pass
    return {}


def save_intraday(ticker, by_ts):
    rows = [by_ts[k] for k in sorted(by_ts)]
    json.dump(rows, open(f"{PDIR}/{ticker}_intraday.json", "w"))


def collect(ticker):
    by_date = load_rows(ticker)
    by_ts = load_intraday(ticker)
    before_d, before_h = len(by_date), len(by_ts)
    try:
        t = yf.Ticker(ticker, session=SESSION)

        # daily closes (1y) -> long-history close per date
        d = t.history(period="1y", interval="1d")
        for idx, row in d.iterrows():
            day = idx.date().isoformat()
            rec = by_date.setdefault(day, {"date": day, "midday": None, "close": None})
            rec["close"] = round(float(row["Close"]), 4)

        # INTRADAY hourly bars WITH extended hours (prepost=True), ~60d back.
        # This is an INDEPENDENT timestamped price series (session-tagged), parallel
        # to the mention series; alignment/lead-lag is done only at analysis time.
        h = t.history(period="60d", interval="1h", prepost=True)
        for idx, row in h.iterrows():
            iso = idx.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
            close = row.get("Close")
            if close is None or (close != close):  # NaN guard
                continue
            by_ts[iso] = {
                "ts": iso,
                "price": round(float(close), 4),
                "session": session_of(iso),
            }
            # keep the daily midday too (1pm ET bar) for the existing chart
            et = idx.tz_convert("America/New_York")
            if et.hour == 13:
                day = et.date().isoformat()
                rec = by_date.setdefault(day, {"date": day, "midday": None, "close": None})
                rec["midday"] = round(float(close), 4)
    except Exception as e:
        return None, str(e)[:60]

    save_rows(ticker, by_date)
    save_intraday(ticker, by_ts)
    return (len(by_date) - before_d) + (len(by_ts) - before_h), "ok"


def main():
    args = sys.argv[1:]
    if args:
        tickers = [a.upper() for a in args]
    else:
        tickers = sorted(os.path.basename(f)[:-6] for f in glob.glob(f"{DATA}/mentions/*.jsonl"))
    for i, tk in enumerate(tickers, 1):
        added, status = collect(tk)
        if added is not None:
            print(f"[{i}/{len(tickers)}] {tk}: {status}, +{added} new trading days", flush=True)
        else:
            print(f"[{i}/{len(tickers)}] {tk}: {status}", flush=True)
        time.sleep(0.8)
    print("DONE")


if __name__ == "__main__":
    main()

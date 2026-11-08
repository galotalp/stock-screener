"""
Turn dated mentions into aligned sentiment + price daily time-series per ticker.

For each ticker with a mention store:
  1. Bucket mentions by calendar day (UTC).
  2. Produce a DAILY sentiment series two ways:
       - raw_daily:   simple mean of that day's mention scores (−1..1)
       - decayed:     14-day half-life EWMA over the daily means (the "current
                      sentiment" reading that emphasises recent days)
     plus daily mention COUNT (volume).
  3. Pull daily CLOSE prices via yfinance for the same date span.
  4. Emit data/timeseries/<TICKER>.json with aligned arrays for charting +
     analysis, and a compact index data/timeseries_index.json.

Run: python3 build_timeseries.py [TICKER ...]   (default: all in data/mentions)
"""
import json
import os
import sys
import glob
import datetime
import math

import yfinance as yf
from curl_cffi import requests as cffi

DATA = "data"
MDIR = f"{DATA}/mentions"
TSDIR = f"{DATA}/timeseries"
os.makedirs(TSDIR, exist_ok=True)

HALF_LIFE = 14.0
DECAY = 0.5 ** (1.0 / HALF_LIFE)  # per-day multiplier
SESSION = cffi.Session(impersonate="chrome")


def day_of(iso):
    if not iso:
        return None
    try:
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).date().isoformat()
    except Exception:
        return None


def load_mentions(ticker):
    p = f"{MDIR}/{ticker}.jsonl"
    if not os.path.exists(p):
        return []
    out = []
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def daily_means(mentions):
    """day -> (mean_score, count)."""
    by_day = {}
    for m in mentions:
        d = day_of(m.get("date"))
        s = m.get("score")
        if d is None or not isinstance(s, (int, float)):
            continue
        by_day.setdefault(d, []).append(s)
    return {d: (sum(v) / len(v), len(v)) for d, v in by_day.items()}


def date_range(days):
    if not days:
        return []
    lo = datetime.date.fromisoformat(min(days))
    hi = datetime.date.fromisoformat(max(days))
    out = []
    d = lo
    while d <= hi:
        out.append(d.isoformat())
        d += datetime.timedelta(days=1)
    return out


def decayed_series(dates, day_map):
    """14-day half-life EWMA over daily means; carries forward on empty days."""
    out = []
    ewma = None
    for d in dates:
        if d in day_map:
            val = day_map[d][0]
            ewma = val if ewma is None else DECAY * ewma + (1 - DECAY) * val
        elif ewma is not None:
            ewma = DECAY * ewma  # decay toward 0 (neutral) on silent days
        out.append(None if ewma is None else round(ewma, 4))
    return out


def fetch_prices(ticker, start, end):
    """Daily closes for [start, end] inclusive-ish. Returns {date: close}."""
    try:
        t = yf.Ticker(ticker, session=SESSION)
        # pad end by 1 day so the last day is included
        end_d = (datetime.date.fromisoformat(end) + datetime.timedelta(days=2)).isoformat()
        hist = t.history(start=start, end=end_d, interval="1d")
        prices = {}
        for idx, row in hist.iterrows():
            prices[idx.date().isoformat()] = round(float(row["Close"]), 4)
        return prices
    except Exception as e:
        return {}


def build(ticker):
    ms = load_mentions(ticker)
    if not ms:
        return None
    dm = daily_means(ms)
    if not dm:
        return None
    dates = date_range(list(dm.keys()))
    raw = [round(dm[d][0], 4) if d in dm else None for d in dates]
    counts = [dm[d][1] if d in dm else 0 for d in dates]
    decayed = decayed_series(dates, dm)
    prices = fetch_prices(ticker, dates[0], dates[-1])
    price_series = [prices.get(d) for d in dates]

    ts = {
        "ticker": ticker,
        "dates": dates,
        "sentiment_raw": raw,          # daily mean of mentions (−1..1)
        "sentiment_decayed": decayed,  # 14-day half-life EWMA
        "mention_count": counts,
        "price": price_series,         # daily close (None on non-trading days)
        "n_mentions": len(ms),
        "date_start": dates[0], "date_end": dates[-1],
    }
    json.dump(ts, open(f"{TSDIR}/{ticker}.json", "w"))
    return ts


def main():
    args = sys.argv[1:]
    if args:
        tickers = [a.upper() for a in args]
    else:
        tickers = sorted(os.path.basename(f)[:-6] for f in glob.glob(f"{MDIR}/*.jsonl"))

    index = []
    for i, tk in enumerate(tickers, 1):
        ts = build(tk)
        if ts:
            span = f"{ts['date_start']}..{ts['date_end']}"
            priced = sum(1 for p in ts["price"] if p is not None)
            index.append({"ticker": tk, "n_mentions": ts["n_mentions"],
                          "days": len(ts["dates"]), "priced_days": priced})
            print(f"[{i}/{len(tickers)}] {tk}: {ts['n_mentions']} mentions, "
                  f"{len(ts['dates'])}d span, {priced} priced days", flush=True)
        else:
            print(f"[{i}/{len(tickers)}] {tk}: no data", flush=True)
    json.dump({"tickers": index}, open(f"{DATA}/timeseries_index.json", "w"), indent=2)
    print(f"DONE: {len(index)} tickers with time-series")


if __name__ == "__main__":
    main()

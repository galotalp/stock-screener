"""
Screen the ticker universe with yfinance.

Filter logic:
  PROFITABLE bucket:
    - trailing P/E in (0, PE_MAX]  AND  (net income > 0 OR profit margin > 0)
  SOON_PROFITABLE bucket:
    - not profitable now, but revenue growth >= GROWTH_MIN
      AND a positive forward P/E <= PE_MAX*1.25 (market expects earnings)

Writes/append results incrementally to data/results.csv and tracks
processed tickers in data/processed.txt so the run is resumable.
"""
import csv
import os
import sys
import time
import random

import yfinance as yf
from curl_cffi import requests as cffi_requests

DATA = os.path.join(os.path.dirname(__file__), "data")
UNIVERSE = os.path.join(DATA, "universe.csv")
RESULTS = os.path.join(DATA, "results.csv")
PROCESSED = os.path.join(DATA, "processed.txt")
LOG = os.path.join(DATA, "run.log")

PE_MAX = 40.0
GROWTH_MIN = 0.20  # 20% YoY revenue growth for "soon profitable"

FIELDS = [
    "ticker", "name", "exchange", "bucket", "sector", "industry",
    "trailing_pe", "forward_pe", "profit_margin", "revenue_growth",
    "net_income", "market_cap", "price", "summary",
]


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def load_universe():
    with open(UNIVERSE) as f:
        return list(csv.DictReader(f))


def load_processed():
    if not os.path.exists(PROCESSED):
        return set()
    with open(PROCESSED) as f:
        return set(l.strip() for l in f if l.strip())


def mark_processed(ticker):
    with open(PROCESSED, "a") as f:
        f.write(ticker + "\n")


def append_result(row):
    exists = os.path.exists(RESULTS)
    with open(RESULTS, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if not exists:
            w.writeheader()
        w.writerow(row)


def classify(info):
    """Return (bucket, metrics_dict) or (None, metrics_dict) if it fails the screen."""
    trailing_pe = info.get("trailingPE")
    forward_pe = info.get("forwardPE")
    margin = info.get("profitMargins")           # e.g. 0.23 = 23%
    growth = info.get("revenueGrowth")           # YoY, e.g. 0.35 = 35%
    net_income = info.get("netIncomeToCommon")
    mcap = info.get("marketCap")

    metrics = {
        "trailing_pe": trailing_pe,
        "forward_pe": forward_pe,
        "profit_margin": margin,
        "revenue_growth": growth,
        "net_income": net_income,
        "market_cap": mcap,
    }

    profitable_now = (net_income is not None and net_income > 0) or (
        margin is not None and margin > 0
    )

    # PROFITABLE bucket
    if (
        trailing_pe is not None
        and 0 < trailing_pe <= PE_MAX
        and profitable_now
    ):
        return "PROFITABLE", metrics

    # SOON_PROFITABLE bucket
    if (
        not profitable_now
        and growth is not None
        and growth >= GROWTH_MIN
        and forward_pe is not None
        and 0 < forward_pe <= PE_MAX * 1.25
    ):
        return "SOON_PROFITABLE", metrics

    return None, metrics


def main():
    universe = load_universe()
    processed = load_processed()
    todo = [u for u in universe if u["ticker"] not in processed]
    log(f"Universe={len(universe)}  already_processed={len(processed)}  todo={len(todo)}")

    session = cffi_requests.Session(impersonate="chrome")

    passes = 0
    errors = 0
    for i, u in enumerate(todo, 1):
        ticker = u["ticker"]
        try:
            t = yf.Ticker(ticker, session=session)
            info = t.info
            if not info or info.get("quoteType") not in (None, "EQUITY"):
                mark_processed(ticker)
                continue

            bucket, m = classify(info)
            if bucket:
                row = {
                    "ticker": ticker,
                    "name": u["name"],
                    "exchange": u["exchange"],
                    "bucket": bucket,
                    "sector": info.get("sector", ""),
                    "industry": info.get("industry", ""),
                    "price": info.get("currentPrice") or info.get("regularMarketPrice"),
                    "summary": (info.get("longBusinessSummary", "") or "")[:1500],
                    **m,
                }
                append_result(row)
                passes += 1
                log(f"  PASS [{bucket}] {ticker} — {u['name'][:40]}  PE={m['trailing_pe']} fPE={m['forward_pe']} growth={m['revenue_growth']}")
            mark_processed(ticker)
        except Exception as e:
            errors += 1
            # Don't mark processed on transient errors so a re-run retries it,
            # UNLESS it's clearly a 404/delisted.
            msg = str(e).lower()
            if "404" in msg or "delisted" in msg or "no data found" in msg:
                mark_processed(ticker)
            if errors <= 20 or errors % 50 == 0:
                log(f"  ERR {ticker}: {str(e)[:80]}")

        if i % 100 == 0:
            log(f"progress {i}/{len(todo)}  passes={passes}  errors={errors}")

        # Throttle politely to avoid rate-limiting/blocks.
        time.sleep(random.uniform(0.4, 0.9))

    log(f"DONE. processed={len(todo)} new  passes={passes}  errors={errors}")


if __name__ == "__main__":
    main()

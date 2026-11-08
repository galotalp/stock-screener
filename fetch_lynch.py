"""
Re-fetch Lynch-specific fundamentals for the survivors in review.csv.
Adds: PEG ratio, earnings growth (YoY), earnings-quarterly-growth, debt/equity,
current ratio, dividend yield, forward EPS, trailing EPS, beta, and
5yr rev CAGR estimate where available.
Resumable via data/lynch_processed.txt. Output: data/lynch.csv
"""
import csv
import os
import time
import random

import yfinance as yf
from curl_cffi import requests as cffi_requests

DATA = "data"
SRC = f"{DATA}/review.csv"
OUT = f"{DATA}/lynch.csv"
DONE = f"{DATA}/lynch_processed.txt"

EXTRA_FIELDS = [
    "peg_ratio", "earnings_growth", "earnings_q_growth", "debt_to_equity",
    "current_ratio", "quick_ratio", "dividend_yield", "trailing_eps",
    "forward_eps", "beta", "return_on_equity", "gross_margin",
    "operating_margin", "free_cashflow", "total_cash", "total_debt",
    "recommendation", "target_mean_price",
]


def load_done():
    if not os.path.exists(DONE):
        return set()
    return set(l.strip() for l in open(DONE) if l.strip())


def mark(tk):
    with open(DONE, "a") as f:
        f.write(tk + "\n")


def append(row, fields):
    exists = os.path.exists(OUT)
    with open(OUT, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if not exists:
            w.writeheader()
        w.writerow(row)


def main():
    rows = list(csv.DictReader(open(SRC)))
    src_fields = list(rows[0].keys())
    out_fields = src_fields + EXTRA_FIELDS
    done = load_done()
    todo = [r for r in rows if r["ticker"] not in done]
    print(f"survivors={len(rows)} done={len(done)} todo={len(todo)}", flush=True)

    session = cffi_requests.Session(impersonate="chrome")
    errors = 0
    for i, r in enumerate(todo, 1):
        tk = r["ticker"]
        try:
            info = yf.Ticker(tk, session=session).info
            r["peg_ratio"] = info.get("trailingPegRatio") or info.get("pegRatio")
            r["earnings_growth"] = info.get("earningsGrowth")
            r["earnings_q_growth"] = info.get("earningsQuarterlyGrowth")
            r["debt_to_equity"] = info.get("debtToEquity")
            r["current_ratio"] = info.get("currentRatio")
            r["quick_ratio"] = info.get("quickRatio")
            r["dividend_yield"] = info.get("dividendYield")
            r["trailing_eps"] = info.get("trailingEps")
            r["forward_eps"] = info.get("forwardEps")
            r["beta"] = info.get("beta")
            r["return_on_equity"] = info.get("returnOnEquity")
            r["gross_margin"] = info.get("grossMargins")
            r["operating_margin"] = info.get("operatingMargins")
            r["free_cashflow"] = info.get("freeCashflow")
            r["total_cash"] = info.get("totalCash")
            r["total_debt"] = info.get("totalDebt")
            r["recommendation"] = info.get("recommendationKey")
            r["target_mean_price"] = info.get("targetMeanPrice")
            append(r, out_fields)
            mark(tk)
        except Exception as e:
            errors += 1
            msg = str(e).lower()
            if errors <= 15:
                print(f"  ERR {tk}: {str(e)[:70]}", flush=True)
            # Yahoo rate-limit backoff: if we hit "too many requests", pause hard
            # and DON'T mark processed, so the ticker is retried on the next pass.
            if "too many requests" in msg or "rate limit" in msg or "429" in msg:
                backoff = min(120, 15 * (1 + errors // 50))
                print(f"  …rate-limited; sleeping {backoff}s", flush=True)
                time.sleep(backoff)
        if i % 100 == 0:
            print(f"  {i}/{len(todo)} (errors={errors})", flush=True)
        # Gentler base throttle to stay under Yahoo's limit.
        time.sleep(random.uniform(0.8, 1.4))

    print(f"DONE todo={len(todo)} errors={errors}", flush=True)


if __name__ == "__main__":
    main()

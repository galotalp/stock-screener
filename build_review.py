"""
From data/filtered.csv:
  1. Apply the additional cut: drop Real Estate, Utilities, and raw extractive
     Basic Materials (miners/metals). Keep materials that make real products
     (chemicals, packaging, specialty materials, building products, etc.).
  2. Compute a quality score and flag hints.
  3. Fetch website URLs (one field) via yfinance for the survivors.
  4. Emit review.csv and a sortable review.html.
"""
import csv
import re
import time
import random
import html as htmllib

import yfinance as yf
from curl_cffi import requests as cffi_requests

DATA = "data"

# Sectors to drop entirely in this pass.
# (Real Estate excluded per user; Utilities and raw miners are now KEPT.)
DROP_SECTORS = {"Real Estate"}

NICHE_FOOD_PAT = re.compile(
    r"(snack|energy bar|beverage|confection|packaged food|nutrition bar)",
    re.IGNORECASE,
)


def to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def score_and_flags(r):
    """Composite 0-100 quality score + list of flag strings."""
    tpe = to_float(r.get("trailing_pe"))
    fpe = to_float(r.get("forward_pe"))
    margin = to_float(r.get("profit_margin"))
    growth = to_float(r.get("revenue_growth"))
    mcap = to_float(r.get("market_cap"))
    flags = []

    score = 0.0
    # Valuation: reward low-but-positive P/E (sweet spot ~10-25)
    pe = tpe if (tpe and tpe > 0) else fpe
    if pe and pe > 0:
        if pe < 2:
            flags.append("distorted-PE")
            score += 5
        elif pe <= 15:
            score += 30
        elif pe <= 25:
            score += 25
        elif pe <= 40:
            score += 15
        else:
            score += 5
    # Profitability
    if margin is not None:
        if margin >= 0.20:
            score += 25
        elif margin >= 0.10:
            score += 18
        elif margin > 0:
            score += 10
    # Growth
    if growth is not None:
        if growth >= 1.0:
            flags.append("growth-outlier")
            score += 10
        elif growth >= 0.20:
            score += 25
        elif growth >= 0.05:
            score += 12
        elif growth < 0:
            score += 0
    # Size context (not a filter — just a flag + slight confidence weight)
    if mcap is not None:
        if mcap < 50e6:
            flags.append("nano-cap")
            score += 2
        elif mcap < 300e6:
            flags.append("micro-cap")
            score += 5
        elif mcap < 2e9:
            flags.append("small-cap")
            score += 8
        else:
            score += 8

    if r.get("bucket") == "SOON_PROFITABLE":
        flags.append("soon-profitable")
    if NICHE_FOOD_PAT.search(f"{r.get('name','')} {r.get('summary','')}"):
        flags.append("niche-food?")

    return round(min(score, 100), 1), flags


def main():
    rows = list(csv.DictReader(open(f"{DATA}/filtered.csv")))
    kept = []
    for r in rows:
        sector = r.get("sector", "")
        if sector in DROP_SECTORS:
            continue
        kept.append(r)

    print(f"After additional cut: {len(kept)} (from {len(rows)})")

    # Fetch websites
    session = cffi_requests.Session(impersonate="chrome")
    for i, r in enumerate(kept, 1):
        try:
            info = yf.Ticker(r["ticker"], session=session).info
            r["website"] = info.get("website", "") or ""
        except Exception:
            r["website"] = ""
        if i % 100 == 0:
            print(f"  websites {i}/{len(kept)}", flush=True)
        time.sleep(random.uniform(0.25, 0.55))

    # Score + flags
    for r in kept:
        s, flags = score_and_flags(r)
        r["quality_score"] = s
        r["flags"] = ", ".join(flags)
        yf_base = r["ticker"]
        r["yahoo_link"] = f"https://finance.yahoo.com/quote/{yf_base}"

    kept.sort(key=lambda r: r["quality_score"], reverse=True)

    out_fields = [
        "quality_score", "ticker", "name", "sector", "industry", "bucket",
        "flags", "trailing_pe", "forward_pe", "profit_margin", "revenue_growth",
        "market_cap", "price", "website", "yahoo_link", "summary", "exchange",
    ]
    with open(f"{DATA}/review.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=out_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(kept)

    print(f"Wrote review.csv with {len(kept)} rows")


if __name__ == "__main__":
    main()

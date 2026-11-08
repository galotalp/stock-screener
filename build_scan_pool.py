"""
Build the ~300-stock pool to send to the sentiment/Reddit scan.

Combines two groups so the Reddit views are meaningful:
  A) Top Lynch candidates (by max of LT/ST score) — the picks themselves.
  B) 'Likely-buzz' names — larger caps + recognizable consumer/tech industries
     that Reddit actually discusses (which tend to rank LOW on the Lynch screen,
     so they'd be missed by a Lynch-only pool).

Writes data/scan_pool.json: [{ticker, name, sector, industry, mcap, reason}]
"""
import csv
import json
import re

DATA = "data"
TARGET_LYNCH = 200
TARGET_BUZZ = 120

# Industries whose stocks tend to get Reddit attention (consumer-facing / hyped).
BUZZ_INDUSTRY = re.compile(
    r"(Internet Retail|Internet Content|Software|Semiconduc|Consumer Electronics|"
    r"Entertainment|Gaming|Restaurants|Apparel|Footwear|Auto Manufacturer|"
    r"Airlines|Electric|Solar|Aerospace|Biotechnology|Communication Equipment|"
    r"Specialty Retail|Beverages|Leisure|Travel)", re.IGNORECASE)


def f(v):
    try:
        x = float(v); return x if x == x else None
    except (TypeError, ValueError):
        return None


def lynch_max_score(r):
    # cheap proxy: reuse fields to rank interest — PEG low + growth high + cap
    peg = f(r.get("peg_ratio")); g = f(r.get("revenue_growth")); mcap = f(r.get("market_cap"))
    s = 0
    if peg and 0 < peg <= 1: s += 3
    elif peg and peg <= 1.5: s += 1
    if g and g >= 0.15: s += 2
    if mcap and mcap > 2e9: s += 1
    return s


def main():
    rows = list(csv.DictReader(open(f"{DATA}/lynch.csv")))
    for r in rows:
        r["_mcap"] = f(r.get("market_cap")) or 0
        r["_lynch"] = lynch_max_score(r)

    picked = {}

    # A) top Lynch-interest names
    for r in sorted(rows, key=lambda r: (r["_lynch"], r["_mcap"]), reverse=True)[:TARGET_LYNCH]:
        picked[r["ticker"]] = "lynch-candidate"

    # B) likely-buzz: biggest caps in buzzy industries not already picked
    buzz = [r for r in rows if BUZZ_INDUSTRY.search(r.get("industry", "") or "")]
    for r in sorted(buzz, key=lambda r: r["_mcap"], reverse=True):
        if len(picked) >= TARGET_LYNCH + TARGET_BUZZ:
            break
        picked.setdefault(r["ticker"], "likely-buzz")

    by_tk = {r["ticker"]: r for r in rows}
    pool = []
    for tk, reason in picked.items():
        r = by_tk[tk]
        pool.append({
            "ticker": tk, "name": r["name"], "sector": r.get("sector", ""),
            "industry": r.get("industry", ""), "mcap": f(r.get("market_cap")),
            "reason": reason,
        })
    json.dump(pool, open(f"{DATA}/scan_pool.json", "w"), indent=2)
    from collections import Counter
    print(f"scan pool: {len(pool)} stocks")
    for reason, n in Counter(p["reason"] for p in pool).items():
        print(f"  {reason}: {n}")


if __name__ == "__main__":
    main()

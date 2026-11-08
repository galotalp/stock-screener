"""
Build the self-expanding tracked universe for the sentiment study.

Universe = union of:
  1. StockTwits trending equities (~30/run) — the organic "what's popular now" feed
  2. Top-N per GICS sector by market cap, from data/lynch.csv fundamentals
  3. Everything already tracked (data/mentions/*.jsonl) — never drop a tracked name
The trending pull auto-adds newly-popular tickers on each weekly run (accumulate).

Also builds category metadata per ticker (sector, industry, mcap bucket) from
lynch.csv. Source-favored (social vs news) is derived later from mention counts.

Output: data/universe_tracked.json = {
   "tickers": [...], "meta": {ticker: {sector, industry, mcap, mcap_bucket}}, "generated": <date> }

Run: python3 build_universe_dynamic.py [--per-sector N]   (default N=18)
"""
import json
import os
import csv
import glob
import re
import sys

from curl_cffi import requests as cffi

DATA = "data"
SESSION = cffi.Session(impersonate="chrome")

# Common ETFs/indices to exclude from an equity study
ETF_LIKE = {"IWM", "TLT", "SPY", "QQQ", "DIA", "LABD", "LABU", "SQQQ", "TQQQ",
            "UVXY", "VXX", "SOXL", "SOXS", "GLD", "SLV", "XLF", "XLE", "XLK",
            "ARKK", "VOO", "VTI", "HYG", "EEM", "GDX", "USO"}


def mcap_bucket(mcap):
    try:
        m = float(mcap)
    except (TypeError, ValueError):
        return "unknown"
    if m >= 200e9: return "mega"
    if m >= 10e9:  return "large"
    if m >= 2e9:   return "mid"
    if m >= 300e6: return "small"
    return "micro"


def load_fundamentals():
    rows = {}
    p = f"{DATA}/lynch.csv"
    if not os.path.exists(p):
        return rows
    for r in csv.DictReader(open(p)):
        tk = r["ticker"].replace(".TO", "").upper()
        rows[tk] = {
            "sector": r.get("sector", "") or "Unknown",
            "industry": r.get("industry", "") or "Unknown",
            "market_cap": r.get("market_cap", ""),
        }
    return rows


def stocktwits_trending():
    try:
        resp = SESSION.get("https://api.stocktwits.com/api/2/trending/symbols/equities.json", timeout=15)
        if resp.status_code == 200:
            syms = [s["symbol"].upper() for s in resp.json().get("symbols", [])]
            return [s for s in syms if s not in ETF_LIKE and re.fullmatch(r"[A-Z]{1,5}", s)]
    except Exception:
        pass
    return []


def already_tracked():
    return sorted(os.path.basename(f)[:-6] for f in glob.glob(f"{DATA}/mentions/*.jsonl"))


def top_per_sector(fund, n):
    by_sector = {}
    for tk, m in fund.items():
        if tk in ETF_LIKE:
            continue
        try:
            mc = float(m["market_cap"])
        except (TypeError, ValueError):
            continue
        by_sector.setdefault(m["sector"], []).append((mc, tk))
    picks = []
    for sector, arr in by_sector.items():
        arr.sort(reverse=True)
        picks.extend(tk for _, tk in arr[:n])
    return picks


def main():
    per_sector = 18
    if "--per-sector" in sys.argv:
        per_sector = int(sys.argv[sys.argv.index("--per-sector") + 1])

    fund = load_fundamentals()
    trending = stocktwits_trending()
    sector_picks = top_per_sector(fund, per_sector)
    tracked = already_tracked()

    universe = sorted(set(trending) | set(sector_picks) | set(tracked))

    meta = {}
    for tk in universe:
        f = fund.get(tk, {})
        meta[tk] = {
            "sector": f.get("sector", "Unknown"),
            "industry": f.get("industry", "Unknown"),
            "market_cap": f.get("market_cap", ""),
            "mcap_bucket": mcap_bucket(f.get("market_cap")),
        }

    out = {"tickers": universe, "meta": meta, "generated": None,
           "sources": {"trending": len(trending), "sector_picks": len(set(sector_picks)),
                       "already_tracked": len(tracked)}}
    json.dump(out, open(f"{DATA}/universe_tracked.json", "w"), indent=2)

    from collections import Counter
    print(f"Universe: {len(universe)} tickers")
    print(f"  from trending: {len(trending)}  ({', '.join(trending[:10])}...)")
    print(f"  from top-{per_sector}-per-sector: {len(set(sector_picks))}")
    print(f"  already tracked: {len(tracked)}")
    print("\nby sector:")
    for s, c in Counter(m["sector"] for m in meta.values()).most_common():
        print(f"  {s}: {c}")
    print("\nby mcap bucket:")
    for b, c in Counter(m["mcap_bucket"] for m in meta.values()).most_common():
        print(f"  {b}: {c}")


if __name__ == "__main__":
    main()

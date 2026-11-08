"""
Build Peter Lynch-style dual lists (long-term 5yr+ and short-term 1yr) from
data/lynch.csv, factoring in current holdings (held_tickers.txt) (complement & diversify) and
excluded categories. Outputs data/lynch_lists.json for the artifact.
"""
import csv
import json
import os
import re

DATA = "data"
SRC = f"{DATA}/lynch.csv"

# --- Current holdings: theme concentration to diversify away from ---
# Read from held_tickers.txt (one ticker per line, gitignored); empty if absent.
HELD_FILE = "held_tickers.txt"
HELD = set()
if os.path.exists(HELD_FILE):
    with open(HELD_FILE) as _fh:
        HELD = {ln.strip().upper() for ln in _fh if ln.strip() and not ln.startswith("#")}
# Industries the user is already concentrated in (down-weight to diversify)
CONCENTRATED_PAT = re.compile(
    r"(Semiconductor|Solar|Electric Vehicle|EV |Charging|"
    r"Internet Content|Streaming|Gaming|Video Game)", re.IGNORECASE)

# Extra safety: re-exclude anything that leaked past earlier filters.
# Includes consumer-lending / rent-to-own / BNPL that Yahoo mis-tags as "Technology".
EXCLUDE_PAT = re.compile(
    r"(Bank|Insurance|Capital Markets|Credit Services|Asset Management|Mortgage|"
    r"Defense|Weapon|Oil & Gas|Coal|Tobacco|Drug Manufacturer|Pharmaceutical|"
    r"Biotechnology|Gambling|Casino|Crypto|Bitcoin|"
    r"lending|rent-to-own|rent to own|consumer credit|buy now|installment|"
    r"payday|lease-to-own|lease to own)", re.IGNORECASE)

# Tickers to hard-exclude: consumer-finance/lending names Yahoo tags as Tech.
HARD_EXCLUDE = {"PGY", "UPBD", "PRTH", "PRTH.TO"}


def f(v):
    try:
        x = float(v)
        return x if x == x else None  # filter NaN
    except (TypeError, ValueError):
        return None


# Industries that are inherently cyclical (Lynch treated these very differently
# from fast growers — earnings spikes are rebounds, not durable growth).
CYCLICAL_PAT = re.compile(
    r"(Marine Shipping|Shipping|Airlines|Auto Manufacturer|Auto Parts|"
    r"Steel|Aluminum|Copper|Homebuild|Building Materials|Semiconductor Equipment|"
    r"Travel Services|Lodging|Resorts|Paper|Packaging|Chemicals|Agricultural)",
    re.IGNORECASE)


def lynch_category(r):
    g = f(r.get("revenue_growth"))         # revenue growth is the durable signal
    eg = f(r.get("earnings_growth"))       # earnings growth can be a one-off rebound
    mcap = f(r.get("market_cap"))
    industry = r.get("industry", "")

    # Cyclicals first — a shipping/auto/steel name with an earnings spike is a
    # cyclical rebound, NOT a fast grower. Judge these on revenue trend only.
    if CYCLICAL_PAT.search(industry):
        return "Cyclical"
    if r.get("bucket") == "SOON_PROFITABLE":
        return "Turnaround"
    # Fast grower requires durable REVENUE growth, not just an earnings bounce.
    rev = g if g is not None else 0
    if rev >= 0.20:
        return "Fast Grower"
    if mcap and mcap > 10e9 and 0.08 <= rev < 0.20:
        return "Stalwart"
    if rev < 0.05:
        return "Slow Grower"
    return "Moderate Grower"


def long_term_score(r):
    """Durable compounders: PEG, sustainable growth, balance sheet, profitability."""
    s = 0.0
    reasons = []
    peg = f(r.get("peg_ratio"))
    g = f(r.get("revenue_growth"))
    eg = f(r.get("earnings_growth"))
    de = f(r.get("debt_to_equity"))
    cr = f(r.get("current_ratio"))
    roe = f(r.get("return_on_equity"))
    margin = f(r.get("profit_margin"))
    fcf = f(r.get("free_cashflow"))
    growth = max(g or 0, eg or 0)

    # PEG — the heart of Lynch (lower better; sweet spot < 1)
    if peg is not None and peg > 0:
        if peg < 0.5: s += 30; reasons.append(f"PEG {peg:.2f} (deeply undervalued vs growth)")
        elif peg <= 1.0: s += 25; reasons.append(f"PEG {peg:.2f} (Lynch sweet spot)")
        elif peg <= 1.5: s += 15; reasons.append(f"PEG {peg:.2f} (fair)")
        elif peg <= 2.0: s += 6
        else: s += 0
    # Sustainable growth 15-40% (Lynch avoided >50% as unsustainable)
    if 0.15 <= growth <= 0.40: s += 22; reasons.append(f"{growth*100:.0f}% growth (sustainable range)")
    elif 0.40 < growth <= 0.60: s += 14; reasons.append(f"{growth*100:.0f}% growth (fast)")
    elif growth > 0.60: s += 8
    elif 0.08 <= growth < 0.15: s += 12; reasons.append(f"{growth*100:.0f}% steady growth")
    # Balance sheet — Lynch hated debt
    if de is not None:
        if de < 30: s += 15; reasons.append("low debt")
        elif de < 80: s += 9
        elif de < 200: s += 3
        else: s -= 3; reasons.append("high leverage (caution)")
    else:
        s += 6  # often means little/no debt reported
    if cr is not None and cr >= 1.5: s += 6; reasons.append("healthy liquidity")
    # Quality
    if roe is not None and roe >= 0.15: s += 10; reasons.append(f"ROE {roe*100:.0f}%")
    if margin is not None and margin >= 0.15: s += 8
    if fcf is not None and fcf > 0: s += 6; reasons.append("positive free cash flow")
    return s, reasons


def short_term_score(r):
    """1-yr re-rating: earnings inflecting, cheap vs forward, analyst upside."""
    s = 0.0
    reasons = []
    tpe = f(r.get("trailing_pe"))
    fpe = f(r.get("forward_pe"))
    price = f(r.get("price"))
    target = f(r.get("target_mean_price"))
    rec = (r.get("recommendation") or "").lower()
    eqg = f(r.get("earnings_q_growth"))
    peg = f(r.get("peg_ratio"))

    # Forward P/E meaningfully below trailing => earnings expected to jump
    if tpe and fpe and fpe > 0 and tpe > 0:
        drop = (tpe - fpe) / tpe
        if drop >= 0.4: s += 25; reasons.append("earnings inflecting up sharply (fwd P/E << trailing)")
        elif drop >= 0.2: s += 16; reasons.append("earnings improving next year")
        elif drop >= 0.05: s += 8
    # Cheap forward multiple
    if fpe and 0 < fpe <= 15: s += 15; reasons.append(f"cheap fwd P/E {fpe:.1f}")
    elif fpe and 15 < fpe <= 25: s += 8
    # Analyst target upside
    if price and target and price > 0:
        up = (target - price) / price
        if up >= 0.30: s += 22; reasons.append(f"{up*100:.0f}% analyst upside")
        elif up >= 0.15: s += 14; reasons.append(f"{up*100:.0f}% analyst upside")
        elif up >= 0.05: s += 6
    # Analyst conviction
    if rec in ("strong_buy",): s += 12; reasons.append("strong-buy consensus")
    elif rec in ("buy",): s += 7; reasons.append("buy consensus")
    # Recent earnings momentum
    if eqg is not None and eqg > 0.15: s += 10; reasons.append("strong recent earnings momentum")
    # Value cushion
    if peg is not None and 0 < peg <= 1.0: s += 8; reasons.append(f"PEG {peg:.2f}")
    # Lynch's balance-sheet discipline applies to short-term too: heavy leverage
    # makes a 1-yr thesis fragile if rates/refinancing bite. Penalize it.
    de = f(r.get("debt_to_equity"))
    if de is not None:
        if de > 300: s -= 18; reasons.append("very high leverage (risk)")
        elif de > 150: s -= 10; reasons.append("high leverage")
        elif de < 50: s += 5; reasons.append("clean balance sheet")
    return s, reasons


def portfolio_adjust(r, score):
    """Down-weight themes the user is already concentrated in."""
    industry = r.get("industry", "")
    name = r.get("name", "")
    if CONCENTRATED_PAT.search(f"{industry} {name}"):
        return score * 0.75, True  # diversification penalty
    return score, False


def main():
    rows = list(csv.DictReader(open(SRC)))
    seen_names = {}
    clean = []
    for r in rows:
        tk = r["ticker"].upper()
        base = tk.replace(".TO", "")
        if base in HELD or tk in HELD:
            continue
        if base in HARD_EXCLUDE or tk in HARD_EXCLUDE:
            continue
        if EXCLUDE_PAT.search(f"{r.get('sector','')} {r.get('industry','')} {r.get('name','')} {r.get('summary','')}"):
            continue
        # Drop obvious CDR/duplicate depositary receipts (Canadian CDRs of US megacaps)
        if "CDR" in r.get("name", "").upper():
            continue
        # Dedup by company name (avoid dual-listings)
        nm = re.sub(r"[^a-z]", "", r.get("name","").lower())[:20]
        if nm in seen_names:
            continue
        seen_names[nm] = tk
        r["category"] = lynch_category(r)
        clean.append(r)

    for r in clean:
        lt, lt_r = long_term_score(r)
        st, st_r = short_term_score(r)
        lt, lt_pen = portfolio_adjust(r, lt)
        st, st_pen = portfolio_adjust(r, st)
        r["_lt"] = lt; r["_lt_reasons"] = lt_r; r["_lt_pen"] = lt_pen
        r["_st"] = st; r["_st_reasons"] = st_r; r["_st_pen"] = st_pen

    long_list = sorted(clean, key=lambda r: r["_lt"], reverse=True)[:15]
    short_list = sorted(clean, key=lambda r: r["_st"], reverse=True)[:15]

    def pack(r, kind):
        sc = r["_lt"] if kind == "long" else r["_st"]
        rs = r["_lt_reasons"] if kind == "long" else r["_st_reasons"]
        pen = r["_lt_pen"] if kind == "long" else r["_st_pen"]
        return {
            "ticker": r["ticker"], "name": r["name"], "sector": r["sector"],
            "industry": r["industry"], "category": r["category"],
            "score": round(sc, 1),
            "pe": f(r.get("trailing_pe")), "fpe": f(r.get("forward_pe")),
            "peg": f(r.get("peg_ratio")), "growth": f(r.get("revenue_growth")),
            "earnings_growth": f(r.get("earnings_growth")),
            "margin": f(r.get("profit_margin")), "roe": f(r.get("return_on_equity")),
            "de": f(r.get("debt_to_equity")), "mcap": f(r.get("market_cap")),
            "price": f(r.get("price")), "target": f(r.get("target_mean_price")),
            "rec": r.get("recommendation",""), "website": r.get("website",""),
            "yahoo": r.get("yahoo_link",""),
            "reasons": rs, "diversification_note": pen,
            "summary": r.get("summary","")[:600],
        }

    out = {
        "long_term": [pack(r, "long") for r in long_list],
        "short_term": [pack(r, "short") for r in short_list],
        "universe_size": len(clean),
    }
    with open(f"{DATA}/lynch_lists.json", "w") as fp:
        json.dump(out, fp, indent=2)

    print(f"Universe after portfolio/exclusion filters: {len(clean)}")
    print("\n=== LONG-TERM (5yr+) TOP 15 ===")
    for r in long_list:
        print(f"  {r['_lt']:>5.1f}  {r['ticker']:<8} {r['name'][:34]:<34} {r['category']:<14} PEG={f(r.get('peg_ratio'))}")
    print("\n=== SHORT-TERM (1yr) TOP 15 ===")
    for r in short_list:
        print(f"  {r['_st']:>5.1f}  {r['ticker']:<8} {r['name'][:34]:<34} {r['category']:<14} fPE={f(r.get('forward_pe'))}")


if __name__ == "__main__":
    main()

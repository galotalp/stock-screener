"""
Build the unified candidates dataset that drives all six ranked views + backtest.

Reads:
  data/lynch.csv       — filtered survivors with Lynch fundamentals
  data/sentiment.json  — {ticker: {reddit:{...}, msm:{...}, reddit_mentions:int}}
  data/fable.json      — {ticker: {verdict, category, conviction, ...}} (optional)

Writes:
  data/candidates.json — {generated, exclusions, candidates:[...]} with per-stock
                         Lynch LT/ST scores + sentiment + (optional) Fable verdict.

De-personalized: NO portfolio holdings excluded, NO diversification down-weighting.
Category exclusions were already applied upstream in filter.py / build_review.py.
"""
import csv
import json
import os
import re

DATA = "data"
SRC = f"{DATA}/lynch.csv"

# Mirror of the five stated exclusions (from filter.py) — shown on the page.
EXCLUSIONS_DISPLAY = ["Finance", "Defense", "Oil & Gas", "Crypto", "Meme stocks", "Real Estate"]

# Safety re-exclude (leaks Yahoo mis-tags as Tech, plus consumer-lending).
EXCLUDE_PAT = re.compile(
    r"(Bank|Insurance|Capital Markets|Credit Services|Asset Management|Mortgage|"
    r"Defense|Weapon|Oil & Gas|\bCoal\b|Crypto|Bitcoin|"
    r"lending|rent-to-own|payday)", re.IGNORECASE)

CYCLICAL_PAT = re.compile(
    r"(Marine Shipping|Shipping|Airlines|Auto Manufacturer|Auto Parts|"
    r"Steel|Aluminum|Copper|Homebuild|Building Materials|Semiconductor Equipment|"
    r"Travel Services|Lodging|Resorts|Paper|Packaging|Chemicals|Agricultural)",
    re.IGNORECASE)


def f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def lynch_category(r):
    g = f(r.get("revenue_growth"))
    eg = f(r.get("earnings_growth"))
    mcap = f(r.get("market_cap"))
    industry = r.get("industry", "")
    if CYCLICAL_PAT.search(industry):
        return "Cyclical"
    if r.get("bucket") == "SOON_PROFITABLE":
        return "Turnaround"
    rev = g if g is not None else 0
    if rev >= 0.20:
        return "Fast Grower"
    if mcap and mcap > 10e9 and 0.08 <= rev < 0.20:
        return "Stalwart"
    if rev < 0.05:
        return "Slow Grower"
    return "Moderate Grower"


def long_term_score(r):
    s = 0.0; reasons = []
    peg = f(r.get("peg_ratio")); g = f(r.get("revenue_growth")); eg = f(r.get("earnings_growth"))
    de = f(r.get("debt_to_equity")); cr = f(r.get("current_ratio")); roe = f(r.get("return_on_equity"))
    margin = f(r.get("profit_margin")); fcf = f(r.get("free_cashflow"))
    growth = max(g or 0, eg or 0)
    if peg is not None and peg > 0:
        if peg < 0.5: s += 30; reasons.append(f"PEG {peg:.2f} (deeply undervalued vs growth)")
        elif peg <= 1.0: s += 25; reasons.append(f"PEG {peg:.2f} (Lynch sweet spot)")
        elif peg <= 1.5: s += 15; reasons.append(f"PEG {peg:.2f} (fair)")
        elif peg <= 2.0: s += 6
    if 0.15 <= growth <= 0.40: s += 22; reasons.append(f"{growth*100:.0f}% growth (sustainable range)")
    elif 0.40 < growth <= 0.60: s += 14; reasons.append(f"{growth*100:.0f}% growth (fast)")
    elif growth > 0.60: s += 8
    elif 0.08 <= growth < 0.15: s += 12; reasons.append(f"{growth*100:.0f}% steady growth")
    if de is not None:
        if de < 30: s += 15; reasons.append("low debt")
        elif de < 80: s += 9
        elif de < 200: s += 3
        else: s -= 3; reasons.append("high leverage (caution)")
    else:
        s += 6
    if cr is not None and cr >= 1.5: s += 6; reasons.append("healthy liquidity")
    if roe is not None and roe >= 0.15: s += 10; reasons.append(f"ROE {roe*100:.0f}%")
    if margin is not None and margin >= 0.15: s += 8
    if fcf is not None and fcf > 0: s += 6; reasons.append("positive free cash flow")
    return s, reasons


def short_term_score(r):
    s = 0.0; reasons = []
    tpe = f(r.get("trailing_pe")); fpe = f(r.get("forward_pe"))
    price = f(r.get("price")); target = f(r.get("target_mean_price"))
    rec = (r.get("recommendation") or "").lower()
    eqg = f(r.get("earnings_q_growth")); peg = f(r.get("peg_ratio")); de = f(r.get("debt_to_equity"))
    if tpe and fpe and fpe > 0 and tpe > 0:
        drop = (tpe - fpe) / tpe
        if drop >= 0.4: s += 25; reasons.append("earnings inflecting up sharply (fwd P/E << trailing)")
        elif drop >= 0.2: s += 16; reasons.append("earnings improving next year")
        elif drop >= 0.05: s += 8
    if fpe and 0 < fpe <= 15: s += 15; reasons.append(f"cheap fwd P/E {fpe:.1f}")
    elif fpe and 15 < fpe <= 25: s += 8
    if price and target and price > 0:
        up = (target - price) / price
        if up >= 0.30: s += 22; reasons.append(f"{up*100:.0f}% analyst upside")
        elif up >= 0.15: s += 14; reasons.append(f"{up*100:.0f}% analyst upside")
        elif up >= 0.05: s += 6
    if rec == "strong_buy": s += 12; reasons.append("strong-buy consensus")
    elif rec == "buy": s += 7; reasons.append("buy consensus")
    if eqg is not None and eqg > 0.15: s += 10; reasons.append("strong recent earnings momentum")
    if peg is not None and 0 < peg <= 1.0: s += 8; reasons.append(f"PEG {peg:.2f}")
    if de is not None:
        if de > 300: s -= 18; reasons.append("very high leverage (risk)")
        elif de > 150: s -= 10; reasons.append("high leverage")
        elif de < 50: s += 5; reasons.append("clean balance sheet")
    return s, reasons


def upside_pct(r):
    p, t = f(r.get("price")), f(r.get("target_mean_price"))
    if p and t and p > 0:
        return round((t - p) / p * 100, 1)
    return None


def main():
    rows = list(csv.DictReader(open(SRC)))
    sentiment = json.load(open(f"{DATA}/sentiment.json")) if os.path.exists(f"{DATA}/sentiment.json") else {}
    fable = json.load(open(f"{DATA}/fable.json")) if os.path.exists(f"{DATA}/fable.json") else {}

    seen = {}
    cands = []
    for r in rows:
        tk = r["ticker"].upper(); base = tk.replace(".TO", "")
        if EXCLUDE_PAT.search(f"{r.get('sector','')} {r.get('industry','')} {r.get('name','')} {r.get('summary','')}"):
            continue
        if "CDR" in r.get("name", "").upper():
            continue
        nm = re.sub(r"[^a-z]", "", r.get("name", "").lower())[:20]
        if nm in seen:
            continue
        seen[nm] = tk

        lt, lt_r = long_term_score(r)
        st, st_r = short_term_score(r)
        sent = sentiment.get(base, {})
        reddit = sent.get("reddit", {}) or {}
        msm = sent.get("msm", {}) or {}
        reddit_score = reddit.get("score")
        msm_score = msm.get("score")
        overall = None
        if isinstance(reddit_score, (int, float)) and isinstance(msm_score, (int, float)):
            overall = round((reddit_score + msm_score) / 2, 1)
        elif isinstance(msm_score, (int, float)):
            overall = msm_score
        elif isinstance(reddit_score, (int, float)):
            overall = reddit_score

        cands.append({
            "ticker": r["ticker"], "name": r["name"], "sector": r.get("sector", ""),
            "industry": r.get("industry", ""), "category": lynch_category(r),
            "lt_score": round(lt, 1), "st_score": round(st, 1),
            "lt_reasons": lt_r, "st_reasons": st_r,
            "pe": f(r.get("trailing_pe")), "fpe": f(r.get("forward_pe")),
            "peg": f(r.get("peg_ratio")), "growth": f(r.get("revenue_growth")),
            "earnings_growth": f(r.get("earnings_growth")),
            "margin": f(r.get("profit_margin")), "roe": f(r.get("return_on_equity")),
            "de": f(r.get("debt_to_equity")), "mcap": f(r.get("market_cap")),
            "price": f(r.get("price")), "target": f(r.get("target_mean_price")),
            "upside": upside_pct(r), "rec": r.get("recommendation", ""),
            "website": r.get("website", ""), "yahoo": r.get("yahoo_link", ""),
            "summary": (r.get("summary", "") or "")[:600],
            # sentiment (scores are time-decay weighted; freshness reflects evidence age)
            "reddit_label": reddit.get("label"), "reddit_score": reddit_score,
            "reddit_summary": reddit.get("summary"), "reddit_mentions": sent.get("reddit_mentions"),
            "reddit_freshness": reddit.get("freshness"), "reddit_age_days": reddit.get("effective_age_days"),
            "msm_label": msm.get("label"), "msm_score": msm_score, "msm_summary": msm.get("summary"),
            "msm_freshness": msm.get("freshness"), "msm_age_days": msm.get("effective_age_days"),
            "overall_sentiment": overall,
            # fable deep verdict (optional)
            "fable": fable.get(base) or fable.get(tk),
        })

    out = {
        "generated": None,  # stamped by caller / update script
        "exclusions": EXCLUSIONS_DISPLAY,
        "candidates": cands,
        "universe_size": len(cands),
    }
    with open(f"{DATA}/candidates.json", "w") as fp:
        json.dump(out, fp, indent=2)
    print(f"candidates.json: {len(cands)} stocks")
    have_sent = sum(1 for c in cands if c["overall_sentiment"] is not None)
    have_fable = sum(1 for c in cands if c["fable"])
    print(f"  with sentiment: {have_sent}   with fable verdict: {have_fable}")


if __name__ == "__main__":
    main()

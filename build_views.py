"""
Compute the six ranked top-20 views from data/candidates.json, and write a dated
history snapshot for the backtest.

Views:
  lynch_long        Top 20 Lynch Long-Term Holds (5yr)     — by lt_score
  lynch_short       Top 20 Lynch Short-Term Holds (1yr)    — by st_score
  reddit_mentions   Top 20 Most-Mentioned on Reddit        — by reddit_mentions (volume)
  reddit_sentiment  Top 20 Highest Reddit Sentiment        — by reddit_score
  msm_sentiment     Top 20 Highest MSM Sentiment           — by msm_score
  overall_sentiment Top 20 Highest Overall Sentiment       — by overall_sentiment

Outputs:
  data/views.json               — the six lists (top 20 each) for the page
  data/history/<date>.json      — snapshot of each list's tickers + entry prices
"""
import json
import os
import sys

DATA = "data"
N = 20


def num(v):
    return v if isinstance(v, (int, float)) else None


def top_by(cands, key, needs_positive=True, tiebreak="lt_score"):
    def k(c):
        v = num(c.get(key))
        return v if v is not None else float("-inf")
    pool = [c for c in cands if num(c.get(key)) is not None]
    if needs_positive:
        pool = [c for c in pool if num(c.get(key)) > 0]
    pool.sort(key=lambda c: (k(c), num(c.get(tiebreak)) or 0), reverse=True)
    return pool[:N]


def slim(c, rank, sort_key):
    return {
        "rank": rank, "ticker": c["ticker"], "name": c["name"],
        "sector": c["sector"], "industry": c["industry"], "category": c["category"],
        "sort_value": c.get(sort_key),
        "lt_score": c.get("lt_score"), "st_score": c.get("st_score"),
        "pe": c.get("pe"), "fpe": c.get("fpe"), "peg": c.get("peg"),
        "growth": c.get("growth"), "margin": c.get("margin"), "roe": c.get("roe"),
        "de": c.get("de"), "mcap": c.get("mcap"), "price": c.get("price"),
        "upside": c.get("upside"), "rec": c.get("rec"),
        "reddit_label": c.get("reddit_label"), "reddit_score": c.get("reddit_score"),
        "reddit_summary": c.get("reddit_summary"), "reddit_mentions": c.get("reddit_mentions"),
        "reddit_freshness": c.get("reddit_freshness"), "reddit_age_days": c.get("reddit_age_days"),
        "msm_label": c.get("msm_label"), "msm_score": c.get("msm_score"),
        "msm_summary": c.get("msm_summary"), "overall_sentiment": c.get("overall_sentiment"),
        "msm_freshness": c.get("msm_freshness"), "msm_age_days": c.get("msm_age_days"),
        "website": c.get("website"), "yahoo": c.get("yahoo"),
        "summary": c.get("summary"), "fable": c.get("fable"),
        "lt_reasons": c.get("lt_reasons"), "st_reasons": c.get("st_reasons"),
    }


VIEWS = [
    ("lynch_long",        "Top 20 Lynch Long-Term Holds (5 yr)",       "lt_score"),
    ("lynch_short",       "Top 20 Lynch Short-Term Holds (1 yr)",      "st_score"),
    ("reddit_mentions",   "Top 20 Most-Mentioned Stocks on Reddit",    "reddit_mentions"),
    ("reddit_sentiment",  "Top 20 Highest Sentiment Stocks — Reddit",  "reddit_score"),
    ("msm_sentiment",     "Top 20 Highest Sentiment Stocks — Mainstream Media", "msm_score"),
    ("overall_sentiment", "Top 20 Highest Overall Sentiment Stocks",   "overall_sentiment"),
]


def main(date_str):
    blob = json.load(open(f"{DATA}/candidates.json"))
    cands = blob["candidates"]

    views = {}
    for key, title, sort_key in VIEWS:
        needs_pos = key != "reddit_mentions"  # mentions can legitimately be 0
        ranked = top_by(cands, sort_key, needs_positive=needs_pos)
        views[key] = {
            "title": title,
            "sort_key": sort_key,
            "items": [slim(c, i + 1, sort_key) for i, c in enumerate(ranked)],
        }

    out = {
        "generated": date_str,
        "exclusions": blob.get("exclusions", []),
        "universe_size": blob.get("universe_size"),
        "views": views,
        "view_order": [v[0] for v in VIEWS],
    }
    with open(f"{DATA}/views.json", "w") as fp:
        json.dump(out, fp, indent=2)

    # History snapshot for the backtest: tickers + entry prices per list.
    os.makedirs(f"{DATA}/history", exist_ok=True)
    snapshot = {
        "date": date_str,
        "lists": {
            key: [
                {"ticker": it["ticker"], "name": it["name"], "entry_price": it["price"]}
                for it in views[key]["items"]
            ]
            for key, _, _ in VIEWS
        },
    }
    with open(f"{DATA}/history/{date_str}.json", "w") as fp:
        json.dump(snapshot, fp, indent=2)

    print(f"views.json written for {date_str}")
    for key, title, sort_key in VIEWS:
        items = views[key]["items"]
        top = items[0] if items else None
        print(f"  {title[:40]:<40} {len(items):>2} names"
              + (f"  (#1 {top['ticker']} {top['sort_value']})" if top else "  (empty)"))
    print(f"history/{date_str}.json snapshot saved")


if __name__ == "__main__":
    # date passed in (scripts can't call datetime.now reliably here); default via arg
    date_str = sys.argv[1] if len(sys.argv) > 1 else "unknown-date"
    main(date_str)

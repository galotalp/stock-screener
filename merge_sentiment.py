"""
Merge sentiment agent batch outputs into data/sentiment.json, computing
TIME-DECAYED scores (14-day half-life) from each source's dated data points.

New agent format per ticker:
  {"ticker","reddit":{"label","mention_volume","summary","points":[{"score","date"|"bucket"}]},
                "msm":{"label","summary","points":[{"score","date"|"bucket"}]}}

We compute decayed score + freshness per source. If an agent returned only a flat
"score" (old format) we treat it as a single point dated at the scrape date.
"""
import json
import os
import glob
import datetime

from decay_score import decayed

DATA = "data"
SCRAPE_DATE = os.environ.get("SCRAPE_DATE", "2026-07-10")
SCRAPE_ORD = datetime.date.fromisoformat(SCRAPE_DATE).toordinal()

VOL_MAP = {"none": 0, "low": 5, "moderate": 20, "high": 50, "very high": 100}


def d2o(s):
    try:
        return datetime.date.fromisoformat(s).toordinal()
    except Exception:
        return None


def vol_to_int(v):
    if isinstance(v, (int, float)):
        return int(v)
    return VOL_MAP.get((v or "").strip().lower(), 0)


def source_block(src):
    """Return (score, freshness, effective_age, label, summary) for a source dict."""
    if not isinstance(src, dict):
        return None
    points = src.get("points")
    if not points and isinstance(src.get("score"), (int, float)):
        # old flat format -> single point dated today
        points = [{"score": src["score"], "date": SCRAPE_DATE}]
    dec = decayed(points, SCRAPE_ORD, d2o)
    if dec is None:
        return {
            "label": src.get("label"), "summary": src.get("summary"),
            "score": src.get("score"), "freshness": None,
            "effective_age_days": None, "n_points": 0,
        }
    return {
        "label": src.get("label"), "summary": src.get("summary"),
        "score": dec["score"], "freshness": dec["freshness"],
        "effective_age_days": dec["effective_age_days"], "n_points": dec["n_points"],
    }


def main():
    merged = {}
    if os.path.exists(f"{DATA}/sentiment.json"):
        try:
            merged = json.load(open(f"{DATA}/sentiment.json"))
        except Exception:
            merged = {}

    files = sorted(glob.glob(f"{DATA}/scan_result_*.json"))
    added = 0
    for fn in files:
        try:
            arr = json.load(open(fn))
        except Exception as e:
            print(f"  skip {fn}: {e}")
            continue
        for row in arr:
            tk = row.get("ticker", "").upper().replace(".TO", "")
            if not tk:
                continue
            reddit = source_block(row.get("reddit", {})) or {}
            msm = source_block(row.get("msm", {})) or {}
            merged[tk] = {
                "reddit": reddit,
                "msm": msm,
                "reddit_mentions": vol_to_int((row.get("reddit") or {}).get("mention_volume")),
            }
            added += 1

    json.dump(merged, open(f"{DATA}/sentiment.json", "w"), indent=2)
    fresh = sum(1 for v in merged.values() if (v.get("reddit") or {}).get("freshness") == "fresh")
    print(f"merged {added} tickers (half-life 14d); total {len(merged)}; reddit 'fresh': {fresh}")


if __name__ == "__main__":
    main()

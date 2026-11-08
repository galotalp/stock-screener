"""
Approximate time-decay for the EXISTING flat sentiment scan, without re-running agents.

The flat scan gave one score + a text summary per source. Many summaries embed
datable recency signals ("July 8", "recent downgrade", "Q1 beat", "~40% YTD",
"52-week low"). We parse those into an estimated age (days from the scrape date),
then apply the 14-day-half-life decay engine so a single-point score still gets a
freshness rating. When no signal is found we mark it 'aging' (unknown, ~30d).

This is an APPROXIMATION: it recency-tags the flat scores rather than truly
re-weighting multiple dated points. Honest, cheap, and quota-free. The next full
scrape with the dated-datapoint agent format supersedes it.
"""
import json
import os
import re
import datetime

from decay_score import _freshness  # reuse freshness bucketing

DATA = "data"
SCRAPE_DATE = os.environ.get("SCRAPE_DATE", "2026-07-10")
SCRAPE = datetime.date.fromisoformat(SCRAPE_DATE)

MONTHS = {m.lower(): i for i, m in enumerate(
    ["", "January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"])}

# recency phrases -> approximate age in days
PHRASE_AGE = [
    (r"\btoday\b", 0), (r"\bthis week\b|\bpast week\b|\bin recent days\b", 4),
    (r"\blast week\b", 10), (r"\brecently\b|\brecent\b|\bfresh\b|\bjust \b", 10),
    (r"\bthis month\b|\bpast month\b", 20),
    (r"\bmid-?july\b", 6), (r"\bearly july\b", 12), (r"\blate june\b", 18),
    (r"\bpast quarter\b|\blast quarter\b|\bq1\b|\bq2\b", 45),
    (r"\bmonths ago\b|\bearlier this year\b|\bytd\b|\byear-to-date\b|\b52-week\b", 90),
    (r"\bpast year\b|\bone-year\b|\byear-over-year\b|\byoy\b|\b2025\b", 150),
]


def estimate_age(summary):
    """Best-effort age (days) for a summary's evidence relative to scrape date."""
    if not summary:
        return None
    s = summary.lower()

    # explicit "Month DD" or "Month DDth" (assume 2026 unless clearly 2025)
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})\b", s)
    if m:
        mon = None
        for name, idx in MONTHS.items():
            if name and name.startswith(m.group(1)):
                mon = idx
                break
        if mon:
            day = int(m.group(2))
            year = 2025 if "2025" in s else 2026
            try:
                d = datetime.date(year, mon, min(day, 28))
                age = (SCRAPE - d).days
                if 0 <= age <= 400:
                    return age
            except ValueError:
                pass

    # phrase-based
    for pat, age in PHRASE_AGE:
        if re.search(pat, s):
            return age
    return None  # unknown


def decay_single(score, age):
    """Apply 14-day half-life to a single dated score. Returns (decayed unchanged, freshness)."""
    # For a single point, the score value itself doesn't change — decay affects how
    # much we TRUST it, surfaced as freshness. (Multi-point decay comes with real
    # dated data on the next scrape.) We still expose the age & freshness.
    if age is None:
        age = 30  # unknown -> treat as ~1 month ('aging')
    return score, round(age, 0), _freshness(age)


def process_source(src):
    if not isinstance(src, dict):
        return src
    score = src.get("score")
    age = estimate_age(src.get("summary"))
    if isinstance(score, (int, float)):
        _, a, fresh = decay_single(score, age)
        src["effective_age_days"] = a
        src["freshness"] = fresh
        src["age_estimated"] = True  # flag: derived from summary text, not dated points
    return src


def main():
    sent = json.load(open(f"{DATA}/sentiment.json"))
    from collections import Counter
    fr = Counter()
    for tk, v in sent.items():
        v["reddit"] = process_source(v.get("reddit", {}))
        v["msm"] = process_source(v.get("msm", {}))
        if v["reddit"].get("freshness"):
            fr["reddit_" + v["reddit"]["freshness"]] += 1
        if v["msm"].get("freshness"):
            fr["msm_" + v["msm"]["freshness"]] += 1
    json.dump(sent, open(f"{DATA}/sentiment.json", "w"), indent=2)
    print(f"recency-tagged {len(sent)} tickers (14d half-life, summary-derived ages)")
    for k in sorted(fr):
        print(f"  {k}: {fr[k]}")


if __name__ == "__main__":
    main()

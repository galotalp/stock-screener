"""
Time-decay sentiment scoring (14-day half-life).

Each sentiment source (reddit / msm) comes as a list of dated data points:
  {"score": 0-100, "date": "YYYY-MM-DD"}  OR  {"score": 0-100, "bucket": "this_week"}

We convert each point to an age in days (relative to the scrape date), weight it by
  w = 0.5 ** (age_days / HALF_LIFE)
and return the weighted-average score plus a 'freshness' summary:
  - effective_age: weighted-average age of the evidence (days)
  - freshness: fresh | recent | aging | stale  (from effective_age)
  - n_points: how many data points fed the score

Recency BUCKETS map to a representative age (days) when an exact date is absent.
"""
HALF_LIFE = 14.0  # days

BUCKET_AGE = {
    "today": 0, "this_week": 3, "last_week": 10, "this_month": 20,
    "1-3_months": 60, "3-6_months": 135, "older": 240,
    # tolerant aliases
    "week": 3, "month": 20, "quarter": 75, "recent": 7, "stale": 150,
}


def _age_days(point, scrape_ordinal, date_to_ordinal):
    """Age in days for one data point, given the scrape date's ordinal."""
    d = point.get("date")
    if d:
        o = date_to_ordinal(d)
        if o is not None:
            return max(0, scrape_ordinal - o)
    b = (point.get("bucket") or "").strip().lower().replace(" ", "_").replace("-", "-")
    return BUCKET_AGE.get(b, 30)  # default: ~1 month if unknown


def _freshness(age):
    if age <= 7:
        return "fresh"
    if age <= 21:
        return "recent"
    if age <= 60:
        return "aging"
    return "stale"


def decayed(points, scrape_ordinal, date_to_ordinal):
    """Return dict with decayed score + freshness, or None if no usable points."""
    pts = []
    for p in points or []:
        s = p.get("score")
        if not isinstance(s, (int, float)):
            continue
        age = _age_days(p, scrape_ordinal, date_to_ordinal)
        w = 0.5 ** (age / HALF_LIFE)
        pts.append((s, age, w))
    if not pts:
        return None
    wsum = sum(w for _, _, w in pts)
    if wsum <= 0:
        return None
    score = sum(s * w for s, _, w in pts) / wsum
    eff_age = sum(a * w for _, a, w in pts) / wsum
    return {
        "score": round(score, 1),
        "effective_age_days": round(eff_age, 1),
        "freshness": _freshness(eff_age),
        "n_points": len(pts),
    }


if __name__ == "__main__":
    # self-test with a fixed scrape date
    import datetime
    def d2o(s):
        try:
            return datetime.date.fromisoformat(s).toordinal()
        except Exception:
            return None
    scrape = datetime.date(2026, 7, 10).toordinal()
    demo = [
        {"score": 80, "date": "2026-07-09"},   # ~1 day old -> weight ~1
        {"score": 30, "date": "2026-05-10"},   # ~60 days old -> weight tiny
    ]
    print("decayed:", decayed(demo, scrape, d2o))
    print("buckets:", decayed(
        [{"score": 90, "bucket": "this_week"}, {"score": 20, "bucket": "1-3_months"}],
        scrape, d2o))

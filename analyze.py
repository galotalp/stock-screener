"""
Align the two INDEPENDENT series (sentiment + price) at analysis time and compute
lead/lag relationships. No storage-time coupling; everything is derived here.

Per ticker:
  - Build a daily sentiment series (mean of that day's mention scores) + a decayed
    14-day-half-life version + daily mention volume, from data/mentions/<T>.jsonl.
  - Build a daily price series (close) from data/prices/<T>.json, and daily returns.
  - Cross-correlate sentiment (level and velocity) vs forward price return at
    offsets k = -7..+7 days.  k>0 => sentiment LEADS price by k days.
  - Report the strongest lead/lag, and the by-source breakdown of mentions.

Emits data/analysis.json (per-ticker) for the chart UI, and prints a leaderboard.
Direction-agnostic: we keep the sign of every correlation.
"""
import json
import os
import glob
import datetime

MDIR = "data/mentions"
PDIR = "data/prices"
MAX_LAG = 7
HALF_LIFE = 14.0
DECAY = 0.5 ** (1.0 / HALF_LIFE)


def day_of(iso):
    try:
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).date()
    except Exception:
        return None


# Map each raw source to one of two families for the social/news/both toggle.
def source_family(src):
    s = (src or "").lower()
    if s.startswith("news"):
        return "news"
    if s.startswith("stocktwits") or s.startswith("reddit"):
        return "social"
    return "social"  # default unknown social


# ---------- sentiment series (independent), split by source family ----------
def sentiment_daily(ticker):
    """Return per-day mean/count for each source scope: both, social, news."""
    p = f"{MDIR}/{ticker}.jsonl"
    if not os.path.exists(p):
        return {}
    # scope -> day -> list of scores
    buckets = {"both": {}, "social": {}, "news": {}}
    src_counts = {}
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except Exception:
            continue
        d = day_of(m.get("date"))
        s = m.get("score")
        if d is None or not isinstance(s, (int, float)):
            continue
        fam = source_family(m.get("source"))
        buckets["both"].setdefault(d, []).append(s)
        buckets[fam].setdefault(d, []).append(s)
        src_counts[fam] = src_counts.get(fam, 0) + 1

    def summarize(day_map):
        return {"mean": {d: sum(v) / len(v) for d, v in day_map.items()},
                "count": {d: len(v) for d, v in day_map.items()}}
    return {"both": summarize(buckets["both"]),
            "social": summarize(buckets["social"]),
            "news": summarize(buckets["news"]),
            "src_counts": src_counts}


# ---------- price series (independent) ----------
def price_daily(ticker):
    p = f"{PDIR}/{ticker}.json"
    if not os.path.exists(p):
        return {}
    out = {}
    for r in json.load(open(p)):
        try:
            d = datetime.date.fromisoformat(r["date"])
        except Exception:
            continue
        if r.get("close") is not None:
            out[d] = r["close"]
    return out


def daterange(lo, hi):
    out = []
    d = lo
    while d <= hi:
        out.append(d)
        d += datetime.timedelta(days=1)
    return out


def pearson(xs, ys):
    n = len(xs)
    if n < 5:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / (vx * vy) ** 0.5


def ffill(d_list, dmap):
    out = []
    last = None
    for d in d_list:
        if d in dmap:
            last = dmap[d]
        out.append(last)
    return out


def xcorr_signal(signal, returns, n):
    out = {}
    for k in range(-MAX_LAG, MAX_LAG + 1):
        xs, ys = [], []
        for t in range(n):
            tk = t + k
            if 0 <= tk < n and signal[t] is not None and returns[tk] is not None:
                xs.append(signal[t]); ys.append(returns[tk])
        c = pearson(xs, ys)
        if c is not None:
            out[k] = {"corr": round(c, 3), "n": len(xs)}
    return out


def series_for_scope(day_map, days):
    """decayed EWMA + velocity + raw level for a given source-scope day_map."""
    decayed, ew = [], None
    for d in days:
        if d in day_map:
            v = day_map[d]
            ew = v if ew is None else DECAY * ew + (1 - DECAY) * v
        elif ew is not None:
            ew = DECAY * ew
        decayed.append(ew)
    velocity = [None] + [
        (decayed[i] - decayed[i - 1]) if (decayed[i] is not None and decayed[i - 1] is not None) else None
        for i in range(1, len(days))
    ]
    raw = [day_map.get(d) for d in days]
    return raw, decayed, velocity


def analyze(ticker, meta=None):
    sent = sentiment_daily(ticker)
    prices = price_daily(ticker)
    if not sent.get("both", {}).get("mean") or not prices:
        return None

    # WINDOW = the sentiment span, padded by ±1 day (per spec). Price is clipped to
    # this window so the x-axis only covers time we actually have sentiment for.
    smin = min(sent["both"]["mean"]) - datetime.timedelta(days=1)
    smax = max(sent["both"]["mean"]) + datetime.timedelta(days=1)
    days = daterange(smin, smax)
    price_ff = ffill(days, prices)

    # daily returns (within the sentiment window)
    returns = [None]
    for i in range(1, len(days)):
        if price_ff[i] is not None and price_ff[i - 1] not in (None, 0):
            returns.append((price_ff[i] - price_ff[i - 1]) / price_ff[i - 1])
        else:
            returns.append(None)

    n = len(days)
    scopes = {}
    for scope in ("both", "social", "news"):
        mean_map = sent[scope]["mean"]
        raw, decayed, velocity = series_for_scope(mean_map, days)
        xc_level = xcorr_signal(decayed, returns, n)
        xc_vel = xcorr_signal(velocity, returns, n)

        def best(xc):
            if not xc:
                return None, None
            k, v = max(xc.items(), key=lambda kv: abs(kv[1]["corr"]))
            return k, v["corr"]
        bl_k, bl_c = best(xc_level)
        bv_k, bv_c = best(xc_vel)
        # per-scope index bounds: clip to where BOTH sentiment AND price exist, so the
        # chart's two lines always start/end together (no sentiment drawn where we have
        # no price — e.g. an old news mention that predates our price history).
        present = [i for i in range(n) if raw[i] is not None and price_ff[i] is not None]
        if present:
            idx0 = max(0, present[0] - 1)
            idx1 = min(n - 1, present[-1] + 1)
            # keep the pad only where price still exists
            while idx0 < present[0] and price_ff[idx0] is None:
                idx0 += 1
            while idx1 > present[-1] and price_ff[idx1] is None:
                idx1 -= 1
        else:
            idx0, idx1 = 0, n - 1
        # overlap = days WITHIN the displayed window where both signals exist
        overlap = sum(1 for i in range(idx0, idx1 + 1)
                      if decayed[i] is not None and returns[i] is not None)
        scopes[scope] = {
            "sentiment_level": [round(x, 4) if x is not None else None for x in raw],
            "sentiment_decayed": [round(x, 4) if x is not None else None for x in decayed],
            "sentiment_velocity": [round(x, 4) if x is not None else None for x in velocity],
            "mention_count": [sent[scope]["count"].get(d, 0) for d in days],
            "xcorr_level": xc_level, "xcorr_velocity": xc_vel,
            "best_level_lag": bl_k, "best_level_corr": bl_c,
            "best_velocity_lag": bv_k, "best_velocity_corr": bv_c,
            "n_overlap": overlap,
            "n_mentions": sum(sent[scope]["count"].values()),
            "idx_start": idx0, "idx_end": idx1,  # x-axis clip range for this scope
        }

    sc = sent.get("src_counts", {})
    social_n, news_n = sc.get("social", 0), sc.get("news", 0)
    source_favored = "social" if social_n > news_n else ("news" if news_n > social_n else "balanced")

    m = meta or {}
    return {
        "ticker": ticker,
        "dates": [d.isoformat() for d in days],
        "price": [round(x, 4) if x is not None else None for x in price_ff],
        "scopes": scopes,
        "n_mentions": scopes["both"]["n_mentions"],
        "n_overlap": scopes["both"]["n_overlap"],
        # category metadata
        "sector": m.get("sector", "Unknown"),
        "industry": m.get("industry", "Unknown"),
        "mcap_bucket": m.get("mcap_bucket", "unknown"),
        "source_favored": source_favored,
        "social_mentions": social_n, "news_mentions": news_n,
    }


def main():
    meta_all = {}
    up = "data/universe_tracked.json"
    if os.path.exists(up):
        meta_all = json.load(open(up)).get("meta", {})
    tickers = sorted(os.path.basename(f)[:-6] for f in glob.glob(f"{MDIR}/*.jsonl"))
    out = {}
    for tk in tickers:
        a = analyze(tk, meta_all.get(tk))
        # Show every stock we have mentions + price for, however narrow the window.
        # Windows widen over time as weekly collection accumulates. Need >=2 overlap
        # days just to draw a line and compute any correlation at all.
        if a and a["n_overlap"] >= 2:
            out[tk] = a
    json.dump(out, open("data/analysis.json", "w"))

    def blc(a): return a["scopes"]["both"]["best_level_corr"]
    rows = [a for a in out.values() if blc(a) is not None]
    rows.sort(key=lambda a: abs(blc(a)), reverse=True)
    print(f"{len(out)} tickers analyzed (>=6 overlap days)\n")
    print(f"  {'TICKER':<7}{'sector':<20}{'mcap':<7}{'src':<9}{'lvl_lag':>8}{'lvl_corr':>9}{'overlap':>8}{'ment':>6}")
    for a in rows[:20]:
        b = a["scopes"]["both"]
        print(f"  {a['ticker']:<7}{a['sector'][:19]:<20}{a['mcap_bucket']:<7}{a['source_favored']:<9}"
              f"{b['best_level_lag']:>+8}{b['best_level_corr']:>+9.2f}{b['n_overlap']:>8}{a['n_mentions']:>6}")
    print("\nlag>0 = sentiment LEADS price by k days; lag<0 = sentiment LAGS (reacts to) price")


if __name__ == "__main__":
    main()

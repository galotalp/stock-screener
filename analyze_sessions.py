"""
Session-aware lead/lag analysis: does sentiment lead the REGULAR-session price but
lag (or coincide with) the EXTENDED-HOURS price? If the extended-hours market has
already absorbed a sentiment shift by the regular open, then regular-hours retail
sentiment was not actionable.

Both series are INDEPENDENT (mentions vs prices); we align only here.

For each ticker we build two hourly-return series from the intraday price store and
correlate the (decayed) sentiment-change against each, across ±lag hours:

  1. FULL series   — every hourly bar (pre-market + regular + after-hours). Captures
                     price moves that happen in extended hours.
  2. REGULAR-only  — returns computed only across regular-session bars (the price
                     path a retail trader who only trades 9:30-4 would experience).

Interpretation:
  - sentiment leads FULL and REGULAR similarly  -> genuinely predictive
  - sentiment leads REGULAR but is coincident/lagging on FULL -> the extended-hours
    market already moved on it; retail-hours signal is "too late"

Outputs data/session_analysis.json (per ticker) + prints a summary.
"""
import json
import os
import glob
import datetime as dt

MDIR = "data/mentions"
PDIR = "data/prices"
EDIR = "data/earnings"
MAX_LAG_H = 24          # hours
HALF_LIFE_H = 14 * 24   # keep the 14-day half-life, expressed in hours
DECAY_H = 0.5 ** (1.0 / HALF_LIFE_H)


def parse(iso):
    try:
        return dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception:
        return None


def pearson(xs, ys):
    n = len(xs)
    if n < 6:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / (vx * vy) ** 0.5


def hourly_sentiment(ticker):
    """decayed sentiment value at each UTC hour that has >=1 mention (EWMA over the
    hourly mean of mention scores). Returns {hour_dt: decayed_value}."""
    p = f"{MDIR}/{ticker}.jsonl"
    if not os.path.exists(p):
        return {}
    by_hour = {}
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        d = parse(r.get("date"))
        s = r.get("score")
        if d is None or not isinstance(s, (int, float)):
            continue
        hk = d.replace(minute=0, second=0, microsecond=0)
        by_hour.setdefault(hk, []).append(s)
    if not by_hour:
        return {}
    means = {h: sum(v) / len(v) for h, v in by_hour.items()}
    # EWMA across the continuous hourly grid
    lo, hi = min(means), max(means)
    out, ew = {}, None
    h = lo
    while h <= hi:
        if h in means:
            v = means[h]
            ew = v if ew is None else DECAY_H * ew + (1 - DECAY_H) * v
        elif ew is not None:
            ew = DECAY_H * ew
        if ew is not None:
            out[h] = ew
        h += dt.timedelta(hours=1)
    return out


def price_returns(ticker):
    """From the intraday store, build hourly returns two ways: full (all bars) and
    regular-only (returns across consecutive REGULAR bars). Returns
    (full_ret{hour:ret}, reg_ret{hour:ret})."""
    p = f"{PDIR}/{ticker}_intraday.json"
    if not os.path.exists(p):
        return {}, {}
    bars = json.load(open(p))
    bars = [(parse(b["ts"]), b["price"], b.get("session")) for b in bars if b.get("price")]
    bars = [(t, pr, s) for t, pr, s in bars if t is not None]
    bars.sort()
    full, reg = {}, {}
    for i in range(1, len(bars)):
        t0, p0, _ = bars[i - 1]
        t1, p1, s1 = bars[i]
        if p0 and p0 != 0:
            full[t1.replace(minute=0, second=0, microsecond=0)] = (p1 - p0) / p0
    # regular-only: consecutive regular bars
    regbars = [(t, pr) for t, pr, s in bars if s == "regular"]
    for i in range(1, len(regbars)):
        t0, p0 = regbars[i - 1]
        t1, p1 = regbars[i]
        if p0 and p0 != 0:
            reg[t1.replace(minute=0, second=0, microsecond=0)] = (p1 - p0) / p0
    return full, reg


def earnings_windows(ticker):
    """[(start_dt, end_dt)] contaminated windows for a ticker: [ts, next_regular_open).
    Empty if no earnings file. Used to split analysis into earnings vs normal regimes."""
    p = f"{EDIR}/{ticker}.json"
    if not os.path.exists(p):
        return []
    try:
        data = json.load(open(p))
    except Exception:
        return []
    wins = []
    for e in data.get("events", []):
        s = parse(e.get("ts"))
        en = parse(e.get("window_end")) if e.get("window_end") else None
        if s is None:
            continue
        # if window_end is missing, fall back to a minimal same-instant window so the
        # event is still flagged (start hour only).
        if en is None or en <= s:
            en = s + dt.timedelta(hours=1)
        wins.append((s, en))
    return wins


def _in_windows(hour_dt, wins):
    """True if hour_dt falls in any [start, end) earnings-contaminated window."""
    for s, e in wins:
        if s <= hour_dt < e:
            return True
    return False


def xcorr(sent_hourly, ret_hourly, wins=None, regime="all"):
    """corr(sentiment_change[h], return[h+lag]) over ±MAX_LAG_H hours.

    regime: 'all'      -> use every paired point
            'ex_earn'  -> exclude points whose PRICE hour is in an earnings window
            'earn'     -> keep only points whose price hour is in an earnings window
    Earnings contaminate the price move, so we classify each pair by its return hour
    (h+lag). `wins` is the list of (start,end) windows; ignored when regime='all'."""
    hours = sorted(sent_hourly)
    change = {}
    prev = None
    for h in hours:
        if prev is not None and (h - prev) == dt.timedelta(hours=1):
            change[h] = sent_hourly[h] - sent_hourly[prev]
        prev = h
    out = {}
    for lag in range(-MAX_LAG_H, MAX_LAG_H + 1):
        xs, ys = [], []
        for h, c in change.items():
            hr = h + dt.timedelta(hours=lag)
            if hr not in ret_hourly:
                continue
            if regime != "all" and wins:
                inw = _in_windows(hr, wins)
                if regime == "ex_earn" and inw:
                    continue
                if regime == "earn" and not inw:
                    continue
            xs.append(c)
            ys.append(ret_hourly[hr])
        r = pearson(xs, ys)
        if r is not None:
            out[lag] = {"corr": round(r, 3), "n": len(xs)}
    return out


def best(xc):
    if not xc:
        return None, None
    k, v = max(xc.items(), key=lambda kv: abs(kv[1]["corr"]))
    return k, v["corr"]


def analyze(ticker):
    sent = hourly_sentiment(ticker)
    full_ret, reg_ret = price_returns(ticker)
    if not sent or (not full_ret and not reg_ret):
        return None
    wins = earnings_windows(ticker)
    # base (all-data) full/regular xcorr — unchanged output for back-compat
    xc_full = xcorr(sent, full_ret)
    xc_reg = xcorr(sent, reg_ret)
    bf_lag, bf_corr = best(xc_full)
    br_lag, br_corr = best(xc_reg)

    # regime comparison on the FULL price path (the one earnings actually move):
    # all / earnings-excluded / earnings-only. Only meaningful when we have windows.
    xc_all = xc_full
    xc_ex = xcorr(sent, full_ret, wins, "ex_earn") if wins else {}
    xc_earn = xcorr(sent, full_ret, wins, "earn") if wins else {}
    ea_lag, ea_corr = best(xc_ex)
    en_lag, en_corr = best(xc_earn)
    # how many price hours fall inside an earnings window (data sufficiency signal)
    earn_hours = sum(1 for h in full_ret if _in_windows(h, wins)) if wins else 0

    return {
        "ticker": ticker,
        "full_best_lag_h": bf_lag, "full_best_corr": bf_corr,
        "regular_best_lag_h": br_lag, "regular_best_corr": br_corr,
        "xcorr_full": {str(k): v for k, v in xc_full.items()},
        "xcorr_regular": {str(k): v for k, v in xc_reg.items()},
        "n_sent_hours": len(sent),
        # --- earnings regime comparison (all on the full price path) ---
        "n_earnings_events": len(wins),
        "n_earnings_price_hours": earn_hours,
        "regime": {
            "all":     {"best_lag_h": bf_lag, "best_corr": bf_corr,
                        "xcorr": {str(k): v for k, v in xc_all.items()}},
            "ex_earn": {"best_lag_h": ea_lag, "best_corr": ea_corr,
                        "xcorr": {str(k): v for k, v in xc_ex.items()}},
            "earn":    {"best_lag_h": en_lag, "best_corr": en_corr,
                        "xcorr": {str(k): v for k, v in xc_earn.items()}},
        },
    }


def main():
    tickers = sorted(os.path.basename(f)[:-6] for f in glob.glob(f"{MDIR}/*.jsonl"))
    out = {}
    for tk in tickers:
        a = analyze(tk)
        if a and (a["full_best_corr"] is not None or a["regular_best_corr"] is not None):
            out[tk] = a
    json.dump(out, open("data/session_analysis.json", "w"))
    print(f"{len(out)} tickers with session-aware analysis\n")
    print(f"  {'TICKER':<7}{'full_lag':>9}{'full_r':>8}{'reg_lag':>9}{'reg_r':>8}")
    rows = [a for a in out.values() if a["full_best_corr"] is not None]
    rows.sort(key=lambda a: abs(a["full_best_corr"] or 0), reverse=True)
    for a in rows[:15]:
        fl = f"{a['full_best_lag_h']:+d}h" if a['full_best_lag_h'] is not None else "-"
        rl = f"{a['regular_best_lag_h']:+d}h" if a['regular_best_lag_h'] is not None else "-"
        print(f"  {a['ticker']:<7}{fl:>9}{(a['full_best_corr'] or 0):>+8.2f}"
              f"{rl:>9}{(a['regular_best_corr'] or 0):>+8.2f}")
    print("\nlag>0 = sentiment LEADS price (hours); lag<0 = sentiment LAGS price")

    # earnings regime comparison
    print("\n=== EARNINGS REGIME (full price path): all vs earnings-excluded vs earnings-only ===")
    print(f"  {'TICKER':<7}{'events':>7}{'e_hrs':>6}  "
          f"{'all_r':>7}{'exEarn_r':>9}{'earn_r':>8}")
    ecmp = [a for a in out.values() if a.get("n_earnings_events")]
    ecmp.sort(key=lambda a: a.get("n_earnings_price_hours", 0), reverse=True)
    for a in ecmp[:15]:
        rg = a["regime"]
        def f(x):
            return f"{x:+.2f}" if x is not None else "   -"
        print(f"  {a['ticker']:<7}{a['n_earnings_events']:>7}{a['n_earnings_price_hours']:>6}  "
              f"{f(rg['all']['best_corr']):>7}{f(rg['ex_earn']['best_corr']):>9}{f(rg['earn']['best_corr']):>8}")
    print("  (exEarn = the sentiment->price relationship with earnings shocks removed;")
    print("   a big all-vs-exEarn gap means earnings were driving the apparent correlation)")


if __name__ == "__main__":
    main()

"""
analyze_v2.py — does news sentiment predict a stock's *next* return, once
earnings, market moves and reverse causality are accounted for?

News only: StockTwits is still collected by the scraper but is not used here.

Read-only over the scraped stores (data/mentions, data/prices, data/earnings);
writes data/analysis_v2.json, which gen_analysis.py renders. Pure Python (no
numpy/pandas) so it runs unchanged on the t3.micro's Python 3.9.

Design (each choice fixes a specific flaw in analyze.py / analyze_sessions.py)
  Stance        sentiment_v2.news_stance(): each article's Claude Haiku label
                (news_llm.py) — bullish +1 / neutral 0 / bearish -1; articles
                not about the company or only reporting a price move carry no
                stance but still count as attention. Unlabeled articles fall
                back to the word list.
  Slices        all news; "new information" (a specific new fact or event, not
                about earnings); earnings news. The last two need Haiku labels.
  Time windows  Trading-day windows on the NYSE clock, not UTC dates:
                  C_d  close(d-1) -> close(d)          daily
                  O_d  close(d-1) -> first regular bar (overnight + pre-market)
                  R_d  first regular bar -> close(d)   regular session
                Sentiment is always measured in a window that ENDS before the
                return window starts, so a reaction can't masquerade as a lead.
  Returns       Log returns minus the equal-weighted average of every other
                tracked stock in the same window (abnormal return), winsorized
                at 0.5% / 99.5%.
  Earnings      Trading days -1..+2 around each report are flagged; every test
                is run on all days, on non-earnings days only and on earnings
                days only.
  Inference     One pooled regression across all stocks with stock fixed
                effects, controls for the return already known at the time and
                for attention (mention volume), and standard errors clustered by
                date (market-wide news hits every stock on the same day). Plus a
                daily long-short check, a first-70% / last-30% split, and per-
                stock correlations with a false-discovery-rate count.
"""
import bisect
import datetime as dt
import glob
import json
import math
import os

import sentiment_v2 as sv

MIN_N = 1              # articles with a stance needed for a window's sentiment to count
MIN_ROWS = 20          # rows a stock needs to enter a pooled regression
MIN_ROWS_EARN = 4      # earnings-only slice: a stock has ~4 earnings days per report
MIN_MKT = 20           # stocks needed in a window to form the market average
EARN_PRE, EARN_POST = 1, 2
SCOPES = ("news", "newinfo", "earnnews")
REGIMES = ("all", "ex_earn", "earn")
PERIODS = ("full", "early", "late")
SPLIT = 0.7
DAILY_LAGS = range(-5, 6)
SESS_LAGS = range(-6, 7)
OUT = "data/analysis_v2.json"


def _epoch(iso):
    try:
        return int(dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())
    except Exception:
        return None


# --------------------------------------------------------------------------
# calendar
# --------------------------------------------------------------------------
def calendar(tickers):
    """[(date, open_u, close_u)] for every date any stock has a daily close."""
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    dates = set()
    for tk in tickers:
        try:
            for r in json.load(open(f"data/prices/{tk}.json")):
                if isinstance(r.get("close"), (int, float)):
                    dates.add(r["date"])
        except Exception:
            continue
    try:
        import market_session as ms
        sched_for = ms._schedule_for_year
    except Exception:
        sched_for = None
    out = []
    for d in sorted(dates):
        day = dt.date.fromisoformat(d)
        if day.weekday() >= 5:
            continue
        row = None
        if sched_for is not None:
            try:
                row = sched_for(day.year).get(day)
            except Exception:
                row = None
        if row:
            o, c = int(row[0].timestamp()), int(row[1].timestamp())
        else:
            o = int(dt.datetime(day.year, day.month, day.day, 9, 30, tzinfo=et).timestamp())
            c = int(dt.datetime(day.year, day.month, day.day, 16, 0, tzinfo=et).timestamp())
        out.append((d, o, c))
    return out


# --------------------------------------------------------------------------
# per-stock windows
# --------------------------------------------------------------------------
def _new_win(T):
    return {sc: {k: [[0, 0, 0.0] for _ in range(T)] for k in "COR"} for sc in SCOPES}


def load_stock(tk, cal, closes_u, val):
    T = len(cal)
    didx = {d: i for i, (d, _, _) in enumerate(cal)}
    close = [None] * T
    try:
        for r in json.load(open(f"data/prices/{tk}.json")):
            i = didx.get(r.get("date"))
            v = r.get("close")
            if i is not None and isinstance(v, (int, float)) and v > 0:
                close[i] = float(v)
    except Exception:
        return None
    # open proxy: first regular-session hourly bar within 90 min of the open
    opx, ot = [None] * T, [None] * T
    p = f"data/prices/{tk}_intraday.json"
    if os.path.exists(p):
        try:
            bars = json.load(open(p))
        except Exception:
            bars = []
        for b in bars:
            u = _epoch(b.get("ts") or "")
            v = b.get("price")
            if u is None or not isinstance(v, (int, float)) or v <= 0 or b.get("session") != "regular":
                continue
            i = bisect.bisect_left(closes_u, u)
            if i >= T or not (cal[i][1] <= u <= cal[i][1] + 5400):
                continue
            if ot[i] is None or u < ot[i]:
                ot[i], opx[i] = u, float(v)

    def lr(a, b):
        if a is None or b is None:
            return None
        r = math.log(b / a)
        return r if abs(r) < 0.5 else None      # guard split/bad-tick artefacts

    rC, rO, rR = [None] * T, [None] * T, [None] * T
    for i in range(1, T):
        rC[i] = lr(close[i - 1], close[i])
        if opx[i] is not None:
            rO[i] = lr(close[i - 1], opx[i])
            rR[i] = lr(opx[i], close[i])
    # mentions -> windows
    win = _new_win(T)
    earn_m = [0] * T
    n_m = n_st = 0
    p = f"data/mentions/{tk}.jsonl"
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            try:
                m = json.loads(line)
            except Exception:
                continue
            u = _epoch(m.get("date") or "")
            if u is None:
                continue
            if sv._family(m.get("source")) != "news":
                continue
            fl = {"earn": bool(sv.EARN_RE.search(m.get("blurb") or "")),
                  "move": bool(sv.MOVE_RE.search(m.get("blurb") or ""))}
            s, method, lab = sv.news_stance(tk, m, fl)
            _validate(val, tk, m, s, method, lab)
            i = bisect.bisect_left(closes_u, u)   # close(i-1) < u <= close(i)
            if i == 0 or i >= T:
                continue
            n_m += 1
            kinds = ["C"]
            if ot[i] is not None:
                kinds.append("O" if u <= ot[i] else "R")
            if (lab["earn"] if lab else fl["earn"]):
                earn_m[i] += 1
            scopes = ["news"]
            if lab is not None and lab.get("about", True) and not lab.get("move"):
                if lab.get("earn"):
                    scopes.append("earnnews")
                elif lab.get("new"):
                    scopes.append("newinfo")
            for sc in scopes:
                for k in kinds:
                    cell = win[sc][k][i]
                    cell[0] += 1
                    if s is not None:
                        cell[1] += 1
                        cell[2] += s
            if s is not None:
                n_st += 1
    # earnings reaction days
    earn_day = [False] * T
    n_ev = 0
    p = f"data/earnings/{tk}.json"
    if os.path.exists(p):
        try:
            evs = json.load(open(p)).get("events", [])
        except Exception:
            evs = []
        for e in evs:
            u = _epoch(e.get("ts") or "")
            if u is None or u > closes_u[-1]:
                continue
            after = e.get("session") in ("after_hours", "overnight")
            j = bisect.bisect_right(closes_u, u) if after else bisect.bisect_left(closes_u, u)
            if j >= T:
                continue
            n_ev += 1
            for i in range(max(0, j - EARN_PRE), min(T, j + EARN_POST + 1)):
                earn_day[i] = True
    return {"rC": rC, "rO": rO, "rR": rR, "win": win, "earn": earn_day, "earn_m": earn_m,
            "n_m": n_m, "n_st": n_st, "n_ev": n_ev, "has_sess": any(o is not None for o in ot)}


def _validate(val, tk, m, s, method, lab):
    val["mix"][method] = val["mix"].get(method, 0) + 1
    val["n"] += 1
    if lab is None:
        return
    for f in ("about", "earn", "move", "new"):
        val["flags"][f] += bool(lab.get(f))
    val["stance"][lab.get("stance", "neutral")] += 1
    pos, neg = sv.lex_score(m.get("blurb") or "", False)
    if pos != neg:                      # agreement with the word list where it has a view
        v = val["agree"]
        v["n"] += 1
        llm = sv.LLM_VAL.get(lab.get("stance"), 0.0)
        if llm == 0:
            v["llm_neutral"] += 1
        elif (llm > 0) == (pos > neg):
            v["same"] += 1
        else:
            v["opposite"] += 1
    # reservoir of labeled examples for the page
    ex = val["examples"]
    val["seen"] += 1
    item = {"tk": tk, "text": (m.get("blurb") or "")[:220], "stance": lab.get("stance"),
            "flags": [f for f in ("about", "earn", "move", "new") if lab.get(f)]}
    if len(ex) < 16:
        ex.append(item)
    else:
        j = val["rng"].randrange(val["seen"])
        if j < 16:
            ex[j] = item


# --------------------------------------------------------------------------
# market adjustment
# --------------------------------------------------------------------------
def abnormal(stocks, T):
    for key in ("rC", "rO", "rR"):
        S, N = [0.0] * T, [0] * T
        for st in stocks.values():
            for i, r in enumerate(st[key]):
                if r is not None:
                    S[i] += r
                    N[i] += 1
        vals = []
        for st in stocks.values():
            a = [None] * T
            for i, r in enumerate(st[key]):
                if r is not None and N[i] >= MIN_MKT:
                    a[i] = r - (S[i] - r) / (N[i] - 1)
                    vals.append(a[i])
            st["a" + key[1]] = a
        if not vals:
            continue
        vals.sort()
        lo, hi = vals[int(0.005 * (len(vals) - 1))], vals[int(0.995 * (len(vals) - 1))]
        for st in stocks.values():
            st["a" + key[1]] = [None if v is None else min(hi, max(lo, v)) for v in st["a" + key[1]]]


def net(cell, mn=MIN_N):
    return cell[2] / cell[1] if cell[1] >= mn else None


# --------------------------------------------------------------------------
# pooled regression with stock fixed effects and date-clustered SEs
# --------------------------------------------------------------------------
def _solve(A, b):
    n = len(A)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[p][c]) < 1e-12:
            return None
        M[c], M[p] = M[p], M[c]
        for r in range(n):
            if r != c:
                f = M[r][c] / M[c][c]
                for k in range(c, n + 1):
                    M[r][k] -= f * M[c][k]
    return [M[i][n] / M[i][i] for i in range(n)]


def _inv(A):
    n = len(A)
    cols = [_solve(A, [1.0 if i == j else 0.0 for i in range(n)]) for j in range(n)]
    if any(c is None for c in cols):
        return None
    return [[cols[j][i] for j in range(n)] for i in range(n)]


def _z(vals):
    n = len(vals)
    if n < 2:
        return None
    m = sum(vals) / n
    sd = math.sqrt(sum((v - m) ** 2 for v in vals) / (n - 1))
    return None if sd <= 0 else [(v - m) / sd for v in vals]


def pooled(rows, zcols, min_rows=MIN_ROWS):
    """rows: (tk, cluster, y, [x...]). Column 0 is the coefficient of interest.
    Columns listed in zcols are z-scored within stock; then y and every x are
    demeaned within stock (fixed effects)."""
    by = {}
    for r in rows:
        by.setdefault(r[0], []).append(r)
    data = []
    for tk, rs in by.items():
        if len(rs) < min_rows:
            continue
        K = len(rs[0][3])
        cols = [[r[3][k] for r in rs] for k in range(K)]
        ok = True
        for k in zcols:
            z = _z(cols[k])
            if z is None:
                ok = False
                break
            cols[k] = z
        if not ok:
            continue
        ys = [r[2] for r in rs]
        my = sum(ys) / len(ys)
        means = [sum(c) / len(c) for c in cols]
        for j, r in enumerate(rs):
            data.append((r[1], ys[j] - my, [cols[k][j] - means[k] for k in range(K)], tk))
    if len(data) < 50:
        return None
    K = len(data[0][2])
    A = [[0.0] * K for _ in range(K)]
    b = [0.0] * K
    for _, y, x, _ in data:
        for i in range(K):
            b[i] += x[i] * y
            for j in range(K):
                A[i][j] += x[i] * x[j]
    beta = _solve(A, b)
    Ai = _inv(A)
    if beta is None or Ai is None:
        return None
    g = {}
    for c, y, x, _ in data:
        u = y - sum(beta[i] * x[i] for i in range(K))
        s = g.setdefault(c, [0.0] * K)
        for i in range(K):
            s[i] += x[i] * u
    meat = [[0.0] * K for _ in range(K)]
    for s in g.values():
        for i in range(K):
            for j in range(K):
                meat[i][j] += s[i] * s[j]
    N, G = len(data), len(g)
    n_fe = len({d[3] for d in data})
    corr = (G / max(1, G - 1)) * ((N - 1) / max(1, N - K - n_fe))
    v00 = sum(Ai[0][i] * meat[i][j] * Ai[j][0] for i in range(K) for j in range(K)) * corr
    se = math.sqrt(max(v00, 0.0))
    t = beta[0] / se if se > 0 else 0.0
    return {"b": round(beta[0], 4), "se": round(se, 4), "t": round(t, 2),
            "p": round(math.erfc(abs(t) / math.sqrt(2)), 4), "n": N, "nt": n_fe, "nd": G}


# --------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------
TESTS = {
    # key: (label, kind)
    "daily":    ("Sentiment over a trading day → next day's return (close to close)", "pred"),
    "open":     ("Overnight + pre-market sentiment → that day's regular-session return", "pred"),
    "overnight": ("Regular-session sentiment → the next overnight gap (close to next open)", "pred"),
    "react":    ("A day's return → sentiment over the following day (reaction)", "react"),
    "same":     ("Sentiment and return over the same day (coincident)", "same"),
}


def _att(cell):
    return math.log1p(cell[0])


def test_rows(key, stocks, scope, regime, keep_date):
    mn = MIN_N

    def net(cell):
        return cell[2] / cell[1] if cell[1] >= mn else None
    rows = []
    for tk, st in stocks.items():
        W = st["win"][scope]
        E = st["earn"]
        T = len(E)
        for i in range(1, T - 1):
            if key == "daily":
                x, y, d = net(W["C"][i]), st["aC"][i + 1], i + 1
                ctrl = [st["aC"][i], _att(W["C"][i])]
                e = E[i] or E[i + 1]
            elif key == "open":
                x, y, d = net(W["O"][i]), st["aR"][i], i
                ctrl = [st["aO"][i], _att(W["O"][i])]
                e = E[i]
            elif key == "overnight":
                x, y, d = net(W["R"][i]), st["aO"][i + 1], i + 1
                ctrl = [st["aR"][i], _att(W["R"][i])]
                e = E[i] or E[i + 1]
            elif key == "react":
                x, y, d = st["aC"][i], net(W["C"][i + 1]), i + 1
                ctrl = [net(W["C"][i]), _att(W["C"][i + 1])]
                e = E[i] or E[i + 1]
            else:  # same
                x, y, d = net(W["C"][i]), st["aC"][i], i
                ctrl = [_att(W["C"][i])]
                e = E[i]
            if x is None or y is None or any(c is None for c in ctrl):
                continue
            if (regime == "ex_earn" and e) or (regime == "earn" and not e):
                continue
            if not keep_date(d):
                continue
            if key == "react":
                rows.append((tk, d, y, [x] + ctrl))
            else:
                rows.append((tk, d, y * 1e4, [x] + ctrl))
    return rows


def run_tests(stocks, T):
    # split where the data actually is: dense mention coverage starts well after
    # the first price date, so cut at 70% of the dates that enter the main test
    dates = sorted({r[1] for r in test_rows("daily", stocks, "news", "all", lambda d: True)})
    cut = dates[int(len(dates) * SPLIT)] if dates else T
    keep = {"full": lambda d: True, "early": lambda d: d < cut, "late": lambda d: d >= cut}
    out = {}
    for key, (label, kind) in TESTS.items():
        # sentiment columns are z-scored within stock; for "react" the outcome is
        # sentiment, so it is z-scored too (coefficient = sd of sentiment per 1% return)
        z = [0, 2] if key in ("daily", "open", "overnight") else ([0, 1] if key == "react" else [0])
        res = {}
        for sc in SCOPES:
            res[sc] = {}
            for rg in REGIMES:
                res[sc][rg] = {}
                for pe in PERIODS:
                    rows = test_rows(key, stocks, sc, rg, keep[pe])
                    if key == "react":
                        rows = _z_outcome(rows)
                    res[sc][rg][pe] = pooled(rows, z, MIN_ROWS_EARN if rg == "earn" else MIN_ROWS)
        out[key] = {"label": label, "kind": kind, "res": res}
    return out, cut


def _z_outcome(rows):
    """z-score the outcome (sentiment) within stock for the reaction test; with the
    return regressor also z-scored, the coefficient is in sd-per-sd units."""
    by = {}
    for r in rows:
        by.setdefault(r[0], []).append(r)
    out = []
    for tk, rs in by.items():
        z = _z([r[2] for r in rs])
        if z is None:
            continue
        for r, v in zip(rs, z):
            out.append((r[0], r[1], v, r[3]))
    return out


def long_short(stocks, T, key, regime):
    """Each date: buy the top fifth of stocks by prior sentiment, short the bottom
    fifth; mean spread in bp of abnormal return, with a Newey-West(3) t-stat."""
    # sentiment ranks within date use raw net sentiment (stocks differ in baseline,
    # so demean by each stock's own average first)
    means = {}
    for tk, st in stocks.items():
        vals = [net(c) for c in st["win"]["news"]["C" if key == "daily" else "O"]]
        vals = [v for v in vals if v is not None]
        means[tk] = sum(vals) / len(vals) if vals else 0.0
    by_date = {}
    for tk, d, y, x in test_rows(key, stocks, "news", regime, lambda d: True):
        by_date.setdefault(d, []).append((x[0] - means[tk], y))
    spreads = []
    for d in sorted(by_date):
        xs = sorted(by_date[d])
        q = len(xs) // 5
        if q < 10:
            continue
        lo = sum(y for _, y in xs[:q]) / q
        hi = sum(y for _, y in xs[-q:]) / q
        spreads.append(hi - lo)
    n = len(spreads)
    if n < 20:
        return None
    m = sum(spreads) / n
    e = [s - m for s in spreads]
    v = sum(x * x for x in e) / n
    for L in range(1, 4):
        w = 1 - L / 4
        v += 2 * w * sum(e[i] * e[i - L] for i in range(L, n)) / n
    se = math.sqrt(max(v, 0) / n)
    t = m / se if se > 0 else 0.0
    return {"mean_bp": round(m, 2), "t": round(t, 2), "p": round(math.erfc(abs(t) / math.sqrt(2)), 4),
            "nd": n, "hit": round(sum(s > 0 for s in spreads) / n, 3)}


# --------------------------------------------------------------------------
# per-stock lead/lag
# --------------------------------------------------------------------------
def _corr(pairs):
    n = len(pairs)
    if n < 15:
        return None
    mx = sum(p[0] for p in pairs) / n
    my = sum(p[1] for p in pairs) / n
    sxx = sum((p[0] - mx) ** 2 for p in pairs)
    syy = sum((p[1] - my) ** 2 for p in pairs)
    if sxx <= 0 or syy <= 0:
        return None
    return sum((p[0] - mx) * (p[1] - my) for p in pairs) / math.sqrt(sxx * syy)


def _pval(r, n):
    if r is None or n < 4 or abs(r) >= 1:
        return None
    t = r * math.sqrt((n - 2) / (1 - r * r))
    return math.erfc(abs(t) / math.sqrt(2))


def per_stock(st, scope, regime):
    W, E, T = st["win"][scope], st["earn"], len(st["earn"])
    mn = MIN_N

    def net(cell):
        return cell[2] / cell[1] if cell[1] >= mn else None
    daily = []
    for k in DAILY_LAGS:
        pairs = []
        for i in range(1, T):
            j = i + k
            if j < 1 or j >= T:
                continue
            x, y = net(W["C"][i]), st["aC"][j]
            if x is None or y is None or (regime == "ex_earn" and (E[i] or E[j])):
                continue
            pairs.append((x, y))
        r = _corr(pairs)
        daily.append([k, None if r is None else round(r, 3), len(pairs)])
    sess = []
    if st["has_sess"]:
        seq = []   # (sentiment, abnormal return, day) for O_0, R_0, O_1, R_1, ...
        for i in range(1, T):
            seq.append((net(W["O"][i]), st["aO"][i], i))
            seq.append((net(W["R"][i]), st["aR"][i], i))
        for k in SESS_LAGS:
            pairs = []
            for w in range(len(seq)):
                v = w + k
                if v < 0 or v >= len(seq):
                    continue
                x, y = seq[w][0], seq[v][1]
                if x is None or y is None or (regime == "ex_earn" and (E[seq[w][2]] or E[seq[v][2]])):
                    continue
                pairs.append((x, y))
            r = _corr(pairs)
            sess.append([k, None if r is None else round(r, 3), len(pairs)])
    return daily, sess


def bh_count(ps, q=0.10):
    ps = sorted(p for p in ps if p is not None)
    m, k = len(ps), 0
    for i, p in enumerate(ps, 1):
        if p <= q * i / m:
            k = i
    return k


# --------------------------------------------------------------------------
def main():
    tickers = sorted(os.path.basename(p)[:-6] for p in glob.glob("data/mentions/*.jsonl"))
    cal = calendar(tickers)
    if len(cal) < 30:
        print("analyze_v2: not enough trading days")
        return
    T = len(cal)
    closes_u = [c for _, _, c in cal]
    import random
    val = {"n": 0, "mix": {}, "flags": {"about": 0, "earn": 0, "move": 0, "new": 0},
           "stance": {"bullish": 0, "bearish": 0, "neutral": 0},
           "agree": {"n": 0, "same": 0, "opposite": 0, "llm_neutral": 0},
           "examples": [], "seen": 0, "rng": random.Random(11)}
    stocks = {}
    for tk in tickers:
        st = load_stock(tk, cal, closes_u, val)
        if st is not None:          # every stock with prices feeds the market average
            stocks[tk] = st
    abnormal(stocks, T)
    tests, cut = run_tests(stocks, T)
    ls = {}
    for key in ("daily", "open"):
        ls[key] = {rg: long_short(stocks, T, key, rg) for rg in ("all", "ex_earn")}
    # per stock
    per = {}
    k1 = {sc: {rg: [] for rg in ("all", "ex_earn")} for sc in SCOPES}
    for tk, st in stocks.items():
        if not st["n_m"]:
            continue
        rec = {"n_m": st["n_m"], "n_st": st["n_st"], "n_ev": st["n_ev"], "daily": {}, "sess": {}, "k1": {}}
        for sc in SCOPES:
            rec["daily"][sc], rec["sess"][sc], rec["k1"][sc] = {}, {}, {}
            for rg in ("all", "ex_earn"):
                d, s = per_stock(st, sc, rg)
                rec["daily"][sc][rg], rec["sess"][sc][rg] = d, s
                row = next((x for x in d if x[0] == 1), None)
                p = _pval(row[1], row[2]) if row and row[1] is not None else None
                rec["k1"][sc][rg] = {"r": row[1] if row else None, "n": row[2] if row else 0,
                                     "p": None if p is None else round(p, 4)}
                if p is not None:
                    k1[sc][rg].append(p)
        per[tk] = rec
    fdr = {sc: {rg: {"n": len(ps), "sig05": sum(p < 0.05 for p in ps),
                     "expected05": round(0.05 * len(ps), 1), "fdr10": bh_count(ps)}
                for rg, ps in v.items()} for sc, v in k1.items()}
    nl = sum(val["stance"].values())
    validation = {
        "n_news": val["n"], "n_llm": nl, "mix": val["mix"],
        "llm_share": round(nl / val["n"], 3) if val["n"] else 0,
        "stance": val["stance"], "flags": val["flags"], "agree": val["agree"],
        "examples": sorted(val["examples"], key=lambda e: (e["stance"] or "", e["tk"])),
    }
    has_sess = [i for i in range(T) if any(st["rO"][i] is not None for st in stocks.values())]
    out = {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "params": {"min_n": MIN_N, "min_rows": MIN_ROWS, "model": "claude-haiku-5-5", "earn_window": [-EARN_PRE, EARN_POST],
                   "split": SPLIT},
        "coverage": {"n_stocks": sum(1 for s in stocks.values() if s["n_m"]),
                     "n_market": len(stocks),
                     "n_mentions": sum(s["n_m"] for s in stocks.values()),
                     "n_stanced": sum(s["n_st"] for s in stocks.values()),
                     "daily_from": cal[1][0], "daily_to": cal[-1][0], "n_days": T - 1,
                     "split_date": cal[cut][0] if cut < T else None,
                     "sess_from": cal[has_sess[0]][0] if has_sess else None,
                     "n_sess_days": len(has_sess)},
        "validation": validation, "tests": tests, "longshort": ls, "fdr": fdr, "stocks": per,
    }
    tmp = OUT + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(out, fh, separators=(",", ":"))
    os.replace(tmp, OUT)
    r = tests["daily"]["res"]["news"]["ex_earn"]["full"] or {}
    print(f"analyze_v2: {out['coverage']['n_stocks']} stocks with news, {T - 1} days; daily test (ex-earnings) "
          f"b={r.get('b')}bp t={r.get('t')} n={r.get('n')}")


if __name__ == "__main__":
    main()

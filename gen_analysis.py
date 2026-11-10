"""
Generate the Sentiment vs Price dashboard (v2 chart).

Outputs
  analysis.html          the page. Embeds a small index payload: per-stock summary
                         and lead/lag results, earnings events, pipeline freshness,
                         and one shared half-hour time grid.
  series/<TICKER>.json   per-stock chart data (hourly prices + half-hour mention
                         sums), fetched by the page when that stock is selected.
                         Publish this folder next to index.html (run_scrape.sh).

Chart model
  - Time axis: one half-hour grid in US/Eastern time shared by every stock. Each
    slot carries its market session from market_session.session_of(), so the page
    uses exactly the same session labels as the mention store. Pre-market, regular
    and after-hours slots get equal width; every closed run (overnight, weekend,
    holiday) is collapsed client-side to one fixed-width marked break.
  - Price: hourly bars from data/prices/<T>_intraday.json, placed at their slot.
  - Sentiment: per slot, the mean stance and count of mentions that carry one
    (sentiment_v2.stance: StockTwits Bullish/Bearish tag, else a cleaned finance
    lexicon; news that only reports a price move and text with no hits have no
    stance), plus the count of all mentions. Kept separately for social and news
    so the source control can recombine them. The page draws a
    time-decayed level: sum(w*score)/sum(w) with w = 0.5^(age/half-life), so the
    half-life (6h or 14d) is real clock time, the level holds through quiet spells,
    and it is hidden once the decayed evidence falls below MIN_WEIGHT mentions.
    Mentions before the grid start seed that level (the "init" block).
  - Study-wide results, per-stock lead/lag and session lead/lag come from
    data/analysis_v2.json (analyze_v2.py). data/analysis.json (analyze.py) still
    supplies the stock list, sector/industry/cap categories and mention counts.

The previous single-file generator is kept as gen_analysis_v1.py.
"""
import datetime as dt
import glob
import json
import math
import os
import shutil

import sentiment_v2 as sv

SLOT = 1800                      # half-hour slots, seconds
FAST_HL_SLOTS = 12               # 6 hours
SLOW_HL_SLOTS = 14 * 48          # 14 days
SERIES_DIR = "series"

# session -> single-char code (same codes as v1)
SESS_CODE = {"pre_market": "p", "regular": "r", "after_hours": "a",
             "overnight": "o", "weekend": "w", "holiday": "h"}
# time-of-day index 0..47 (half hours) -> one character
TOD_CH = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKL"


def _json_safe(obj):
    """Replace NaN/Inf with None so the output is strict JSON (browsers reject NaN)."""
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def _epoch(iso):
    try:
        return int(dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())
    except Exception:
        return None


def _family(src):
    """news:* -> news; stocktwits/reddit/unknown -> social (as analyze.py)."""
    return "news" if (src or "").lower().startswith("news") else "social"


# --------------------------------------------------------------------------
# shared time grid
# --------------------------------------------------------------------------
def intraday_span(tickers):
    """(first, last) unix second across all intraday price stores."""
    lo = hi = None
    for tk in tickers:
        p = f"data/prices/{tk}_intraday.json"
        if not os.path.exists(p):
            continue
        try:
            bars = json.load(open(p))
        except Exception:
            continue
        for b in (bars[0], bars[-1]) if bars else ():
            u = _epoch(b.get("ts", ""))
            if u is None:
                continue
            lo = u if lo is None else min(lo, u)
            hi = u if hi is None else max(hi, u)
    return lo, hi


def build_grid(t_lo, t_hi):
    """Half-hour grid from ET midnight of t_lo's date through t_hi.

    Returns {start, n, c, tod, days}: c = one session code per slot, tod = one
    TOD_CH character per slot (ET wall-clock half hour), days = [[slot, label]]
    at each ET midnight."""
    import market_session as ms
    from zoneinfo import ZoneInfo
    et_tz = ZoneInfo("America/New_York")
    d0 = dt.datetime.fromtimestamp(t_lo, et_tz).date()
    start = int(dt.datetime(d0.year, d0.month, d0.day, tzinfo=et_tz).timestamp())
    n = max(1, (t_hi - start) // SLOT + 1)
    codes, tods, days, last = [], [], [], None
    for k in range(n):
        utc = dt.datetime.fromtimestamp(start + k * SLOT, dt.timezone.utc)
        codes.append(SESS_CODE.get(ms.session_of(utc), "?"))
        et = utc.astimezone(et_tz)
        tods.append(TOD_CH[(et.hour * 60 + et.minute) // 30])
        if et.date() != last:
            days.append([k, et.strftime("%a %b ") + str(et.day)])
            last = et.date()
    return {"start": start, "n": n, "c": "".join(codes), "tod": "".join(tods),
            "days": days}


# --------------------------------------------------------------------------
# per-stock series
# --------------------------------------------------------------------------
def _decayed(slots, hl, until):
    """Decayed (S, W) after the given {slot: [sum, count]} items, aged to `until`."""
    S = W = 0.0
    prev = None
    for k in sorted(slots):
        if prev is not None:
            f = 0.5 ** ((k - prev) / hl)
            S *= f
            W *= f
        s, c = slots[k]
        S += s
        W += c
        prev = k
    if prev is not None:
        f = 0.5 ** ((until - prev) / hl)
        S *= f
        W *= f
    return [round(S, 6), round(W, 6)]


def _delta(ks):
    out, prev = [], 0
    for k in ks:
        out.append(k - prev)
        prev = k
    return out


def build_series(tk, grid):
    start, n = grid["start"], grid["n"]
    # ---- prices: one hourly bar per slot (last write wins) ----
    price = {}
    p = f"data/prices/{tk}_intraday.json"
    if os.path.exists(p):
        try:
            for b in json.load(open(p)):
                u = _epoch(b.get("ts", ""))
                v = b.get("price")
                if u is None or not isinstance(v, (int, float)) or math.isnan(v):
                    continue
                k = (u - start) // SLOT
                if 0 <= k < n:
                    price[k] = round(float(v), 2)
        except Exception:
            pass
    # ---- mentions: per family, per slot [sum, count]; pre-grid kept for init ----
    acc = {"social": {}, "news": {}}
    pre = {"social": {}, "news": {}}
    p = f"data/mentions/{tk}.jsonl"
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                m = json.loads(line)
            except Exception:
                continue
            u = _epoch(m.get("date") or "")
            if u is None:
                continue
            k = (u - start) // SLOT
            if k >= n:
                continue
            s, _ = sv.stance(m)
            tgt = (acc if k >= 0 else pre)[_family(m.get("source"))]
            cell = tgt.setdefault(k, [0.0, 0, 0])   # stance sum, stanced count, all mentions
            cell[2] += 1
            if s is not None:
                cell[0] += s
                cell[1] += 1
    used = list(price) + [k for f in acc.values() for k in f]
    if not used:
        return None
    out = {"t": tk, "i0": min(used), "i1": n - 1}
    pk = sorted(price)
    out["p"] = _delta(pk)
    out["pv"] = [price[k] for k in pk]
    out["m"] = {}
    for fam, slots in acc.items():
        ks = sorted(slots)
        out["m"][fam] = [_delta(ks),
                         [round(1000 * slots[k][0] / slots[k][1]) if slots[k][1] else 0 for k in ks],
                         [slots[k][1] for k in ks],
                         [slots[k][2] for k in ks]]
    both_pre = {}
    for fam in pre.values():
        for k, (s, c, _) in fam.items():
            if c:
                cell = both_pre.setdefault(k, [0.0, 0])
                cell[0] += s
                cell[1] += c
    out["init"] = {}
    for scope, slots in (("both", both_pre), ("social", pre["social"]), ("news", pre["news"])):
        sl = {k: v[:2] for k, v in slots.items() if v[1]}
        out["init"][scope] = {"f": _decayed(sl, FAST_HL_SLOTS, -1),
                              "s": _decayed(sl, SLOW_HL_SLOTS, -1)}
    return out


# --------------------------------------------------------------------------
# index payload pieces (unchanged from v1 where noted)
# --------------------------------------------------------------------------
def load_earnings_compact(tickers):
    """{ticker: [{t, sess, we, est, rep, surp}]} for markers, hover and the regime control."""
    out = {}
    for tk in tickers:
        p = f"data/earnings/{tk}.json"
        if not os.path.exists(p):
            continue
        try:
            data = json.load(open(p))
        except Exception:
            continue
        evs = []
        for e in data.get("events", []):
            u = _epoch(e.get("ts") or "")
            if u is None:
                continue
            evs.append({"t": u, "sess": e.get("session"),
                        "we": _epoch(e["window_end"]) if e.get("window_end") else None,
                        "est": e.get("eps_estimate"), "rep": e.get("reported_eps"),
                        "surp": e.get("surprise_pct")})
        if evs:
            out[tk] = evs
    return out


def load_mcaps():
    """Raw market cap per ticker from lynch.csv (dropdown order, biggest first)."""
    import csv
    out = {}
    p = "data/lynch.csv"
    if os.path.exists(p):
        for r in csv.DictReader(open(p)):
            tk = r["ticker"].replace(".TO", "").upper()
            try:
                out[tk] = float(r.get("market_cap") or 0)
            except (TypeError, ValueError):
                pass
    return out


def build_freshness():
    """Generated-at stamp + newest mention date, so a stale page announces itself
    (see v1 for the incident that motivated this)."""
    now = int(dt.datetime.now(dt.timezone.utc).timestamp())
    out = {"gen": now, "mentions_max": None, "price_max": None, "n_tickers": 0}
    newest = None
    for p in glob.glob("data/mentions/*.jsonl"):
        try:
            with open(p, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - 32768))
                tail = fh.read().decode("utf-8", "ignore").splitlines()
            if size > 32768 and tail:
                tail = tail[1:]
            for line in tail:
                try:
                    d = (json.loads(line).get("date") or "")[:10]
                except Exception:
                    continue
                if len(d) == 10 and (newest is None or d > newest):
                    newest = d
        except Exception:
            continue
    out["mentions_max"] = newest
    return out


SUMMARY_KEYS = ("n_mentions", "sector", "industry", "mcap_bucket",
                "source_favored", "social_mentions", "news_mentions")
SCOPE_MENTIONS = "n_mentions"


def main():
    analysis = json.load(open("data/analysis.json"))
    mcaps = load_mcaps()
    order = sorted(analysis, key=lambda t: mcaps.get(t, 0), reverse=True)
    t_lo, t_hi = intraday_span(order)
    now = int(dt.datetime.now(dt.timezone.utc).timestamp())
    if t_lo is None:
        t_lo = now - 30 * 86400
    grid = build_grid(t_lo, max(now, t_hi or now))

    v2 = None
    if os.path.exists("data/analysis_v2.json"):
        try:
            v2 = json.load(open("data/analysis_v2.json"))
        except Exception:
            v2 = None
    per = (v2 or {}).pop("stocks", {}) if v2 else {}
    earnings = load_earnings_compact(order)

    # Per-stock files carry everything only the selected stock needs (chart data,
    # lead/lag curves, session analysis, earnings); the page embeds just the summary
    # used by the dropdown, filters and stat tiles.
    tmp = SERIES_DIR + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    summary, kept = {}, []
    price_max = None
    for tk in order:
        s = build_series(tk, grid)
        if s is None:
            continue
        a = analysis[tk]
        scopes = a.get("scopes") or {}
        pv = per.get(tk) or {}
        s["ll"] = {"daily": pv.get("daily"), "sess": pv.get("sess")}
        s["earn"] = earnings.get(tk, [])
        with open(f"{tmp}/{tk}.json", "w") as fh:
            json.dump(_json_safe(s), fh, separators=(",", ":"), allow_nan=False)
        row = {k: a.get(k) for k in SUMMARY_KEYS}
        row["market_cap"] = mcaps.get(tk, 0)
        row["he"] = bool(s["earn"])
        row["scopes"] = {sc: {"n_mentions": v.get(SCOPE_MENTIONS)} for sc, v in scopes.items()}
        row["v2"] = {"n_m": pv.get("n_m"), "n_st": pv.get("n_st"), "n_ev": pv.get("n_ev"),
                     "k1": pv.get("k1")} if pv else None
        summary[tk] = row
        kept.append(tk)
        if s["p"]:
            last = grid["start"] + sum(s["p"]) * SLOT
            price_max = last if price_max is None else max(price_max, last)
    shutil.rmtree(SERIES_DIR, ignore_errors=True)
    os.rename(tmp, SERIES_DIR)

    fresh = build_freshness()
    fresh["n_tickers"] = len(kept)
    if price_max is not None:
        fresh["price_max"] = dt.datetime.fromtimestamp(price_max, dt.timezone.utc).date().isoformat()
    payload = {"analysis": summary, "order": kept, "grid": grid,
               "freshness": fresh, "series": SERIES_DIR + "/", "study": v2}
    payload = _json_safe(payload)
    data_js = json.dumps(payload, separators=(",", ":"), allow_nan=False)
    html = TEMPLATE.replace("/*DATA*/", data_js.replace("</", "<\\/"))
    out = ('<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
           '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
           '<title>Sentiment vs Price · Gal Av-Gay</title>\n</head>\n<body>\n'
           + html + '\n</body>\n</html>\n')
    open("analysis.html", "w", encoding="utf-8").write(out)
    print(f"wrote analysis.html {len(out)} bytes; {len(kept)} tickers; "
          f"{SERIES_DIR}/ {len(kept)} files; grid {grid['n']} slots")


TEMPLATE = r"""
<style>
  /* Dark theme. Series steps are the dark-surface steps of the reference palette
     (validated: lightness band, chroma, CVD and contrast on #1a1a19). */
  :root{
    color-scheme:dark;
    --bg:#121211; --surface:#1a1a19; --panel:#1f1f1d; --raise:#262624;
    --ink:#f0efec; --body:#c3c2b7; --faint:#898781;
    --hair:#383835; --hair2:#2a2a28; --rib-r:#33332f; --rib-x:#2a2a27; --rib-c:#40403b;
    --link:#7aa7ef; --link-hover:#a9c6f5; --accent:#7aa7ef;
    --price:#3987e5; --senti:#d95926; --muted-mark:#5d5c57;
    --good:#0ca30c; --warn:#fab219; --crit:#d03b3b; --earn:#c9a227;
    --serif:"Times New Roman",Times,Georgia,serif;
    --ui:system-ui,-apple-system,"Segoe UI",sans-serif;
  }
  *{box-sizing:border-box;}
  html,body{overflow-x:hidden;background:var(--bg);}
  body{margin:0;color:var(--ink);font-family:var(--serif);font-size:17px;line-height:1.55;padding:3px;}
  .wrap{max-width:1080px;margin:0 auto;padding:0 32px 80px;width:100%;}
  a{color:var(--link);text-decoration:none;} a:hover{color:var(--link-hover);text-decoration:underline;}
  .topbar{display:flex;align-items:center;gap:18px;flex-wrap:wrap;padding:16px 0 0;font-size:14px;}
  .topbar .home{font-weight:bold;}
  h1{color:var(--ink);font-size:30px;font-weight:bold;border-bottom:3px solid var(--accent);padding:0 0 6px;margin:18px 0 14px;text-align:center;}
  h2{font-size:22px;color:var(--ink);margin:34px 0 10px;text-align:center;}
  h3{font-size:17px;color:var(--ink);margin:0 0 6px;font-family:var(--ui);}
  .intro{font-size:17px;color:var(--body);margin:0 auto 14px;line-height:1.6;text-align:justify;}
  .intro b{color:var(--ink);}
  .freshbar{font-family:var(--ui);font-size:14px;margin:0 0 14px;padding:10px 14px;border-radius:8px;border:1px solid var(--hair);
            background:var(--panel);display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;}
  .freshbar .dot{width:9px;height:9px;border-radius:50%;flex:none;align-self:center;}
  .freshbar .msg{font-weight:bold;} .freshbar .detail{color:var(--faint);font-size:13px;}
  .freshbar.ok .dot{background:var(--good);} .freshbar.ok .msg{color:var(--ink);}
  .freshbar.warn{border-color:#5c4a1c;} .freshbar.warn .dot{background:var(--warn);} .freshbar.warn .msg{color:var(--warn);}
  .freshbar.bad{border-color:#5c2626;} .freshbar.bad .dot{background:var(--crit);} .freshbar.bad .msg{color:#f08a8a;}
  .howto{font-size:15px;color:var(--body);margin:0 0 16px;line-height:1.6;text-align:justify;background:var(--panel);
         border:1px solid var(--hair);border-radius:8px;padding:14px 18px;}
  .howto b{color:var(--ink);}
  .controls{display:flex;gap:16px;flex-wrap:wrap;align-items:end;justify-content:center;margin:0 0 8px;padding:14px 18px;
            background:var(--panel);border:1px solid var(--hair);border-radius:8px;}
  .fld{display:flex;flex-direction:column;gap:4px;}
  .fld label{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--faint);font-weight:bold;font-family:var(--ui);}
  select{font-family:var(--ui);font-size:15px;color:var(--ink);background:var(--raise);border:1px solid var(--hair);border-radius:5px;padding:8px 12px;}
  select:focus{outline:2px solid var(--link);border-color:var(--link);}
  .mentionsbtn{font-family:var(--ui);font-size:14px;font-weight:bold;color:var(--link);background:var(--raise);border:1px solid var(--hair);
               border-radius:5px;padding:8px 14px;white-space:nowrap;}
  .mentionsbtn:hover{border-color:var(--link);text-decoration:none;}
  .statrow{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:16px 0 12px;font-family:var(--ui);}
  .stat{border:1px solid var(--hair);border-radius:8px;padding:11px 14px;background:var(--surface);}
  .stat .k{font-size:11.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--faint);font-weight:bold;}
  .stat .v{font-size:21px;font-weight:600;color:var(--ink);margin-top:3px;}
  .stat small{font-size:12.5px;color:var(--faint);font-weight:normal;display:block;margin-top:2px;}
  .catrow{font-size:14px;color:var(--faint);margin:-2px 0 12px;text-align:center;font-family:var(--ui);} .catrow b{color:var(--body);}
  .caveat{background:#2a2414;border:1px solid #5c4a1c;border-radius:6px;padding:11px 14px;font-size:14px;color:#e8cf8a;margin:12px 0;text-align:center;font-family:var(--ui);}
  footer{margin-top:40px;border-top:1px solid var(--hair);padding-top:18px;color:var(--faint);font-size:13px;line-height:1.6;text-align:center;}

  .card{border:1px solid var(--hair);border-radius:12px;padding:16px 18px;margin:0 0 16px;background:var(--surface);font-family:var(--ui);font-size:14px;color:var(--body);}
  .card p{margin:0 0 8px;line-height:1.55;}
  .vcontrols{display:flex;flex-wrap:wrap;align-items:center;margin-bottom:12px;}
  .vcontrols .grp{display:inline-flex;align-items:center;margin:0 22px 8px 0;}
  .vcontrols .grp > span{margin-right:8px;font-size:13px;color:var(--body);}
  .seg{display:inline-flex;border-radius:7px;overflow:hidden;border:1px solid var(--hair);}
  .seg button{font:inherit;font-size:13px;padding:5px 12px;border:0;cursor:pointer;background:transparent;color:var(--body);}
  .seg button.on{font-weight:600;background:#3a3a36;color:var(--ink);}
  .seg button:focus-visible,.zreset:focus-visible{outline:2px solid var(--link);outline-offset:1px;}
  .zreset{font:inherit;font-size:13px;padding:5px 12px;border:1px solid var(--hair);border-radius:7px;background:var(--raise);color:var(--body);cursor:pointer;}
  .vstats{display:flex;flex-wrap:wrap;margin:0 -5px 10px;}
  .tile{flex:1 1 190px;margin:0 5px 6px;padding:8px 10px;}
  .tile .k{font-size:12px;color:var(--body);} .tile .v{font-size:20px;font-weight:600;line-height:1.2;margin-top:2px;color:var(--ink);}
  .tile .s{font-size:12px;color:var(--faint);}
  .vlegend{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:12px;margin:4px 0 6px;color:var(--body);}
  .vlegend i{display:inline-block;vertical-align:middle;margin-right:6px;}
  .sw-line{width:16px;height:3px;border-radius:2px;}
  .sw-dot{width:9px;height:9px;border-radius:50%;background:var(--senti);opacity:0.6;}
  .sw-box{width:12px;height:12px;border-radius:3px;border:1px solid var(--hair);}
  .sw-earn{width:3px;height:14px;background:var(--earn);}
  .sw-ring{width:9px;height:9px;border-radius:50%;border:2px solid var(--body);}
  .sw-band{width:14px;height:10px;border-radius:2px;background:#2f2f2c;border:1px dashed #6b6a65;}
  .vwrap{position:relative;}
  #chart{overflow:visible;}
  svg{display:block;width:100%;height:auto;}
  .grid{stroke:var(--hair2);stroke-width:1;} .zero,.brk{stroke:var(--hair);stroke-width:1;} .daysep{stroke:var(--hair2);stroke-width:1;}
  .bg-ext{fill:#1f1f1d;} .bg-closed{fill:#262624;}
  .line-price{fill:none;stroke:var(--price);stroke-width:2;stroke-linejoin:round;stroke-linecap:round;}
  .line-senti{fill:none;stroke:var(--senti);stroke-width:2.2;stroke-linejoin:round;stroke-linecap:round;}
  .dot-mention{fill:var(--senti);fill-opacity:0.6;stroke:var(--surface);stroke-width:1.5;}
  .dot-price{fill:var(--price);stroke:var(--surface);stroke-width:2;} .dot-senti{fill:var(--senti);stroke:var(--surface);stroke-width:2;}
  .xhair{stroke:#6b6a65;stroke-width:1;}
  .rb-r{fill:var(--rib-r);} .rb-p,.rb-a{fill:var(--rib-x);} .rb-o,.rb-w,.rb-h,.rb-q{fill:var(--rib-c);}
  .rb-text{fill:var(--ink);font-size:10.5px;}
  .tick{fill:var(--body);font-size:11px;} .axis-title{fill:var(--body);font-size:11px;}
  .earnline{stroke:var(--earn);stroke-width:1.3;stroke-dasharray:4 3;opacity:0.85;}
  .earntag{fill:var(--earn);} .earntaglbl{fill:#121211;font-size:10px;font-weight:bold;}
  .earntiptxt{fill:var(--ink);font-family:var(--ui);}
  .zoomrect{fill:var(--price);fill-opacity:0.14;stroke:var(--price);stroke-dasharray:3 2;}
  .tip{position:absolute;top:8px;pointer-events:none;font-size:12px;line-height:1.45;padding:7px 10px;border-radius:8px;
       box-shadow:0 4px 14px rgba(0,0,0,0.5);min-width:200px;max-width:290px;background:var(--raise);color:var(--ink);border:1px solid #4a4945;}
  .tt-head{font-weight:600;margin-bottom:2px;} .tip .muted{color:var(--faint);}
  .vnote{font-size:12.5px;margin:8px 0 0;color:var(--faint);line-height:1.5;}
  details.card summary{cursor:pointer;font-size:13px;font-weight:600;color:var(--body);}
  .tblwrap{max-height:340px;overflow:auto;margin-top:10px;}
  table{border-collapse:collapse;width:100%;font-size:12.5px;color:var(--ink);}
  th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--hair2);}
  th{position:sticky;top:0;background:var(--surface);color:var(--body);}
  td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;}
  /* study section */
  .verdict{border-left:3px solid var(--accent);background:var(--panel);border-radius:6px;padding:12px 16px;margin:0 0 14px;font-size:15px;line-height:1.55;color:var(--ink);}
  .verdict .sub{display:block;color:var(--body);font-size:13.5px;margin-top:6px;}
  .lbl{fill:var(--ink);font-size:12.5px;} .lbl2{fill:var(--faint);font-size:11px;}
  .ci{stroke:var(--price);stroke-width:2;stroke-linecap:round;} .pt-full{fill:var(--price);stroke:var(--surface);stroke-width:2;}
  .pt-half{fill:var(--surface);stroke:var(--body);stroke-width:1.8;}
  .bar-sig{fill:var(--price);} .bar-ns{fill:var(--muted-mark);}
  .band{fill:#2f2f2c;stroke:#6b6a65;stroke-dasharray:3 3;stroke-width:1;}
  .methods h4{font-size:13px;color:var(--ink);margin:14px 0 4px;}
  .methods p,.methods li{font-size:13px;color:var(--body);line-height:1.55;}
  .methods ul{margin:4px 0 8px 18px;padding:0;}
  @media(max-width:760px){ .controls{gap:10px;} .wrap{padding:0 12px 70px;} }
</style>

<div class="wrap">
  <nav class="topbar">
    <a class="home" href="../index.html">← galavgay.com</a>
    <a class="home" href="../projects.html">Projects</a>
    <a class="home" href="proposal.html">Research proposal</a>
  </nav>

  <h1>Sentiment vs Price</h1>
  <div id="freshBar" class="freshbar" style="display:none;"></div>
  <p class="intro">Does what people say about a stock in the news and on social media tell us anything about where its
     price goes <b>next</b>, once earnings reports, market-wide moves and the fact that people talk about moves that already
     happened are taken into account? The first section answers that across every tracked stock; the second lets you look
     at one stock at a time. Methodology follows the <a href="proposal.html">research proposal</a>.</p>

  <section class="card" id="study">
    <h3>Across all stocks</h3>
    <div class="verdict" id="verdict"></div>
    <div class="vcontrols">
      <div class="grp"><span>Source</span>
        <div class="seg" id="stScope" role="group" aria-label="Mention source for the study results">
          <button type="button" data-v="both">Both</button><button type="button" data-v="social">Social</button><button type="button" data-v="news">News</button></div></div>
      <div class="grp"><span>Earnings days</span>
        <div class="seg" id="stReg" role="group" aria-label="Earnings regime for the study results">
          <button type="button" data-v="all">Included</button><button type="button" data-v="ex_earn">Excluded</button><button type="button" data-v="earn">Only</button></div></div>
    </div>
    <div class="vstats" id="studyTiles"></div>
    <div class="vlegend">
      <span><i class="sw-dot" style="background:var(--price);opacity:1"></i>Whole sample, line = 95% confidence interval</span>
      <span><i class="sw-ring"></i>First 70% / last 30% of dates (does it replicate?)</span>
    </div>
    <div class="vwrap" id="fwrap">
      <svg id="forest" viewBox="0 0 960 250" role="img" aria-label="Estimated effect of sentiment on the next return, with confidence intervals"></svg>
      <div id="ftip" class="tip" hidden></div>
    </div>
    <p class="vnote" id="studyNote"></p>
    <details class="methods"><summary style="cursor:pointer;font-weight:600;color:var(--body);margin-top:6px;">How this is measured</summary><div id="methods"></div></details>
  </section>

  <h2>One stock at a time</h2>
  <div class="howto">
    <p style="margin:0 0 8px;"><b>Reading the chart.</b> The <b style="color:var(--price)">blue line</b> is the
       <b>price</b> (left axis) and the <b style="color:var(--senti)">orange line</b> is the <b>sentiment level</b>
       (right axis, −1 bearish to +1 bullish), a time-decayed average of mention stances. Orange dots are the raw averages per
       time bucket, sized by how many mentions had a stance. Time runs in half-hour steps across pre-market, regular hours and
       after-hours; closed time (overnight, weekends, holidays) is squeezed into a marked break. Pick a window, or
       <b>drag across the chart to zoom</b>.</p>
    <p style="margin:0;">The chart shows timing only. The <b>lead/lag charts</b> below it test it, with a band showing how
       big a correlation chance alone produces.</p>
  </div>

  <div class="controls">
    <div class="fld"><label for="tkSel">Stock</label><select id="tkSel"></select></div>
    <div class="fld"><label for="srcSel">Mention source</label>
      <select id="srcSel"><option value="both">Both</option><option value="social">Social media</option><option value="news">News</option></select></div>
    <div class="fld"><label for="catSel">Group / filter stocks by</label>
      <select id="catSel"><option value="all">All stocks</option><option value="sector">Sector</option><option value="mcap_bucket">Market cap</option><option value="source_favored">Source-favored</option></select></div>
    <div class="fld" id="catValWrap" style="display:none;"><label for="catVal">&nbsp;</label><select id="catVal"></select></div>
    <div class="fld"><label for="earnReg">Earnings days</label>
      <select id="earnReg"><option value="all">Included</option><option value="ex_earn">Excluded</option></select></div>
    <div class="fld"><label>&nbsp;</label><a id="mentionsBtn" class="mentionsbtn" href="#" target="_blank" rel="noopener">View mentions ↗</a></div>
  </div>

  <div class="caveat" id="caveat" style="display:none;"></div>
  <div class="statrow" id="stats"></div>
  <div class="catrow" id="catline"></div>

  <section class="card">
    <div class="vcontrols">
      <div class="grp"><span>Smoothing</span>
        <div class="seg" id="smooth" role="group" aria-label="Sentiment smoothing">
          <button type="button" data-s="fast">6h</button><button type="button" data-s="slow">14d</button></div></div>
      <div class="grp"><span>Window</span>
        <div class="seg" id="range" role="group" aria-label="Time range">
          <button type="button" data-d="2">2D</button><button type="button" data-d="5">5D</button>
          <button type="button" data-d="14">14D</button><button type="button" data-d="30">30D</button>
          <button type="button" data-d="0">All</button></div></div>
      <div class="grp"><button type="button" class="zreset" id="zoomReset" hidden>Reset zoom</button></div>
    </div>
    <div class="vstats" id="vstats"></div>
    <div class="vlegend">
      <span><i class="sw-line" style="background:var(--price)"></i>Price, $ (left axis)</span>
      <span><i class="sw-line" style="background:var(--senti)"></i><b id="sentiKey">Sentiment, 6h half-life</b> (right axis, −1 to +1)</span>
      <span><i class="sw-dot"></i><span id="dotKey">Mean per half-hour with mentions (size = count)</span></span>
      <span><i class="sw-box" style="background:#1f1f1d"></i>Extended hours</span>
      <span><i class="sw-box" style="background:#262624"></i>Closed (collapsed)</span>
      <span id="earnLegend" style="display:none;"><i class="sw-earn"></i>Earnings report (hover)</span>
    </div>
    <div class="vwrap" id="vwrap">
      <svg id="chart" viewBox="0 0 960 400" role="img" aria-label="Sentiment and price over time, half-hourly, with closed hours collapsed"></svg>
      <div id="tip" class="tip" hidden></div>
    </div>
    <p class="vnote" id="vnote"></p>
  </section>
  <details class="card">
    <summary>Table view (newest first)</summary>
    <div class="tblwrap"><table>
      <thead><tr><th>Time (ET)</th><th>Session</th><th class="num">Price</th><th class="num">Sentiment level</th><th class="num">Mentions</th><th class="num">With stance (mean)</th></tr></thead>
      <tbody id="tbody"></tbody></table></div>
  </details>

  <section class="card">
    <h3>Lead / lag by trading day</h3>
    <p>Correlation between a day’s sentiment and this stock’s market-adjusted return some trading days earlier or later.
       Bars <b>right of 0</b> ask whether sentiment comes <b>before</b> the move; bars <b>left of 0</b> whether it
       <b>follows</b> one. Bars inside the dashed band are within what chance alone produces (95%); only bars that leave it are
       <b style="color:var(--price)">coloured</b>. Testing 11 offsets, about one in twenty will leave the band by luck.</p>
    <div class="vwrap" id="lwrap"><svg id="lag" viewBox="0 0 960 230" role="img" aria-label="Daily lead-lag correlation with chance band"></svg><div id="ltip" class="tip" hidden></div></div>
  </section>
  <section class="card">
    <h3>Was it priced in after hours?</h3>
    <p>The same test in half-day steps: each trading day is split into the <b>overnight + pre-market</b> stretch (prior close
       to the first regular-hours price) and the <b>regular session</b>. Offset +1 is the very next stretch, so a bar at +1
       after an overnight window asks whether pre-market chatter predicts the regular session that follows. Hourly prices
       start mid-April 2026, so this has less data than the daily view. <a href="scenarios.html">How to read these plots.</a></p>
    <div class="vwrap" id="swrap"><svg id="sesslag" viewBox="0 0 960 230" role="img" aria-label="Half-session lead-lag correlation with chance band"></svg><div id="stip" class="tip" hidden></div></div>
  </section>

  <footer>Research prototype. Stance is the poster’s own StockTwits Bullish/Bearish tag where given, otherwise a finance word
     list (Loughran-McDonald based, with price-move and uncertainty words removed); no LLM. Results update with every daily run.
     See the <a href="proposal.html">research proposal</a>.</footer>
</div>

<script id="payload" type="application/json">/*DATA*/</script>
<script>
'use strict';
const D = JSON.parse(document.getElementById('payload').textContent);
const A = D.analysis, G = D.grid;
const NS = 'http://www.w3.org/2000/svg';
function $(id){ return document.getElementById(id); }
function el(t,a,parent){ const e=document.createElementNS(NS,t); for(const k in a) e.setAttribute(k,a[k]); if(parent) parent.appendChild(e); return e; }
function txt(x,y,s,cls,anchor,extra){ const e=el('text',Object.assign({x,y,class:cls||'axislbl','text-anchor':anchor||'start'},extra||{})); e.textContent=s; return e; }
const MON=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

// ---- pipeline freshness banner ----
(function(){
  const f=D.freshness, bar=$('freshBar'); if(!f||!bar||!f.gen) return;
  const ageH=(Date.now()/1000-f.gen)/3600, ageD=ageH/24;
  let cls,msg;
  if(ageD<3){cls='ok';msg='Data current';} else if(ageD<5){cls='warn';msg='Update overdue';} else {cls='bad';msg='Pipeline may have stopped';}
  const nH=Math.floor(ageH), nD=Math.floor(ageD);
  const when=ageH<1?'less than an hour ago':ageH<24?nH+' hour'+(nH===1?'':'s')+' ago':nD+' day'+(nD===1?'':'s')+' ago';
  const bits=['Last updated '+when];
  if(f.price_max) bits.push('prices through '+f.price_max);
  if(f.mentions_max) bits.push('mentions through '+f.mentions_max);
  if(f.n_tickers) bits.push(f.n_tickers+' stocks');
  let extra=''; if(cls==='warn') extra=' — the daily run has not published in over 3 days.';
  if(cls==='bad') extra=' — no successful update in over 5 days; the figures below are stale.';
  bar.className='freshbar '+cls;
  bar.innerHTML='<span class="dot"></span><span class="msg">'+msg+'</span><span class="detail">'+bits.join(' &middot; ')+extra+'</span>';
  bar.style.display='';
})();

// ======================================================================
// Shared time grid -> collapsed x coordinate (U), sessions, segments
// ======================================================================
const SLOT=1800, N=G.n, CODE=G.c;
const TODCH='0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKL';
const CLOSED={o:1,w:1,h:1,'?':1};
const RUN_W=6;                         // every closed run collapses to this many slot-widths
const NAMES={p:'Pre-market',r:'Regular',a:'After-hours',o:'Overnight',w:'Weekend',h:'Holiday','?':'Closed'};
const SESS_FULL={pre_market:'pre-market',regular:'regular',after_hours:'after-hours',overnight:'overnight',weekend:'weekend',holiday:'holiday'};
const TOD=new Int16Array(N), DAYI=new Int32Array(N), DAYL=G.days.map(d=>d[1]);
for(let k=0;k<N;k++) TOD[k]=TODCH.indexOf(G.tod[k])*30;
for(let j=0;j<G.days.length;j++){ const a=G.days[j][0], b=j+1<G.days.length?G.days[j+1][0]:N; for(let k=a;k<b;k++) DAYI[k]=j; }
const U=new Float64Array(N+1), SEGS=[], RUNS=[];
(function(){
  let u=0, k=0;
  while(k<N){
    const c=CODE[k];
    if(CLOSED[c]){
      let e=k; while(e<N && CLOSED[CODE[e]]) e++;
      const w=RUN_W/(e-k), u0=u;
      for(let i=k;i<e;i++){ U[i]=u; u+=w; }
      // label each sub-segment of the run by its own kind and real duration
      let s=k; while(s<e){ let t=s; while(t<e && CODE[t]===CODE[s]) t++;
        const hrs=(t-s)/2, h=hrs%1? hrs.toFixed(1):String(hrs);
        SEGS.push([U[s], t<e?U[t]:u, CODE[s], NAMES[CODE[s]]+' · closed '+h+'h', NAMES[CODE[s]]]); s=t; }
      RUNS.push([u0,u,k,e]); k=e;
    } else {
      let e=k; while(e<N && CODE[e]===c) e++;
      const u0=u; for(let i=k;i<e;i++){ U[i]=u; u+=1; }
      SEGS.push([u0,u,c,NAMES[c],c==='r'?'Regular':NAMES[c].slice(0,3)]); k=e;
    }
  }
  U[N]=u;
})();
const FIRSTOPEN=new Int32Array(DAYL.length).fill(-1);
for(let k=0;k<N;k++){ if(!CLOSED[CODE[k]] && FIRSTOPEN[DAYI[k]]<0) FIRSTOPEN[DAYI[k]]=k; }
const OPENCUM=new Int32Array(N+1);            // prefix count of open slots
for(let k=0;k<N;k++) OPENCUM[k+1]=OPENCUM[k]+(CLOSED[CODE[k]]?0:1);
function countOpen(a,b){ return OPENCUM[b+1]-OPENCUM[a]; }
function slotOf(t){ return Math.floor((t-G.start)/SLOT); }
function fmtTod(m){ const h=Math.floor(m/60), mi=m%60, ap=h<12?'AM':'PM'; let h12=h%12; if(h12===0) h12=12; return h12+(mi?':'+String(mi).padStart(2,'0'):'')+' '+ap; }
function sgn(v){ return (v<0?'−':v>0?'+':'')+Math.abs(v).toFixed(2); }
function niceStep(span,n){ const raw=span/n, mag=Math.pow(10,Math.floor(Math.log10(raw))), f=raw/mag; return (f<=1?1:f<=2?2:f<=5?5:10)*mag; }

// ======================================================================
// Per-stock series: fetched on demand, decoded once, derived per scope
// ======================================================================
const CACHE={};
function fetchSeries(tk){
  if(CACHE[tk]) return Promise.resolve(CACHE[tk]);
  return fetch(D.series+encodeURIComponent(tk)+'.json?v='+(D.freshness&&D.freshness.gen||0))
    .then(r=>{ if(!r.ok) throw new Error('HTTP '+r.status); return r.json(); })
    .then(j=>{ CACHE[tk]=decode(j); return CACHE[tk]; });
}
function undelta(a){ const o=new Array(a.length); let s=0; for(let i=0;i<a.length;i++){ s+=a[i]; o[i]=s; } return o; }
function decode(j){
  const P=new Float64Array(N).fill(NaN);
  const pk=undelta(j.p); pk.forEach((k,i)=>{ P[k]=j.pv[i]; });
  const fam={};
  ['social','news'].forEach(f=>{ const m=j.m[f]||[[],[],[],[]]; const ks=undelta(m[0]);
    const sum=new Float64Array(N), cnt=new Int32Array(N), tot=new Int32Array(N);
    ks.forEach((k,i)=>{ cnt[k]=m[2][i]; sum[k]=m[1][i]/1000*m[2][i]; tot[k]=m[3]?m[3][i]:m[2][i]; });
    fam[f]={sum,cnt,tot}; });
  return {t:j.t, i0:j.i0, i1:j.i1, P, fam, init:j.init, scopes:{}, ll:j.ll||{}, earn:j.earn||[]};
}
const HL={fast:12, slow:14*48}, MIN_WEIGHT=0.05;
function scopeData(S, scope){
  if(S.scopes[scope]) return S.scopes[scope];
  let sum, cnt, tot;
  if(scope==='both'){ sum=new Float64Array(N); cnt=new Int32Array(N); tot=new Int32Array(N);
    for(let k=0;k<N;k++){ sum[k]=S.fam.social.sum[k]+S.fam.news.sum[k]; cnt[k]=S.fam.social.cnt[k]+S.fam.news.cnt[k]; tot[k]=S.fam.social.tot[k]+S.fam.news.tot[k]; } }
  else { sum=S.fam[scope].sum; cnt=S.fam[scope].cnt; tot=S.fam[scope].tot; }
  const lev={};
  for(const sp of ['fast','slow']){
    const f=Math.pow(0.5,1/HL[sp]), out=new Float64Array(N);
    let [s,w]=(S.init[scope]||{})[sp==='fast'?'f':'s']||[0,0];
    for(let k=0;k<N;k++){ s*=f; w*=f; if(cnt[k]){ s+=sum[k]; w+=cnt[k]; } out[k]=w>=MIN_WEIGHT? s/w : NaN; }
    lev[sp]=out;
  }
  return (S.scopes[scope]={sum,cnt,tot,lev});
}

// ======================================================================
// Main chart (v2)
// ======================================================================
const VW=960, PX0=84, PX1=896, PW=PX1-PX0, PY0=14, PY1=318, PH=PY1-PY0;
const RIB_Y=330, RIB_H=20, TICK_Y=368, DAY_Y=388;
const EDGE={0:'',240:'4a',570:'9:30',960:'4p',1200:'8p'};
const view={tk:null, scope:'both', smooth:'fast', days:14, zoom:null};
const svg=$('chart'), wrap=$('vwrap'), tip=$('tip');
function fitSvg(){ const w=wrap.clientWidth||VW; svg.setAttribute('width',w); svg.setAttribute('height',Math.round(w*400/VW)); }
// Windows count TRADING time: "2D" = the last 2 trading days' worth of open
// (pre/regular/after) slots, 32 per full day, so a Monday view isn't mostly weekend.
function windowOf(S){
  if(view.zoom) return view.zoom;
  const i1=S.i1; if(view.days<=0) return [S.i0,i1];
  let need=view.days*32, i=i1;
  while(i>S.i0){ if(!CLOSED[CODE[i]] && --need<=0) break; i--; }
  return [i,i1];
}
function fitLabel(sg,wpx){ if(sg[3].length*6.2+10<=wpx) return sg[3]; if(sg[4].length*6.2+10<=wpx) return sg[4]; return ''; }

function earnHover(tk,ev){
  const d=new Date(ev.t*1000);
  const when=MON[d.getUTCMonth()]+' '+d.getUTCDate()+' '+d.getUTCFullYear()+(ev.sess?' ('+(SESS_FULL[ev.sess]||ev.sess)+')':'');
  const line2=(ev.surp==null||ev.rep==null)?'Upcoming / not yet reported':((ev.surp>=0?'BEAT':'MISSED')+' by '+(ev.surp>0?'+':'')+ev.surp+'%');
  const line3=(ev.est!=null||ev.rep!=null)?'EPS: est '+(ev.est==null?'?':ev.est)+' → reported '+(ev.rep==null?'?':ev.rep):'';
  return tk+' earnings · '+when+'\n'+line2+(line3?'\n'+line3:'');
}
function hideEarnTip(){ const n=document.getElementById('earnTip'); if(n) n.remove(); }
function showEarnTip(s,x,yTop,text,cw){
  hideEarnTip(); const lines=text.split('\n'), LH=15, PAD=8;
  const g=el('g',{id:'earnTip','pointer-events':'none'}); const bg=el('rect',{rx:5,fill:'#262624',stroke:'#4a4945','fill-opacity':0.97},g);
  const tn=lines.map((l,i)=>{ const t=txt(0,0,l,'earntiptxt','start'); t.setAttribute('font-size',i===0?'12.5':'12'); t.setAttribute('font-weight',i===0?'bold':'normal'); g.appendChild(t); return t; });
  s.appendChild(g);
  const w=Math.max(...tn.map(t=>t.getComputedTextLength()))+PAD*2, h=lines.length*LH+PAD*2-3;
  let tx=x+12; if(tx+w>cw-4) tx=x-12-w; if(tx<4) tx=4; const ty=Math.max(4,yTop-4);
  bg.setAttribute('x',tx); bg.setAttribute('y',ty); bg.setAttribute('width',w); bg.setAttribute('height',h);
  tn.forEach((t,i)=>{ t.setAttribute('x',tx+PAD); t.setAttribute('y',ty+PAD+12+i*LH); });
}

function drawMain(){
  const tk=view.tk, S=CACHE[tk]; if(!S) return;
  fitSvg(); svg.innerHTML=''; tip.hidden=true;
  const sd=scopeData(S,view.scope), SER=sd.lev[view.smooth], P=S.P;
  const [i0,i1]=windowOf(S), uA=U[i0], uB=U[i1+1], span=Math.max(1e-9,uB-uA);
  const X=u=> PX0+(u-uA)/span*PW, cu=i=>(U[i]+U[i+1])/2;
  // price domain
  let lo=Infinity, hi=-Infinity;
  for(let i=i0;i<=i1;i++){ const p=P[i]; if(p===p){ if(p<lo) lo=p; if(p>hi) hi=p; } }
  const noPrice = lo===Infinity; if(noPrice){ lo=0; hi=1; }
  let pad=(hi-lo)*0.08; if(!(pad>0)) pad=Math.max(hi*0.05,0.5); lo-=pad; hi+=pad;
  const step=niceStep(hi-lo,4), yP=v=>PY1-(v-lo)/(hi-lo)*PH, yS=v=>PY1-(v+1)/2*PH;
  // 1. backgrounds
  SEGS.forEach(sg=>{ const a=Math.max(sg[0],uA), b=Math.min(sg[1],uB);
    if(b>a && (sg[2]==='p'||sg[2]==='a')) el('rect',{x:X(a),y:PY0,width:X(b)-X(a),height:PH,class:'bg-ext'},svg); });
  const longView=countOpen(i0,i1)>16*32;
  RUNS.forEach(r=>{ const a=Math.max(r[0],uA), b=Math.min(r[1],uB);
    if(b>a) el('rect',{x:X(a),y:PY0,width:X(b)-X(a),height:PH,class:'bg-closed'},svg);
    if(!longView) [r[0],r[1]].forEach(e=>{ if(e>uA && e<uB) el('line',{x1:X(e),x2:X(e),y1:PY0,y2:RIB_Y+RIB_H,class:'brk'},svg); }); });
  // 2. grid + axes
  if(!noPrice) for(let v=Math.ceil(lo/step)*step; v<=hi+1e-9; v+=step){ const y=yP(v);
    el('line',{x1:PX0,x2:PX1,y1:y,y2:y,class:'grid'},svg); svg.appendChild(txt(PX0-10,y+4,'$'+v.toFixed(step<1?2:0),'tick','end')); }
  el('line',{x1:PX0,x2:PX1,y1:yS(0),y2:yS(0),class:'zero'},svg);
  [-1,-0.5,0,0.5,1].forEach(v=>svg.appendChild(txt(PX1+10,yS(v)+4,(v<0?'−':v>0?'+':'')+Math.abs(v).toFixed(1),'tick','start')));
  const mid=(PY0+PY1)/2;
  svg.appendChild(txt(14,mid,'Price ($)','axis-title','middle',{transform:'rotate(-90 14 '+mid+')'}));
  svg.appendChild(txt(VW-14,mid,'Sentiment','axis-title','middle',{transform:'rotate(90 '+(VW-14)+' '+mid+')'}));
  // 3. earnings markers (under the lines)
  S.earn.forEach(ev=>{ const k=slotOf(ev.t); if(k<i0||k>i1) return; const x=X(U[k]);
    const g=el('g',{},svg); el('line',{x1:x,x2:x,y1:PY0,y2:PY1,class:'earnline'},g);
    el('rect',{x:x-6,y:PY0-1,width:12,height:13,rx:2,class:'earntag'},g); g.appendChild(txt(x,PY0+9,'E','earntaglbl','middle'));
    const hit=el('rect',{x:x-9,y:PY0-3,width:18,height:PH+6,fill:'transparent'},g); hit.style.cursor='pointer';
    hit.addEventListener('mouseenter',()=>showEarnTip(svg,x,PY0,earnHover(tk,ev),VW)); hit.addEventListener('mouseleave',hideEarnTip); });
  // 4. price line, broken where bars are more than 2 slots apart
  let d='', last=-1e9;
  for(let i=i0;i<=i1;i++){ const p=P[i]; if(p!==p) continue; d+=(i-last>2?'M':'L')+X(cu(i)).toFixed(1)+' '+yP(p).toFixed(1)+' '; last=i; }
  if(d) el('path',{d,class:'line-price'},svg);
  // 5. sentiment level, broken where the decayed evidence is too thin
  d='';
  for(let i=i0;i<=i1;i++){ const s=SER[i]; if(s!==s) continue; d+=((i===i0||SER[i-1]!==SER[i-1])?'M':'L')+X(cu(i)).toFixed(1)+' '+yS(s).toFixed(1)+' '; }
  if(d) el('path',{d,class:'line-senti'},svg);
  // 6. raw means, bucketed so long windows stay readable: half-hour, 1h, 4h, then per day
  const openN=countOpen(i0,i1), B=openN<=320?1:openN<=640?2:openN<=1600?8:0;
  const dots=[]; let cmax=1;
  if(B>0){ for(let a=i0;a<=i1;a+=B){ let s=0,c=0; const e=Math.min(i1,a+B-1); for(let i=a;i<=e;i++){ s+=sd.sum[i]; c+=sd.cnt[i]; }
      if(c){ dots.push([(U[a]+U[e+1])/2,s/c,c]); if(c>cmax) cmax=c; } } }
  else { let a=i0; while(a<=i1){ let e=a; while(e<i1 && DAYI[e+1]===DAYI[a]) e++; let s=0,c=0; for(let i=a;i<=e;i++){ s+=sd.sum[i]; c+=sd.cnt[i]; }
      const f=FIRSTOPEN[DAYI[a]]; if(c){ const uc=(f>=a&&f<=e)? (U[f]+U[Math.min(e,f+31)+1])/2 : (U[a]+U[e+1])/2; dots.push([uc,s/c,c]); if(c>cmax) cmax=c; } a=e+1; } }
  const bucketName=B===1?'half-hour':B===2?'hour':B===8?'4 hours':'day';
  dots.forEach(d=>el('circle',{cx:X(d[0]).toFixed(1),cy:yS(d[1]).toFixed(1),r:(2.2+2.8*Math.sqrt(d[2]/cmax)).toFixed(1),class:'dot-mention'},svg));
  $('dotKey').textContent='Mean per '+bucketName+' with mentions (size = count)';
  // 7. session ribbon
  SEGS.forEach(sg=>{ const a=Math.max(sg[0],uA), b=Math.min(sg[1],uB); if(b<=a) return;
    const x0=X(a), x1=X(b); el('rect',{x:x0,y:RIB_Y,width:x1-x0,height:RIB_H,class:'rb-'+(sg[2]==='?'?'q':sg[2])},svg);
    const lab=fitLabel(sg,x1-x0); if(lab) svg.appendChild(txt((x0+x1)/2,RIB_Y+14,lab,'rb-text','middle')); });
  // 8. time + day ticks (ET), thinned so labels never collide
  const nDays=countOpen(i0,i1)/32; let lastT=-1e9, lastD=-1e9;
  // day labels sit at each day's FIRST OPEN slot (so weekends/holidays, which have
  // none, get no label inside their collapsed break); separators at that same point
  for(let i=i0;i<=i1;i++){
    if(FIRSTOPEN[DAYI[i]]===i){
      const x=X(U[i]); if(nDays<=16 && i>i0) el('line',{x1:x,x2:x,y1:PY0,y2:RIB_Y+RIB_H,class:'daysep'},svg);
      if(x-lastD>=74 && x<=PX1-30){ svg.appendChild(txt(x+3,DAY_Y,DAYL[DAYI[i]],'tick','start')); lastD=x; } }
    const tod=TOD[i]; if(tod===0 || !(tod in EDGE) || CLOSED[CODE[i]]) continue; const x=X(U[i]);
    if(nDays<=10 && x-lastT>=36){ svg.appendChild(txt(x,TICK_Y,EDGE[tod],'tick','middle')); lastT=x; } }
  // 9. hover + drag-to-zoom layer (last, on top)
  const xh=el('line',{x1:0,x2:0,y1:PY0,y2:PY1,class:'xhair',visibility:'hidden'},svg);
  const dP=el('circle',{r:4.5,class:'dot-price',visibility:'hidden'},svg), dS=el('circle',{r:4.5,class:'dot-senti',visibility:'hidden'},svg);
  const ov=el('rect',{x:PX0,y:PY0,width:PW,height:PH,fill:'transparent'},svg); ov.style.cursor='crosshair';
  const slotAt=px=>{ const u=uA+(px-PX0)/PW*span; let a=i0,b=i1; while(a<b){ const m=(a+b+1)>>1; if(U[m]<=u) a=m; else b=m-1; } return a; };
  const vbx=e=>{ const r=svg.getBoundingClientRect(); return (e.clientX-r.left)/r.width*VW; };
  const place=(c,ok,x,y)=>{ if(!ok){ c.setAttribute('visibility','hidden'); return; } c.setAttribute('cx',x.toFixed(1)); c.setAttribute('cy',y.toFixed(1)); c.setAttribute('visibility','visible'); };
  function show(j){
    const x=X(cu(j)); xh.setAttribute('x1',x); xh.setAttribute('x2',x); xh.setAttribute('visibility','visible');
    place(dP,P[j]===P[j],x,P[j]===P[j]?yP(P[j]):0); place(dS,SER[j]===SER[j],x,SER[j]===SER[j]?yS(SER[j]):0);
    let pt; if(P[j]===P[j]) pt='$'+P[j].toFixed(2)+' (bar)'; else { let q=j-1; while(q>=i0 && j-q<=2 && P[q]!==P[q]) q--;
      pt=(q>=i0 && j-q<=2 && P[q]===P[q])? '$'+P[q].toFixed(2)+' (last bar, '+fmtTod(TOD[q])+')' : '<span class="muted">no trade</span>'; }
    const c=sd.cnt[j], tt=sd.tot[j];
    tip.innerHTML='<div class="tt-head">'+DAYL[DAYI[j]]+' · '+fmtTod(TOD[j])+' ET</div><div>'+NAMES[CODE[j]]+'</div>'+
      '<div>Price: '+pt+'</div><div>Sentiment level: '+(SER[j]===SER[j]?sgn(SER[j]):'—')+'</div>'+
      '<div>Mentions: '+tt+(tt? ' · '+c+' with a stance'+(c?' (mean '+sgn(sd.sum[j]/c)+')':''):'')+'</div>';
    tip.hidden=false; const rw=svg.getBoundingClientRect().width; let left=x/VW*rw+14; if(left>rw-250) left-=284; tip.style.left=Math.max(0,left)+'px';
  }
  function hide(){ [xh,dP,dS].forEach(n=>n.setAttribute('visibility','hidden')); tip.hidden=true; }
  let dragX=null, zr=null;
  ov.addEventListener('mousedown',e=>{ dragX=vbx(e); zr=el('rect',{x:dragX,y:PY0,width:0,height:PH,class:'zoomrect','pointer-events':'none'},svg); });
  ov.addEventListener('mousemove',e=>{ const px=vbx(e);
    if(dragX!==null){ zr.setAttribute('x',Math.min(dragX,px)); zr.setAttribute('width',Math.abs(px-dragX)); hide(); }
    else show(slotAt(px)); });
  ov.addEventListener('mouseup',e=>{ if(dragX===null) return; const px=vbx(e), a=Math.min(dragX,px), b=Math.max(dragX,px);
    dragX=null; if(zr){ zr.remove(); zr=null; }
    if(b-a>8){ const s0=slotAt(a), s1=slotAt(b); if(s1-s0>=4){ view.zoom=[s0,s1]; drawMain(); } } });
  ov.addEventListener('mouseleave',()=>{ hide(); dragX=null; if(zr){ zr.remove(); zr=null; } });
  $('zoomReset').hidden=!view.zoom;
  // stats tiles + note + table
  let lp=NaN, lpi=-1, ls=NaN, n24=0;
  for(let i=S.i1;i>=S.i0;i--){ if(lpi<0 && P[i]===P[i]){ lp=P[i]; lpi=i; } if(ls!==ls && SER[i]===SER[i]) ls=SER[i]; if(lpi>=0 && ls===ls) break; }
  for(let i=Math.max(0,S.i1-47);i<=S.i1;i++) n24+=sd.tot[i];
  const tile=(k,v,s)=>'<div class="tile"><div class="k">'+k+'</div><div class="v">'+v+'</div><div class="s">'+s+'</div></div>';
  $('vstats').innerHTML=tile('Latest price',lp===lp?'$'+lp.toFixed(2):'—',lpi>=0?DAYL[DAYI[lpi]]+', '+fmtTod(TOD[lpi])+' ET':'no price bars yet')+
    tile('Sentiment level',ls===ls?sgn(ls):'—',(view.smooth==='fast'?'6-hour':'14-day')+' half-life, −1 to +1')+
    tile('Mentions, last 24h',String(n24),view.scope==='both'?'social + news':view.scope);
  $('sentiKey').textContent=view.smooth==='fast'?'Sentiment, 6h half-life':'Sentiment, 14-day half-life';
  $('vnote').textContent='Each slot is 30 minutes (US Eastern). Pre-market, regular and after-hours share one scale; each closed run is one fixed-width break, so order is kept but spacing inside a break is not. '+
    'The line is a time-decayed average of mention stances (poster’s Bullish/Bearish tag where given, otherwise the finance word list) with the chosen half-life; it holds its value through quiet spells and is hidden once fewer than '+MIN_WEIGHT+' mentions’ worth of decayed evidence remain. '+
    'Dots are raw mean stances per '+bucketName+' (coarser on longer windows), sized by how many mentions had a stance. Mentions with no stance (no finance words, or news that only reports a price move) count toward volume but not toward sentiment. This chart shows timing only; the tests are below.';
  const rows=[];
  for(let i=i1;i>=i0 && rows.length<300;i--){ const c=sd.cnt[i]; if(P[i]!==P[i] && !sd.tot[i]) continue;
    rows.push('<tr><td>'+DAYL[DAYI[i]]+' '+fmtTod(TOD[i])+'</td><td>'+NAMES[CODE[i]]+'</td><td class="num">'+(P[i]===P[i]?'$'+P[i].toFixed(2):'—')+
      '</td><td class="num">'+(SER[i]===SER[i]?sgn(SER[i]):'—')+'</td><td class="num">'+sd.tot[i]+'</td><td class="num">'+(c? c+' ('+sgn(sd.sum[i]/c)+')':'0')+'</td></tr>'); }
  $('tbody').innerHTML=rows.join('');
}
function drawAll(){ const S=CACHE[view.tk]; if(!S) return; drawMain(); drawStockLags(S); }
function loadMain(tk){
  view.tk=tk; view.zoom=null;
  if(CACHE[tk]){ drawAll(); return; }
  svg.innerHTML=''; $('vstats').innerHTML=''; $('tbody').innerHTML=''; $('lag').innerHTML=''; $('sesslag').innerHTML='';
  svg.appendChild(txt(VW/2,200,'Loading '+tk+'…','tick','middle'));
  fetchSeries(tk).then(()=>{ if(view.tk===tk) drawAll(); })
    .catch(err=>{ if(view.tk!==tk) return; svg.innerHTML=''; svg.appendChild(txt(VW/2,200,'Could not load chart data for '+tk+' ('+err.message+').','tick','middle')); });
}


// ======================================================================
// Study-wide results (analysis_v2.json)
// ======================================================================
const ST=D.study, study={scope:'both', reg:'ex_earn'};
const PRED=[['daily','Next trading day','sentiment over a day → next day, close to close'],
            ['open','Regular session','overnight + pre-market sentiment → that day’s 9:30–4:00 return'],
            ['overnight','Overnight gap','regular-session sentiment → close to next open']];
const SCOPE_NAME={both:'news and social',social:'social-media',news:'news'};
const REG_NAME={all:'earnings days included',ex_earn:'earnings days excluded',earn:'earnings days only'};
function fmtB(b){ return (b>0?'+':b<0?'−':'')+Math.abs(b).toFixed(1); }
function fmtP(p){ return p==null?'—':p<0.001?'p < 0.001':'p = '+p.toFixed(p<0.01?3:2); }
function res(key,scope,reg,pe){ const t=ST&&ST.tests[key]; return t? (((t.res[scope]||{})[reg]||{})[pe]||null) : null; }
function robust(key){ const f=res(key,study.scope,study.reg,'full'), e=res(key,study.scope,study.reg,'early'), l=res(key,study.scope,study.reg,'late');
  return !!(f && e && l && f.p<0.05 && Math.sign(e.b)===Math.sign(f.b) && Math.sign(l.b)===Math.sign(f.b) && Math.abs(e.t)>=1 && Math.abs(l.t)>=1); }
function drawStudy(){
  const vd=$('verdict');
  if(!ST){ vd.innerHTML='The study-wide analysis has not been generated yet (data/analysis_v2.json missing).'; return; }
  syncSeg2('stScope',study.scope); syncSeg2('stReg',study.reg);
  const sc=study.scope, rg=study.reg, who=SCOPE_NAME[sc];
  const preds=PRED.map(p=>[p,res(p[0],sc,rg,'full')]).filter(x=>x[1]);
  const rob=PRED.filter(p=>robust(p[0])), nom=preds.filter(x=>x[1].p<0.05);
  const re=res('react',sc,rg,'full'), same=res('same',sc,rg,'full');
  let head;
  if(!preds.length) head='Not enough '+who+' data with '+REG_NAME[rg]+' to run the tests.';
  else if(rob.length){ const r=res(rob[0][0],sc,rg,'full');
    head=(r.b>0?'Higher':'Lower')+' '+who+' sentiment is followed by a <b>'+(r.b>0?'higher':'lower')+'</b> '+rob[0][1].toLowerCase()+' return ('+fmtB(r.b)+' basis points per standard deviation of sentiment, '+fmtP(r.p)+'), and the sign holds in both halves of the data. '+
      (r.b<0?'That is a <b>reversal</b>: enthusiasm comes before underperformance, not outperformance. ':'')+'Treat it as a lead to re-test, not a trading signal: '+(PRED.length*3*3)+' variants are tested here, and some will look significant by chance.'; }
  else head='<b>No reliable evidence that '+who+' sentiment predicts the next return</b> ('+REG_NAME[rg]+'). '+
      (nom.length? nom.length+' of '+preds.length+' tests clear p < 0.05 on the whole sample, but '+(nom.length===1?'it does':'they do')+' not hold up in both halves of the data. ' : 'None of the '+preds.length+' timing tests clears p < 0.05. ');
  let sub='';
  if(re && re.t>3) sub+='Sentiment does <b>follow</b> price: after a day that is one standard deviation better than usual, the next day’s sentiment is '+re.b.toFixed(2)+' standard deviations more bullish (t = '+re.t.toFixed(1)+'). ';
  if(re && re.t<=3) sub+='There is no clear reaction to the previous day’s return in this slice either (t = '+re.t.toFixed(1)+'). ';
  if(same && same.t>3) sub+='On the same day the link is strong ('+fmtB(same.b)+' bp per standard deviation, t = '+same.t.toFixed(1)+'), which is what reacting to price, rather than anticipating it, looks like.';
  else if(same) sub+='The same-day link is not clearly different from zero here ('+fmtB(same.b)+' bp, t = '+same.t.toFixed(1)+'); this slice may simply have too little data.';
  vd.innerHTML=head+(sub?'<span class="sub">'+sub+'</span>':'');
  // tiles
  const t=(k,v,s)=>'<div class="tile"><div class="k">'+k+'</div><div class="v">'+v+'</div><div class="s">'+s+'</div></div>';
  const d=res('daily',sc,rg,'full'), ls=((ST.longshort||{}).daily||{})[rg==='earn'?'all':rg], fd=((ST.fdr||{})[sc]||{})[rg==='earn'?'all':rg];
  $('studyTiles').innerHTML=
    t('Next-day effect',d?fmtB(d.b)+' bp':'—',d?'per 1 sd of sentiment · 95% CI '+fmtB(d.b-1.96*d.se)+' to '+fmtB(d.b+1.96*d.se)+' · '+fmtP(d.p):'not enough data')+
    t('Sentiment follows price',re?re.b.toFixed(2)+' sd':'—',re?'per 1 sd return the day before · t = '+re.t.toFixed(1):'not enough data')+
    t('Top vs bottom fifth',ls?fmtB(ls.mean_bp)+' bp/day':'—',ls?'next-day spread, both sources'+(rg==='earn'?', earnings days included':'')+' · '+fmtP(ls.p)+' · '+ls.nd+' days':'needs 50+ stocks a day')+
    t('Stocks with a “significant” next-day link',fd?fd.sig05+' of '+fd.n:'—',fd?'about '+Math.round(fd.expected05)+' expected by chance · '+fd.fdr10+' survive a false-discovery check'+(rg==='earn'?' (earnings days included)':''):'');
  drawForest(sc,rg);
  const cv=ST.coverage||{};
  $('studyNote').textContent='Effects are in basis points (0.01%) of market-adjusted return per one standard deviation of a stock’s own sentiment, from one regression pooled across all stocks with stock fixed effects, controls for the return already known and for mention volume, and standard errors clustered by date. '+
    'Data: '+(cv.n_stocks||0)+' stocks, '+(cv.n_mentions||0).toLocaleString()+' mentions ('+(cv.n_stanced||0).toLocaleString()+' with a stance); daily prices '+cv.daily_from+' to '+cv.daily_to+', hourly prices from '+cv.sess_from+'. Holdout split at '+cv.split_date+'. Updated '+ST.generated+'.';
  drawMethods();
}
function syncSeg2(id,val){ document.querySelectorAll('#'+id+' button').forEach(b=>{ const on=b.getAttribute('data-v')===val; b.className=on?'on':''; b.setAttribute('aria-pressed',on?'true':'false'); }); }
function drawForest(sc,rg){
  const s=$('forest'), tipEl=$('ftip'); s.innerHTML=''; tipEl.hidden=true;
  const W=960, L=300, R=930, top=34, rowH=62;
  const rows=PRED.map(p=>({p, f:res(p[0],sc,rg,'full'), e:res(p[0],sc,rg,'early'), l:res(p[0],sc,rg,'late')}));
  let m=5; rows.forEach(r=>[r.f,r.e,r.l].forEach(x=>{ if(x){ m=Math.max(m,Math.abs(x.b)+1.96*x.se); } }));
  m=Math.ceil(m*1.1/5)*5;
  const X=v=>L+(v+m)/(2*m)*(R-L), H=top+rows.length*rowH+48;
  s.setAttribute('viewBox','0 0 '+W+' '+H);
  const step=niceStep(2*m,6);
  for(let v=-Math.floor(m/step)*step; v<=m+1e-9; v+=step){ const x=X(v);
    el('line',{x1:x,x2:x,y1:top-8,y2:H-48,class:v===0?'zero':'grid'},s); s.appendChild(txt(x,H-32,(v>0?'+':v<0?'−':'')+Math.abs(v).toFixed(step<1?1:0),'tick','middle')); }
  s.appendChild(txt(X(0),H-10,'basis points of next return per 1 sd of sentiment','axis-title','middle'));
  s.appendChild(txt(X(-m)+4,top-14,'← sentiment followed by lower returns','lbl2','start'));
  s.appendChild(txt(X(m)-4,top-14,'followed by higher returns →','lbl2','end'));
  rows.forEach((r,i)=>{ const y=top+i*rowH+rowH/2;
    s.appendChild(txt(16,y-4,r.p[1],'lbl','start')); s.appendChild(txt(16,y+12,r.p[2],'lbl2','start'));
    if(!r.f){ s.appendChild(txt(X(0)+8,y+4,'not enough data','lbl2','start')); return; }
    const a=r.f.b-1.96*r.f.se, b=r.f.b+1.96*r.f.se;
    el('line',{x1:X(Math.max(-m,a)),x2:X(Math.min(m,b)),y1:y,y2:y,class:'ci'},s);
    el('circle',{cx:X(r.f.b),cy:y,r:6,class:'pt-full'},s);
    if(r.e) el('circle',{cx:X(r.e.b),cy:y-14,r:4.5,class:'pt-half'},s);
    if(r.l) el('circle',{cx:X(r.l.b),cy:y+14,r:4.5,class:'pt-half'},s);
    if(r.e) s.appendChild(txt(X(r.e.b)+8,y-10,'first 70%','lbl2','start'));
    if(r.l) s.appendChild(txt(X(r.l.b)+8,y+18,'last 30%','lbl2','start'));
    const hit=el('rect',{x:0,y:y-rowH/2,width:W,height:rowH,fill:'transparent'},s);
    hit.addEventListener('mousemove',ev=>{ const q=x=>x? fmtB(x.b)+' bp (t = '+x.t.toFixed(2)+', '+x.n.toLocaleString()+' stock-days, '+x.nt+' stocks)':'—';
      tipEl.innerHTML='<div class="tt-head">'+r.p[1]+'</div><div>Whole sample: '+q(r.f)+'</div><div>95% CI: '+fmtB(a)+' to '+fmtB(b)+' bp · '+fmtP(r.f.p)+'</div><div>First 70%: '+q(r.e)+'</div><div>Last 30%: '+q(r.l)+'</div>';
      tipEl.hidden=false; const rr=s.getBoundingClientRect(); let left=ev.clientX-rr.left+14; if(left>rr.width-300) left-=320; tipEl.style.left=Math.max(0,left)+'px'; tipEl.style.top=(y/H*rr.height+10)+'px'; });
    hit.addEventListener('mouseleave',()=>{ tipEl.hidden=true; });
  });
}
function drawMethods(){
  const v=ST.validation||{}, nw=v.new_lexicon||{}, od=v.old_score||{}, mix=v.mix||{}, P=ST.params||{};
  const pc=x=>x==null?'—':(100*x).toFixed(0)+'%';
  const soc=mix.social||{}, nws=mix.news||{}, ns=(soc.tag||0)+(soc.lex||0)+(soc.none||0), nn=(nws.lex||0)+(nws.move||0)+(nws.none||0);
  $('methods').innerHTML=
   '<h4>Stance (how each mention is scored)</h4><ul>'+
   '<li><b>StockTwits posts tagged Bullish or Bearish by the poster</b> use that tag (+1 / −1): '+pc(ns?(soc.tag||0)/ns:null)+' of social mentions.</li>'+
   '<li>Other text uses a finance word list based on Loughran-McDonald, with words that describe a price move (“fell”, “surged”, “rally”) and uncertainty words (“risk”, “volatile”) removed, and simple negation handling. Stance = (bullish − bearish hits) / (all hits).</li>'+
   '<li><b>No stance</b> when a mention has no hits ('+pc(ns?(soc.none||0)/ns:null)+' of social, '+pc(nn?(nws.none||0)/nn:null)+' of news), and for news whose text only reports a price move ('+pc(nn?(nws.move||0)/nn:null)+' of news): a headline that says a stock fell is about the past move, and scoring it would make sentiment follow price by construction. These still count as mention volume.</li>'+
   '<li>A window needs at least '+P.min_n+' mentions with a stance ('+P.min_n_news+' for news) to have a sentiment value.</li></ul>'+
   '<h4>Checking the word list against posters’ own tags</h4><p>On '+(v.tagged_posts||0).toLocaleString()+' tagged posts ('+pc(v.tagged_bull_share)+' Bullish), scoring the text alone and ignoring the tag:</p>'+
   '<table><thead><tr><th>Scorer</th><th class="num">Posts with a score</th><th class="num">Bullish caught</th><th class="num">Bearish caught</th><th class="num">Balanced accuracy</th></tr></thead><tbody>'+
   '<tr><td>Word list used here</td><td class="num">'+pc(nw.coverage)+'</td><td class="num">'+pc(nw.bull_recall)+'</td><td class="num">'+pc(nw.bear_recall)+'</td><td class="num">'+pc(nw.balanced_acc)+'</td></tr>'+
   '<tr><td>Previous word list</td><td class="num">'+pc(od.coverage)+'</td><td class="num">'+pc(od.bull_recall)+'</td><td class="num">'+pc(od.bear_recall)+'</td><td class="num">'+pc(od.balanced_acc)+'</td></tr></tbody></table>'+
   '<p style="margin-top:6px;">A word list reads only about a quarter of short social posts, and gets roughly one in four of those wrong (50% is a coin flip). That is why the tags carry most of the social signal, and why a model-based scorer would be the next upgrade.</p>'+
   '<h4>Timing</h4><ul><li>Windows follow the NYSE clock, not calendar dates: <b>day</b> = prior close to close; <b>overnight + pre-market</b> = prior close to the first regular-hours price; <b>regular</b> = that price to the close.</li>'+
   '<li>Sentiment is always measured in a window that ends before the return window starts.</li></ul>'+
   '<h4>Returns and controls</h4><ul><li>Log returns minus the average return of every other tracked stock over the same window (market-adjusted), with extreme values capped at the 0.5th / 99.5th percentile.</li>'+
   '<li>Controls: the stock’s own return in the window just before (already known when the prediction is made) and its mention volume, plus stock fixed effects. Sentiment is standardised within each stock, because some stocks’ crowds are always more bullish than others.</li>'+
   '<li>Earnings: trading days '+((P.earn_window||[-1,2])[0])+' to +'+((P.earn_window||[-1,2])[1])+' around each report are flagged, so every test can be run with them included, excluded, or alone.</li></ul>'+
   '<h4>Significance</h4><ul><li>Standard errors are clustered by date, because a market-wide story moves every stock on the same day.</li>'+
   '<li>Robustness: the same regression on the first 70% and the last 30% of dates; a long-short check (each day, top fifth of stocks by sentiment minus bottom fifth); and for single stocks, a Benjamini-Hochberg false-discovery count at 10%, since 600+ stocks tested at p < 0.05 give about 30 “hits” by chance.</li></ul>'+
   '<h4>Known limits</h4><ul><li>About five months of dense mention history; StockTwits is sampled at up to 20 posts an hour per stock, and news is thin for most stocks.</li>'+
   '<li>The market adjustment uses this tracked universe, not an index, and does not adjust for sector.</li>'+
   '<li>The open is the first regular-session hourly price (up to 90 minutes after 9:30), not the official opening auction.</li></ul>';
}
document.querySelectorAll('#stScope button').forEach(b=>b.addEventListener('click',()=>{ study.scope=b.getAttribute('data-v'); drawStudy(); }));
document.querySelectorAll('#stReg button').forEach(b=>b.addEventListener('click',()=>{ study.reg=b.getAttribute('data-v'); drawStudy(); }));

// ======================================================================
// Per-stock lead/lag with chance band
// ======================================================================
function drawLagBars(svgId,tipId,rows,unitLabel,lagName,leftLbl,rightLbl){
  const s=$(svgId), tipEl=$(tipId); s.innerHTML=''; tipEl.hidden=true;
  const W=960,H=230,mL=56,mR=18,mT=26,mB=46;
  const ok=(rows||[]).filter(r=>r[1]!=null);
  if(!ok.length){ s.appendChild(txt(W/2,H/2,'Not enough overlapping data for this stock and source yet.','tick','middle')); return; }
  const ks=rows.map(r=>r[0]), kmin=Math.min(...ks), kmax=Math.max(...ks), nK=kmax-kmin+1;
  let m=0.3; ok.forEach(r=>{ m=Math.max(m,Math.abs(r[1]),1.96/Math.sqrt(Math.max(4,r[2]))); }); m=Math.ceil(m*1.15*10)/10;
  const cx=k=>mL+(W-mL-mR)*((k-kmin+0.5)/nK), y0=mT+(H-mT-mB)/2, yS=v=>y0-v/m*((H-mT-mB)/2), bw=(W-mL-mR)/nK*0.56;
  [-m,-m/2,m/2,m].forEach(v=>{ const yy=yS(v); el('line',{x1:mL,x2:W-mR,y1:yy,y2:yy,class:'grid'},s); s.appendChild(txt(mL-6,yy+4,(v>0?'+':'−')+Math.abs(v).toFixed(2),'tick','end')); });
  rows.forEach(r=>{ if(r[2]<4) return; const b=1.96/Math.sqrt(r[2]); el('rect',{x:cx(r[0])-bw/2-5,y:yS(b),width:bw+10,height:yS(-b)-yS(b),rx:3,class:'band'},s); });
  el('line',{x1:mL,x2:W-mR,y1:y0,y2:y0,class:'zero'},s);
  el('line',{x1:cx(0),x2:cx(0),y1:mT,y2:H-mB,class:'brk','stroke-dasharray':'2 3'},s);
  rows.forEach(r=>{ const k=r[0];
    s.appendChild(txt(cx(k),H-mB+16,(k>0?'+':k<0?'−':'')+Math.abs(k),'tick','middle'));
    if(r[1]==null) return;
    const sig=r[2]>=4 && Math.abs(r[1])>1.96/Math.sqrt(r[2]), yy=yS(r[1]), h=Math.max(1,Math.abs(yy-y0));
    // bar with rounded data end anchored to the zero line
    const x=cx(k)-bw/2, rr=Math.min(4,h);
    const d=r[1]>=0? 'M'+x+' '+y0+'V'+(y0-h+rr)+'Q'+x+' '+(y0-h)+' '+(x+rr)+' '+(y0-h)+'H'+(x+bw-rr)+'Q'+(x+bw)+' '+(y0-h)+' '+(x+bw)+' '+(y0-h+rr)+'V'+y0+'Z'
                   : 'M'+x+' '+y0+'V'+(y0+h-rr)+'Q'+x+' '+(y0+h)+' '+(x+rr)+' '+(y0+h)+'H'+(x+bw-rr)+'Q'+(x+bw)+' '+(y0+h)+' '+(x+bw)+' '+(y0+h-rr)+'V'+y0+'Z';
    el('path',{d,class:sig?'bar-sig':'bar-ns'},s);
    const hit=el('rect',{x:cx(k)-(W-mL-mR)/nK/2,y:mT,width:(W-mL-mR)/nK,height:H-mT-mB,fill:'transparent'},s);
    hit.addEventListener('mousemove',ev=>{ const p=(function(){ const t=r[1]*Math.sqrt((r[2]-2)/Math.max(1e-9,1-r[1]*r[1])); return Math.min(1,2*(1-ncdf(Math.abs(t)))); })();
      tipEl.innerHTML='<div class="tt-head">'+lagName(k)+'</div><div>Correlation: '+(r[1]>=0?'+':'−')+Math.abs(r[1]).toFixed(3)+'</div><div>Chance band: ±'+(1.96/Math.sqrt(r[2])).toFixed(3)+' · '+fmtP(p)+'</div><div class="muted">'+r[2]+' '+unitLabel+'</div>';
      tipEl.hidden=false; const rc=s.getBoundingClientRect(); let left=ev.clientX-rc.left+14; if(left>rc.width-230) left-=250; tipEl.style.left=Math.max(0,left)+'px'; });
    hit.addEventListener('mouseleave',()=>{ tipEl.hidden=true; });
  });
  s.appendChild(txt(14,y0,'Correlation','axis-title','middle',{transform:'rotate(-90 14 '+y0+')'}));
  s.appendChild(txt((mL+W-mR)/2,H-8,'Offset','axis-title','middle'));
  s.appendChild(txt(mL,14,leftLbl,'lbl2','start')); s.appendChild(txt(W-mR,14,rightLbl,'lbl2','end'));
}
function ncdf(x){ const t=1/(1+0.2316419*x), d=0.3989423*Math.exp(-x*x/2); return 1-d*t*(0.3193815+t*(-0.3565638+t*(1.781478+t*(-1.821256+t*1.330274)))); }
function drawStockLags(S){
  const reg=$('earnReg').value, sc=view.scope, ll=S.ll||{};
  const daily=((ll.daily||{})[sc]||{})[reg], sess=((ll.sess||{})[sc]||{})[reg];
  drawLagBars('lag','ltip',daily,'trading days with both values',
    k=>k>0?'Sentiment, then the return '+k+' trading day'+(k>1?'s':'')+' later':k<0?'Return, then sentiment '+(-k)+' trading day'+(k<-1?'s':'')+' later':'Same trading day',
    '← sentiment follows the move','sentiment comes before the move →');
  drawLagBars('sesslag','stip',sess,'half-day windows with both values',
    k=>k>0?'Sentiment, then the return '+k+' half-day step'+(k>1?'s':'')+' later':k<0?'Return, then sentiment '+(-k)+' half-day step'+(k<-1?'s':'')+' later':'Same half-day window',
    '← sentiment follows the move','sentiment comes before the move →');
}

// ======================================================================
// Per-stock stats tiles
// ======================================================================
function drawStats(tk,a,scope){
  const v=a.v2||{}, reg=$('earnReg').value, k1=((v.k1||{})[scope]||{})[reg]||{};
  const r=k1.r, n=k1.n||0, p=k1.p;
  $('stats').innerHTML=
    '<div class="stat"><div class="k">Next-day correlation</div><div class="v">'+(r==null?'—':(r>=0?'+':'−')+Math.abs(r).toFixed(2))+'</div><small>'+(r==null?'not enough data':fmtP(p)+' · '+n+' days · '+(p!=null&&p<0.05?'outside':'inside')+' chance band')+'</small></div>'+
    '<div class="stat"><div class="k">Mentions</div><div class="v">'+(v.n_m!=null?v.n_m.toLocaleString():(a.n_mentions||0))+'</div><small>'+(v.n_st!=null?v.n_st.toLocaleString()+' with a stance · ':'')+'social '+a.social_mentions+' · news '+a.news_mentions+'</small></div>'+
    '<div class="stat"><div class="k">Earnings reports</div><div class="v">'+(v.n_ev||0)+'</div><small>in the price history</small></div>';
  $('catline').innerHTML='Category: <b>'+a.sector+'</b> · <b>'+a.industry+'</b> · <b>'+a.mcap_bucket+'-cap</b> · leans <b>'+a.source_favored+'</b>';
  const cav=$('caveat');
  if(r!=null && n<40){ cav.style.display='block'; cav.innerHTML='Only '+n+' trading days with enough '+(scope==='both'?'':scope+' ')+'mentions for '+tk+': per-stock correlations this small-sample are <b>illustrative</b>. The study-wide results above pool every stock.'; }
  else cav.style.display='none';
}

// ======================================================================
// Controls
// ======================================================================
const tkSel=$('tkSel'), srcSel=$('srcSel'), catSel=$('catSel'), catVal=$('catVal'), catValWrap=$('catValWrap');
function catValue(tk,key){ return key==='all'?'all':(A[tk][key]||'unknown'); }
function fillStocks(list){ tkSel.innerHTML=''; list.forEach(tk=>{ const o=document.createElement('option'); o.value=tk;
  const v=A[tk].v2; o.textContent=tk+'  ('+((v&&v.n_m!=null)?v.n_m:A[tk].n_mentions).toLocaleString()+' mentions)'; tkSel.appendChild(o); }); }
function populateStocks(){ const cat=catSel.value, f=catVal.value; const list=D.order.filter(tk=>cat==='all'||catValue(tk,cat)===f);
  fillStocks(list); if(list.length) render(list[0]); }
function populateCatValues(){ const cat=catSel.value; if(cat==='all'){ catValWrap.style.display='none'; populateStocks(); return; }
  const vals=[...new Set(D.order.map(tk=>catValue(tk,cat)))].sort(); catVal.innerHTML='';
  vals.forEach(v=>{ const o=document.createElement('option'); o.value=v; o.textContent=v; catVal.appendChild(o); });
  catValWrap.style.display=''; populateStocks(); }
function render(tk){
  tkSel.value=tk; const a=A[tk]; let scope=srcSel.value; if(!(((a.scopes||{})[scope]||{}).n_mentions>0)) scope='both';
  view.scope=scope; $('mentionsBtn').href='mentions.html?ticker='+encodeURIComponent(tk);
  $('earnLegend').style.display=a.he?'inline':'none';
  drawStats(tk,a,scope); loadMain(tk);
}
function syncSeg(id,attr,val){ document.querySelectorAll('#'+id+' button').forEach(b=>{ const on=b.getAttribute(attr)===String(val); b.className=on?'on':''; b.setAttribute('aria-pressed',on?'true':'false'); }); }
document.querySelectorAll('#range button').forEach(b=>b.addEventListener('click',()=>{ view.days=+b.getAttribute('data-d'); view.zoom=null; syncSeg('range','data-d',view.days); drawMain(); }));
document.querySelectorAll('#smooth button').forEach(b=>b.addEventListener('click',()=>{ view.smooth=b.getAttribute('data-s'); syncSeg('smooth','data-s',view.smooth); drawMain(); }));
$('zoomReset').addEventListener('click',()=>{ view.zoom=null; drawMain(); });
tkSel.addEventListener('change',()=>render(tkSel.value));
srcSel.addEventListener('change',()=>render(tkSel.value));
catSel.addEventListener('change',populateCatValues); catVal.addEventListener('change',populateStocks);
$('earnReg').addEventListener('change',()=>{ const S=CACHE[view.tk]; drawStats(view.tk,A[view.tk],view.scope); if(S) drawStockLags(S); });
let rz=null; window.addEventListener('resize',()=>{ clearTimeout(rz); rz=setTimeout(drawMain,120); });
syncSeg('range','data-d',view.days); syncSeg('smooth','data-s',view.smooth);
drawStudy();
fillStocks(D.order); if(D.order.length) render(D.order[0]);
</script>
"""

if __name__ == "__main__":
    main()

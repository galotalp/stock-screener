"""
Generate the Sentiment vs Price analysis page (self-contained, static).

Chart design (per user spec):
  - DUAL axis, honestly scaled: price on LEFT y-axis (its real $ range), sentiment
    on RIGHT y-axis FIXED to its true -1..+1 range (not auto-hugged to price — this
    prevents manufacturing visual correlation). X-axis = Date.
  - ALL axes labelled: "Price ($)" left, "Sentiment (-1 to +1)" right, "Date" bottom.
  - X-axis restricted to the sentiment window (±1 day), computed upstream in analyze.py.
  - Source dropdown: Both / Social / News (Both default) — switches the sentiment series.
  - Category filter: sector / market-cap bucket / source-favored, to slice the stock list.
  - Separate lead/lag cross-correlation chart is the rigorous correlation measure.

Matches galavgay.com styling. Two-series palette validated (price #3b5bdb, senti #0ca678).
"""
import json
import os
import glob

import math

# session -> single-char code for compact embedding
SESS_CODE = {"pre_market": "p", "regular": "r", "after_hours": "a",
             "overnight": "o", "weekend": "w", "holiday": "h", None: "?"}


def _json_safe(obj):
    """Recursively replace NaN/Inf floats with None so the embedded payload is
    strict-valid JSON (browser JSON.parse rejects NaN/Infinity). Everything else
    passes through unchanged."""
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def load_intraday_compact(ticker):
    """Compact intraday series for embedding: parallel arrays keyed off a base epoch.
    Returns {t:[unix_seconds...], p:[price...], s:"codes..."} or None."""
    p = f"data/prices/{ticker}_intraday.json"
    if not os.path.exists(p):
        return None
    import datetime as dt
    try:
        bars = json.load(open(p))
    except Exception:
        return None
    if not bars:
        return None
    ts, pr, sc = [], [], []
    for b in bars:
        try:
            u = int(dt.datetime.fromisoformat(b["ts"].replace("Z", "+00:00")).timestamp())
        except Exception:
            continue
        if b.get("price") is None:
            continue
        ts.append(u)
        pr.append(round(float(b["price"]), 2))
        sc.append(SESS_CODE.get(b.get("session"), "?"))
    if not ts:
        return None
    return {"t": ts, "p": pr, "s": "".join(sc)}


def build_nyse_schedule(intraday):
    """Compact NYSE schedule for the date span the intraday data covers, so the
    browser can classify EVERY hour of the x-axis (not only hours that have a
    price bar) exactly the way market_session.py does. Weekends and holidays are
    simply the dates ABSENT from this map; early-close days carry their real
    (open,close). Returns {"YYYY-MM-DD":[open_unix, close_unix], ...}.

    The client-side sessionAt() mirrors session_of(): a timestamp on a date in
    this map is pre/regular/after by its open/close; a date NOT in the map is
    weekend (Sat/Sun) or holiday (weekday); 20:00->04:00 between two trading days
    is overnight. So the ONE authoritative input the browser needs is this set of
    trading days with their exact open/close, which is what we export here."""
    import datetime as dt
    try:
        import market_session as ms
    except Exception:
        return {}
    # date span = min..max across all embedded intraday series (+/- a day of pad)
    lo = hi = None
    for c in intraday.values():
        if not c.get("t"):
            continue
        t0, t1 = c["t"][0], c["t"][-1]
        lo = t0 if lo is None else min(lo, t0)
        hi = t1 if hi is None else max(hi, t1)
    if lo is None:
        return {}
    d0 = dt.datetime.fromtimestamp(lo, dt.timezone.utc).date() - dt.timedelta(days=2)
    d1 = dt.datetime.fromtimestamp(hi, dt.timezone.utc).date() + dt.timedelta(days=2)
    out = {}
    for yr in range(d0.year, d1.year + 1):
        sched = ms._schedule_for_year(yr)  # {date: (open_utc, close_utc)}
        for day, (o_utc, c_utc) in sched.items():
            if d0 <= day <= d1:
                out[day.isoformat()] = [int(o_utc.timestamp()), int(c_utc.timestamp())]
    return out


def load_earnings_compact(tickers):
    """Per-ticker earnings events for the chart's vertical markers + hover.
    Returns {ticker: [{t:unix, sess, we:unix|None, est, rep, surp}, ...]} — only for
    tickers that have an earnings file. Compact keys keep the payload small."""
    import datetime as dt
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
            ts = e.get("ts")
            if not ts:
                continue
            try:
                u = int(dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
            except Exception:
                continue
            we = e.get("window_end")
            try:
                weu = int(dt.datetime.fromisoformat(we.replace("Z", "+00:00")).timestamp()) if we else None
            except Exception:
                weu = None
            evs.append({
                "t": u,
                "sess": e.get("session"),
                "we": weu,
                "est": e.get("eps_estimate"),
                "rep": e.get("reported_eps"),
                "surp": e.get("surprise_pct"),
            })
        if evs:
            out[tk] = evs
    return out


def load_mcaps():
    """Raw market cap per ticker from lynch.csv (for ordering biggest-first)."""
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
    """Pipeline health, embedded in the page so staleness is VISIBLE to a reader.

    Why this exists: in late July 2026 the nightly run began exceeding daily-run.sh's
    timeout (serial per-ticker rate-limit sleeps grow with the universe), so it was
    killed after collecting but before publishing. The dashboard silently served a
    12-day-old page — nothing on it said so, and the failure was only noticed by
    eyeballing the charts. A generated-at stamp plus the newest date actually present
    in the data makes that failure mode self-announcing.

    Returns {gen: unix, mentions_max: "YYYY-MM-DD"|None, price_max: "YYYY-MM-DD"|None,
             n_tickers: int}. The client turns this into an age and a banner.
    """
    import datetime as dt
    now = int(dt.datetime.now(dt.timezone.utc).timestamp())
    out = {"gen": now, "mentions_max": None, "price_max": None, "n_tickers": 0}
    # Newest mention date across the store. Records carry an ISO "date" field
    # (e.g. "2026-07-10T23:51:52Z"). Appends are roughly chronological but not
    # guaranteed sorted, so scan the tail of each file (not just the final line)
    # and keep the max — reading ~32 KB per ticker instead of the whole 117 MB store.
    newest_m = None
    for p in glob.glob("data/mentions/*.jsonl"):
        try:
            with open(p, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - 32768))
                tail = fh.read().decode("utf-8", "ignore").splitlines()
            # drop a possibly-truncated first line from the mid-file seek
            if size > 32768 and tail:
                tail = tail[1:]
            for line in tail:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = (json.loads(line).get("date") or "")[:10]
                except Exception:
                    continue
                if len(d) == 10 and (newest_m is None or d > newest_m):
                    newest_m = d
        except Exception:
            continue
    out["mentions_max"] = newest_m
    return out


def main():
    analysis = json.load(open("data/analysis.json"))
    mcaps = load_mcaps()
    # attach raw mcap to each ticker (for the client) + order the dropdown by it,
    # biggest first (NVDA, AAPL, MSFT, ...). Unknown-cap names sink to the bottom.
    for tk in analysis:
        analysis[tk]["market_cap"] = mcaps.get(tk, 0)
    order = sorted(analysis.keys(),
                   key=lambda t: mcaps.get(t, 0), reverse=True)
    # attach compact intraday series so the chart can zoom into session-shaded hours
    intraday = {}
    for tk in analysis:
        c = load_intraday_compact(tk)
        if c:
            intraday[tk] = c
    # session-aware lead/lag analysis (full extended-hours path vs regular-only path)
    session = {}
    if os.path.exists("data/session_analysis.json"):
        session = json.load(open("data/session_analysis.json"))
    # NYSE trading-day schedule so the browser can classify every hour of the
    # x-axis (weekend/holiday/overnight bands, not just hours with a price bar).
    nyse = build_nyse_schedule(intraday)
    # earnings events per ticker (vertical markers + hover + regime control)
    earnings = load_earnings_compact(list(analysis.keys()))
    payload = {"analysis": analysis, "order": order, "intraday": intraday,
               "session": session, "nyse": nyse, "earnings": earnings}
    # pipeline-health block (see build_freshness): newest price date comes from the
    # analysis we already hold, so it costs nothing to add here.
    fresh = build_freshness()
    fresh["n_tickers"] = len(analysis)
    pmax = None
    for a in analysis.values():
        ds = a.get("dates") or []
        pr = a.get("price") or []
        for i in range(len(ds) - 1, -1, -1):
            if i < len(pr) and pr[i] is not None:
                if pmax is None or ds[i] > pmax:
                    pmax = ds[i]
                break
    fresh["price_max"] = pmax
    payload["freshness"] = fresh
    # CRITICAL: yfinance can leave NaN in a price series. Python's json.dumps emits a
    # bare `NaN` token, which is NOT valid JSON — the browser's JSON.parse then throws
    # and the ENTIRE dashboard fails to render (blank dropdown + blank charts). Scrub
    # every NaN/Inf to null so the embedded payload is always strict-valid JSON; the
    # chart already skips null price points. allow_nan=False is the belt-and-braces
    # tripwire: if anything slips past the scrub it raises here (a loud generator
    # error) instead of silently shipping a broken page.
    payload = _json_safe(payload)
    data_js = json.dumps(payload, separators=(",", ":"), allow_nan=False)
    html = TEMPLATE.replace("/*DATA*/", data_js)
    out = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sentiment vs Price · Gal Av-Gay</title>
</head>
<body>
{html}
</body>
</html>
'''
    open("analysis.html", "w", encoding="utf-8").write(out)
    print("wrote analysis.html", len(out), "bytes;", len(analysis), "tickers")


TEMPLATE = r"""
<style>
  :root{
    --bg:#ffffff; --ink:#333; --body:#696969; --faint:#8a8a8a;
    --navy:#00008b; --link:#597ccd; --link-hover:#00008b;
    --hair:#e6e6e6; --hair2:#f0f0f0; --panel:#fafafb;
    --price:#3b5bdb; --senti:#0ca678; --vol:#c9ced8;
    --pos:#1a7f37; --neg:#c0392b;
    /* Times New Roman throughout, matching the research proposal */
    --serif:"Times New Roman",Times,Georgia,serif;
    --sans:"Times New Roman",Times,Georgia,serif; --head:"Times New Roman",Times,Georgia,serif;
    --mono:"Times New Roman",Times,Georgia,serif;
  }
  *{box-sizing:border-box;}
  html,body{overflow-x:hidden;}
  body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--serif);font-size:17px;line-height:1.55;padding:3px;}
  .wrap{max-width:1080px;margin:0 auto;padding:0 32px 80px;width:100%;}
  a{color:var(--link);text-decoration:none;} a:hover{color:var(--link-hover);text-decoration:underline;}
  .topbar{display:flex;align-items:center;gap:18px;flex-wrap:wrap;padding:16px 0 0;font-size:14px;}
  .topbar .home{font-weight:bold;}
  h1{font-family:var(--head);color:#000;font-size:30px;font-weight:bold;border-bottom:3px solid var(--navy);padding:0 0 6px;margin:18px 0 14px;text-align:center;}
  .intro{font-size:16px;color:var(--body);margin:0 auto 10px;max-width:none;line-height:1.6;text-align:justify;}
  .intro.lead{font-size:17px;color:var(--ink);}
  .intro b{color:var(--ink);}
  /* earnings tooltip text (drawn panel, see showEarnTip) */
  .earntiptxt{fill:#fff;font-family:var(--sans);}
  /* pipeline freshness banner — green/amber/red by data age (see updateFreshness) */
  .freshbar{font-family:var(--sans);font-size:14px;margin:0 0 14px;padding:10px 14px;
            border-radius:8px;border:1px solid var(--hair);background:var(--panel);
            display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;}
  .freshbar .dot{width:9px;height:9px;border-radius:50%;flex:none;align-self:center;}
  .freshbar .msg{font-weight:bold;}
  .freshbar .detail{color:var(--faint);font-size:13px;}
  .freshbar.ok{border-color:#cfe8d5;background:#f4fbf6;}
  .freshbar.ok .dot{background:var(--pos);} .freshbar.ok .msg{color:var(--pos);}
  .freshbar.warn{border-color:#f0dfae;background:#fdf9ee;}
  .freshbar.warn .dot{background:#b7791f;} .freshbar.warn .msg{color:#8a5a00;}
  .freshbar.bad{border-color:#eebfb8;background:#fdf3f1;}
  .freshbar.bad .dot{background:var(--neg);} .freshbar.bad .msg{color:var(--neg);}
  .howto{font-size:15px;color:var(--body);margin:0 0 16px;line-height:1.6;text-align:justify;
         background:var(--panel);border:1px solid var(--hair);border-radius:8px;padding:14px 18px;}
  .howto b{color:var(--ink);} .howto .pos{color:var(--pos);font-weight:bold;} .howto .neg{color:var(--neg);font-weight:bold;}
  .controls{display:flex;gap:16px;flex-wrap:wrap;align-items:end;justify-content:center;margin:0 0 8px;padding:14px 18px;background:var(--panel);border:1px solid var(--hair);border-radius:8px;}
  .fld{display:flex;flex-direction:column;gap:4px;}
  .fld label{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--faint);font-family:var(--head);font-weight:bold;}
  select{font-family:var(--sans);font-size:15px;color:var(--ink);background:#fff;border:1px solid var(--hair);border-radius:5px;padding:8px 12px;}
  select:focus{outline:2px solid var(--link);border-color:var(--link);}
  .mentionsbtn{font-family:var(--sans);font-size:14px;font-weight:bold;color:var(--link);background:#fff;
    border:1px solid var(--hair);border-radius:5px;padding:8px 14px;text-decoration:none;white-space:nowrap;}
  .mentionsbtn:hover{border-color:var(--link);text-decoration:none;background:#f6f8ff;}

  .statrow{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:16px 0 12px;}
  .stat{border:1px solid var(--hair);border-radius:6px;padding:11px 14px;background:#fff;}
  .stat .k{font-size:11.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--faint);font-family:var(--head);font-weight:bold;}
  .stat .v{font-family:var(--mono);font-size:21px;font-weight:bold;color:var(--ink);margin-top:3px;}
  .stat .v.lead{color:var(--pos);} .stat .v.lag{color:var(--navy);}
  .stat small{font-size:12.5px;color:var(--faint);font-family:var(--sans);font-weight:normal;display:block;margin-top:2px;}
  .catrow{font-size:14px;color:var(--faint);font-family:var(--mono);margin:-2px 0 12px;text-align:center;}
  .catrow b{color:var(--ink);}

  .legend{display:flex;gap:24px;flex-wrap:wrap;font-size:16px;margin:8px 0 8px;justify-content:center;align-items:center;}
  .legend span{display:inline-flex;align-items:center;gap:9px;}
  .zoombar{display:flex;align-items:center;justify-content:center;gap:14px;margin:2px 0 4px;}
  .zoomhint{font-size:13px;color:var(--faint);font-style:italic;}
  .zoomreset{font-family:var(--sans);font-size:13px;color:var(--link);background:#fff;border:1px solid var(--hair);border-radius:5px;padding:5px 12px;cursor:pointer;}
  .zoomreset:hover{border-color:var(--link);}
  .sesslegend{gap:18px;font-size:15px;margin:2px 0 6px;}
  .sesslegend i{width:26px;height:16px;border-radius:3px;display:inline-block;}
  .sessverdict{background:var(--panel);border:1px solid var(--hair);border-left:3px solid var(--navy);border-radius:6px;
     padding:12px 16px;font-size:15px;line-height:1.55;margin:8px 0 10px;text-align:justify;}
  .sessverdict b{color:var(--ink);} .sessverdict .tag{font-weight:bold;}
  .sessverdict .lead-tag{color:var(--pos);} .sessverdict .coincide-tag{color:var(--neut,#9a7a12);} .sessverdict .lag-tag{color:var(--neg);}
  .legend span{display:inline-flex;align-items:center;gap:6px;}
  .legend i{width:26px;height:6px;border-radius:3px;display:inline-block;}
  .chartwrap{border:1px solid var(--hair);border-radius:8px;padding:10px 6px 4px;background:#fff;overflow-x:auto;}
  svg{display:block;width:100%;height:auto;font-family:var(--mono);}
  .axislbl{fill:var(--faint);font-size:12px;}
  .leglbl{fill:var(--ink);font-size:14px;font-weight:bold;}
  .axistitle{fill:var(--ink);font-size:13px;font-family:var(--head);font-weight:bold;}
  .gridln{stroke:var(--hair2);stroke-width:1;} .zeroln{stroke:var(--hair);stroke-width:1;stroke-dasharray:3 3;}
  .earnline{stroke:#b58900;stroke-width:1.3;stroke-dasharray:4 3;opacity:0.75;}
  .earntag{fill:#b58900;} .earntaglbl{fill:#fff;font-size:10px;font-weight:bold;}
  .earnmark{cursor:help;} .earnmark:hover .earnline{opacity:1;stroke-width:2;}
  h3{font-family:var(--head);font-size:18px;color:#000;margin:26px 0 6px;text-align:center;}
  .note{font-size:14px;color:var(--body);margin-top:10px;line-height:1.6;text-align:justify;}
  .lagbar.pos{fill:var(--pos);} .lagbar.neg{fill:var(--neg);}
  .caveat{background:#fff8e6;border:1px solid #f0e0b0;border-radius:6px;padding:11px 14px;font-size:14px;color:#7a6318;margin:12px 0;text-align:center;}
  footer{margin-top:40px;border-top:1px solid var(--hair);padding-top:18px;color:var(--faint);font-size:13px;line-height:1.6;text-align:center;}
  @media(max-width:760px){
    .controls{gap:10px;}
    .wrap{padding:0 12px 70px;}
    /* On phones a 1000-wide chart squashed into ~360px makes labels unreadable.
       Give the charts a minimum width and let the wrapper scroll horizontally, so
       the chart stays legible at full size instead of shrinking to nothing. */
    .chartwrap{width:100%;max-width:100%;}
    /* Charts scale to fit the phone; bump the in-SVG font sizes (which scale with the
       viewBox) so axis labels stay readable at small sizes instead of shrinking away. */
    .axislbl{font-size:19px;} .axistitle{font-size:21px;} .leglbl{font-size:22px;}
  }
</style>

<div class="wrap">
  <nav class="topbar">
    <a class="home" href="../index.html">← galavgay.com</a>
    <a class="home" href="../projects.html">Projects</a>
    <a class="home" href="proposal.html">Research proposal</a>
  </nav>

  <h1>Sentiment vs Price</h1>
  <!-- Pipeline freshness. Populated by updateFreshness() from D.freshness; hidden
       until then so a JS failure never shows a misleading "fresh" state. -->
  <div id="freshBar" class="freshbar" style="display:none;"></div>
  <p class="intro lead">This dashboard explores whether public sentiment about a stock, drawn from news and
     social media, anticipates, follows, or has no relationship to its price, following the methodology set
     out in the <a href="proposal.html">research proposal</a>.</p>
  <div class="howto">
    <p style="margin:0 0 8px;"><b>Reading the chart.</b> For the selected stock, the <b style="color:var(--price)">blue line</b>
       is the <b>price</b> (left axis, in dollars) and the <b style="color:var(--senti)">green line</b> is the
       <b>sentiment</b> (right axis), a smoothed trend with faint dots at each day's mentions. Grey bars at the bottom
       show how many mentions occurred each day. <b>Drag across the chart to zoom in;</b> once zoomed to intraday
       resolution the background shades by market session (pre-market, regular hours, after-hours, overnight, weekend,
       holiday), so you can see whether price moved before the regular session even opened.</p>
    <p style="margin:0;"><b>Sentiment runs from −1 to +1:</b> <span class="neg">−1</span> means the mentions were
       maximally <b>negative / bearish</b>, <span style="color:var(--neut,#9a7a12);font-weight:bold;">0</span> is neutral
       or mixed, and <span class="pos">+1</span> means maximally <b>positive / bullish</b>. The <b>lead/lag chart</b> below
       is the rigorous test: it measures whether sentiment moves <i>before</i> price (leads) or <i>after</i> it (reacts).</p>
  </div>

  <div class="controls">
    <div class="fld"><label for="tkSel">Stock</label><select id="tkSel"></select></div>
    <div class="fld"><label for="srcSel">Mention source</label>
      <select id="srcSel"><option value="both">Both</option><option value="social">Social media</option><option value="news">News</option></select></div>
    <div class="fld"><label for="catSel">Group / filter stocks by</label>
      <select id="catSel"><option value="all">All stocks</option><option value="sector">Sector</option><option value="mcap_bucket">Market cap</option><option value="source_favored">Source-favored</option></select></div>
    <div class="fld" id="catValWrap" style="display:none;"><label for="catVal">&nbsp;</label><select id="catVal"></select></div>
    <div class="fld" id="earnRegWrap" style="display:none;"><label for="earnReg">Earnings</label>
      <select id="earnReg"><option value="all">Show all</option><option value="ex_earn">Exclude earnings</option><option value="earn">Earnings only</option></select></div>
    <div class="fld"><label>&nbsp;</label><a id="mentionsBtn" class="mentionsbtn" href="#" target="_blank" rel="noopener">View mentions ↗</a></div>
  </div>

  <div class="caveat" id="caveat"></div>
  <div class="statrow" id="stats"></div>
  <div class="catrow" id="catline"></div>

  <div class="legend">
    <span><i style="background:var(--price)"></i> Price ($, left axis)</span>
    <span><i style="background:var(--senti)"></i> Sentiment (14-day smoothed trend; faint dots = daily means) (−1…+1, right axis)</span>
    <span><i style="background:var(--vol);height:8px;width:8px;border-radius:2px"></i> Mention volume</span>
    <span id="earnLegend" style="display:none;"><i style="background:#b58900;width:3px;height:16px;"></i> Earnings report (hover for beat/miss)</span>
  </div>
  <div class="zoombar">
    <span id="zoomHint" class="zoomhint">Tip: drag across the chart to zoom in (sessions shade when zoomed to intraday).</span>
    <button id="zoomReset" class="zoomreset" style="display:none;">Reset zoom</button>
  </div>
  <div class="legend sesslegend" id="sessLegend" style="display:none;"></div>
  <div class="chartwrap"><svg id="chart" viewBox="0 0 1000 380" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Sentiment and price over time"></svg></div>

  <h3>Lead / lag cross-correlation</h3>
  <p class="note">Correlation of decayed sentiment with price return at each day-offset. Bars <b>right of 0</b>
     (positive lag): sentiment <b>leads</b> price by that many days. Bars <b>left of 0</b>: sentiment
     <b>reacts to</b> earlier price moves. This is the rigorous correlation measure (the overlay above is
     for visual context only).</p>
  <div class="chartwrap"><svg id="lag" viewBox="0 0 1000 220" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Lead-lag cross correlation"></svg></div>

  <h3>Was the move already priced in after hours?</h3>
  <p class="note">This is the study's central question about timing. Using hourly prices, sentiment change is
     correlated against two versions of the price path across a range of <b>hour</b> offsets: the
     <b style="color:var(--price)">full path</b>, which includes pre-market and after-hours moves, and the
     <b style="color:var(--senti)">regular-hours-only path</b>, which is what a trader active only from 9:30 to 4:00
     would experience. If sentiment leads the regular path but only coincides with, or lags, the full path, the
     extended-hours market had likely absorbed it before the regular session opened, so a retail trader could not
     have acted on it in time. New to these plots, or seeing the two charts disagree?
     <a href="scenarios.html">See how to read them, with examples of each scenario.</a></p>
  <div class="sessverdict" id="sessVerdict"></div>
  <div class="chartwrap"><svg id="sesslag" viewBox="0 0 1000 240" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Session lead-lag comparison"></svg></div>

  <footer>Research prototype. Sentiment scored deterministically (Loughran-McDonald lexicon), no LLM.
     Correlations on limited data are illustrative, not statistically significant; they firm up as the
     mention store accumulates over weekly collection. See the <a href="proposal.html">research proposal</a>.</footer>
</div>

<script id="payload" type="application/json">/*DATA*/</script>
<script>
const D = JSON.parse(document.getElementById('payload').textContent);

// ---- pipeline freshness banner -------------------------------------------
// Surfaces staleness that used to be invisible: when the nightly run is killed
// before it publishes, the site keeps serving the last good page with no hint
// that it is old. Age is measured in ELAPSED DAYS since generation (the run is
// daily, so >1 day late means a run was missed), while the data dates shown are
// the real newest mention/price dates. Thresholds are deliberately forgiving of
// weekends: a Friday-generated page read on Sunday is 2 days old but fine.
function updateFreshness(){
  const f = D.freshness; const bar = document.getElementById('freshBar');
  if(!f || !bar || !f.gen) return;                 // no data -> stay hidden
  const ageH = (Date.now()/1000 - f.gen) / 3600;
  const ageD = ageH / 24;
  // Grace: the run is daily, but weekends have no new market data, so allow 3
  // days before warning and 5 before calling it broken.
  let cls, msg;
  if(ageD < 3)      { cls='ok';   msg='Data current'; }
  else if(ageD < 5) { cls='warn'; msg='Update overdue'; }
  else              { cls='bad';  msg='Pipeline may have stopped'; }
  // floor, not round: never overstate how old the data is (2.5 days reads "2 days")
  const nH=Math.floor(ageH), nD=Math.floor(ageD);
  const when = ageH < 1  ? 'less than an hour ago'
             : ageH < 24 ? nH+' hour'+(nH===1?'':'s')+' ago'
             :             nD+' day'+(nD===1?'':'s')+' ago';
  const bits = ['Last updated '+when];
  if(f.price_max)    bits.push('prices through '+f.price_max);
  if(f.mentions_max) bits.push('mentions through '+f.mentions_max);
  if(f.n_tickers)    bits.push(f.n_tickers+' stocks');
  let extra='';
  if(cls==='warn') extra=' — the daily run has not published in over 3 days.';
  if(cls==='bad')  extra=' — no successful update in over 5 days; the figures below are stale.';
  bar.className='freshbar '+cls;
  bar.innerHTML='<span class="dot"></span><span class="msg">'+msg+'</span>'
               +'<span class="detail">'+bits.join(' &middot; ')+extra+'</span>';
  bar.style.display='';
}
updateFreshness();
const A = D.analysis;
const NS = "http://www.w3.org/2000/svg";
function el(t,a){const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);return e;}
function txt(x,y,s,cls,anchor,extra){const e=el('text',Object.assign({x,y,class:cls||'axislbl','text-anchor':anchor||'start'},extra||{}));e.textContent=s;return e;}

const SESS_FULL={p:'pre-market',r:'regular',a:'after-hours',o:'overnight',w:'weekend',h:'holiday',
  pre_market:'pre-market',regular:'regular',after_hours:'after-hours',overnight:'overnight',weekend:'weekend',holiday:'holiday'};
function fmtDateT(t){ const d=new Date(t*1000);
  return MON[d.getUTCMonth()]+' '+d.getUTCDate()+' '+d.getUTCFullYear(); }
function earnHover(tk,ev){
  // Native SVG <title> tooltip text for an earnings marker: date, session,
  // beat/miss + surprise%, and estimated vs reported EPS.
  const when=fmtDateT(ev.t)+(ev.sess?(' ('+(SESS_FULL[ev.sess]||ev.sess)+')'):'');
  let line2;
  if(ev.surp==null||ev.rep==null){ line2='Upcoming / not yet reported'; }
  else{ const beat=ev.surp>=0; line2=(beat?'BEAT':'MISSED')+' by '+(ev.surp>0?'+':'')+ev.surp+'%'; }
  let line3='';
  if(ev.est!=null||ev.rep!=null){
    line3='EPS: est '+(ev.est==null?'?':ev.est)+' → reported '+(ev.rep==null?'?':ev.rep); }
  return tk+' earnings · '+when+'\n'+line2+(line3?('\n'+line3):'');
}

// ---- earnings tooltip (drawn, not native <title>) -------------------------
// Renders the earnHover text as a real SVG panel anchored to the marker. Native
// <title> was unreliable (first-child-only, unstyleable, never fired on the "E"
// tag); this is ordinary SVG we control and can test. One shared node per chart,
// created lazily and removed on mouseleave.
function hideEarnTip(){ const n=document.getElementById('earnTip'); if(n) n.remove(); }
function showEarnTip(svg,x,yTop,text){
  hideEarnTip();
  const lines=text.split('\n');
  const LH=15, PAD=8;
  const g=el('g',{id:'earnTip','pointer-events':'none'});
  const bg=el('rect',{rx:5,fill:'#2b2b2b','fill-opacity':0.95,stroke:'#000','stroke-opacity':0.25});
  g.appendChild(bg);
  const tnodes=lines.map((s,i)=>{
    const t=txt(0, 0, s, 'earntiptxt', 'start');
    t.setAttribute('font-size', i===0?'12.5':'12');
    t.setAttribute('font-weight', i===0?'bold':'normal');
    g.appendChild(t); return t;
  });
  svg.appendChild(g);                                 // last child => draws on top
  // Size from the ACTUAL rendered text (getComputedTextLength) rather than a
  // characters-times-width guess: ticker/EPS strings vary enough that an estimate
  // either clips the text or leaves a lopsided box.
  const w=Math.max(...tnodes.map(t=>t.getComputedTextLength())) + PAD*2;
  const h=lines.length*LH + PAD*2 - 3;
  // flip to the left of the marker when it would overflow the right edge
  let tx=x+12; if(tx+w > CW-4) tx=x-12-w; if(tx<4) tx=4;
  const ty=Math.max(4, yTop-4);
  bg.setAttribute('x',tx); bg.setAttribute('y',ty);
  bg.setAttribute('width',w); bg.setAttribute('height',h);
  tnodes.forEach((t,i)=>{ t.setAttribute('x',tx+PAD); t.setAttribute('y',ty+PAD+12+i*LH); });
}

const tkSel=document.getElementById('tkSel'), srcSel=document.getElementById('srcSel'),
      catSel=document.getElementById('catSel'), catVal=document.getElementById('catVal'),
      catValWrap=document.getElementById('catValWrap');

function catValue(tk,key){ if(key==='all')return 'all'; return A[tk][key]||'unknown'; }

function populateStocks(){
  const cat=catSel.value; const filter=catVal.value;
  let list=D.order.filter(tk=>{
    if(cat==='all')return true;
    return catValue(tk,cat)===filter;
  });
  tkSel.innerHTML='';
  list.forEach(tk=>{const o=document.createElement('option');o.value=tk;
    o.textContent=`${tk}  (${A[tk].n_mentions} mentions, ${A[tk].n_overlap}d)`;tkSel.appendChild(o);});
  if(list.length) render(list[0]);
}
function populateCatValues(){
  const cat=catSel.value;
  if(cat==='all'){catValWrap.style.display='none'; populateStocks(); return;}
  const vals=[...new Set(D.order.map(tk=>catValue(tk,cat)))].sort();
  catVal.innerHTML=''; vals.forEach(v=>{const o=document.createElement('option');o.value=v;o.textContent=v;catVal.appendChild(o);});
  catValWrap.style.display=''; populateStocks();
}
D.order.forEach(tk=>{const o=document.createElement('option');o.value=tk;
  o.textContent=`${tk}  (${A[tk].n_mentions} mentions, ${A[tk].n_overlap}d)`;tkSel.appendChild(o);});

function fmtLag(k){ if(k===null||k===undefined) return 'n/a'; if(k>0) return `leads ${k}d`; if(k<0) return `lags ${-k}d`; return 'same day'; }
const MON=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
function fmtDate(iso){ if(!iso)return ''; const p=iso.split('-'); return `${MON[+p[1]-1]} ${p[2]} ’${p[0].slice(2)}`; }  // e.g. "Aug 23 ’25"

// ---- TIME-AXIS chart with drag-to-zoom + session shading ----
// Everything is plotted against unix-seconds. `viewWin` = [minT,maxT] visible span.
const DAY=86400, HOUR=3600;
// two versions: BAND = light tint for chart background (subtle, keeps data readable);
// SWATCH = a stronger, clearly-distinct color for the legend so sessions are told apart.
const SESSION_COLORS={p:'#dbe7fb',r:'#ffffff',a:'#fde3cf',o:'#e7dcf5',w:'#e2e2e2',h:'#f6d5d5'};
const SESSION_SWATCH={p:'#4a80e0',r:'#c9ced8',a:'#e8863a',o:'#8a5cd0',w:'#8a8a8a',h:'#d15b5b'};
const SESSION_NAME={p:'pre-market',r:'regular',a:'after-hours',o:'overnight',w:'weekend',h:'holiday'};

// ---- client-side session classifier: mirrors market_session.py exactly ----
// Input is a unix-second timestamp; output is a single-char session code. The ONLY
// authoritative data it needs is D.nyse = { "YYYY-MM-DD": [open_unix, close_unix] }
// for every trading day (weekends/holidays are the dates absent from that map).
// ET calendar date + weekday come from Intl (America/New_York), so DST and the
// UTC->ET date rollover are handled correctly without shipping a tz database.
const _ET_DOW = new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',weekday:'short'});
const _ET_YMD = new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',
  year:'numeric',month:'2-digit',day:'2-digit'});   // en-CA => "YYYY-MM-DD"
const PRE_START=4*HOUR;      // 04:00 ET, seconds past ET midnight
const AH_END=20*HOUR;        // 20:00 ET
function _etDate(tsec){ return _ET_YMD.format(new Date(tsec*1000)); }  // "YYYY-MM-DD" in ET
function _etDow(tsec){ return _ET_DOW.format(new Date(tsec*1000)); }   // "Mon".."Sun"
function _prevDateStr(d){ const dt=new Date(d+'T12:00:00Z'); dt.setUTCDate(dt.getUTCDate()-1);
  return dt.toISOString().slice(0,10); }
function _isWeekend(d){ const w=_ET_DOW.format(new Date(d+'T16:00:00Z')); return w==='Sat'||w==='Sun'; }
function _closedKind(d){ return _isWeekend(d) ? 'w' : 'h'; }  // weekend vs weekday holiday
// seconds-past-ET-midnight for a timestamp (its offset from that ET calendar day's 00:00)
function _etSecondsOfDay(tsec){
  // find ET midnight of tsec's ET date by parsing its Y-M-D and reconstructing.
  const d=_etDate(tsec);
  // ET midnight in unix: the moment whose ET clock reads 00:00:00 on date d.
  // Approximate via the day's known open (09:30 ET) when available, else via
  // the UTC guess corrected by measuring the ET time of a probe.
  const sched=D.nyse[d];
  if(sched){ // open is 09:30 ET => ET-midnight = open - 9.5h (covers DST since open is real)
    return tsec - (sched[0] - 9.5*HOUR);
  }
  // no schedule (weekend/holiday): derive ET midnight by formatting the ET wall time.
  const parts=new Intl.DateTimeFormat('en-GB',{timeZone:'America/New_York',
    hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).formatToParts(new Date(tsec*1000));
  let h=0,m=0,s=0; for(const p of parts){ if(p.type==='hour')h=+p.value; if(p.type==='minute')m=+p.value; if(p.type==='second')s=+p.value; }
  if(h===24)h=0;
  return h*HOUR+m*60+s;
}
function sessionAt(tsec){
  const d=_etDate(tsec), sched=D.nyse[d];
  const secs=_etSecondsOfDay(tsec);
  const prev=_prevDateStr(d), prevTrading=!!D.nyse[prev];
  if(sched){
    // open/close expressed as seconds-past-ET-midnight for THIS date. ET midnight
    // is the day's open (09:30 ET) minus 9.5h, which stays correct across DST
    // because `open` is the calendar's real UTC open for that date.
    const etMid = sched[0] - 9.5*HOUR;             // unix of 00:00 ET on d
    const openSec = sched[0]-etMid, closeSec = sched[1]-etMid;
    if(secs>=openSec && secs<closeSec) return 'r';
    if(secs>=PRE_START && secs<openSec) return 'p';
    if(secs>=closeSec && secs<AH_END) return 'a';
    if(secs>=AH_END) return 'o';                    // 20:00–24:00 on a trading day
    // 00:00–04:00 on a trading day: overnight tail of prior trading evening, else
    // the weekend/holiday that just ended.
    return prevTrading ? 'o' : _closedKind(prev);
  }
  // not a trading day: 00:00–04:00 may be overnight tail of prior trading evening.
  if(secs<PRE_START && prevTrading) return 'o';
  return _closedKind(d);
}

let viewWin=null;           // current [minT,maxT] or null = full
let curTk=null, curScope='both';
const CW=1000,CH=380,cmL=60,cmR=66,cmT=18,cmB=46;

function dayToT(d){ return Math.floor(new Date(d+'T00:00:00Z').getTime()/1000); }

function fullExtent(a,S){
  // full time extent = the daily window we already clip to (idx_start..idx_end)
  const t0=dayToT(a.dates[S.idx_start]), t1=dayToT(a.dates[S.idx_end])+DAY;
  return [t0,t1];
}

function drawChart(a, scope){
  curTk=a.ticker; curScope=scope;
  // point the "View mentions" button at this ticker's raw-mentions table
  const mb=document.getElementById('mentionsBtn');
  if(mb){ mb.href='mentions.html?ticker='+encodeURIComponent(a.ticker); }
  const svg=document.getElementById('chart'); svg.innerHTML='';
  const S=a.scopes[scope];
  const ext=fullExtent(a,S);
  const win = viewWin || ext;
  const [tMin,tMax]=win, tSpan=Math.max(1,tMax-tMin);
  const yTop=cmT, yBot=CH-cmB;
  const X=t=> cmL + (CW-cmL-cmR)*((t-tMin)/tSpan);
  const inWin=t=> t>=tMin && t<=tMax;

  // decide intraday vs daily: use intraday hourly bars when zoomed to <= ~20 days
  const intr=D.intraday[a.ticker];
  const useIntraday = intr && tSpan <= 20*DAY;
  // shade sessions only when zoomed enough that an hour is >= ~3px wide
  const showSessions = useIntraday && ((CW-cmL-cmR)*(HOUR/tSpan) >= 2.2);

  // ---- price points in-window (intraday or daily) ----
  let pricePts=[];  // [{t, v}]
  if(useIntraday){
    for(let i=0;i<intr.t.length;i++){ const t=intr.t[i];
      if(t>=tMin-HOUR && t<=tMax+HOUR) pricePts.push({t, v:intr.p[i], s:intr.s[i]}); }
  } else {
    a.dates.forEach((d,i)=>{ if(a.price[i]==null)return; const t=dayToT(d)+ (13*HOUR);
      if(t>=tMin-DAY && t<=tMax+DAY) pricePts.push({t, v:a.price[i]}); });
  }
  const pvals=pricePts.map(o=>o.v);
  const pmin=Math.min(...pvals), pmax=Math.max(...pvals), pr=(pmax-pmin)||1;
  const yPrice=v=> yBot-(yBot-yTop)*((v-pmin)/pr);
  const yShift=v=> yBot-(yBot-yTop)*((v+1)/2);

  // ---- session shading bands (background) ----
  // Classify EVERY hour of the visible window by its own timestamp (via sessionAt,
  // which mirrors market_session.py), not just hours that carry a price bar. This
  // makes weekend / holiday / overnight regions shade as their own bands even where
  // there is no price data, and prevents a sparse pre-market band from bleeding
  // across empty hours. Consecutive same-session hours merge into one rectangle.
  if(showSessions && D.nyse && Object.keys(D.nyse).length){
    let bandStart=null, bandSess=null, bandEnd=null;
    const flush=()=>{ if(bandStart!==null && bandSess && bandSess!=='r'){
      const xs=Math.max(cmL,X(bandStart)), xe=Math.min(CW-cmR,X(bandEnd));
      if(xe>xs) svg.appendChild(el('rect',{x:xs,y:yTop,width:xe-xs,height:yBot-yTop,
        fill:SESSION_COLORS[bandSess]||'#fff'})); } };
    // step across the window on hour boundaries, aligned so bands snap to real
    // session edges. Cap the iteration count as a safety net (<=20d => <=480 hrs).
    const start=Math.floor(tMin/HOUR)*HOUR;
    for(let t=start; t<tMax; t+=HOUR){
      const s=sessionAt(t+1);            // classify mid-hour to avoid boundary ties
      const hEnd=t+HOUR;
      if(bandStart===null){ bandSess=s; bandStart=t; bandEnd=hEnd; }
      else if(s===bandSess){ bandEnd=hEnd; }
      else { flush(); bandSess=s; bandStart=t; bandEnd=hEnd; }
    }
    flush();
  }

  // gridlines + price labels
  [pmin,(pmin+pmax)/2,pmax].forEach(v=>{ const yy=yPrice(v);
    svg.appendChild(el('line',{x1:cmL,y1:yy,x2:CW-cmR,y2:yy,class:'gridln'}));
    svg.appendChild(txt(cmL-8,yy+4,'$'+v.toFixed(v<10?2:0),'axislbl','end')); });
  [-1,-0.5,0,0.5,1].forEach(v=>{ const yy=yShift(v);
    if(v===0) svg.appendChild(el('line',{x1:cmL,y1:yy,x2:CW-cmR,y2:yy,class:'zeroln'}));
    svg.appendChild(txt(CW-cmR+8,yy+4,v.toFixed(1),'axislbl','start')); });

  // mention-volume bars (daily) within window
  const vmax=Math.max(1,...S.mention_count);
  S.mention_count.forEach((c,i)=>{ if(c<=0)return; const t=dayToT(a.dates[i])+13*HOUR;
    if(!inWin(t))return; const h=(yBot-yTop)*0.15*c/vmax;
    svg.appendChild(el('rect',{x:X(t)-1.3,y:yBot-h,width:2.6,height:h,fill:'var(--vol)'})); });

  // ---- earnings markers (vertical dashed lines + hover) ----
  // Drawn UNDER the price/sentiment lines so they read as background context. Each
  // earnings event in-window gets a dashed vertical rule and an "E" tag; a native
  // <title> gives the beat/miss + surprise% + EPS hover.
  const earn=(D.earnings||{})[a.ticker]||[];
  earn.forEach(ev=>{ if(!inWin(ev.t)) return;
    const x=X(ev.t);
    const g=el('g',{class:'earnmark'});
    // NOTE: this deliberately does NOT use a native SVG <title>. That is the obvious
    // way to do a tooltip, but it proved unreliable here — a <title> is only honoured
    // when it is the first child of its parent, it cannot be styled or positioned,
    // and it never appeared on the "E" tag readers actually aim at. We draw our own
    // tooltip instead (showEarnTip below): it is real SVG we control, so it renders
    // identically everywhere and can be verified.
    g.appendChild(el('line',{x1:x,y1:yTop,x2:x,y2:yBot,class:'earnline'}));
    // small "E" tag at the top of the line
    g.appendChild(el('rect',{x:x-6,y:yTop-1,width:12,height:13,rx:2,class:'earntag'}));
    const et=txt(x,yTop+9,'E','earntaglbl','middle'); g.appendChild(et);
    // wide invisible hit target so the thin dashed line is not a pixel-hunt; it spans
    // the full height INCLUDING the tag, so tag and line both trigger the tooltip.
    const hit=el('rect',{x:x-9,y:yTop-3,width:18,height:(yBot-yTop)+6,fill:'transparent'});
    hit.style.cursor='pointer';
    hit.addEventListener('mouseenter',()=>showEarnTip(svg,x,yTop,earnHover(a.ticker,ev)));
    hit.addEventListener('mouseleave',hideEarnTip);
    g.appendChild(hit);
    svg.appendChild(g);
  });

  // price path
  let dp='',st=false;
  pricePts.forEach(o=>{ dp+=(st?'L':'M')+X(o.t)+' '+yPrice(o.v)+' '; st=true; });
  svg.appendChild(el('path',{d:dp,fill:'none',stroke:'var(--price)','stroke-width':2,'stroke-linejoin':'round'}));

  // sentiment (smoothed decayed EWMA) on daily grid within window.
  // Build the full list of non-null points, then draw from the last point BEFORE
  // the window through the first point AFTER it — so the segments that cross the
  // window edges are drawn and the line spans the full visible range (mirrors the
  // price path's ±margin). A hard inWin filter here would drop those crossing
  // segments and leave the (daily-sampled) line stopping short inside the window.
  const sentPts=[];
  S.sentiment_decayed.forEach((v,i)=>{ if(v==null)return;
    sentPts.push({t:dayToT(a.dates[i])+13*HOUR, v}); });
  // index range that brackets the window: keep one point beyond each edge
  let sLo=0, sHi=sentPts.length-1;
  for(let i=0;i<sentPts.length;i++){ if(sentPts[i].t<=tMin) sLo=i; if(sentPts[i].t>=tMax){ sHi=i; break; } }
  let ds='',st2=false;
  for(let i=sLo;i<=sHi && i<sentPts.length;i++){
    ds+=(st2?'L':'M')+X(sentPts[i].t)+' '+yShift(sentPts[i].v)+' '; st2=true; }
  svg.appendChild(el('path',{d:ds,fill:'none',stroke:'var(--senti)','stroke-width':2.4,'stroke-linejoin':'round','stroke-linecap':'round'}));
  // daily sentiment-level dots: keep the in-window filter (dots outside the window
  // would sit in the margins and look like stray marks).
  S.sentiment_level.forEach((v,i)=>{ if(v==null)return; const t=dayToT(a.dates[i])+13*HOUR;
    if(!inWin(t))return; svg.appendChild(el('circle',{cx:X(t),cy:yShift(v),r:2.4,fill:'var(--senti)','fill-opacity':0.35})); });

  // axis titles
  svg.appendChild(txt(16,(yTop+yBot)/2,'Price ($)','axistitle','middle',{transform:`rotate(-90,16,${(yTop+yBot)/2})`,fill:'var(--price)'}));
  svg.appendChild(txt(CW-14,(yTop+yBot)/2,'Sentiment (−1 to +1)','axistitle','middle',{transform:`rotate(90,${CW-14},${(yTop+yBot)/2})`,fill:'var(--senti)'}));
  svg.appendChild(txt(CW/2,CH-6,'Date','axistitle','middle'));

  // x tick labels (date, and time-of-day when intraday)
  const nTicks=4;
  for(let k=0;k<=nTicks;k++){ const t=tMin+tSpan*k/nTicks;
    const d=new Date(t*1000);
    const lbl = useIntraday
      ? d.toLocaleDateString('en-US',{month:'short',day:'numeric',timeZone:'America/New_York'})+' '+d.toLocaleTimeString('en-US',{hour:'numeric',timeZone:'America/New_York'})
      : fmtDate(d.toISOString().slice(0,10));
    svg.appendChild(txt(X(t),CH-28,lbl,'axislbl','middle')); }

  // session legend (only when shading is shown). Use the STRONG swatch colors so the
  // sessions are clearly distinguishable, even though the chart bands are lighter.
  const leg=document.getElementById('sessLegend');
  if(showSessions){ leg.style.display='flex';
    leg.innerHTML=['p','r','a','o','w','h'].map(c=>
      `<span><i style="background:${SESSION_SWATCH[c]};border:1px solid rgba(0,0,0,0.25)"></i>${SESSION_NAME[c]}</span>`).join(''); }
  else leg.style.display='none';

  // zoom hint / reset button state
  document.getElementById('zoomReset').style.display = viewWin ? 'inline-block' : 'none';
  document.getElementById('zoomHint').textContent = viewWin
    ? (showSessions? 'Zoomed in — session shading on. Drag to zoom further, or Reset.' : 'Drag to zoom further, or Reset.')
    : 'Tip: drag across the chart to zoom in (sessions shade when zoomed to intraday).';

  setupZoom(svg, X, tMin, tMax, ext);
}

// drag-to-zoom overlay
function setupZoom(svg, X, tMin, tMax, ext){
  const yTop=cmT, yBot=CH-cmB;
  let sx=null, rect=null;
  const invX=px=>{ const frac=(px-cmL)/(CW-cmL-cmR); return tMin+frac*(tMax-tMin); };
  const pt=evt=>{ const r=svg.getBoundingClientRect();
    return (evt.clientX-r.left)/r.width*CW; };
  svg.style.cursor='crosshair';
  svg.onmousedown=e=>{ sx=pt(e);
    rect=el('rect',{x:sx,y:yTop,width:0,height:yBot-yTop,fill:'#3b5bdb','fill-opacity':0.12,stroke:'#3b5bdb','stroke-dasharray':'3 2'});
    svg.appendChild(rect); };
  svg.onmousemove=e=>{ if(sx===null)return; const cx=pt(e);
    const x0=Math.min(sx,cx), w=Math.abs(cx-sx);
    rect.setAttribute('x',x0); rect.setAttribute('width',w); };
  svg.onmouseup=e=>{ if(sx===null)return; const cx=pt(e);
    const a=invX(Math.min(sx,cx)), b=invX(Math.max(sx,cx)); sx=null;
    if(rect){rect.remove();rect=null;}
    if(b-a >= HOUR){ viewWin=[Math.max(a,ext[0]),Math.min(b,ext[1])]; redraw(); } };
  svg.onmouseleave=()=>{ if(rect){rect.remove();rect=null;} sx=null; };
}
function redraw(){ if(curTk) drawChart(A[curTk], curScope); }

function drawLag(a, scope){
  const svg=document.getElementById('lag'); svg.innerHTML='';
  const W=1000,H=220,mL=52,mR=18,mT=20,mB=44;
  const xc=a.scopes[scope].xcorr_level||{};
  const cx=k=> mL + (W-mL-mR)*((k+7)/14);
  const y0=(H-mB+mT)/2, yS=v=> y0 - (H/2-mT-8)*v;
  svg.appendChild(el('line',{x1:mL,y1:y0,x2:W-mR,y2:y0,stroke:'var(--hair)','stroke-width':1}));
  [-1,-0.5,0.5,1].forEach(v=>{ const yy=yS(v);
    svg.appendChild(el('line',{x1:mL,y1:yy,x2:W-mR,y2:yy,class:'gridln'}));
    svg.appendChild(txt(mL-5,yy+3,v.toFixed(1),'axislbl','end')); });
  const bw=(W-mL-mR)/14*0.55;
  for(let k=-7;k<=7;k++){ const c=xc[k]?xc[k].corr:0; const yy=yS(c); const h=Math.abs(y0-yy);
    svg.appendChild(el('rect',{x:cx(k)-bw/2,y:Math.min(y0,yy),width:bw,height:h||0.6,class:'lagbar '+(c>=0?'pos':'neg'),rx:2}));
    svg.appendChild(txt(cx(k),H-24,(k>0?'+':'')+k,'axislbl','middle')); }
  // axis titles
  svg.appendChild(txt(12,(y0),'Correlation','axistitle','middle',{transform:`rotate(-90,12,${y0})`}));
  svg.appendChild(txt(W/2,H-6,'Offset in days (sentiment vs future price return)','axistitle','middle'));
  svg.appendChild(txt(mL,12,'← sentiment reacts to price','axislbl','start'));
  svg.appendChild(txt(W-mR,12,'sentiment leads price →','axislbl','end'));
}

// ---- Session lead/lag: full path vs regular-hours-only, hour offsets ----
function drawSessionLag(tk){
  const svg=document.getElementById('sesslag'); svg.innerHTML='';
  const vbox=document.getElementById('sessVerdict');
  const S=(D.session||{})[tk];
  if(!S || (!S.full_best_corr && !S.regular_best_corr)){
    vbox.style.display='none';
    svg.parentElement.style.display='none';
    return;
  }
  svg.parentElement.style.display='';
  // EARNINGS REGIME: the selected regime re-slices the FULL price path (earnings
  // contaminate price, so only the full path changes; regular-hours path is shown
  // for reference). regimeData holds the all/ex_earn/earn xcorr + best lag/corr.
  const regime=(document.getElementById('earnReg')||{}).value||'all';
  const R=(S.regime||{})[regime]||null;
  // full-path xcorr + best point come from the regime slice when available
  const fullXc = R ? (R.xcorr||{}) : (S.xcorr_full||{});
  const fullBestLag = R ? R.best_lag_h : S.full_best_lag_h;
  const fullBestCorr = R ? R.best_corr : S.full_best_corr;
  // does the chosen regime actually have data? (earnings-only is often empty on
  // limited intraday history — we say so honestly rather than draw nothing.)
  const regimeEmpty = (regime!=='all') && (!R || fullBestCorr==null || Object.keys(fullXc).length===0);
  const W=1000,H=240,mL=54,mR=18,mT=24,mB=46, LAG=24;
  const cx=k=> mL + (W-mL-mR)*((k+LAG)/(2*LAG));
  const y0=(H-mB+mT)/2, yS=v=> y0 - (H/2-mT-10)*v;
  // axes
  svg.appendChild(el('line',{x1:mL,y1:y0,x2:W-mR,y2:y0,stroke:'var(--hair)','stroke-width':1}));
  [-1,-0.5,0.5,1].forEach(v=>{ const yy=yS(v);
    svg.appendChild(el('line',{x1:mL,y1:yy,x2:W-mR,y2:yy,class:'gridln'}));
    svg.appendChild(txt(mL-5,yy+3,v.toFixed(1),'axislbl','end')); });
  // zero-offset vertical guide
  svg.appendChild(el('line',{x1:cx(0),y1:mT,x2:cx(0),y2:H-mB,class:'zeroln'}));
  // draw a correlation curve as a line
  const curve=(xc,color)=>{ let d='',st=false;
    for(let k=-LAG;k<=LAG;k++){ const o=xc[String(k)]; if(!o)continue;
      d+=(st?'L':'M')+cx(k)+' '+yS(o.corr)+' '; st=true; }
    if(d) svg.appendChild(el('path',{d,fill:'none',stroke:color,'stroke-width':2.2,'stroke-linejoin':'round'})); };
  curve(fullXc, 'var(--price)');
  curve(S.xcorr_regular||{}, 'var(--senti)');
  // mark the best-lag points
  const mark=(lag,corr,color)=>{ if(lag==null||corr==null)return;
    svg.appendChild(el('circle',{cx:cx(lag),cy:yS(corr),r:4,fill:color})); };
  mark(fullBestLag,fullBestCorr,'var(--price)');
  mark(S.regular_best_lag_h,S.regular_best_corr,'var(--senti)');
  // honest "accumulating" note when the chosen earnings regime has no data yet
  if(regimeEmpty){
    const msg = regime==='earn'
      ? 'Not enough earnings-window data yet to plot the earnings-only regime — accumulating as intraday history deepens.'
      : 'No earnings windows overlap this stock’s intraday data yet.';
    svg.appendChild(txt(W/2,H/2,msg,'axislbl','middle',{fill:'var(--faint)'}));
  }
  // x ticks (hours)
  [-24,-12,0,12,24].forEach(k=>{ svg.appendChild(txt(cx(k),H-24,(k>0?'+':'')+k+'h','axislbl','middle')); });
  svg.appendChild(txt(12,y0,'Correlation','axistitle','middle',{transform:`rotate(-90,12,${y0})`}));
  svg.appendChild(txt(W/2,H-6,'Offset in hours (sentiment vs future price return)','axistitle','middle'));
  svg.appendChild(txt(mL,14,'← sentiment reacts to price','axislbl','start'));
  svg.appendChild(txt(W-mR,14,'sentiment leads price →','axislbl','end'));
  // inline legend (larger swatches + text)
  svg.appendChild(el('rect',{x:W/2-210,y:3,width:26,height:7,rx:2,fill:'var(--price)'}));
  svg.appendChild(txt(W/2-178,12,'full path (incl. extended hours)','leglbl','start'));
  svg.appendChild(el('rect',{x:W/2+40,y:3,width:26,height:7,rx:2,fill:'var(--senti)'}));
  svg.appendChild(txt(W/2+72,12,'regular hours only','leglbl','start'));

  // ---- plain-language verdict ----
  vbox.style.display='';
  const fl=fullBestLag, fc=fullBestCorr, rl=S.regular_best_lag_h, rc=S.regular_best_corr;
  const regLabel = regime==='ex_earn' ? ' (earnings windows excluded)'
                 : regime==='earn' ? ' (earnings windows only)' : '';
  const fmtH=(l,c)=>{ if(l==null||c==null)return 'no clear relationship';
    const dir=l>0?`leads by ${l}h`:l<0?`lags by ${-l}h`:'is coincident';
    const str=Math.abs(c)>=0.5?'strong':Math.abs(c)>=0.3?'moderate':'weak';
    return `${dir} (${str}, r=${c>=0?'+':''}${c.toFixed(2)})`; };
  let interp='', cls='coincide-tag';
  if(rl!=null && fl!=null){
    if(rl>0 && fl<=0){ interp='Sentiment appears to lead the regular-hours price, but not the full path, which suggests the move was largely <b>priced in during extended hours</b> before the regular session opened. A retail trader active only in regular hours would likely have been <span class="tag lag-tag">too late</span>.'; cls='lag-tag'; }
    else if(rl>0 && fl>0){ interp='Sentiment appears to lead <b>both</b> the regular and full price paths, which is the pattern most consistent with a <span class="tag lead-tag">genuinely anticipatory</span>, potentially actionable signal.'; cls='lead-tag'; }
    else if(rl<=0){ interp='Sentiment does not clearly lead the regular-hours price; it looks more <span class="tag lag-tag">reactive</span> than predictive here.'; cls='lag-tag'; }
    else { interp='The relationship is mixed and does not cleanly indicate lead or lag.'; }
  } else { interp='Not enough overlapping hourly data yet to judge the extended-hours question for this stock; it will sharpen as intraday data accumulates.'; }

  // earnings regime context
  const nEv=S.n_earnings_events||0, nEh=S.n_earnings_price_hours||0;
  if(regimeEmpty){
    const en = regime==='earn'
      ? `This stock has ${nEv} known earnings date${nEv===1?'':'s'}, but too few fall inside its intraday window to measure yet (accumulating).`
      : `No earnings windows overlap the intraday data yet.`;
    vbox.innerHTML=`<b>Earnings regime${regLabel}.</b> ${en} `
      +`<span style="color:var(--faint);font-size:13px;">The control is in place and will produce a comparison as intraday history deepens.</span>`;
    return;
  }
  let earnNote='';
  if(nEv){ earnNote=` <span style="color:var(--faint);font-size:13px;">`
    +(regime==='all'
        ? `This stock has ${nEv} known earnings date${nEv===1?'':'s'}; ${nEh} price-hour${nEh===1?'':'s'} fall in an earnings window — use the Earnings control to exclude or isolate them.`
        : `Computed on the ${regime==='earn'?'earnings-only':'earnings-excluded'} slice (${nEh} earnings price-hour${nEh===1?'':'s'}).`)
    +`</span>`; }

  vbox.innerHTML=`Against the <b style="color:var(--price)">full price path</b>${regLabel}, sentiment ${fmtH(fl,fc)}. `
    +`Against the <b style="color:var(--senti)">regular-hours-only path</b>, it ${fmtH(rl,rc)}. `
    +`<span class="${cls}">${interp}</span>`
    +earnNote+` `
    +`<span style="color:var(--faint);font-size:13px;">(Based on ${S.n_sent_hours||0} sentiment-hours; illustrative until more intraday data accumulates.)</span>`;
}

function drawStats(a, scope){
  const S=a.scopes[scope];
  const c=(S.best_level_corr??0);
  const strength=Math.abs(c)>=0.5?'strong':Math.abs(c)>=0.3?'moderate':'weak';
  const ll=S.best_level_lag>0?'lead':'lag';
  document.getElementById('stats').innerHTML=`
    <div class="stat"><div class="k">Best correlation</div><div class="v">${c>=0?'+':''}${c.toFixed(2)}</div><small>${strength} · ${scope}</small></div>
    <div class="stat"><div class="k">Timing</div><div class="v ${ll}">${fmtLag(S.best_level_lag)}</div><small>at peak correlation</small></div>
    <div class="stat"><div class="k">Mentions (${scope})</div><div class="v">${S.n_mentions}</div><small>social ${a.social_mentions} · news ${a.news_mentions}</small></div>
    <div class="stat"><div class="k">Overlap</div><div class="v">${S.n_overlap} days</div><small>sentiment ∩ price</small></div>`;
  document.getElementById('catline').innerHTML=
    `Category: <b>${a.sector}</b> · <b>${a.industry}</b> · <b>${a.mcap_bucket}-cap</b> · leans <b>${a.source_favored}</b>`;
  const cav=document.getElementById('caveat');
  if(S.n_overlap<20){cav.style.display='block';
    cav.innerHTML=`⚠ Only ${S.n_overlap} overlapping days for “${scope}”: correlation is <b>illustrative, not significant</b>; it firms up as mentions accumulate.`;}
  else cav.style.display='none';
}

function render(tk){ tkSel.value=tk; const a=A[tk]; const scope=srcSel.value;
  viewWin=null;  // reset zoom when switching stock/source
  const useScope=(a.scopes[scope].n_mentions>0)?scope:'both';
  // show the earnings controls/legend only for tickers that actually have earnings
  const hasEarn=!!((D.earnings||{})[tk] && D.earnings[tk].length);
  const el1=document.getElementById('earnRegWrap'); if(el1) el1.style.display=hasEarn?'':'none';
  const el2=document.getElementById('earnLegend'); if(el2) el2.style.display=hasEarn?'inline-flex':'none';
  drawStats(a,useScope); drawChart(a,useScope); drawLag(a,useScope); drawSessionLag(tk); }

tkSel.addEventListener('change',()=>render(tkSel.value));
srcSel.addEventListener('change',()=>render(tkSel.value));
catSel.addEventListener('change',populateCatValues);
catVal.addEventListener('change',populateStocks);
const earnRegSel=document.getElementById('earnReg');
if(earnRegSel) earnRegSel.addEventListener('change',()=>drawSessionLag(tkSel.value));
document.getElementById('zoomReset').addEventListener('click',()=>{ viewWin=null; redraw(); });
render(D.order[0]);
</script>
"""

if __name__ == "__main__":
    main()

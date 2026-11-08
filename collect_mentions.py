"""
Incremental, deduplicated mention collector (no LLM).

A 'mention' = one dated occurrence of a ticker in a source, with:
  id      : stable unique id = source + native post id  (dedup key)
  ticker  : the symbol
  date    : ISO timestamp of the post/article
  source  : e.g. 'stocktwits', 'news:yahoo'
  blurb   : ~20 words around the ticker (merged if overlapping)
  score   : Loughran-McDonald sentiment in [-1,1] (computed at ingest)
  pos,neg : term counts
  st_tag  : native bull/bear label when the source provides one (StockTwits)

Storage: one JSONL file per ticker at data/mentions/<TICKER>.jsonl (append-only).
On each run we load existing ids for a ticker and SKIP anything already stored,
so repeated scrapes never double-count.

Usage: python3 collect_mentions.py TICKER [TICKER ...]
       python3 collect_mentions.py --from-views   (use tickers in views.json)
"""
import json
import os
import re
import sys
import time
import hashlib

from curl_cffi import requests as cffi
import feedparser
from email.utils import parsedate_to_datetime
import datetime

from lm_sentiment import score_text
from market_session import session_of
import entity_scope as escope


def norm_date(s):
    """Normalize any source date to ISO 8601 UTC 'YYYY-MM-DDTHH:MM:SSZ'; None if unparseable."""
    if not s:
        return None
    s = s.strip()
    # already ISO?
    try:
        d = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        pass
    # RFC 2822 (RSS: 'Sat, 11 Jul 2026 06:00:00 +0000')
    try:
        d = parsedate_to_datetime(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=datetime.timezone.utc)
        return d.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None

DATA = "data"
MDIR = f"{DATA}/mentions"
os.makedirs(MDIR, exist_ok=True)

SESSION = cffi.Session(impersonate="chrome")
WORDS_AROUND = 20

# --- company-name cache (for entity-scoped relevance + scoring) ---
# Loaded once; a ticker missing from the cache is fetched from yfinance and the
# result (including a negative "" so we don't refetch every run) is persisted.
_NAMES = escope.load_names()
_NAMES_DIRTY = False


def company_name(ticker):
    """Cached core company name for a ticker ('' if none/unresolvable)."""
    global _NAMES_DIRTY
    key = ticker.upper().replace(".TO", "")
    if key in _NAMES:
        return _NAMES[key]
    name = escope.fetch_name(ticker) or ""
    _NAMES[key] = name
    _NAMES_DIRTY = True
    return name


def flush_names():
    """Persist the names cache if it changed this run."""
    global _NAMES_DIRTY
    if _NAMES_DIRTY:
        escope.save_names(_NAMES)
        _NAMES_DIRTY = False


def store_path(ticker):
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", ticker.upper())
    return f"{MDIR}/{safe}.jsonl"


def load_existing_ids(ticker):
    p = store_path(ticker)
    ids = set()
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                ids.add(json.loads(line)["id"])
            except Exception:
                pass
    return ids


def append_mentions(ticker, rows):
    if not rows:
        return
    with open(store_path(ticker), "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def mk_id(source, native):
    return f"{source}:{hashlib.sha1(str(native).encode()).hexdigest()[:16]}"


def extract_blurbs(text, ticker):
    """~20 words around each ticker mention; merge overlapping windows."""
    if not text:
        return None
    words = text.split()
    up = ticker.upper().lstrip("$")
    idxs = [i for i, w in enumerate(words)
            if re.sub(r"[^A-Za-z]", "", w).upper() == up]
    if not idxs:
        # ticker not literally in text (e.g. came from a symbol feed) -> use whole text
        return " ".join(words[:60])
    spans = []
    for i in idxs:
        spans.append((max(0, i - WORDS_AROUND), min(len(words), i + WORDS_AROUND + 1)))
    # merge overlapping spans
    spans.sort()
    merged = [spans[0]]
    for s, e in spans[1:]:
        if s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return " … ".join(" ".join(words[s:e]) for s, e in merged)


# ---------------- StockTwits ----------------
# Gap-aware uniform sampling. StockTwits only returns the newest ~30 msgs/page, so a
# naive "keep the newest N" biases high-volume stocks toward whatever was hot right
# before each run, MISSING the middle of the gap between runs. Instead we page BACK
# until we cover the whole gap (reach the newest mention we already stored), keeping
# at most ST_PER_HOUR msgs per hourly UTC bin. This makes the sample TIME-representative
# rather than recency-weighted: busy hours are capped, quiet/overnight hours keep their
# genuinely-few messages (empty bins are real signal, not missing data).
import datetime as _dt

ST_PER_HOUR = 20            # max messages kept per hourly bin (caps high-volume hours)
ST_MAX_PAGES = 40           # safety backstop: don't page a single ticker forever
ST_FIRST_RUN_DAYS = 30      # if we have no prior mentions, cover this far back at most
ST_HARD_FLOOR_DAYS = 400    # never keep anything older than this


def _st_history(ticker):
    """Return (newest_datetime, per_hour_counts) for already-stored StockTwits
    mentions. newest marks where the gap starts; per_hour_counts lets us enforce the
    hourly cap against the TOTAL stored (not just this run's additions)."""
    p = store_path(ticker)
    newest = None
    per_hour = {}
    if not os.path.exists(p):
        return newest, per_hour
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("source") != "stocktwits" or not r.get("date"):
            continue
        try:
            d = _dt.datetime.fromisoformat(r["date"].replace("Z", "+00:00"))
        except Exception:
            continue
        if newest is None or d > newest:
            newest = d
        hk = d.strftime("%Y-%m-%dT%H")
        per_hour[hk] = per_hour.get(hk, 0) + 1
    return newest, per_hour


def collect_stocktwits(ticker, seen):
    base = ticker.upper().replace(".TO", "")
    now = _dt.datetime.now(_dt.timezone.utc)
    # Gap start = newest mention we already have; seed per-hour counts from what's
    # already stored so the hourly cap applies to the TOTAL, not just this run.
    gap_start, per_hour = _st_history(ticker)
    if gap_start is None:
        gap_start = now - _dt.timedelta(days=ST_FIRST_RUN_DAYS)
    hard_floor = now - _dt.timedelta(days=ST_HARD_FLOOR_DAYS)
    out = []
    max_id = None
    pages = 0
    try:
        while pages < ST_MAX_PAGES:
            url = f"https://api.stocktwits.com/api/2/streams/symbol/{base}.json"
            if max_id:
                url += f"?max={max_id}"
            resp = SESSION.get(url, timeout=20)
            if resp.status_code != 200:
                return out, (f"http {resp.status_code}" if pages == 0 else "ok")
            data = resp.json()
            msgs = data.get("messages", [])
            if not msgs:
                break
            pages += 1
            oldest_dt = None
            for m in msgs:
                created = m.get("created_at")
                try:
                    d = _dt.datetime.fromisoformat((created or "").replace("Z", "+00:00"))
                except Exception:
                    continue
                if oldest_dt is None or d < oldest_dt:
                    oldest_dt = d
                if d < hard_floor:
                    continue
                mid = mk_id("stocktwits", m.get("id"))
                if mid in seen:
                    continue
                # uniform hourly cap: keep at most ST_PER_HOUR per hour bin
                hour_key = d.strftime("%Y-%m-%dT%H")
                if per_hour.get(hour_key, 0) >= ST_PER_HOUR:
                    continue
                per_hour[hour_key] = per_hour.get(hour_key, 0) + 1
                seen.add(mid)
                body = m.get("body", "") or ""
                blurb = extract_blurbs(body, ticker) or body
                sc = score_text(blurb)
                ent = (m.get("entities") or {}).get("sentiment") or {}
                iso = norm_date(created)
                out.append({
                    "id": mid, "ticker": base, "date": iso,
                    "source": "stocktwits", "blurb": blurb[:500],
                    "score": sc["score"], "pos": sc["pos"], "neg": sc["neg"],
                    "st_tag": ent.get("basic"),
                    "session": session_of(iso) if iso else None,
                })
            # STOP when we've paged back to cover the whole gap (reached gap_start),
            # NOT when bins are full — overnight bins can never fill.
            cur = data.get("cursor") or {}
            if oldest_dt is not None and oldest_dt <= gap_start:
                break
            if oldest_dt is not None and oldest_dt < hard_floor:
                break
            if not cur.get("more"):
                break
            max_id = cur.get("max")
            if not max_id:
                break
            time.sleep(0.3)  # gentle between pages
        status = "ok" if pages < ST_MAX_PAGES else f"ok(capped {pages}p)"
        return out, status
    except Exception as e:
        return out, f"err {str(e)[:60]}"


# ---------------- News RSS (Yahoo Finance) ----------------
def collect_news(ticker, seen, name=""):
    out = []
    dropped = 0
    base = ticker.upper().replace(".TO", "")
    url = (f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={base}"
           "&region=US&lang=en-US")
    try:
        feed = feedparser.parse(url)
        for e in feed.entries:
            link = e.get("link", "")
            native = link or e.get("id", "") or e.get("title", "")
            mid = mk_id("news", native)
            if mid in seen:
                continue
            seen.add(mid)
            title = e.get("title", "") or ""
            summ = re.sub("<[^>]+>", "", e.get("summary", "") or "")
            text = f"{title}. {summ}"
            # RELEVANCE + ATTRIBUTION: keep only the sentences of title+summary that
            # actually name this ticker or its company. Yahoo's RSS returns "related"
            # articles (an eBay story listed under WMT); if NO sentence names the
            # target, it isn't a real mention -> drop it. We then score L-M over ONLY
            # the entity sentences, so a co-mentioned company's tone (e.g. Netflix in
            # a "Netflix to buy Warner Bros" piece) can't leak onto the target.
            sents = escope.entity_sentences(text, base, name)
            if not sents:
                dropped += 1
                continue
            scoped = " ".join(sents)
            sc = score_text(scoped)
            date = e.get("published", "") or e.get("updated", "")
            iso = norm_date(date)
            out.append({
                "id": mid, "ticker": base, "date": iso,
                "source": "news:yahoo", "blurb": scoped[:500],
                "score": sc["score"], "pos": sc["pos"], "neg": sc["neg"],
                "st_tag": None,
                "session": session_of(iso) if iso else None,
                "matched_on": escope.matched_on(scoped, base, name),
            })
        return out, f"ok (dropped {dropped} off-topic)" if dropped else "ok"
    except Exception as e:
        return out, f"err {str(e)[:60]}"


# ---------------- Reddit (drop-in; requires reddit_creds.json) ----------------
def collect_reddit(ticker, seen):
    """Enabled only if reddit_creds.json exists (OAuth client-credentials)."""
    if not os.path.exists("reddit_creds.json"):
        return [], "skipped (no creds)"
    # Implementation intentionally deferred until creds available; returns empty.
    return [], "creds present but collector not yet wired"


def collect_ticker(ticker):
    seen = load_existing_ids(ticker)
    before = len(seen)
    cname = company_name(ticker)   # cached; used for entity-scoped relevance
    all_new = []
    # news is entity-scoped (needs the company name); the others take (ticker, seen).
    st_rows, _ = collect_stocktwits(ticker, seen); all_new.extend(st_rows); time.sleep(0.4)
    news_rows, _ = collect_news(ticker, seen, cname); all_new.extend(news_rows); time.sleep(0.4)
    rd_rows, _ = collect_reddit(ticker, seen); all_new.extend(rd_rows); time.sleep(0.4)
    append_mentions(ticker, all_new)
    return len(all_new), before + len(all_new)


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    if args[0] == "--from-universe":
        u = json.load(open(f"{DATA}/universe_tracked.json"))
        tickers = u["tickers"]
    elif args[0] == "--from-views":
        v = json.load(open(f"{DATA}/views.json"))
        tickers = sorted({it["ticker"].replace(".TO", "")
                          for view in v["views"].values() for it in view["items"]})
    else:
        tickers = [a.upper() for a in args]

    total_new = 0
    for i, tk in enumerate(tickers, 1):
        n, tot = collect_ticker(tk)
        total_new += n
        print(f"[{i}/{len(tickers)}] {tk}: +{n} new mentions (store now {tot})", flush=True)
        flush_names()   # persist any newly-fetched company names as we go
        time.sleep(0.5)
    flush_names()
    print(f"DONE: {total_new} new mentions across {len(tickers)} tickers")


if __name__ == "__main__":
    main()

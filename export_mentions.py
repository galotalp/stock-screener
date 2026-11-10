#!/usr/bin/env python3
"""
export_mentions.py — write one compact JSON per ticker for the public "validate
the mentions" table (mentions.html). Reads data/mentions/<TICKER>.jsonl and emits
mentions_export/<TICKER>.json containing every collected NEWS item for that ticker,
newest first, with only the fields the table needs. StockTwits posts are still
collected but not exported (the dashboard is news-only).

score = the stance the dashboard uses (sentiment_v2.news_stance): the article's
Claude Haiku label (bullish +1 / neutral 0 / bearish -1, none for off-topic items
and price-move recaps), or the word-list score until it is labeled.
lab = Haiku label or null; fl = Haiku flags; how = "llm" | "offtopic" | "move" |
"lex" | "none"; pos / neg = word-list hit counts.

Output shape (mentions_export/<TICKER>.json):
{
  "ticker": "NVDA",
  "count": 1147,
  "generatedAt": "2026-07-15T06:00:00Z",
  "mentions": [
    {"date": "...Z", "source": "news:yahoo", "score": 1.0, "lab": "bullish",
     "fl": ["about", "new"], "how": "llm", "pos": 1, "neg": 0,
     "session": "regular", "blurb": "..."},
    ...
  ]
}

The loop (run_scrape.sh) runs this after collect_mentions.py, then syncs
mentions_export/ to s3://<bucket>/<prefix>/mentions/.
"""
import glob, json, os, sys

import sentiment_v2 as sv

MDIR = "data/mentions"
OUT = "mentions_export"
KEEP = ("date", "source", "session", "blurb")

def main():
    stamp = sys.argv[1] if len(sys.argv) > 1 else ""
    os.makedirs(OUT, exist_ok=True)
    files = sorted(glob.glob(f"{MDIR}/*.jsonl"))
    total_files = total_rows = 0
    for fp in files:
        ticker = os.path.basename(fp)[:-6]  # strip .jsonl
        rows = []
        for ln in open(fp, encoding="utf-8"):
            if not ln.strip():
                continue
            try:
                r = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if not (r.get("source") or "").lower().startswith("news"):
                continue
            row = {k: r.get(k) for k in KEEP}
            st, how, lab = sv.news_stance(ticker, r)
            pos, neg = sv.lex_score(r.get("blurb") or "", False)
            row.update({"score": st, "how": how, "pos": pos, "neg": neg,
                        "lab": lab.get("stance") if lab else None,
                        "fl": [f for f in ("about", "earn", "move", "new") if lab.get(f)] if lab else []})
            rows.append(row)
        # newest first
        rows.sort(key=lambda r: r.get("date") or "", reverse=True)
        out = {"ticker": ticker, "count": len(rows), "generatedAt": stamp, "mentions": rows}
        # compact (no indent) to keep the on-demand fetch small
        json.dump(out, open(f"{OUT}/{ticker}.json", "w", encoding="utf-8"),
                  ensure_ascii=False, separators=(",", ":"))
        total_files += 1
        total_rows += len(rows)
    print(f"exported {total_files} ticker files, {total_rows} news items -> {OUT}/")

if __name__ == "__main__":
    main()

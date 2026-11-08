#!/usr/bin/env python3
"""
export_mentions.py — write one compact JSON per ticker for the public "validate
the mentions" table (mentions.html). Reads data/mentions/<TICKER>.jsonl and emits
mentions_export/<TICKER>.json containing every collected mention for that ticker,
newest first, with only the fields the table needs.

Output shape (mentions_export/<TICKER>.json):
{
  "ticker": "NVDA",
  "count": 1147,
  "generatedAt": "2026-07-15T06:00:00Z",
  "mentions": [
    {"date": "...Z", "source": "stocktwits", "score": 0.0, "pos": 0, "neg": 0,
     "session": "regular", "blurb": "..."},
    ...
  ]
}

The loop (run_scrape.sh) runs this after collect_mentions.py, then syncs
mentions_export/ to s3://<bucket>/<prefix>/mentions/.
"""
import glob, json, os, sys

MDIR = "data/mentions"
OUT = "mentions_export"
KEEP = ("date", "source", "score", "pos", "neg", "session", "blurb")

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
            rows.append({k: r.get(k) for k in KEEP})
        # newest first
        rows.sort(key=lambda r: r.get("date") or "", reverse=True)
        out = {"ticker": ticker, "count": len(rows), "generatedAt": stamp, "mentions": rows}
        # compact (no indent) to keep the on-demand fetch small
        json.dump(out, open(f"{OUT}/{ticker}.json", "w", encoding="utf-8"),
                  ensure_ascii=False, separators=(",", ":"))
        total_files += 1
        total_rows += len(rows)
    print(f"exported {total_files} ticker files, {total_rows} mentions -> {OUT}/")

if __name__ == "__main__":
    main()

"""
One-time cleanup of already-stored contaminated NEWS mentions.

Yahoo's RSS returned "related" articles that never actually name the ticker or its
company (an eBay story stored under WMT, a DoorDash story under NVDA, etc.). The
new collector (entity_scope) drops these going forward; this script removes the
ones ALREADY stored, using the SAME entity-relevance logic so history and future
collection agree.

SAFETY (this deletes stored data — the corpus we must never lose):
  * DRY-RUN BY DEFAULT. Prints exactly what it would remove, per ticker, with
    examples. Writes nothing without --apply.
  * BACKUP FIRST. --apply refuses to run unless a backup exists (or --backup is
    given, which creates one: data/mentions_backup_<STAMP>/ as a full copy).
  * NEWS ONLY. Social (StockTwits) and any non-news source are NEVER touched —
    a cashtag IS the attribution, so social mentions are relevant by construction.
  * ABORT ON ABSURD REMOVAL. If the overall news-removal rate exceeds
    --max-drop-frac (default 0.85) it aborts: that signals a matcher/name problem,
    not real contamination.
  * Rows with an UNKNOWN company name (name lookup failed) are KEPT, not dropped,
    so a missing name can't nuke a ticker's whole news history. (Symbol match still
    applies; only name-based rescue is unavailable.)

Usage:
  python3 cleanup_mentions.py                 # dry-run over all tickers
  python3 cleanup_mentions.py WMT MRK         # dry-run for specific tickers
  python3 cleanup_mentions.py --apply --backup   # backup + actually remove
  python3 cleanup_mentions.py --apply         # remove (requires an existing backup)

A stamp for the backup dir must be passed with --stamp (so runs are reproducible
and we never call a forbidden clock); e.g. --stamp 20260715-1200.
"""
import json
import os
import sys
import glob
import shutil

import entity_scope as escope

DATA = "data"
MDIR = f"{DATA}/mentions"
MAX_DROP_FRAC_DEFAULT = 0.85


def is_news(row):
    return "news" in (row.get("source") or "").lower()


def load_rows(path):
    rows = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            # keep malformed lines verbatim so we never silently lose data
            rows.append({"__raw__": line})
    return rows


def relevant(row, ticker, name):
    """True if this NEWS row still names the ticker/company (keep); False => drop.
    Non-news rows and malformed rows are always relevant (kept)."""
    if "__raw__" in row:
        return True
    if not is_news(row):
        return True
    text = row.get("blurb") or ""
    # If we have no company name AND the symbol isn't present, we still KEEP the row
    # unless the symbol-only check already fails — but to be safe with missing names,
    # only DROP when we positively fail BOTH symbol and (available) name.
    sents = escope.entity_sentences(text, ticker, name or "")
    return bool(sents)


def backup_dir(stamp):
    return f"{DATA}/mentions_backup_{stamp}"


def make_backup(stamp):
    dst = backup_dir(stamp)
    if os.path.exists(dst):
        print(f"  backup already exists: {dst}")
        return dst
    shutil.copytree(MDIR, dst)
    n = len(glob.glob(f"{dst}/*.jsonl"))
    print(f"  BACKUP created: {dst}  ({n} files)")
    return dst


def any_backup_exists():
    return bool(glob.glob(f"{DATA}/mentions_backup_*"))


def main():
    args = sys.argv[1:]
    apply = "--apply" in args
    do_backup = "--backup" in args
    max_drop = MAX_DROP_FRAC_DEFAULT
    stamp = None
    tickers = []
    it = iter(args)
    for a in it:
        if a == "--apply" or a == "--backup":
            continue
        if a == "--max-drop-frac":
            max_drop = float(next(it))
        elif a == "--stamp":
            stamp = next(it)
        elif a.startswith("--"):
            print(f"unknown flag {a}"); return
        else:
            tickers.append(a.upper())

    names = escope.load_names()

    files = ([f"{MDIR}/{t}.jsonl" for t in tickers if os.path.exists(f"{MDIR}/{t}.jsonl")]
             if tickers else sorted(glob.glob(f"{MDIR}/*.jsonl")))
    if not files:
        print("no mention files found"); return

    # ---- pass 1: assess (always) ----
    # PER-TICKER SAFETY: a ticker with NO cached company name matches on symbol only,
    # so news that says "Apple" but not "AAPL" would be wrongly dropped. If such a
    # ticker also shows a high drop rate, that's almost certainly the missing name,
    # not real contamination — so we SKIP it entirely (keep all rows) and flag it,
    # rather than delete genuine mentions. Populate names.json first
    # (entity_scope.py --refresh-mentions) to actually clean these.
    NO_NAME_DROP_GUARD = 0.5   # skip cleaning a nameless ticker dropping >50% of news
    tot_news = tot_drop = 0
    per_file = []      # (path, ticker, name, kept_rows, dropped_examples, n_news, n_drop, skipped)
    skipped_no_name = []
    for path in files:
        ticker = os.path.basename(path)[:-6].upper()
        name = names.get(ticker, "")
        rows = load_rows(path)
        n_news = sum(1 for r in rows if is_news(r))
        kept, drop_ex, n_drop = [], [], 0
        for r in rows:
            if relevant(r, ticker, name):
                kept.append(r)
            else:
                n_drop += 1
                if len(drop_ex) < 3:
                    drop_ex.append((r.get("blurb") or "")[:90])
        # apply the no-name guard
        skipped = False
        if (not name) and n_news and (n_drop / n_news) > NO_NAME_DROP_GUARD:
            skipped = True
            skipped_no_name.append((ticker, n_drop, n_news))
            kept = rows          # keep everything for this ticker
            n_drop = 0
        tot_news += n_news
        tot_drop += n_drop
        per_file.append((path, ticker, name, kept, drop_ex, n_news, n_drop, skipped))

    # ---- report ----
    print(f"\nScanned {len(files)} tickers.  NEWS mentions: {tot_news}   "
          f"would remove: {tot_drop} ({(100*tot_drop/tot_news if tot_news else 0):.0f}%)\n")
    worst = sorted([p for p in per_file if p[5] >= 3 and not p[7]],
                   key=lambda p: (p[6] / p[5]) if p[5] else 0, reverse=True)
    print("Most-contaminated tickers (drop / news):")
    for path, tk, name, kept, ex, n_news, n_drop, skipped in worst[:15]:
        frac = (100 * n_drop / n_news) if n_news else 0
        print(f"  {frac:5.0f}%  {tk:6} {n_drop:3}/{n_news:<3}  name={name!r}")
        for e in ex:
            print(f"          drop: {e}")

    if skipped_no_name:
        print(f"\nSKIPPED {len(skipped_no_name)} tickers with NO cached name + high "
              f"drop rate (kept intact — populate names.json first):")
        preview = sorted(skipped_no_name, key=lambda x: x[1], reverse=True)[:12]
        print("  " + ", ".join(f"{tk}({d}/{n})" for tk, d, n in preview)
              + (" ..." if len(skipped_no_name) > 12 else ""))
        print("  Fix: python3 entity_scope.py --refresh-mentions   then re-run this.")

    # ---- safety gate ----
    overall = (tot_drop / tot_news) if tot_news else 0
    if overall > max_drop:
        print(f"\nABORT: overall news-removal rate {overall:.0%} exceeds "
              f"--max-drop-frac {max_drop:.0%}. Looks like a matcher/name problem, "
              f"not real contamination. Nothing changed.")
        return

    if not apply:
        print("\nDRY-RUN only. Re-run with --apply --backup to remove these "
              "(a backup is created/required first). Nothing was changed.")
        return

    # ---- apply path: require a backup ----
    if do_backup:
        if not stamp:
            print("ERROR: --backup requires --stamp <STAMP> (e.g. 20260715-1200)."); return
        make_backup(stamp)
    if not any_backup_exists():
        print("ERROR: --apply refused — no backup found. Re-run with --backup --stamp <STAMP>.")
        return

    removed = 0
    for path, tk, name, kept, ex, n_news, n_drop, skipped in per_file:
        if n_drop == 0:
            continue
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for r in kept:
                if "__raw__" in r:
                    f.write(r["__raw__"] + "\n")
                else:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
        removed += n_drop
    print(f"\nAPPLIED: removed {removed} contaminated news mentions across "
          f"{sum(1 for p in per_file if p[6])} tickers. Backups in "
          f"{DATA}/mentions_backup_*.")


if __name__ == "__main__":
    main()

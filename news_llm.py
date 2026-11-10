"""
news_llm.py — label every news mention with Claude Haiku 5.5 (Message Batches API).

Read-only over the mention store. Writes a cache, data/news_llm.jsonl, one line per
(ticker, article):
  {"k": "NVDA|<mention id>", "stance": "bullish"|"bearish"|"neutral",
   "about": bool,      # the item is substantially about this company
   "earn": bool,       # about earnings / guidance / quarterly results
   "move": bool,       # mainly reports a price move that already happened
   "new": bool,        # reports a new fact or event (not commentary or a recap)
   "m": model, "t": unix time scored}
sentiment_v2.stance() reads it; items not scored yet fall back to the word list.

Batches finish within 24 hours (usually under one), so the nightly run collects
any finished batch, submits the items that still need a label, and waits up to
--wait minutes for that new batch before leaving it for the next run.

Usage
  python3 news_llm.py                 collect, submit new items, wait (nightly)
  python3 news_llm.py --sample 200    score a random sample synchronously and print it
  python3 news_llm.py --dry-run       count what would be sent, with a cost estimate
  python3 news_llm.py --limit N       cap items submitted this run

Credentials: ANTHROPIC_API_KEY, or the first line of ~/.anthropic_api_key
(chmod 600). Without a key or the `anthropic` package the script prints why and
exits 0, so the pipeline carries on with word-list scores.
"""
import argparse
import glob
import json
import os
import random
import sys
import time

MODEL = "claude-haiku-5-5"
PER_REQUEST = 40                    # headlines per API request
MAX_REQUESTS_PER_BATCH = 5000
CACHE = "data/news_llm.jsonl"
STATE = "data/news_llm_batches.json"
NAMES = "data/names.json"

SYSTEM = """You label short financial news items for a research study on whether news predicts stock returns.

For each item you get a stock ticker, the company name when known, and the item's headline/summary text. Judge the item ONLY from its text, from the point of view of that one company's shareholders.

Fields:
- stance: what the item's information implies for the company's stock. "bullish" = good news for the company (e.g. strong results, raised guidance, new contract, upgrade, approval, buyback). "bearish" = bad news (e.g. weak results, cut guidance, lawsuit, probe, downgrade, recall, executive departure under pressure, dilution). "neutral" = no clear implication, mixed, or purely descriptive. Do NOT base stance on a price move the text reports; judge the underlying information. If the text only says the stock rose or fell, stance is "neutral".
- about: true if the item is substantially about this company; false if the company is only mentioned in passing, in a list of many tickers, or the item is about something else.
- earn: true if the item is about earnings, quarterly or annual results, guidance, or an earnings preview/estimates.
- move: true if the item's main content is a stock price move that already happened (e.g. "X shares jump 5%", "why X stock is down today", top gainers/losers lists).
- new: true if the item reports a specific new fact or event (results, deal, product, regulatory or legal action, analyst rating change, management change, guidance). False for opinion, explainers, listicles, recaps, "should you buy" pieces, or price-move reports.

Return one result per item, using the item's index i."""

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "i": {"type": "integer"},
                    "stance": {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
                    "about": {"type": "boolean"},
                    "earn": {"type": "boolean"},
                    "move": {"type": "boolean"},
                    "new": {"type": "boolean"},
                },
                "required": ["i", "stance", "about", "earn", "move", "new"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


def _key(tk, m):
    return f"{tk}|{m.get('id')}"


def load_cache():
    out = {}
    if os.path.exists(CACHE):
        for line in open(CACHE, encoding="utf-8"):
            try:
                r = json.loads(line)
                out[r["k"]] = r
            except Exception:
                continue
    return out


def pending_items(cache, in_flight):
    """News mentions with no label yet and not already in a submitted batch."""
    items = []
    for p in sorted(glob.glob("data/mentions/*.jsonl")):
        tk = os.path.basename(p)[:-6]
        for line in open(p, encoding="utf-8"):
            try:
                m = json.loads(line)
            except Exception:
                continue
            if not (m.get("source") or "").lower().startswith("news") or not m.get("id"):
                continue
            k = _key(tk, m)
            if k in cache or k in in_flight:
                continue
            text = " ".join((m.get("blurb") or "").split())
            if text:
                items.append({"k": k, "tk": tk, "text": text[:600]})
    return items


def _names():
    try:
        return json.load(open(NAMES, encoding="utf-8"))
    except Exception:
        return {}


def _prompt(chunk, names):
    lines = []
    for i, it in enumerate(chunk):
        nm = names.get(it["tk"]) or ""
        nm = nm if isinstance(nm, str) else ""
        lines.append(f"[{i}] ticker={it['tk']}" + (f" company={nm}" if nm else "") + f"\ntext: {it['text']}")
    return "Label each item.\n\n" + "\n\n".join(lines)


def _params(chunk, names):
    return {
        "model": MODEL,
        "max_tokens": 120 + 60 * len(chunk),
        "system": SYSTEM,
        "thinking": {"type": "disabled"},
        "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        "messages": [{"role": "user", "content": _prompt(chunk, names)}],
    }


def _parse(msg, chunk):
    """[(item, label)] from one response; [] on refusal / truncation / bad JSON."""
    if getattr(msg, "stop_reason", None) in ("refusal", "max_tokens"):
        return []
    text = next((b.text for b in msg.content if getattr(b, "type", "") == "text"), "")
    try:
        data = json.loads(text)
    except Exception:
        return []
    out = []
    now = int(time.time())
    for r in data.get("items", []):
        i = r.get("i")
        if isinstance(i, int) and 0 <= i < len(chunk):
            out.append({"k": chunk[i]["k"], "stance": r["stance"], "about": bool(r["about"]),
                        "earn": bool(r["earn"]), "move": bool(r["move"]), "new": bool(r["new"]),
                        "m": MODEL, "t": now})
    return out


def _append(rows):
    if not rows:
        return
    with open(CACHE, "a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")


def _client():
    key = os.environ.get("ANTHROPIC_API_KEY")
    kf = os.path.expanduser("~/.anthropic_api_key")
    if not key and os.path.exists(kf):
        key = open(kf).read().strip().splitlines()[0]
    if not key:
        print("news_llm: no API key (set ANTHROPIC_API_KEY or ~/.anthropic_api_key); skipped")
        return None
    try:
        import anthropic
    except ImportError:
        print("news_llm: `anthropic` package not installed (pip install --user anthropic); skipped")
        return None
    return anthropic.Anthropic(api_key=key)


def _state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"batches": []}


def _save_state(st):
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w"), indent=1)
    os.replace(tmp, STATE)


def collect(client, st):
    """Write results of any ended batch; keep the rest pending."""
    keep, wrote = [], 0
    for b in st["batches"]:
        try:
            info = client.messages.batches.retrieve(b["id"])
        except Exception as e:
            print(f"news_llm: could not check batch {b['id']}: {e}")
            keep.append(b)
            continue
        if info.processing_status != "ended":
            keep.append(b)
            continue
        chunks = b["chunks"]
        rows = []
        for res in client.messages.batches.results(b["id"]):
            chunk = chunks.get(res.custom_id)
            if chunk is None or res.result.type != "succeeded":
                continue          # errored / expired items stay unlabeled and are resubmitted next run
            rows.extend(_parse(res.result.message, chunk))
        _append(rows)
        wrote += len(rows)
        print(f"news_llm: batch {b['id']} ended; {len(rows)} labels written")
    st["batches"] = keep
    _save_state(st)
    return wrote


def submit(client, st, items, names):
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request
    chunks = [items[i:i + PER_REQUEST] for i in range(0, len(items), PER_REQUEST)]
    for s in range(0, len(chunks), MAX_REQUESTS_PER_BATCH):
        part = chunks[s:s + MAX_REQUESTS_PER_BATCH]
        reqs, saved = [], {}
        for j, ch in enumerate(part):
            cid = f"c{s + j}"
            reqs.append(Request(custom_id=cid, params=MessageCreateParamsNonStreaming(**_params(ch, names))))
            saved[cid] = [{"k": it["k"]} for it in ch]
        b = client.messages.batches.create(requests=reqs)
        st["batches"].append({"id": b.id, "created": int(time.time()), "n": sum(len(c) for c in part),
                              "chunks": saved})
        _save_state(st)
        print(f"news_llm: submitted batch {b.id} ({len(part)} requests, {sum(len(c) for c in part)} items)")


def sample(client, items, names, n):
    random.seed(7)
    pick = random.sample(items, min(n, len(items)))
    rows = []
    for i in range(0, len(pick), PER_REQUEST):
        ch = pick[i:i + PER_REQUEST]
        msg = client.messages.create(**_params(ch, names))
        got = _parse(msg, ch)
        if len(got) < len(ch):
            print(f"news_llm: request {i // PER_REQUEST} returned {len(got)}/{len(ch)} labels "
                  f"(stop_reason={msg.stop_reason})")
        rows.extend(got)
    _append(rows)
    text = {it["k"]: it["text"] for it in pick}
    for r in rows:
        flags = ",".join(f for f in ("about", "earn", "move", "new") if r[f])
        print(f"{r['stance']:8} [{flags}] {r['k'].split('|')[0]}: {text[r['k']][:150]}")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--wait", type=int, default=20, help="minutes to wait for a new batch")
    a = ap.parse_args()
    cache = load_cache()
    st = _state()
    in_flight = {it["k"] for b in st["batches"] for ch in b["chunks"].values() for it in ch}
    items = pending_items(cache, in_flight)
    if a.limit:
        items = items[:a.limit]
    if a.dry_run:
        tok_in = sum(len(it["text"]) for it in items) / 4 + 30 * len(items) + 450 * (len(items) / PER_REQUEST)
        tok_out = 25 * len(items)
        cost = (tok_in * 0.10 + tok_out * 0.50) / 1e6 / 2      # batch price is half
        print(f"news_llm: {len(cache)} labeled, {len(in_flight)} in flight, {len(items)} to send "
              f"(~{tok_in / 1e6:.1f}M in / {tok_out / 1e6:.1f}M out tokens, ~${cost:.2f} at batch prices)")
        return
    client = _client()
    if client is None:
        return
    if a.sample:
        sample(client, items, _names(), a.sample)
        return
    collect(client, st)
    if items:
        submit(client, st, items, _names())
        deadline = time.time() + 60 * a.wait
        while st["batches"] and time.time() < deadline:
            time.sleep(60)
            collect(client, st)
    print(f"news_llm: {len(load_cache())} labeled; {sum(b['n'] for b in st['batches'])} still in flight")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:          # never fail the pipeline
        print(f"news_llm: error ({type(e).__name__}: {e}); word-list scores used")
        sys.exit(0)

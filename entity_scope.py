"""
Entity-scoped relevance + sentence extraction for mentions (no LLM, deterministic).

Two jobs, one mechanism:
  1. RELEVANCE FILTER — a news article / social post is only a real mention of a
     ticker if the ticker SYMBOL or the COMPANY NAME actually appears in the text.
     Yahoo's RSS returns "related" articles (an eBay story listed under WMT), which
     poison the data. If neither the symbol nor the name appears, the mention is
     dropped.
  2. ATTRIBUTION SCOPE — even in a relevant article, tone about a co-mentioned
     company (Netflix in a "Netflix to acquire Warner Bros" piece) must not leak
     onto the target. So we score ONLY the sentences that name the target entity,
     not the whole text.

Company names come from yfinance (longName preferred, shortName fallback),
normalized to a matchable "core" ("Merck & Co., Inc." -> "Merck", "Walmart Inc."
-> "Walmart"), cached to data/names.json so we never refetch and so a future
Reddit collector can match on company name too.

Public API:
  load_names()                         -> {TICKER: core_name}
  fetch_name(ticker)                   -> core_name or None (live yfinance)
  core_name(raw)                       -> normalized core string
  entity_sentences(text, ticker, name) -> [sentences naming the entity]  (may be [])
  scope_text(text, ticker, name)       -> " ".join(entity_sentences) or ""

`entity_sentences` returning [] means "not a real mention of this ticker" -> drop.
"""
import json
import os
import re

DATA = "data"
NAMES_PATH = f"{DATA}/names.json"

# ----- company-name normalization -------------------------------------------
# Trailing legal / share-class boilerplate to peel off, applied repeatedly so
# stacked suffixes ("Duolingo, Inc.") fully reduce.
_SUFFIX = re.compile(
    r",?\s+(Incorporated|Inc\.?|Corporation|Corp\.?|Company|Co\.?|Ltd\.?|Limited|"
    r"PLC|N\.V\.|S\.A\.|S\.p\.A\.|AG|SE|Class\s+[A-Z]|Common\s+(?:Stock|Shares)|"
    r"Ordinary\s+Shares|American\s+Depositary\s+Shares|ADR|ADS|Holdings?|Holding|"
    r"Group|Trust|Fund|LP|L\.P\.|LLC|The)\.?$",
    re.IGNORECASE,
)


def core_name(raw):
    """Reduce a raw company name to a matchable core.

    'Merck & Co., Inc.'          -> 'Merck'
    'Walmart Inc.'               -> 'Walmart'
    'Warner Bros. Discovery, Inc.' -> 'Warner Bros. Discovery'
    'Berkshire Hathaway Inc. New'  -> 'Berkshire Hathaway'   (via longName)
    """
    n = (raw or "").strip()
    if not n:
        return ""
    # cut a trailing " - <share class>" tail if present
    n = n.split(" - ")[0].strip()
    # some yfinance shortNames carry a trailing " New"/" Old" etc. class marker
    n = re.sub(r"\s+(New|Old|When\s+Issued)$", "", n, flags=re.IGNORECASE).strip()
    prev = None
    for _ in range(6):
        if n == prev:
            break
        prev = n
        n = _SUFFIX.sub("", n).strip().rstrip(",").strip()
        # drop a now-dangling connective ("Merck &", "Procter & ...")
        n = re.sub(r"\s*(&|and)\s*$", "", n, flags=re.IGNORECASE).strip()
    return n


# ----- names cache (yfinance-backed) ----------------------------------------
def load_names():
    """{TICKER: core_name} from data/names.json (empty dict if absent)."""
    if os.path.exists(NAMES_PATH):
        try:
            return json.load(open(NAMES_PATH, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_names(names):
    tmp = NAMES_PATH + ".tmp"
    json.dump(names, open(tmp, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    os.replace(tmp, NAMES_PATH)


def fetch_name(ticker):
    """Live yfinance lookup -> core company name (longName preferred). None on failure."""
    try:
        import yfinance as yf
    except Exception:
        return None
    base = ticker.upper().replace(".TO", "")
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:
        return None
    raw = info.get("longName") or info.get("shortName") or ""
    name = core_name(raw)
    # guard: a 1-2 char "name" is too generic to match safely; skip it
    return name if len(name) >= 3 else None


# ----- entity-scoped sentence extraction ------------------------------------
# A light, dependency-free sentence splitter. Python lookbehind must be
# fixed-width, so instead of a variable-length "not after an abbreviation"
# lookbehind we MASK the period inside known abbreviations (and company suffixes
# like "Bros." / "Inc.") before splitting, then UNMASK afterward. This keeps
# "Warner Bros. Discovery fell" as one sentence, not three.
_ABBRS = ["Inc", "Corp", "Co", "Ltd", "Bros", "Mr", "Mrs", "Ms", "Dr", "Jr",
          "Sr", "St", "vs", "Fig", "No", "Ph.D", "e.g", "i.e", "a.m", "p.m",
          "U.S", "U.K", "St.", "Jan", "Feb", "Mar", "Apr", "Jun", "Jul",
          "Aug", "Sep", "Sept", "Oct", "Nov", "Dec"]
_DOT = "\x00"  # placeholder for a masked period
# match "<Abbr>." keeping the abbreviation, replacing its trailing dot
_ABBR_RE = re.compile(r"\b(" + "|".join(re.escape(a.rstrip(".")) for a in _ABBRS) + r")\.")
# also mask periods inside decimals / initials like "3.5" or "U.S.A."
_DECIMAL_RE = re.compile(r"(\d)\.(\d)")
_INITIAL_RE = re.compile(r"\b([A-Z])\.(?=[A-Z]\.)")

_SENT_SPLIT = re.compile(
    r"(?<=[.!?])"                # after sentence-ending punctuation
    r"[\"')\]]?"                 # optional closing quote/bracket
    r"\s+"                       # whitespace
    r"(?=[A-Z0-9$\"'(])"         # next sentence starts with capital/number/quote/$
)


def _split_sentences(text):
    text = (text or "").strip()
    if not text:
        return []
    text = re.sub(r"\s+", " ", text)                 # collapse whitespace
    masked = _ABBR_RE.sub(lambda m: m.group(1) + _DOT, text)
    masked = _DECIMAL_RE.sub(lambda m: m.group(1) + _DOT + m.group(2), masked)
    masked = _INITIAL_RE.sub(lambda m: m.group(1) + _DOT, masked)
    parts = _SENT_SPLIT.split(masked)
    return [p.replace(_DOT, ".").strip() for p in parts if p.strip()]


def _entity_patterns(ticker, name):
    """Whole-word, case-insensitive patterns for the symbol (with/without $) and
    the company core-name."""
    base = ticker.upper().replace(".TO", "").lstrip("$")
    pats = []
    # $TSLA or TSLA as a standalone token (word boundary; allow a leading $)
    pats.append(re.compile(r"(?<![A-Za-z0-9])\$?" + re.escape(base) + r"(?![A-Za-z0-9])"))
    if name and len(name) >= 3:
        # match the company name as a phrase; be lenient about a trailing period in
        # names like "Warner Bros." by matching the core tokens as written.
        pats.append(re.compile(r"(?<![A-Za-z])" + re.escape(name) + r"(?![A-Za-z])",
                               re.IGNORECASE))
    return pats


def entity_sentences(text, ticker, name):
    """Sentences in `text` that name the target entity (symbol or company name).
    Empty list => the text is not actually about this ticker (drop the mention)."""
    pats = _entity_patterns(ticker, name)
    out = []
    for s in _split_sentences(text):
        if any(p.search(s) for p in pats):
            out.append(s)
    return out


def scope_text(text, ticker, name):
    """The entity-relevant slice of `text` to feed the scorer, or '' if none."""
    return " ".join(entity_sentences(text, ticker, name))


def matched_on(text, ticker, name):
    """Which signal matched, for auditing: 'symbol' | 'name' | 'both' | None."""
    base = ticker.upper().replace(".TO", "").lstrip("$")
    sym = re.search(r"(?<![A-Za-z0-9])\$?" + re.escape(base) + r"(?![A-Za-z0-9])", text or "")
    nm = None
    if name and len(name) >= 3:
        nm = re.search(r"(?<![A-Za-z])" + re.escape(name) + r"(?![A-Za-z])", text or "", re.IGNORECASE)
    if sym and nm:
        return "both"
    if sym:
        return "symbol"
    if nm:
        return "name"
    return None


def refresh_names(tickers, force=False, sleep_s=0.3):
    """Populate data/names.json for a list of tickers from yfinance. Skips tickers
    already cached (unless force). Persists incrementally so a mid-run interruption
    keeps progress. Returns the names dict."""
    import time
    names = load_names()
    todo = [t for t in tickers if force or t.upper().replace(".TO", "") not in names]
    print(f"refresh_names: {len(todo)} to fetch ({len(names)} already cached)")
    for i, t in enumerate(todo, 1):
        key = t.upper().replace(".TO", "")
        nm = fetch_name(t) or ""
        names[key] = nm
        if i % 10 == 0 or i == len(todo):
            save_names(names)
            print(f"  [{i}/{len(todo)}] ... {key} -> {nm!r}", flush=True)
        time.sleep(sleep_s)
    save_names(names)
    return names


if __name__ == "__main__":
    import sys
    import glob
    args = sys.argv[1:]
    force = "--force" in args
    if "--refresh-universe" in args:
        u = json.load(open(f"{DATA}/universe_tracked.json"))
        tks = u["tickers"]
    elif "--refresh-mentions" in args:
        tks = sorted(os.path.basename(p)[:-6].upper()
                     for p in glob.glob(f"{DATA}/mentions/*.jsonl"))
    else:
        tks = [a.upper() for a in args if not a.startswith("--")]
    if tks:
        refresh_names(tks, force=force)
        print("done.")
    else:
        print("usage: entity_scope.py --refresh-universe | --refresh-mentions | TICKER ...")

"""
options_source.py — THE ONLY MODULE THAT TALKS TO THE OPTIONS DATA PROVIDER.

This isolation is the single most important structural requirement in
options_tool_spec.md, and the reason is worth stating plainly: the free public
option endpoint requires a cookie/crumb handshake that library support for has
broken and been patched repeatedly. The data source is the part of this tool
most likely to break and the part least worth rewriting the tool over.

CONTRACT
--------
`fetch_chains(tickers)` returns (rows, meta) where `rows` is a list of dicts,
each dict being one option contract with exactly these normalised keys:

    ticker              str    underlying symbol
    spot                float  underlying price at fetch time
    spot_source         str    'live' or 'close' — which path produced spot
    contract_symbol     str    OCC symbol
    option_type         str    'call' or 'put'
    strike              float
    bid                 float
    ask                 float
    last_price          float
    volume              int
    open_interest       int
    iv                  float  IMPLIED VOL AS A PROPORTION (0.28, never 28)
    expiry              str    'YYYY-MM-DD'
    expiry_source       str    'occ_symbol' or 'label' — how expiry was derived
    dte                 int    calendar days to expiration
    in_the_money        bool

Every metric, filter, and output stage downstream depends on that table and
NOTHING else. To swap providers, reimplement `fetch_chains` to return the same
shape; no other file changes.

Fallbacks documented in the spec, in order, if chain fetches fail wholesale:
  1. Install yfinance from its development source (fixes land upstream well
     before they are released to PyPI):
         pip install --user git+https://github.com/ranaroussi/yfinance.git
  2. Swap the data source behind this module's contract.
  3. For anything actually traded on, use a broker API — broker quotes are the
     ones that can be executed against. Free feeds are delayed and occasionally
     crossed.

Python 3.9 compatible: no `X | None` annotations, no backslashes in f-strings.
"""
import datetime as dt
import re
import sys
import time

import options_config as cfg

try:
    import yfinance as yf
except ImportError:  # pragma: no cover
    yf = None

# curl_cffi with chrome impersonation is what the rest of this project uses to
# get past bot detection on the same upstream host; reuse it here for
# consistency rather than inventing a second HTTP strategy.
try:
    from curl_cffi import requests as _curl_requests
except ImportError:  # pragma: no cover
    _curl_requests = None


def _log(msg):
    """Print immediately. Row counts after every transformation are a spec
    requirement, and buffered output defeats that when a run is being watched."""
    print(msg)
    sys.stdout.flush()


def _make_session():
    """Chrome-impersonating session, or None to let yfinance use its default."""
    if _curl_requests is None:
        return None
    try:
        return _curl_requests.Session(impersonate="chrome")
    except Exception as exc:  # pragma: no cover
        _log("  (curl_cffi session unavailable: {0}) ".format(exc))
        return None


# ---------------------------------------------------------------------------
# Defensive column mapping
# ---------------------------------------------------------------------------
# The upstream schema has changed historically, so map CANDIDATE names rather
# than assuming a fixed set. First match wins.
_COLUMN_CANDIDATES = {
    "contract_symbol": ("contractSymbol", "ContractSymbol", "contract_symbol", "symbol"),
    "strike":          ("strike", "Strike"),
    "bid":             ("bid", "Bid"),
    "ask":             ("ask", "Ask"),
    "last_price":      ("lastPrice", "LastPrice", "last_price", "last"),
    "volume":          ("volume", "Volume", "vol"),
    "open_interest":   ("openInterest", "OpenInterest", "open_interest", "OI", "oi"),
    "iv":              ("impliedVolatility", "ImpliedVolatility", "implied_volatility",
                        "IV", "iv"),
    "in_the_money":    ("inTheMoney", "InTheMoney", "in_the_money", "itm"),
}


def _pick(row_keys, candidates):
    """Return the first candidate column name present, or None."""
    for name in candidates:
        if name in row_keys:
            return name
    return None


def _build_column_map(columns):
    """Map our canonical field names onto whatever the provider actually sent."""
    cols = set(columns)
    mapping = {}
    for canonical, candidates in _COLUMN_CANDIDATES.items():
        found = _pick(cols, candidates)
        if found is not None:
            mapping[canonical] = found
    return mapping


def _num(value, default=0.0):
    """Coerce to float, mapping None/NaN/blank to a default."""
    if value is None:
        return default
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    if f != f:  # NaN
        return default
    return f


def _int(value, default=0):
    return int(_num(value, default))


def normalise_iv(raw):
    """IV to a PROPORTION.

    The source reports IV as a proportion (0.28) sometimes and as a percentage
    (28) other times. Anything above the threshold is treated as a percentage.
    A genuine proportional IV above 3.0 (300% vol) does not occur in listed
    equity options outside of quote errors, so the threshold is safe.
    """
    iv = _num(raw, 0.0)
    if iv > cfg.IV_PERCENT_THRESHOLD:
        return iv / 100.0
    return iv


# ---------------------------------------------------------------------------
# OCC contract symbol parsing
# ---------------------------------------------------------------------------
# Format: ROOT + YYMMDD + C|P + strike x 1000, zero-padded to 8 digits.
#   AAPL260812C00205000 -> AAPL, 2026-08-12, call, strike 205.0
# Parsing the symbol is more reliable than trusting the provider's expiry
# labels, which have been wrong around timezone boundaries.
_OCC_RE = re.compile(r"^(?P<root>[A-Z0-9]{1,6})(?P<ymd>\d{6})(?P<cp>[CP])(?P<strike>\d{8})$")


def parse_occ_symbol(symbol):
    """Return dict with expiry/option_type/strike, or None if unparseable."""
    if not symbol:
        return None
    m = _OCC_RE.match(str(symbol).strip().upper())
    if not m:
        return None
    ymd = m.group("ymd")
    try:
        expiry = dt.datetime.strptime(ymd, "%y%m%d").date()
    except ValueError:
        return None
    return {
        "expiry": expiry,
        "option_type": "call" if m.group("cp") == "C" else "put",
        "strike": int(m.group("strike")) / 1000.0,
    }


# ---------------------------------------------------------------------------
# Spot price
# ---------------------------------------------------------------------------

def fetch_spot(ticker_obj, symbol):
    """Live quote first, most recent daily close as fallback.

    Returns (spot, source) where source is 'live', 'close', or None on failure.
    Logging which path was used matters: a run built on stale closes should not
    look identical to one built on live quotes.
    """
    # Path 1 — live quote.
    try:
        info = ticker_obj.fast_info
        for key in ("last_price", "lastPrice", "regular_market_price"):
            try:
                value = info[key]
            except (KeyError, TypeError):
                value = getattr(info, key, None)
            price = _num(value, 0.0)
            if price > 0:
                return price, "live"
    except Exception:
        pass

    # Path 2 — most recent daily close.
    try:
        hist = ticker_obj.history(period="5d", interval="1d")
        if hist is not None and len(hist) > 0:
            close = _num(hist["Close"].iloc[-1], 0.0)
            if close > 0:
                return close, "close"
    except Exception as exc:
        _log("    spot fallback failed for {0}: {1}".format(symbol, exc))

    return None, None


# ---------------------------------------------------------------------------
# Expiry listing
# ---------------------------------------------------------------------------

def _expiries_in_window(ticker_obj, symbol, today):
    """All available expiries inside the DTE window.

    The spec requires covering MULTIPLE expiries per ticker, not just the front
    month, so the term structure is visible.
    """
    try:
        raw = ticker_obj.options
    except Exception as exc:
        _log("    could not list expiries for {0}: {1}".format(symbol, exc))
        return []

    if not raw:
        return []

    # Normalise the returned structure. A single expiry is occasionally returned
    # flattened/unwrapped rather than as a collection, so coerce to a list of
    # strings before iterating.
    if isinstance(raw, str):
        raw = [raw]

    keep = []
    for label in raw:
        try:
            exp_date = dt.datetime.strptime(str(label), "%Y-%m-%d").date()
        except ValueError:
            continue
        dte = (exp_date - today).days
        if cfg.DTE_MIN <= dte <= cfg.DTE_MAX:
            keep.append((str(label), exp_date, dte))
    return keep


# ---------------------------------------------------------------------------
# Chain parsing
# ---------------------------------------------------------------------------

def _rows_from_frame(frame, option_type, symbol, spot, spot_source,
                     label, label_date, today, stats):
    """Convert one provider dataframe (calls or puts) into normalised rows."""
    if frame is None or len(frame) == 0:
        return []

    colmap = _build_column_map(list(frame.columns))

    # Without a strike we cannot compute anything; without a contract symbol we
    # lose the reliable expiry path but can still fall back to the label.
    if "strike" not in colmap:
        _log("    {0} {1} {2}: no strike column; columns were {3}".format(
            symbol, label, option_type, list(frame.columns)))
        return []

    out = []
    for record in frame.to_dict("records"):
        contract_symbol = record.get(colmap.get("contract_symbol", ""), "")

        # Expiry from the OCC symbol where possible; fall back to the label.
        parsed = parse_occ_symbol(contract_symbol)
        if parsed is not None:
            expiry_date = parsed["expiry"]
            expiry_source = "occ_symbol"
        else:
            expiry_date = label_date
            expiry_source = "label"
            stats["expiry_fallback"] += 1

        strike = _num(record.get(colmap["strike"]), 0.0)
        if strike <= 0:
            continue

        out.append({
            "ticker": symbol,
            "spot": spot,
            "spot_source": spot_source,
            "contract_symbol": str(contract_symbol),
            "option_type": option_type,
            "strike": strike,
            "bid": _num(record.get(colmap.get("bid", "")), 0.0),
            "ask": _num(record.get(colmap.get("ask", "")), 0.0),
            "last_price": _num(record.get(colmap.get("last_price", "")), 0.0),
            "volume": _int(record.get(colmap.get("volume", "")), 0),
            "open_interest": _int(record.get(colmap.get("open_interest", "")), 0),
            "iv": normalise_iv(record.get(colmap.get("iv", ""))),
            "expiry": expiry_date.isoformat(),
            "expiry_source": expiry_source,
            "dte": (expiry_date - today).days,
            "in_the_money": bool(record.get(colmap.get("in_the_money", ""), False)),
        })
    return out


def _fetch_one_ticker(symbol, session, today, stats):
    """All in-window contracts for one symbol. Errors are logged, never raised."""
    ticker_obj = yf.Ticker(symbol, session=session)

    spot, spot_source = fetch_spot(ticker_obj, symbol)
    if spot is None:
        _log("  {0}: no spot price available; skipping ticker".format(symbol))
        stats["tickers_failed"].append(symbol)
        return []
    time.sleep(cfg.SLEEP_BETWEEN_QUOTES)

    expiries = _expiries_in_window(ticker_obj, symbol, today)
    if not expiries:
        _log("  {0}: spot {1:.2f} ({2}) — no expiries in {3}-{4} DTE window".format(
            symbol, spot, spot_source, cfg.DTE_MIN, cfg.DTE_MAX))
        stats["tickers_failed"].append(symbol)
        return []

    rows = []
    for label, label_date, _dte in expiries:
        try:
            chain = ticker_obj.option_chain(label)
        except Exception as exc:
            _log("    {0} {1}: chain fetch failed ({2})".format(symbol, label, exc))
            stats["chain_errors"] += 1
            continue

        rows.extend(_rows_from_frame(getattr(chain, "calls", None), "call", symbol,
                                     spot, spot_source, label, label_date, today, stats))
        rows.extend(_rows_from_frame(getattr(chain, "puts", None), "put", symbol,
                                     spot, spot_source, label, label_date, today, stats))
        time.sleep(cfg.SLEEP_BETWEEN_CHAINS)

    _log("  {0}: spot {1:.2f} ({2})  expiries {3}  raw contracts {4}".format(
        symbol, spot, spot_source, len(expiries), len(rows)))
    return rows


def fetch_chains(tickers=None, today=None):
    """Fetch and normalise option chains. See module docstring for the contract.

    Returns (rows, meta). Never raises for per-ticker failures: a single bad
    ticker logs and the run continues.
    """
    if yf is None:
        raise RuntimeError(
            "yfinance is not installed. Install with: pip install --user yfinance")

    if tickers is None:
        tickers = cfg.TICKERS
    if today is None:
        today = dt.date.today()

    session = _make_session()
    stats = {"expiry_fallback": 0, "chain_errors": 0, "tickers_failed": []}

    _log("Fetching option chains for {0} tickers (DTE {1}-{2})".format(
        len(tickers), cfg.DTE_MIN, cfg.DTE_MAX))

    all_rows = []
    for symbol in tickers:
        attempt = 0
        rows = []
        while attempt < cfg.MAX_RETRIES_PER_TICKER:
            attempt += 1
            try:
                rows = _fetch_one_ticker(symbol, session, today, stats)
                break
            except Exception as exc:
                _log("  {0}: attempt {1} failed ({2}: {3})".format(
                    symbol, attempt, type(exc).__name__, exc))
                if attempt >= cfg.MAX_RETRIES_PER_TICKER:
                    stats["tickers_failed"].append(symbol)
                else:
                    time.sleep(1.0)
        all_rows.extend(rows)

    ok_tickers = len(set(r["ticker"] for r in all_rows))
    _log("")
    _log("RAW ROWS: {0} contracts across {1}/{2} tickers".format(
        len(all_rows), ok_tickers, len(tickers)))
    if stats["expiry_fallback"]:
        _log("  expiry fallback to label (OCC parse failed): {0} rows".format(
            stats["expiry_fallback"]))
    if stats["chain_errors"]:
        _log("  chain fetch errors: {0}".format(stats["chain_errors"]))
    if stats["tickers_failed"]:
        _log("  tickers with no usable data: {0}".format(
            ", ".join(sorted(set(stats["tickers_failed"])))))

    meta = {
        "fetched_at": dt.datetime.now(dt.timezone.utc).replace(
            microsecond=0).isoformat().replace("+00:00", "Z"),
        "run_date": today.isoformat(),
        "tickers_requested": list(tickers),
        "tickers_ok": sorted(set(r["ticker"] for r in all_rows)),
        "tickers_failed": sorted(set(stats["tickers_failed"])),
        "raw_row_count": len(all_rows),
        "expiry_fallback_rows": stats["expiry_fallback"],
        "chain_errors": stats["chain_errors"],
    }
    return all_rows, meta


if __name__ == "__main__":
    # Smoke test against a couple of liquid names.
    probe = sys.argv[1:] or ["AAPL", "SPY"]
    rows, meta = fetch_chains(probe)
    print("")
    print("meta:", meta)
    if rows:
        print("sample row:", rows[0])

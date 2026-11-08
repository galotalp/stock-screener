"""
options_screener.py — PART 1 of the options tool: the chain screener.

WHAT THIS TOOL IS
-----------------
It measures the structural COST of holding a listed option: the guaranteed,
quantifiable drag paid on entry and over the holding period.

WHAT THIS TOOL IS NOT
---------------------
It is NOT a trade-finder, and must never be described as one. It does not
identify mispriced or underpriced options. Implied volatility is the market's
own volatility estimate, and the probability of finishing in the money is
derived from that same IV — so comparing an option's price against its own
implied probability is circular and will show every contract as fairly priced.

Its use is to identify the LEAST-COSTLY VEHICLE for a directional view already
held on other grounds. It does not generate the view.

Reads its data exclusively from options_source.fetch_chains(), so swapping the
provider requires no change here.

Python 3.9 compatible.
"""
import csv
import datetime as dt
import json
import math
import os
import statistics
import sys

import black_scholes as bs
import options_config as cfg
import options_source

try:
    import market_session
except ImportError:  # pragma: no cover
    market_session = None


def _log(msg):
    print(msg)
    sys.stdout.flush()


def _fmt(value, places=4):
    """Format a float for display, tolerating None/NaN."""
    if value is None:
        return "n/a"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if f != f:
        return "n/a"
    return "{0:.{1}f}".format(f, places)


def _median(values):
    clean = [v for v in values if v is not None and v == v]
    if not clean:
        return None
    return statistics.median(clean)


# ---------------------------------------------------------------------------
# Filtering — print attrition at every step (spec requirement)
# ---------------------------------------------------------------------------

def apply_filters(rows):
    """Liquidity and moneyness filters, logging row counts after each step."""
    _log("")
    _log("FILTERING")
    _log("  start                                  {0:6d} contracts".format(len(rows)))

    step = [r for r in rows if r["spot"] > 0 and r["strike"] > 0]
    _log("  valid spot and strike                  {0:6d}".format(len(step)))

    # Moneyness window: strike within +/-15% of spot.
    step = [r for r in step
            if abs(r["strike"] / r["spot"] - 1.0) <= cfg.MONEYNESS_WINDOW]
    _log("  within +/-{0:.0%} of spot                  {1:6d}".format(
        cfg.MONEYNESS_WINDOW, len(step)))

    step = [r for r in step if cfg.DTE_MIN <= r["dte"] <= cfg.DTE_MAX]
    _log("  DTE in [{0}, {1}]                        {2:6d}".format(
        cfg.DTE_MIN, cfg.DTE_MAX, len(step)))

    if cfg.REQUIRE_NONZERO_BID:
        # A zero bid means the position cannot be exited at ANY price. These are
        # not cheap options, they are untradeable ones.
        step = [r for r in step if r["bid"] > 0]
        _log("  nonzero bid (exitable)                 {0:6d}".format(len(step)))

    step = [r for r in step if r["ask"] > 0]
    _log("  nonzero ask                            {0:6d}".format(len(step)))

    # Crossed quotes (bid > ask) occur in free feeds and produce negative
    # spreads that would silently invert every cost metric.
    step = [r for r in step if r["ask"] >= r["bid"]]
    _log("  not crossed (ask >= bid)               {0:6d}".format(len(step)))

    step = [r for r in step if r["open_interest"] >= cfg.MIN_OPEN_INTEREST]
    _log("  open interest >= {0:<5d}                 {1:6d}".format(
        cfg.MIN_OPEN_INTEREST, len(step)))

    step = [r for r in step if r["volume"] >= cfg.MIN_VOLUME]
    _log("  volume >= {0:<5d}                        {1:6d}".format(
        cfg.MIN_VOLUME, len(step)))

    step = [r for r in step if r["iv"] > 0]
    _log("  positive IV                            {0:6d}".format(len(step)))

    def spread_ok(r):
        mid = (r["bid"] + r["ask"]) / 2.0
        if mid <= 0:
            return False
        return (r["ask"] - r["bid"]) / mid <= cfg.MAX_SPREAD_PCT_MID

    step = [r for r in step if spread_ok(r)]
    _log("  spread <= {0:.0%} of mid                   {1:6d}".format(
        cfg.MAX_SPREAD_PCT_MID, len(step)))

    _log("  SURVIVING                              {0:6d} contracts".format(len(step)))
    return step


# ---------------------------------------------------------------------------
# Per-contract metrics
# ---------------------------------------------------------------------------

def compute_metrics(row):
    """Attach every cost metric to one contract row. Returns a new dict."""
    out = dict(row)

    spot = row["spot"]
    strike = row["strike"]
    bid = row["bid"]
    ask = row["ask"]
    iv = row["iv"]
    is_call = row["option_type"] == "call"

    # Time to expiry in years. A contract expiring today still has intraday
    # life; floor at a fraction of a day so BS does not divide by zero.
    t_years = max(row["dte"], 0.5) / 365.0

    mid = (bid + ask) / 2.0
    spread_abs = ask - bid

    out["mid"] = mid
    out["entry_cost_per_contract"] = ask * cfg.CONTRACT_MULTIPLIER
    out["spread_abs"] = spread_abs
    out["spread_pct_mid"] = spread_abs / mid if mid > 0 else None

    # The immediate, unavoidable loss on a round trip at UNCHANGED quotes.
    # You buy at the ask and sell at the bid, always. This is not a risk, it is
    # a certainty, and it is paid before the position has any chance to work.
    out["round_trip_cost_pct"] = spread_abs / ask if ask > 0 else None

    out["moneyness"] = strike / spot - 1.0

    # Breakeven: the underlying level at which the position recovers the
    # premium paid (ignoring fees; the dollar calculator adds those).
    if is_call:
        breakeven_price = strike + ask
    else:
        breakeven_price = strike - ask
    out["breakeven_price"] = breakeven_price
    out["breakeven_move_pct"] = breakeven_price / spot - 1.0

    # Black-Scholes on the contract's OWN quoted IV.
    g = bs.greeks(spot, strike, t_years, cfg.RISK_FREE_RATE, cfg.DIVIDEND_YIELD,
                  iv, is_call)
    theo = g["theo_price"]
    theta_day = g["theta_day"]

    out["theo_price"] = theo
    out["delta"] = g["delta"]
    out["theta_day"] = theta_day

    # Daily decay as a share of what was paid.
    if ask > 0 and theta_day == theta_day:
        out["theta_pct_of_premium_per_day"] = 100.0 * theta_day / ask
    else:
        out["theta_pct_of_premium_per_day"] = None

    # The bid-ask haircut expressed as an equivalent number of days of decay:
    # "crossing the spread costs you N days of theta before you start".
    if theta_day == theta_day and abs(theta_day) > 1e-12:
        out["spread_in_theta_days"] = spread_abs / abs(theta_day)
    else:
        out["spread_in_theta_days"] = None

    # Quoted ask vs model price on quoted IV — flags stale or crossed quotes.
    if theo == theo and theo > 0:
        out["ask_vs_theo_pct"] = 100.0 * (ask / theo - 1.0)
    else:
        out["ask_vs_theo_pct"] = None

    # One-standard-deviation move over the remaining life.
    sd_move_pct = iv * math.sqrt(t_years)
    out["sd_move_pct"] = sd_move_pct

    # THE KEY NORMALISING METRIC: how many standard deviations of movement are
    # required merely to break even. A raw required move of 2% is trivial for a
    # high-IV name over 60 days and implausible for a low-IV name over 5 days,
    # so expressing it in SDs makes required moves comparable across tickers and
    # horizons. Roughly: 0.5 = half a typical move; 1.0 = a typical move;
    # 2.0+ = a two-sigma move (~5% odds one-sided under a normal assumption),
    # which is where cheap OTM contracts live.
    if sd_move_pct > 0:
        out["breakeven_in_sds"] = abs(out["breakeven_move_pct"]) / sd_move_pct
    else:
        out["breakeven_in_sds"] = None

    # RISK-NEUTRAL probability of finishing ITM, backed out of the contract's
    # own quoted IV. NOT A FORECAST — see black_scholes.n_d2 for why.
    out["p_itm"] = g["n_d2"]

    # WHY BOTH p_itm AND breakeven_in_sds ARE NEEDED
    # ----------------------------------------------
    # They measure different things and can disagree usefully. `p_itm` is the
    # chance of finishing PAST THE STRIKE; `breakeven_in_sds` is the move needed
    # to also RECOVER THE PREMIUM PAID. A contract can have a 40% chance of
    # finishing ITM while needing 1.4 SDs to break even — the holder is
    # directionally right and still loses money. The gap between the two is the
    # premium, expressed in units of movement.

    # LEAST-FRICTION RANKING METRIC — not an expected-return metric.
    # Round-trip spread cost plus the theta that will be paid over the horizon.
    # Both are costs; neither says anything about whether the trade will work.
    rt = out["round_trip_cost_pct"]
    tp = out["theta_pct_of_premium_per_day"]
    if rt is not None and tp is not None:
        out["drag_7d_pct"] = 100.0 * rt + abs(tp) * cfg.DRAG_HORIZON_DAYS
    else:
        out["drag_7d_pct"] = None

    return out


def screen(rows):
    """Filter, then compute metrics. Returns contracts with metrics attached."""
    survivors = apply_filters(rows)
    with_metrics = [compute_metrics(r) for r in survivors]

    # Drop rows where the model produced nothing usable (degenerate inputs).
    usable = [r for r in with_metrics if r["drag_7d_pct"] is not None]
    dropped = len(with_metrics) - len(usable)
    if dropped:
        _log("  dropped {0} rows with unusable model output (NaN)".format(dropped))
    _log("  WITH METRICS                           {0:6d} contracts".format(len(usable)))
    return usable


# ---------------------------------------------------------------------------
# Ranking and summary output
# ---------------------------------------------------------------------------

_RANK_HEADER = (
    "{0:<6s} {1:<4s} {2:>9s} {3:>7s} {4:>4s} {5:>8s} {6:>8s} {7:>8s} "
    "{8:>8s} {9:>7s} {10:>8s}".format(
        "TICKER", "TYPE", "EXPIRY", "STRIKE", "DTE", "ASK", "RT_COST%",
        "THETA%/D", "DRAG7D%", "P_ITM", "BE_SDS"))


def _rank_line(r):
    return ("{0:<6s} {1:<4s} {2:>9s} {3:>7.2f} {4:>4d} {5:>8.2f} {6:>8.2f} "
            "{7:>8.2f} {8:>8.2f} {9:>7.3f} {10:>8.2f}".format(
                r["ticker"], r["option_type"], r["expiry"], r["strike"], r["dte"],
                r["ask"], 100.0 * r["round_trip_cost_pct"],
                r["theta_pct_of_premium_per_day"], r["drag_7d_pct"],
                r["p_itm"] if r["p_itm"] == r["p_itm"] else float("nan"),
                r["breakeven_in_sds"] if r["breakeven_in_sds"] is not None else float("nan")))


def print_ranking(contracts):
    """The LEAST-FRICTION ranking. NOT an expected-return ranking.

    Sorting ascending by drag surfaces the cheapest vehicles to hold, which is
    a statement about cost only. A contract at the top of this list is not
    "a good trade" — it is the least expensive way to express a view you have
    already formed elsewhere.
    """
    _log("")
    _log("=" * 100)
    _log("LOWEST-DRAG CONTRACTS (least-friction ranking — NOT expected return)")
    _log("  drag_7d_pct = round-trip spread cost + {0} days of theta, as % of premium".format(
        cfg.DRAG_HORIZON_DAYS))
    _log("=" * 100)

    ranked = sorted(contracts, key=lambda r: r["drag_7d_pct"])
    _log(_RANK_HEADER)
    for r in ranked[:cfg.TOP_N_DISPLAY]:
        _log(_rank_line(r))
    return ranked


def _summary_block(contracts, label):
    """Print medians for one cohort of contracts."""
    _log("")
    _log("  {0}  (n = {1})".format(label, len(contracts)))
    if not contracts:
        _log("    no contracts in this cohort")
        return None

    stats = {
        "n": len(contracts),
        "round_trip_cost_pct": _median([100.0 * c["round_trip_cost_pct"]
                                        for c in contracts]),
        "theta_pct_per_day": _median([c["theta_pct_of_premium_per_day"]
                                      for c in contracts]),
        "p_itm": _median([c["p_itm"] for c in contracts]),
        "breakeven_move_pct": _median([100.0 * c["breakeven_move_pct"]
                                       for c in contracts]),
        "breakeven_in_sds": _median([c["breakeven_in_sds"] for c in contracts]),
    }

    _log("    median round-trip spread cost   {0} %".format(
        _fmt(stats["round_trip_cost_pct"], 2)))
    _log("    median theta burn per day       {0} % of premium".format(
        _fmt(stats["theta_pct_per_day"], 2)))
    _log("    median implied P(ITM)           {0}   [risk-neutral, NOT a forecast]".format(
        _fmt(stats["p_itm"], 3)))
    _log("    median breakeven move           {0} %".format(
        _fmt(stats["breakeven_move_pct"], 2)))
    _log("    median breakeven in SDs         {0}".format(
        _fmt(stats["breakeven_in_sds"], 2)))
    return stats


def print_summary(contracts):
    """Universe medians, then the cheap-contract cohort breakout."""
    _log("")
    _log("=" * 100)
    _log("SUMMARY — medians across the screened universe")
    _log("=" * 100)

    universe = _summary_block(contracts, "ALL SCREENED CONTRACTS")

    # The "cheap lottery ticket" cohort. Contracts that look cheapest in dollar
    # terms are typically the WORST on every one of these measures. Printing the
    # comparison makes that visible rather than leaving it as folklore.
    cheap = [c for c in contracts if c["ask"] <= cfg.CHEAP_CONTRACT_MAX_ASK]
    cheap_stats = _summary_block(
        cheap, "CONTRACTS AT OR BELOW ${0:.2f} — the 'cheap lottery ticket' cohort".format(
            cfg.CHEAP_CONTRACT_MAX_ASK))

    if not cheap:
        _log("    FINDING: no contract priced at or below ${0:.2f} survived the".format(
            cfg.CHEAP_CONTRACT_MAX_ASK))
        _log("    liquidity filters. Cheap contracts tend to fail the open-interest,")
        _log("    volume, spread, or nonzero-bid tests — which is itself the finding:")
        _log("    at these prices the spread is usually a large fraction of the premium.")
    elif universe is not None and cheap_stats is not None:
        _log("")
        _log("    COMPARISON (cheap cohort vs whole universe):")
        for key, name in (("round_trip_cost_pct", "round-trip cost %"),
                          ("theta_pct_per_day", "theta % per day"),
                          ("breakeven_in_sds", "breakeven in SDs")):
            u, c = universe.get(key), cheap_stats.get(key)
            if u is not None and c is not None:
                _log("      {0:<22s} universe {1:>8s}   cheap {2:>8s}".format(
                    name, _fmt(u, 2), _fmt(c, 2)))

    return universe, cheap_stats


# ---------------------------------------------------------------------------
# Term structure
# ---------------------------------------------------------------------------

def print_term_structure(contracts):
    """Mean ATM IV by ticker and DTE, as a wide table.

    WHY THIS MATTERS: an INVERTED term structure — near-dated IV above
    far-dated — usually indicates an event or stress episode priced into the
    front month. That is the classic decay setup: the elevated front-month
    premium collapses once the event passes, and a holder of near-dated
    premium pays for that collapse even when directionally right.
    """
    _log("")
    _log("=" * 100)
    _log("TERM STRUCTURE — mean ATM IV (|moneyness| <= {0:.0%}) by DTE".format(
        cfg.ATM_MONEYNESS))
    _log("  inverted (near-dated IV > far-dated) usually means an event is priced")
    _log("  into the front month — the classic decay setup")
    _log("=" * 100)

    atm = [c for c in contracts if abs(c["moneyness"]) <= cfg.ATM_MONEYNESS]
    _log("  near-the-money contracts: {0}".format(len(atm)))
    if not atm:
        _log("  (none — cannot build term structure this run)")
        return {}

    # Group by ticker, then by expiry (keyed on DTE for column ordering).
    grouped = {}
    for c in atm:
        grouped.setdefault(c["ticker"], {}).setdefault(c["dte"], []).append(c["iv"])

    all_dtes = sorted(set(d for per in grouped.values() for d in per))
    # Cap displayed columns so the table stays readable in a terminal.
    show_dtes = all_dtes[:12]

    header = "{0:<7s}".format("TICKER") + "".join(
        "{0:>8s}".format(str(d) + "d") for d in show_dtes)
    _log("")
    _log(header)

    table = {}
    for ticker in sorted(grouped):
        cells = []
        row_out = {}
        for d in show_dtes:
            ivs = grouped[ticker].get(d)
            if ivs:
                mean_iv = sum(ivs) / len(ivs)
                row_out[d] = mean_iv
                cells.append("{0:>8.3f}".format(mean_iv))
            else:
                cells.append("{0:>8s}".format("-"))
        table[ticker] = row_out
        _log("{0:<7s}".format(ticker) + "".join(cells))

    # Flag inversions explicitly rather than making the user read the table.
    inverted = []
    for ticker, per_dte in table.items():
        dtes = sorted(per_dte)
        if len(dtes) >= 2:
            near, far = per_dte[dtes[0]], per_dte[dtes[-1]]
            if near > far * 1.05:  # 5% buffer to avoid flagging noise
                inverted.append((ticker, dtes[0], near, dtes[-1], far))

    if inverted:
        _log("")
        _log("  INVERTED TERM STRUCTURE (front-month IV materially above back):")
        for ticker, nd, niv, fd, fiv in sorted(inverted, key=lambda x: -(x[2] / x[4])):
            _log("    {0:<6s} {1}d IV {2:.3f}  vs  {3}d IV {4:.3f}   ratio {5:.2f}".format(
                ticker, nd, niv, fd, fiv, niv / fiv))

    return table


# ---------------------------------------------------------------------------
# History log — the component that compounds in value
# ---------------------------------------------------------------------------
# RATIONALE: IV rank requires a DISTRIBUTION of past IV, which no single
# snapshot can provide. A one-off screen can tell you today's IV but not whether
# that is high or low for this name. Accumulating this log is what makes it
# possible to compute genuine IV percentiles per ticker — and therefore to TEST
# whether volatility mean-reverts in these names rather than assuming it does.
# This file is append-only and is the most valuable artefact the tool produces.

_HISTORY_FIELDS = ["run_ts", "run_date", "ticker", "expiry", "dte", "atm_iv",
                   "contract_count"]


def append_history(contracts, meta):
    """Append one compact record per (ticker, expiry) to the persistent CSV."""
    cfg.ensure_dirs()

    atm = [c for c in contracts if abs(c["moneyness"]) <= cfg.ATM_MONEYNESS]
    grouped = {}
    for c in atm:
        grouped.setdefault((c["ticker"], c["expiry"], c["dte"]), []).append(c["iv"])

    records = []
    for (ticker, expiry, dte), ivs in sorted(grouped.items()):
        records.append({
            "run_ts": meta["fetched_at"],
            "run_date": meta["run_date"],
            "ticker": ticker,
            "expiry": expiry,
            "dte": dte,
            "atm_iv": round(sum(ivs) / len(ivs), 6),
            "contract_count": len(ivs),
        })

    is_new = not os.path.exists(cfg.HISTORY_FILE)
    with open(cfg.HISTORY_FILE, "a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_HISTORY_FIELDS)
        if is_new:
            writer.writeheader()
        for rec in records:
            writer.writerow(rec)

    _log("")
    _log("HISTORY LOG: appended {0} records to {1}".format(
        len(records), os.path.relpath(cfg.HISTORY_FILE)))
    return records


def load_history():
    """Read the accumulated history CSV. Returns [] if absent."""
    if not os.path.exists(cfg.HISTORY_FILE):
        return []
    out = []
    with open(cfg.HISTORY_FILE, "r", newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                row["dte"] = int(row["dte"])
                row["atm_iv"] = float(row["atm_iv"])
            except (TypeError, ValueError):
                continue
            out.append(row)
    return out


def print_iv_rank():
    """IV rank per ticker, once the log spans enough distinct run dates.

    IV rank = (current_IV - min_IV) / (max_IV - min_IV) over the accumulated
    history for the 25-65 DTE band. Below the minimum number of run dates the
    range is too narrow for the ratio to mean anything, so the tool reports how
    many more runs are needed instead of printing a misleading number.
    """
    history = load_history()
    run_dates = sorted(set(r["run_date"] for r in history))

    _log("")
    _log("=" * 100)
    _log("IV RANK ({0}-{1} DTE band)".format(cfg.IV_RANK_DTE_MIN, cfg.IV_RANK_DTE_MAX))
    _log("=" * 100)

    if len(run_dates) < cfg.IV_RANK_MIN_RUN_DATES:
        needed = cfg.IV_RANK_MIN_RUN_DATES - len(run_dates)
        _log("  history spans {0} distinct run date(s); need {1} more before IV rank".format(
            len(run_dates), needed))
        _log("  is meaningful. A percentile computed from a handful of observations")
        _log("  describes the sample, not the distribution.")
        return {}

    band = [r for r in history
            if cfg.IV_RANK_DTE_MIN <= r["dte"] <= cfg.IV_RANK_DTE_MAX]
    latest_date = run_dates[-1]

    per_ticker = {}
    for r in band:
        per_ticker.setdefault(r["ticker"], []).append(r)

    ranks = {}
    for ticker, rows in per_ticker.items():
        ivs = [r["atm_iv"] for r in rows]
        current = [r["atm_iv"] for r in rows if r["run_date"] == latest_date]
        if not current or len(ivs) < 2:
            continue
        lo, hi = min(ivs), max(ivs)
        if hi - lo < 1e-9:
            continue
        cur = sum(current) / len(current)
        ranks[ticker] = {
            "iv_rank": (cur - lo) / (hi - lo),
            "current_iv": cur, "min_iv": lo, "max_iv": hi,
            "observations": len(ivs),
        }

    _log("  {0:<7s} {1:>9s} {2:>10s} {3:>9s} {4:>9s} {5:>6s}".format(
        "TICKER", "IV_RANK", "CURRENT", "MIN", "MAX", "N"))
    for ticker, d in sorted(ranks.items(), key=lambda kv: -kv[1]["iv_rank"]):
        _log("  {0:<7s} {1:>9.3f} {2:>10.3f} {3:>9.3f} {4:>9.3f} {5:>6d}".format(
            ticker, d["iv_rank"], d["current_iv"], d["min_iv"], d["max_iv"],
            d["observations"]))
    return ranks


def write_snapshot(contracts, meta):
    """Full timestamped CSV of every screened contract, plus a latest JSON.

    The CSV is the per-run archive; the JSON is what the site generator and the
    simulation cascade read, so they never re-fetch from the network.
    """
    cfg.ensure_dirs()

    stamp = meta["fetched_at"].replace(":", "").replace("-", "")
    csv_path = os.path.join(cfg.SNAPSHOT_DIR, "screen_{0}.csv".format(stamp))

    if contracts:
        fields = sorted(contracts[0].keys())
        with open(csv_path, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            for c in contracts:
                writer.writerow(c)
        _log("SNAPSHOT:    {0} contracts -> {1}".format(
            len(contracts), os.path.relpath(csv_path)))

    payload = {"meta": meta, "contracts": contracts}
    with open(cfg.LATEST_JSON, "w") as fh:
        json.dump(payload, fh)
    _log("LATEST JSON: {0}".format(os.path.relpath(cfg.LATEST_JSON)))

    prune_snapshots()
    return csv_path


def prune_snapshots():
    """Delete the oldest snapshot CSVs beyond the retention limit.

    Only the per-run snapshot archive is pruned. iv_history.csv is never touched
    — it is the artefact that compounds in value, and it is small enough that
    keeping it forever costs nothing.
    """
    keep = getattr(cfg, "SNAPSHOT_RETENTION", 0)
    if not keep or keep <= 0:
        return

    if not os.path.isdir(cfg.SNAPSHOT_DIR):
        return

    files = sorted(
        (f for f in os.listdir(cfg.SNAPSHOT_DIR)
         if f.startswith("screen_") and f.endswith(".csv")))
    excess = len(files) - keep
    if excess <= 0:
        return

    for name in files[:excess]:
        try:
            os.remove(os.path.join(cfg.SNAPSHOT_DIR, name))
        except OSError:
            pass
    _log("  pruned {0} snapshot(s) beyond the {1}-run retention limit".format(
        excess, keep))


# ---------------------------------------------------------------------------
# Quote freshness
# ---------------------------------------------------------------------------

def quote_session(meta):
    """Which market session the quotes were pulled in.

    The spec is emphatic that quotes must be pulled while the market is open:
    after-close quotes are stale and spreads printed at the open are
    unrepresentatively wide. This pipeline is invoked by an external boot
    sequence whose timing this tool does not control, so rather than assume,
    it detects and labels. The simulation refuses to open positions on
    non-regular-session quotes.
    """
    if market_session is None:
        return "unknown"
    try:
        return market_session.session_of(meta["fetched_at"])
    except Exception:
        return "unknown"


def run(tickers=None):
    """Full Part 1 screen. Returns (contracts, meta)."""
    started = dt.datetime.now()

    rows, meta = options_source.fetch_chains(tickers)
    contracts = screen(rows)

    session = quote_session(meta)
    meta["quote_session"] = session
    meta["screened_count"] = len(contracts)

    _log("")
    _log("QUOTE FRESHNESS: pulled during '{0}' session ({1})".format(
        session, meta["fetched_at"]))
    if session != "regular":
        _log("  WARNING: quotes were NOT pulled during the regular session.")
        _log("  Spreads outside regular hours are unrepresentatively wide and")
        _log("  last prices are stale. Every figure below is INDICATIVE ONLY.")
        _log("  The simulation will not open positions on these quotes.")

    if not contracts:
        _log("")
        _log("No contracts survived the filters. Nothing further to report.")
        return contracts, meta

    print_ranking(contracts)
    universe_stats, cheap_stats = print_summary(contracts)
    term = print_term_structure(contracts)
    append_history(contracts, meta)
    print_iv_rank()
    write_snapshot(contracts, meta)

    meta["summary"] = {"universe": universe_stats, "cheap": cheap_stats}
    meta["term_structure"] = {t: {str(k): v for k, v in d.items()}
                              for t, d in term.items()}

    elapsed = (dt.datetime.now() - started).total_seconds()
    _log("")
    _log("Done in {0:.1f}s".format(elapsed))

    _log("")
    _log("-" * 100)
    _log("CAVEATS — these apply to every figure above")
    _log("  1. This is NOT a mispricing detector. Nothing in a chain snapshot")
    _log("     identifies underpriced options; the quotes are the market's estimate,")
    _log("     set by participants with faster data and better execution.")
    _log("  2. p_itm is RISK-NEUTRAL, not predictive. It is the market's implied")
    _log("     probability, useful only for calibration against your own view.")
    _log("  3. SD scaling assumes lognormal returns. Real equity returns have fat")
    _log("     tails, so genuine 2-SD moves occur more often than implied —")
    _log("     especially to the downside and around catalysts. breakeven_in_sds")
    _log("     is a comparability ruler, not a probability.")
    _log("  4. Free-feed quotes are delayed and sometimes crossed. Any figure")
    _log("     derived from them is indicative, not executable.")
    _log("-" * 100)

    return contracts, meta


if __name__ == "__main__":
    args = sys.argv[1:]
    run(args if args else None)

"""
sim_cascade.py — the sentiment -> options simulation.

THE SELECTION RULE
------------------
Four gates, in order. A ticker must clear every one before a position is
simulated:

  STAGE 1  INTERSECT      ticker is in the options universe AND has sentiment
  STAGE 2  PREDICTIVENESS news historically LEADS regular-session price for
                          this name, on enough observations to mean something
  STAGE 3  UNREFLECTED    sentiment has moved recently and price has NOT yet
                          followed
  STAGE 4  VEHICLE        a contract exists that expresses the view without
                          excessive friction

Most days, nothing passes. THAT IS THE INTENDED BEHAVIOUR — a rule that always
finds a trade is not a rule, it is a formality. Every rejection is logged with
the gate that stopped it and how far short the ticker fell, so the ledger is
informative from the first run and it is visible when a gate starts to bind.

READ-ONLY with respect to the sentiment pipeline. This module never writes to
data/mentions/, data/prices/, or data/earnings/. It consumes what the sentiment
app has already collected and adds nothing to it.

WHAT THIS IS NOT
----------------
Not a claim that options are mispriced. The options screener measures COST, not
value. This cascade supplies a DIRECTIONAL VIEW from an independent source
(sentiment) and then uses the screener to find the least-costly vehicle for it.
Whether the view is any good is the open question the ledger exists to answer.

Python 3.9 compatible.
"""
import datetime as dt
import glob
import json
import os
import sys

import options_config as cfg
import sim_config as scfg

try:
    import market_session
except ImportError:  # pragma: no cover
    market_session = None


def _log(msg):
    print(msg)
    sys.stdout.flush()


def _utc_now_iso():
    return dt.datetime.now(dt.timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_date(value):
    """Parse an ISO date or datetime into a date object; None on failure."""
    if not value:
        return None
    text = str(value)
    try:
        if "T" in text:
            return dt.datetime.fromisoformat(
                text.replace("Z", "+00:00")).date()
        return dt.datetime.strptime(text[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Loading the sentiment side (read-only)
# ---------------------------------------------------------------------------

def load_mentions(ticker):
    """All mentions for one ticker from data/mentions/<T>.jsonl.

    Returns a list of dicts with at least: date, source, score, session.
    Missing file returns []. Malformed lines are skipped, not fatal.
    """
    path = os.path.join(cfg.MENTIONS_DIR, "{0}.jsonl".format(ticker))
    if not os.path.exists(path):
        return []

    out = []
    with open(path, "r") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if "date" in rec and "score" in rec:
                out.append(rec)
    return out


def load_prices(ticker):
    """Daily closes from data/prices/<T>.json as {date_str: close}."""
    path = os.path.join(cfg.PRICES_DIR, "{0}.json".format(ticker))
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as fh:
            rows = json.load(fh)
    except ValueError:
        return {}

    out = {}
    for row in rows:
        date = row.get("date")
        close = row.get("close")
        if date and close:
            out[str(date)[:10]] = float(close)
    return out


def load_earnings(ticker):
    """Forward and past earnings events for one ticker."""
    path = os.path.join(cfg.EARNINGS_DIR, "{0}.json".format(ticker))
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r") as fh:
            payload = json.load(fh)
    except ValueError:
        return []
    return payload.get("events", [])


def load_session_analysis():
    """The lead/lag output from analyze_sessions.py. {} if absent."""
    if not os.path.exists(cfg.SESSION_ANALYSIS):
        return {}
    try:
        with open(cfg.SESSION_ANALYSIS, "r") as fh:
            return json.load(fh)
    except ValueError:
        return {}


def sentiment_tickers():
    """Tickers with a mentions file on disk."""
    pattern = os.path.join(cfg.MENTIONS_DIR, "*.jsonl")
    return set(os.path.basename(p)[:-6] for p in glob.glob(pattern))


# ---------------------------------------------------------------------------
# STAGE 1 — intersect the two universes
# ---------------------------------------------------------------------------

def stage1_intersect(options_universe):
    """Tickers present in BOTH the options screen and the sentiment store.

    The two universes are chosen for different reasons and will never fully
    agree: the options list is picked for contract liquidity, the sentiment list
    is an equity study that structurally excludes index ETFs (SPY/QQQ/IWM are
    in ETF_LIKE in build_universe_dynamic.py and will never appear). That is by
    design, not a defect.
    """
    have_sentiment = sentiment_tickers()
    both = sorted(set(options_universe) & have_sentiment)
    missing = sorted(set(options_universe) - have_sentiment)

    _log("")
    _log("STAGE 1 — INTERSECT options universe with sentiment coverage")
    _log("  options universe:      {0} tickers".format(len(set(options_universe))))
    _log("  sentiment coverage:    {0} tickers".format(len(have_sentiment)))
    _log("  INTERSECTION:          {0} tickers -> {1}".format(
        len(both), ", ".join(both) if both else "(none)"))
    if missing:
        _log("  no sentiment data:     {0}".format(", ".join(missing)))
    return both, missing


# ---------------------------------------------------------------------------
# STAGE 2 — does news lead price for this name?
# ---------------------------------------------------------------------------

def stage2_predictiveness(tickers, analysis):
    """Keep tickers where sentiment demonstrably LEADS regular-session price.

    Uses the cross-correlation already computed by analyze_sessions.py rather
    than recomputing it: that module owns the hourly alignment and the earnings
    regime split, and duplicating the logic here would let the two drift.

    Four conditions, all required:
      1. enough hourly observations for a correlation to be meaningful
      2. |corr| at the best lag clears the threshold
      3. the best lag is NEGATIVE — sentiment moves BEFORE price. A positive
         lag means price leads sentiment, which is commentary, not prediction.
      4. the sign agrees between the all-hours and ex-earnings regimes. If
         removing earnings flips the sign, the correlation was an artifact of a
         handful of event windows.
    """
    _log("")
    _log("STAGE 2 — PREDICTIVENESS: does news lead regular-session price?")
    _log("  requires: n_sent_hours >= {0}, |corr| >= {1}, negative lag, stable sign".format(
        scfg.MIN_SENT_HOURS, scfg.MIN_LEAD_CORR))

    passed, rejected = [], []

    for ticker in tickers:
        entry = analysis.get(ticker)
        if not entry:
            rejected.append((ticker, "no lead/lag analysis available", None))
            continue

        n_hours = entry.get("n_sent_hours") or 0
        regimes = entry.get("regime") or {}
        all_regime = regimes.get("all") or {}
        ex_earn = regimes.get("ex_earn") or {}

        chosen = ex_earn if (scfg.USE_EX_EARN_REGIME and ex_earn.get("best_corr")
                             is not None) else all_regime
        corr = chosen.get("best_corr")
        lag = chosen.get("best_lag_h")

        detail = {"n_sent_hours": n_hours, "corr": corr, "lag_h": lag,
                  "corr_all": all_regime.get("best_corr"),
                  "corr_ex_earn": ex_earn.get("best_corr")}

        # 1 — sample size. This is the gate that currently stops nearly
        # everything, and it should: see the note in sim_config.MIN_SENT_HOURS.
        if n_hours < scfg.MIN_SENT_HOURS:
            rejected.append((ticker, "insufficient history: {0} sentiment-hours, need {1}".format(
                n_hours, scfg.MIN_SENT_HOURS), detail))
            continue

        if corr is None or lag is None:
            rejected.append((ticker, "no usable correlation", detail))
            continue

        # 2 — magnitude
        if abs(corr) < scfg.MIN_LEAD_CORR:
            rejected.append((ticker, "weak correlation {0:+.3f}, need |{1}|".format(
                corr, scfg.MIN_LEAD_CORR), detail))
            continue

        # 3 — direction of causality
        if scfg.REQUIRE_NEGATIVE_LAG and lag >= 0:
            rejected.append((ticker, "lag {0:+d}h — price leads sentiment, not the reverse".format(
                lag), detail))
            continue

        # 4 — sign stability across regimes
        if scfg.REQUIRE_SIGN_STABILITY:
            c_all = all_regime.get("best_corr")
            c_ex = ex_earn.get("best_corr")
            if c_all is not None and c_ex is not None:
                if (c_all > 0) != (c_ex > 0):
                    rejected.append((ticker,
                                     "sign flips between regimes ({0:+.3f} all vs {1:+.3f} ex-earn) "
                                     "— artifact of earnings windows".format(c_all, c_ex), detail))
                    continue

        passed.append({"ticker": ticker, "corr": corr, "lag_h": lag,
                       "n_sent_hours": n_hours,
                       "direction": 1 if corr > 0 else -1})

    for ticker, reason, _d in rejected:
        _log("    reject {0:<6s} {1}".format(ticker, reason))
    _log("  PASSED: {0} ticker(s){1}".format(
        len(passed),
        " -> " + ", ".join(p["ticker"] for p in passed) if passed else ""))

    return passed, rejected


# ---------------------------------------------------------------------------
# STAGE 3 — sentiment moved, price has not
# ---------------------------------------------------------------------------

def _daily_mean_scores(mentions):
    """Mean sentiment score per calendar date -> {date_str: (mean, count)}."""
    buckets = {}
    for m in mentions:
        date = _parse_date(m.get("date"))
        if date is None:
            continue
        try:
            score = float(m.get("score", 0.0))
        except (TypeError, ValueError):
            continue
        buckets.setdefault(date.isoformat(), []).append(score)
    return dict((d, (sum(v) / len(v), len(v))) for d, v in buckets.items())


def stage3_unreflected(candidates, as_of):
    """Keep tickers where sentiment has shifted but price has not yet followed.

    The logic: compare recent mean sentiment against a longer baseline. If the
    change is material AND the price has barely moved over the same window, the
    information has not been absorbed. If price has already moved with
    sentiment, it is priced in and there is nothing left to capture.
    """
    _log("")
    _log("STAGE 3 — UNREFLECTED: sentiment moved, price has not followed")
    _log("  requires: |sentiment change| >= {0} over {1}d vs {2}d baseline,".format(
        scfg.MIN_SENT_CHANGE, scfg.SENT_LOOKBACK_DAYS, scfg.SENT_BASELINE_DAYS))
    _log("            >= {0} recent mentions, |price move| < {1}%".format(
        scfg.MIN_MENTIONS_RECENT, scfg.MAX_PRICE_MOVE_PCT))

    passed, rejected = [], []

    for cand in candidates:
        ticker = cand["ticker"]
        mentions = load_mentions(ticker)
        daily = _daily_mean_scores(mentions)

        if not daily:
            rejected.append((ticker, "no dated mentions", None))
            continue

        recent_start = as_of - dt.timedelta(days=scfg.SENT_LOOKBACK_DAYS)
        base_start = as_of - dt.timedelta(days=scfg.SENT_BASELINE_DAYS)

        recent_scores, recent_count = [], 0
        base_scores = []
        for date_str, (mean, count) in daily.items():
            date = _parse_date(date_str)
            if date is None:
                continue
            if recent_start < date <= as_of:
                recent_scores.append(mean)
                recent_count += count
            elif base_start < date <= recent_start:
                base_scores.append(mean)

        detail = {"recent_days": len(recent_scores), "recent_mentions": recent_count,
                  "baseline_days": len(base_scores)}

        if recent_count < scfg.MIN_MENTIONS_RECENT:
            rejected.append((ticker,
                             "only {0} mention(s) in last {1}d, need {2}".format(
                                 recent_count, scfg.SENT_LOOKBACK_DAYS,
                                 scfg.MIN_MENTIONS_RECENT), detail))
            continue

        if not base_scores:
            rejected.append((ticker, "no baseline sentiment in the prior {0}d window".format(
                scfg.SENT_BASELINE_DAYS - scfg.SENT_LOOKBACK_DAYS), detail))
            continue

        recent_mean = sum(recent_scores) / len(recent_scores)
        base_mean = sum(base_scores) / len(base_scores)
        change = recent_mean - base_mean
        detail.update({"recent_mean": recent_mean, "baseline_mean": base_mean,
                       "sentiment_change": change})

        if abs(change) < scfg.MIN_SENT_CHANGE:
            rejected.append((ticker, "sentiment change {0:+.3f}, need |{1}|".format(
                change, scfg.MIN_SENT_CHANGE), detail))
            continue

        # Has price already moved?
        prices = load_prices(ticker)
        if not prices:
            rejected.append((ticker, "no price history to test against", detail))
            continue

        dated = sorted(prices.items())
        recent_px = [(d, p) for d, p in dated if _parse_date(d) and
                     recent_start < _parse_date(d) <= as_of]
        prior_px = [(d, p) for d, p in dated if _parse_date(d) and
                    _parse_date(d) <= recent_start]

        if not recent_px or not prior_px:
            rejected.append((ticker, "insufficient price history around the window", detail))
            continue

        px_then = prior_px[-1][1]
        px_now = recent_px[-1][1]
        move_pct = 100.0 * (px_now / px_then - 1.0) if px_then else 0.0
        detail.update({"price_then": px_then, "price_now": px_now,
                       "price_move_pct": move_pct})

        if abs(move_pct) >= scfg.MAX_PRICE_MOVE_PCT:
            rejected.append((ticker,
                             "price already moved {0:+.2f}% — likely priced in".format(
                                 move_pct), detail))
            continue

        # Direction: sentiment change, adjusted by the SIGN of the lead/lag
        # correlation. A negative correlation means this name reliably moves
        # AGAINST its sentiment, so a bullish shift implies a bearish position.
        # Trusting a negative correlation is a strong claim on thin data, which
        # is exactly why stage 2's sample-size gate exists upstream.
        direction = 1 if change > 0 else -1
        if cand["direction"] < 0:
            direction = -direction

        # If price has already drifted the way the thesis points, the gap is
        # partly closed and the edge is weaker.
        if scfg.REQUIRE_DIRECTIONAL_GAP and direction * move_pct > 0:
            rejected.append((ticker,
                             "price already drifting with the signal ({0:+.2f}%)".format(
                                 move_pct), detail))
            continue

        cand = dict(cand)
        cand.update(detail)
        cand["trade_direction"] = direction
        cand["option_type"] = "call" if direction > 0 else "put"
        passed.append(cand)

    for ticker, reason, _d in rejected:
        _log("    reject {0:<6s} {1}".format(ticker, reason))
    _log("  PASSED: {0} ticker(s){1}".format(
        len(passed),
        " -> " + ", ".join(
            "{0} {1}".format(p["ticker"], p["option_type"]) for p in passed)
        if passed else ""))

    return passed, rejected


# ---------------------------------------------------------------------------
# STAGE 4 — pick the vehicle
# ---------------------------------------------------------------------------

def _earnings_conflict(ticker, entry_date, expiry_date):
    """(blocks_entry, crosses_expiry) for earnings near this position.

    A position held through an earnings release is dominated by the event, not
    by the sentiment signal under test. Since the object is to learn whether
    sentiment predicts direction, a contaminated position is worse than none.
    """
    events = load_earnings(ticker)
    blocks, crosses = False, False
    for ev in events:
        ev_date = _parse_date(ev.get("ts"))
        if ev_date is None:
            continue
        days_away = (ev_date - entry_date).days
        if 0 <= days_away <= scfg.AVOID_EARNINGS_WITHIN_DAYS:
            blocks = True
        if expiry_date and entry_date < ev_date <= expiry_date:
            crosses = True
    return blocks, crosses


def stage4_select_contract(candidates, contracts, as_of):
    """Choose the least-costly contract expressing each surviving view."""
    _log("")
    _log("STAGE 4 — VEHICLE: least-costly contract for the view")
    _log("  requires: DTE {0}-{1}, breakeven <= {2} SDs, drag7d <= {3}%, |moneyness| <= {4}".format(
        scfg.TARGET_DTE_MIN, scfg.TARGET_DTE_MAX, scfg.MAX_BREAKEVEN_SDS,
        scfg.MAX_DRAG_7D_PCT, scfg.MAX_MONEYNESS))

    by_ticker = {}
    for c in contracts:
        by_ticker.setdefault(c["ticker"], []).append(c)

    selected, rejected = [], []

    for cand in candidates:
        ticker = cand["ticker"]
        want_type = cand["option_type"]
        pool = [c for c in by_ticker.get(ticker, []) if c["option_type"] == want_type]

        if not pool:
            rejected.append((ticker, "no {0} contracts in the screen".format(want_type), None))
            continue

        before = len(pool)
        pool = [c for c in pool
                if scfg.TARGET_DTE_MIN <= c["dte"] <= scfg.TARGET_DTE_MAX]
        if not pool:
            rejected.append((ticker, "no contracts in {0}-{1} DTE window (had {2})".format(
                scfg.TARGET_DTE_MIN, scfg.TARGET_DTE_MAX, before), None))
            continue

        pool = [c for c in pool if abs(c["moneyness"]) <= scfg.MAX_MONEYNESS]
        if not pool:
            rejected.append((ticker, "no contracts within {0} moneyness".format(
                scfg.MAX_MONEYNESS), None))
            continue

        pool = [c for c in pool if c.get("breakeven_in_sds") is not None
                and c["breakeven_in_sds"] <= scfg.MAX_BREAKEVEN_SDS]
        if not pool:
            rejected.append((ticker, "all contracts need > {0} SDs to break even".format(
                scfg.MAX_BREAKEVEN_SDS), None))
            continue

        pool = [c for c in pool if c["drag_7d_pct"] <= scfg.MAX_DRAG_7D_PCT]
        if not pool:
            rejected.append((ticker, "all contracts exceed {0}% 7-day drag".format(
                scfg.MAX_DRAG_7D_PCT), None))
            continue

        # AFFORDABILITY — a selection criterion, not an afterthought.
        #
        # Ranking on drag alone systematically favours deep in-the-money
        # contracts: they carry the least spread and theta as a share of
        # premium, which is exactly what drag_7d_pct measures. But a $205 strike
        # on a $224 stock costs $2,430 per contract, and against a $1,000 budget
        # that buys zero contracts.
        #
        # Filtering here rather than at sizing time matters: if the single
        # lowest-drag contract is unaffordable, the correct behaviour is to pick
        # the best AFFORDABLE one, not to abandon the trade. Discovering the
        # problem after selection would silently drop a position that the
        # cascade had already justified.
        affordable = [c for c in pool
                      if c["ask"] * cfg.CONTRACT_MULTIPLIER <= scfg.SIM_BUDGET]
        if not affordable:
            cheapest = min(pool, key=lambda c: c["ask"])
            rejected.append((ticker,
                             "no contract affordable at ${0:,.0f} budget "
                             "(cheapest qualifying is ${1:,.0f}/contract)".format(
                                 scfg.SIM_BUDGET,
                                 cheapest["ask"] * cfg.CONTRACT_MULTIPLIER), None))
            continue
        pool = affordable

        # Earnings check on the cheapest-by-drag survivor's expiry.
        pool.sort(key=lambda c: (c["drag_7d_pct"],
                                 abs(abs(c["moneyness"]) - scfg.PREFER_MONEYNESS)))
        best = pool[0]
        expiry_date = _parse_date(best["expiry"])
        blocks, crosses = _earnings_conflict(ticker, as_of, expiry_date)

        if blocks:
            rejected.append((ticker,
                             "earnings within {0} days of entry — event would dominate".format(
                                 scfg.AVOID_EARNINGS_WITHIN_DAYS), None))
            continue

        chosen = dict(cand)
        chosen["contract"] = best
        chosen["crosses_earnings"] = crosses
        selected.append(chosen)

    for ticker, reason, _d in rejected:
        _log("    reject {0:<6s} {1}".format(ticker, reason))
    _log("  SELECTED: {0} contract(s)".format(len(selected)))

    return selected, rejected


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

def _append_jsonl(path, record):
    cfg.ensure_dirs()
    with open(path, "a") as fh:
        fh.write(json.dumps(record) + "\n")


def load_open_positions():
    """Open (unsettled) simulated positions."""
    if not os.path.exists(cfg.SIM_POSITIONS):
        return []
    out = []
    with open(cfg.SIM_POSITIONS, "r") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("status") == "open":
                out.append(rec)
    return out


def open_position(selection, meta, as_of):
    """Record one simulated purchase.

    The entry fill is the QUOTED ASK plus commissions — the price actually
    payable, not the mid. Using the mid would flatter every result by half the
    spread, which on these contracts is a large fraction of the edge being
    tested.
    """
    import options_position

    contract = selection["contract"]
    sizing = options_position.size_position(scfg.SIM_BUDGET, contract["ask"])
    if sizing["n_contracts"] < 1:
        # Should be unreachable: stage 4 filters to affordable contracts before
        # selection. If it happens anyway, say so — a position the cascade
        # justified and then silently dropped is the worst possible outcome,
        # because the ledger would show a no-trade day with no reason recorded.
        _log("    ERROR: {0} {1} ${2:.2f} passed selection but costs "
             "${3:,.2f}/contract against a ${4:,.2f} budget — not opened.".format(
                 selection["ticker"], contract["option_type"], contract["strike"],
                 sizing["cost_per_contract"], scfg.SIM_BUDGET))
        return None

    fees = options_position.transaction_costs(sizing["n_contracts"])
    entry_outlay = sizing["capital_deployed"] + fees["entry_fees"]

    record = {
        "position_id": "{0}-{1}-{2}-{3}".format(
            selection["ticker"], contract["expiry"], contract["option_type"],
            contract["strike"]),
        "status": "open",
        "opened_at": _utc_now_iso(),
        "entry_date": as_of.isoformat(),
        "quote_session": meta.get("quote_session"),

        "ticker": selection["ticker"],
        "option_type": contract["option_type"],
        "strike": contract["strike"],
        "expiry": contract["expiry"],
        "dte_at_entry": contract["dte"],

        "entry_ask": contract["ask"],
        "entry_bid": contract["bid"],
        "entry_spot": contract["spot"],
        "entry_iv": contract["iv"],
        "n_contracts": sizing["n_contracts"],
        "capital_deployed": sizing["capital_deployed"],
        "residual_cash": sizing["residual"],
        "entry_fees": fees["entry_fees"],
        "entry_outlay": entry_outlay,

        # Cost metrics at entry, so outcomes can later be attributed to
        # friction rather than to the signal.
        "breakeven_price": contract["breakeven_price"],
        "breakeven_in_sds": contract.get("breakeven_in_sds"),
        "p_itm_at_entry": contract.get("p_itm"),
        "drag_7d_pct": contract.get("drag_7d_pct"),
        "round_trip_cost_pct": contract.get("round_trip_cost_pct"),

        # THE SIGNAL FEATURES — the reason this position exists. These are what
        # later analysis correlates against outcomes to answer "which kinds of
        # signal actually predict?"
        "signal": {
            "sentiment_change": selection.get("sentiment_change"),
            "recent_mean": selection.get("recent_mean"),
            "baseline_mean": selection.get("baseline_mean"),
            "recent_mentions": selection.get("recent_mentions"),
            "recent_days": selection.get("recent_days"),
            "price_move_pct": selection.get("price_move_pct"),
            "lead_corr": selection.get("corr"),
            "lead_lag_h": selection.get("lag_h"),
            "n_sent_hours": selection.get("n_sent_hours"),
            "trade_direction": selection.get("trade_direction"),
        },
        "crosses_earnings": selection.get("crosses_earnings"),

        # Thresholds in force at entry. Without these, a ledger spanning a rule
        # change is uninterpretable — you cannot tell whether an outcome
        # reflects the signal or a threshold that moved underneath it.
        "rule_version": {
            "MIN_SENT_HOURS": scfg.MIN_SENT_HOURS,
            "MIN_LEAD_CORR": scfg.MIN_LEAD_CORR,
            "MIN_SENT_CHANGE": scfg.MIN_SENT_CHANGE,
            "MAX_PRICE_MOVE_PCT": scfg.MAX_PRICE_MOVE_PCT,
            "MAX_BREAKEVEN_SDS": scfg.MAX_BREAKEVEN_SDS,
            "SIM_BUDGET": scfg.SIM_BUDGET,
        },
    }

    _append_jsonl(cfg.SIM_POSITIONS, record)
    return record


def log_decision(as_of, meta, outcome, detail):
    """Append one decision record per run — including no-trade days.

    No-trade days are the majority and are NOT failures: they are the rule
    working. Logging them with reasons is what makes it possible to see later
    which gate was binding, and whether it loosened as data accumulated.
    """
    record = {
        "logged_at": _utc_now_iso(),
        "run_date": as_of.isoformat(),
        "quote_session": meta.get("quote_session"),
        "outcome": outcome,
        "detail": detail,
    }
    _append_jsonl(cfg.SIM_LEDGER, record)
    return record


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run(as_of=None, screen_payload=None):
    """Run the full cascade against the latest screen. Returns a summary dict."""
    if as_of is None:
        as_of = dt.date.today()

    _log("=" * 92)
    _log("SENTIMENT -> OPTIONS SIMULATION CASCADE   ({0})".format(as_of.isoformat()))
    _log("=" * 92)

    if screen_payload is None:
        if not os.path.exists(cfg.LATEST_JSON):
            _log("No screen data at {0}. Run options_screener.py first.".format(
                cfg.LATEST_JSON))
            return {"outcome": "no_screen_data"}
        with open(cfg.LATEST_JSON, "r") as fh:
            screen_payload = json.load(fh)

    meta = screen_payload.get("meta", {})
    contracts = screen_payload.get("contracts", [])
    _log("  screen: {0} contracts, pulled {1} ({2} session)".format(
        len(contracts), meta.get("fetched_at"), meta.get("quote_session")))

    # Quote-freshness gate. Spreads outside the regular session are
    # unrepresentative, and a simulated fill at those prices corrupts the
    # ledger. Refusing to trade is the conservative choice.
    session = meta.get("quote_session")
    if scfg.REQUIRE_REGULAR_SESSION and session != "regular":
        _log("")
        _log("  ABORT: quotes pulled during '{0}', not the regular session.".format(session))
        _log("  Spreads outside regular hours are unrepresentative, and a simulated")
        _log("  fill at those prices would corrupt the ledger. No position opened.")
        detail = {"reason": "non_regular_session", "quote_session": session}
        log_decision(as_of, meta, "no_trade", detail)
        return {"outcome": "no_trade", "detail": detail}

    options_universe = sorted(set(c["ticker"] for c in contracts)) or cfg.TICKERS
    analysis = load_session_analysis()

    stage1, no_sentiment = stage1_intersect(options_universe)
    if not stage1:
        detail = {"reason": "no_overlap", "stage": 1,
                  "options_without_sentiment": no_sentiment}
        log_decision(as_of, meta, "no_trade", detail)
        _log("")
        _log("NO TRADE — no ticker is in both universes.")
        return {"outcome": "no_trade", "detail": detail}

    stage2, s2_rejects = stage2_predictiveness(stage1, analysis)
    if not stage2:
        detail = {"reason": "no_predictive_tickers", "stage": 2,
                  "rejections": [{"ticker": t, "reason": r} for t, r, _ in s2_rejects]}
        log_decision(as_of, meta, "no_trade", detail)
        _log("")
        _log("NO TRADE — no ticker shows a reliable news-leads-price relationship.")
        _log("  This is expected while sentiment history is thin. The gate will")
        _log("  begin to admit names as observations accumulate.")
        return {"outcome": "no_trade", "detail": detail}

    stage3, s3_rejects = stage3_unreflected(stage2, as_of)
    if not stage3:
        detail = {"reason": "no_unreflected_signal", "stage": 3,
                  "rejections": [{"ticker": t, "reason": r} for t, r, _ in s3_rejects]}
        log_decision(as_of, meta, "no_trade", detail)
        _log("")
        _log("NO TRADE — no unreflected sentiment change today.")
        return {"outcome": "no_trade", "detail": detail}

    stage4, s4_rejects = stage4_select_contract(stage3, contracts, as_of)
    if not stage4:
        detail = {"reason": "no_suitable_contract", "stage": 4,
                  "rejections": [{"ticker": t, "reason": r} for t, r, _ in s4_rejects]}
        log_decision(as_of, meta, "no_trade", detail)
        _log("")
        _log("NO TRADE — a view exists but no contract expresses it cheaply enough.")
        return {"outcome": "no_trade", "detail": detail}

    # Rank surviving candidates: strongest signal, then lowest friction.
    stage4.sort(key=lambda s: (-abs(s.get("sentiment_change") or 0),
                               s["contract"]["drag_7d_pct"]))

    already_open = set(p["ticker"] for p in load_open_positions())
    opened = []
    for selection in stage4:
        if len(opened) >= scfg.MAX_POSITIONS_PER_DAY:
            break
        if selection["ticker"] in already_open and scfg.MAX_OPEN_PER_TICKER <= 1:
            _log("    skip {0} — already holding an open position".format(
                selection["ticker"]))
            continue
        rec = open_position(selection, meta, as_of)
        if rec:
            opened.append(rec)
            already_open.add(rec["ticker"])

    if not opened:
        detail = {"reason": "all_candidates_already_held", "stage": 4}
        log_decision(as_of, meta, "no_trade", detail)
        _log("")
        _log("NO TRADE — candidates exist but positions are already open on them.")
        return {"outcome": "no_trade", "detail": detail}

    _log("")
    _log("=" * 92)
    _log("SIMULATED PURCHASE")
    _log("=" * 92)
    for rec in opened:
        _log("  {0} {1} ${2:.2f} exp {3}  x{4} contracts".format(
            rec["ticker"], rec["option_type"].upper(), rec["strike"],
            rec["expiry"], rec["n_contracts"]))
        _log("    entry ask ${0:.2f}  outlay ${1:,.2f}  (residual ${2:,.2f} idle)".format(
            rec["entry_ask"], rec["entry_outlay"], rec["residual_cash"]))
        _log("    breakeven ${0:.2f}  ({1:.2f} SDs)   p_itm {2:.3f} [risk-neutral]".format(
            rec["breakeven_price"], rec["breakeven_in_sds"] or float("nan"),
            rec["p_itm_at_entry"] or float("nan")))
        sig = rec["signal"]
        _log("    signal: sentiment {0:+.3f} vs baseline, price moved {1:+.2f}%".format(
            sig["sentiment_change"] or 0.0, sig["price_move_pct"] or 0.0))
        _log("            lead corr {0:+.3f} at {1}h on {2} sentiment-hours".format(
            sig["lead_corr"] or 0.0, sig["lead_lag_h"], sig["n_sent_hours"]))
        if rec["crosses_earnings"]:
            _log("    NOTE: expiry falls after an earnings date — outcome will be")
            _log("          dominated by the event, not by the sentiment signal.")

    log_decision(as_of, meta, "opened", {
        "positions": [r["position_id"] for r in opened]})

    return {"outcome": "opened", "positions": opened}


if __name__ == "__main__":
    run()

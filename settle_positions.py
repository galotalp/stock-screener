"""
settle_positions.py — mark simulated positions to market, and settle at expiry.

Runs after sim_cascade.py on every pipeline run. Two jobs:

  1. MARK OPEN POSITIONS. Re-price each open position against the current
     screen. Unrealised P/L is indicative only — the position has not been
     closed and the exit spread has not been paid.

  2. SETTLE EXPIRED POSITIONS. Once expiry has passed, compute the realised
     outcome against the actual underlying close and write it to the ledger
     permanently.

SETTLEMENT IS INTRINSIC VALUE AT EXPIRY, NET OF BOTH LEGS OF FEES. No model,
no assumption about early exit. A contract expiring out of the money is a total
loss of premium; one expiring in the money is worth exactly max(S-K, 0) for
calls. This is the one point in the whole tool where the answer is unambiguous,
so it is computed directly and not estimated.

WHY ATTRIBUTION MATTERS: the point of the exercise is not the P/L. It is
learning WHICH SIGNALS PREDICT. So every settled position carries the signal
features that triggered it — sentiment magnitude, mention volume, lead
correlation, source mix, session — and `summarise()` groups outcomes by those
features. With enough positions this answers "does a large sentiment shift beat
a small one?" and "do overnight signals work better than regular-session ones?"
rather than just "am I up or down?".

Python 3.9 compatible.
"""
import datetime as dt
import json
import os
import sys

import options_config as cfg
import sim_config as scfg
import sim_cascade

try:
    import yfinance as yf
except ImportError:  # pragma: no cover
    yf = None

try:
    from curl_cffi import requests as _curl_requests
except ImportError:  # pragma: no cover
    _curl_requests = None


def _log(msg):
    print(msg)
    sys.stdout.flush()


def _utc_now_iso():
    return dt.datetime.now(dt.timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_date(value):
    if not value:
        return None
    text = str(value)
    try:
        if "T" in text:
            return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        return dt.datetime.strptime(text[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Position store
# ---------------------------------------------------------------------------

def load_all_positions():
    """Every position record, open and settled."""
    if not os.path.exists(cfg.SIM_POSITIONS):
        return []
    out = []
    with open(cfg.SIM_POSITIONS, "r") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def rewrite_positions(records):
    """Rewrite the position store.

    The file is append-only during normal operation; settlement is the one
    operation that mutates existing records (open -> settled), so it rewrites
    the whole file. Written to a temporary path and renamed so an interrupted
    run cannot truncate the ledger.
    """
    cfg.ensure_dirs()
    tmp = cfg.SIM_POSITIONS + ".tmp"
    with open(tmp, "w") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
    os.replace(tmp, cfg.SIM_POSITIONS)


# ---------------------------------------------------------------------------
# Underlying price at expiry
# ---------------------------------------------------------------------------

def _local_close(ticker, target_date):
    """Closing price on or nearest before target_date, from the local store."""
    prices = sim_cascade.load_prices(ticker)
    if not prices:
        return None, None
    candidates = [(d, p) for d, p in prices.items()
                  if _parse_date(d) and _parse_date(d) <= target_date]
    if not candidates:
        return None, None
    candidates.sort()
    return candidates[-1][1], candidates[-1][0]


def _fetch_close(ticker, target_date):
    """Closing price at expiry, local store first then network.

    The local store is preferred because it costs nothing and is already
    maintained by collect_prices.py. The network path exists because settlement
    can fall on a date the local store has not caught up to yet — and an
    unsettled position that should have settled is worse than one extra request.
    """
    close, used_date = _local_close(ticker, target_date)
    if close is not None:
        used = _parse_date(used_date)
        # Accept the local value only if it is close to the target; a stale
        # store could otherwise settle a position against a month-old price.
        if used and (target_date - used).days <= 5:
            return close, "local:{0}".format(used_date)

    if yf is None:
        return close, "local_stale" if close is not None else None

    try:
        session = None
        if _curl_requests is not None:
            session = _curl_requests.Session(impersonate="chrome")
        obj = yf.Ticker(ticker, session=session)
        start = target_date - dt.timedelta(days=7)
        end = target_date + dt.timedelta(days=2)
        hist = obj.history(start=start.isoformat(), end=end.isoformat(),
                           interval="1d")
        if hist is not None and len(hist) > 0:
            rows = [(idx.date(), float(row["Close"]))
                    for idx, row in hist.iterrows()]
            usable = [(d, p) for d, p in rows if d <= target_date]
            if usable:
                usable.sort()
                return usable[-1][1], "network:{0}".format(usable[-1][0].isoformat())
    except Exception as exc:
        _log("    price fetch failed for {0}: {1}".format(ticker, exc))

    return close, "local_stale" if close is not None else None


# ---------------------------------------------------------------------------
# Settlement
# ---------------------------------------------------------------------------

def settle_one(position, as_of):
    """Settle a single expired position. Returns the updated record, or None."""
    expiry = _parse_date(position["expiry"])
    if expiry is None or expiry >= as_of:
        return None

    ticker = position["ticker"]
    close, source = _fetch_close(ticker, expiry)
    if close is None:
        _log("    {0}: no closing price for {1}; leaving open".format(
            position["position_id"], expiry))
        return None

    strike = position["strike"]
    n = position["n_contracts"]
    is_call = position["option_type"] == "call"

    # Intrinsic value at expiry. Unambiguous: no model, no exit-timing
    # assumption. Out of the money is a total loss of premium.
    intrinsic = max(close - strike, 0.0) if is_call else max(strike - close, 0.0)
    gross_proceeds = intrinsic * n * cfg.CONTRACT_MULTIPLIER

    # Exit fees apply only if the contract had value to exercise or sell.
    # A worthless option is abandoned, not traded, so no exit commission.
    if intrinsic > 0:
        exit_fees = n * (cfg.COMMISSION_PER_CONTRACT + cfg.REG_FEE_PER_CONTRACT)
    else:
        exit_fees = 0.0

    net_proceeds = gross_proceeds - exit_fees
    entry_outlay = position["entry_outlay"]
    pl_dollars = net_proceeds - entry_outlay
    pl_pct = 100.0 * pl_dollars / entry_outlay if entry_outlay > 0 else 0.0

    # Return on the full stated budget, not just deployed capital: idle residual
    # is part of the cost of an integer-contract position.
    budget = position.get("rule_version", {}).get("SIM_BUDGET", scfg.SIM_BUDGET)
    pl_pct_budget = 100.0 * pl_dollars / budget if budget else None

    # Was the DIRECTION right, independent of whether the position made money?
    # This is the key diagnostic: a signal can be directionally correct and
    # still lose, because the move has to clear the premium as well as the
    # strike. Separating the two tells you whether the signal is wrong or
    # merely too weak to overcome friction.
    entry_spot = position["entry_spot"]
    underlying_move_pct = 100.0 * (close / entry_spot - 1.0) if entry_spot else 0.0
    direction_correct = ((underlying_move_pct > 0) == is_call
                         if abs(underlying_move_pct) > 1e-9 else False)

    settled = dict(position)
    settled.update({
        "status": "settled",
        "settled_at": _utc_now_iso(),
        "settle_date": expiry.isoformat(),
        "settle_close": close,
        "settle_price_source": source,
        "intrinsic_at_expiry": intrinsic,
        "gross_proceeds": gross_proceeds,
        "exit_fees": exit_fees,
        "net_proceeds": net_proceeds,
        "pl_dollars": pl_dollars,
        "pl_pct_of_outlay": pl_pct,
        "pl_pct_of_budget": pl_pct_budget,
        "underlying_move_pct": underlying_move_pct,
        "direction_correct": direction_correct,
        "expired_worthless": intrinsic <= 0,
        # Did it clear the breakeven, i.e. recover the premium as well as the
        # strike? A position can be directionally right and fail this.
        "cleared_breakeven": (close > position["breakeven_price"] if is_call
                              else close < position["breakeven_price"]),
    })
    return settled


def mark_open(position, contracts_by_key):
    """Attach an indicative mark to an open position from the current screen."""
    key = (position["ticker"], position["option_type"], position["expiry"],
           position["strike"])
    current = contracts_by_key.get(key)
    marked = dict(position)

    if current is None:
        marked["mark_status"] = "no_quote"
        return marked

    n = position["n_contracts"]
    # Mark at the BID: that is what the position could actually be sold for.
    # Marking at the mid would overstate every open position by half the
    # spread, which on these contracts is a material fraction of the premium.
    exit_fees = n * (cfg.COMMISSION_PER_CONTRACT + cfg.REG_FEE_PER_CONTRACT)
    gross = current["bid"] * n * cfg.CONTRACT_MULTIPLIER
    unrealised = gross - exit_fees - position["entry_outlay"]

    marked.update({
        "mark_status": "quoted",
        "marked_at": _utc_now_iso(),
        "mark_bid": current["bid"],
        "mark_spot": current["spot"],
        "mark_dte": current["dte"],
        "unrealised_pl": unrealised,
        "unrealised_pct": (100.0 * unrealised / position["entry_outlay"]
                           if position["entry_outlay"] else None),
    })
    return marked


def run(as_of=None, screen_payload=None):
    """Mark open positions and settle expired ones."""
    if as_of is None:
        as_of = dt.date.today()

    _log("")
    _log("=" * 92)
    _log("POSITION SETTLEMENT  ({0})".format(as_of.isoformat()))
    _log("=" * 92)

    positions = load_all_positions()
    if not positions:
        _log("  no simulated positions yet")
        return {"settled": 0, "open": 0}

    # Current quotes for marking.
    contracts_by_key = {}
    if screen_payload is None and os.path.exists(cfg.LATEST_JSON):
        try:
            with open(cfg.LATEST_JSON, "r") as fh:
                screen_payload = json.load(fh)
        except ValueError:
            screen_payload = None
    if screen_payload:
        for c in screen_payload.get("contracts", []):
            contracts_by_key[(c["ticker"], c["option_type"], c["expiry"],
                              c["strike"])] = c

    updated, newly_settled, still_open = [], [], []

    for pos in positions:
        if pos.get("status") == "settled":
            updated.append(pos)
            continue

        settled = settle_one(pos, as_of)
        if settled is not None:
            updated.append(settled)
            newly_settled.append(settled)
        else:
            marked = mark_open(pos, contracts_by_key)
            updated.append(marked)
            still_open.append(marked)

    rewrite_positions(updated)

    if newly_settled:
        _log("")
        _log("  SETTLED {0} position(s):".format(len(newly_settled)))
        for s in newly_settled:
            verdict = "WORTHLESS" if s["expired_worthless"] else "ITM"
            _log("    {0:<28s} {1:>9s}  close ${2:.2f} vs strike ${3:.2f}".format(
                s["position_id"], verdict, s["settle_close"], s["strike"]))
            _log("      P/L ${0:+,.2f}  ({1:+.1f}% of outlay)   underlying {2:+.2f}%".format(
                s["pl_dollars"], s["pl_pct_of_outlay"], s["underlying_move_pct"]))
            _log("      direction {0}   cleared breakeven {1}".format(
                "CORRECT" if s["direction_correct"] else "wrong",
                "yes" if s["cleared_breakeven"] else "no"))
            if s["direction_correct"] and not s["cleared_breakeven"]:
                _log("      ^ directionally right but still lost: the move did not")
                _log("        cover the premium. This is the premium, in movement.")

    if still_open:
        _log("")
        _log("  OPEN {0} position(s):".format(len(still_open)))
        for o in still_open:
            if o.get("mark_status") == "quoted":
                _log("    {0:<28s} unrealised ${1:+,.2f} ({2:+.1f}%)  {3}d left".format(
                    o["position_id"], o["unrealised_pl"], o["unrealised_pct"],
                    o["mark_dte"]))
            else:
                _log("    {0:<28s} no current quote".format(o["position_id"]))
        _log("    (marks are at the BID and are indicative — nothing has been closed)")

    return {"settled": len(newly_settled), "open": len(still_open),
            "newly_settled": newly_settled}


# ---------------------------------------------------------------------------
# Attribution — the part that answers the actual research question
# ---------------------------------------------------------------------------

def _bucket_stats(rows):
    if not rows:
        return None
    wins = [r for r in rows if r["pl_dollars"] > 0]
    correct = [r for r in rows if r.get("direction_correct")]
    total_pl = sum(r["pl_dollars"] for r in rows)
    return {
        "n": len(rows),
        "total_pl": total_pl,
        "mean_pl": total_pl / len(rows),
        "win_rate": 100.0 * len(wins) / len(rows),
        "direction_accuracy": 100.0 * len(correct) / len(rows),
    }


def summarise():
    """Cumulative performance, and outcomes grouped by signal feature.

    The grouping is the point. Overall P/L on a handful of positions says
    almost nothing; what compounds in value is knowing whether large sentiment
    shifts outperform small ones, whether high-mention-volume signals beat
    thin ones, and whether positions crossing earnings behave differently.
    """
    positions = load_all_positions()
    settled = [p for p in positions if p.get("status") == "settled"]

    _log("")
    _log("=" * 92)
    _log("CUMULATIVE PERFORMANCE")
    _log("=" * 92)

    if not settled:
        open_count = len([p for p in positions if p.get("status") == "open"])
        _log("  no positions have settled yet ({0} open)".format(open_count))
        _log("  performance attribution becomes meaningful after ~20-30 settled")
        _log("  positions; below that, results are dominated by variance.")
        return {"settled": 0}

    overall = _bucket_stats(settled)
    total_deployed = sum(p["entry_outlay"] for p in settled)

    _log("  settled positions        {0}".format(overall["n"]))
    _log("  total capital deployed  ${0:,.2f}".format(total_deployed))
    _log("  total P/L               ${0:+,.2f}   ({1:+.1f}% on deployed)".format(
        overall["total_pl"],
        100.0 * overall["total_pl"] / total_deployed if total_deployed else 0.0))
    _log("  win rate                 {0:.1f}%".format(overall["win_rate"]))
    _log("  direction accuracy       {0:.1f}%   <- was the VIEW right?".format(
        overall["direction_accuracy"]))

    # The diagnostic gap: right direction, wrong outcome.
    right_but_lost = [p for p in settled
                      if p.get("direction_correct") and p["pl_dollars"] <= 0]
    if right_but_lost:
        _log("")
        _log("  {0} position(s) were DIRECTIONALLY RIGHT but still lost money.".format(
            len(right_but_lost)))
        _log("  That gap is the premium: the move cleared the strike but not the")
        _log("  cost of entry. It is the single most important number here — it")
        _log("  separates 'the signal is wrong' from 'the signal is too weak to")
        _log("  overcome friction', which have completely different remedies.")

    if len(settled) < 20:
        _log("")
        _log("  NOTE: {0} settled position(s) is too few for the breakdowns below".format(
            len(settled)))
        _log("  to be meaningful. They are printed to show the shape of the")
        _log("  analysis, not to support conclusions.")

    # Attribution buckets.
    def report(title, groups):
        _log("")
        _log("  {0}".format(title))
        any_row = False
        for name, rows in groups:
            stats = _bucket_stats(rows)
            if stats is None:
                continue
            any_row = True
            _log("    {0:<24s} n={1:<4d} mean P/L ${2:>+9,.2f}  win {3:>5.1f}%  dir {4:>5.1f}%".format(
                name, stats["n"], stats["mean_pl"], stats["win_rate"],
                stats["direction_accuracy"]))
        if not any_row:
            _log("    (no data)")

    def sig(p, key, default=0.0):
        value = (p.get("signal") or {}).get(key)
        return default if value is None else value

    report("BY SENTIMENT-CHANGE MAGNITUDE", [
        ("small (<0.25)", [p for p in settled if abs(sig(p, "sentiment_change")) < 0.25]),
        ("medium (0.25-0.50)", [p for p in settled
                                if 0.25 <= abs(sig(p, "sentiment_change")) < 0.50]),
        ("large (>=0.50)", [p for p in settled
                            if abs(sig(p, "sentiment_change")) >= 0.50]),
    ])

    report("BY MENTION VOLUME", [
        ("thin (<10)", [p for p in settled if sig(p, "recent_mentions") < 10]),
        ("moderate (10-30)", [p for p in settled
                              if 10 <= sig(p, "recent_mentions") < 30]),
        ("heavy (>=30)", [p for p in settled if sig(p, "recent_mentions") >= 30]),
    ])

    report("BY DIRECTION", [
        ("calls (bullish)", [p for p in settled if p["option_type"] == "call"]),
        ("puts (bearish)", [p for p in settled if p["option_type"] == "put"]),
    ])

    report("BY EARNINGS CONTAMINATION", [
        ("crosses earnings", [p for p in settled if p.get("crosses_earnings")]),
        ("clean window", [p for p in settled if not p.get("crosses_earnings")]),
    ])

    report("BY ENTRY FRICTION", [
        ("low drag (<10%)", [p for p in settled if (p.get("drag_7d_pct") or 0) < 10]),
        ("mid drag (10-25%)", [p for p in settled
                               if 10 <= (p.get("drag_7d_pct") or 0) < 25]),
        ("high drag (>=25%)", [p for p in settled
                               if (p.get("drag_7d_pct") or 0) >= 25]),
    ])

    _log("")
    _log("  CAVEAT: these are SIMULATED fills on delayed free-feed quotes, entered")
    _log("  at the ask and settled at intrinsic value. Real execution would differ.")
    _log("  Source-level attribution (which publisher predicted best) is NOT")
    _log("  available: the sentiment collector stores only the feed ('stocktwits'")
    _log("  or 'news:yahoo'), having discarded the article URL and publisher.")

    return {"settled": len(settled), "overall": overall}


if __name__ == "__main__":
    run()
    summarise()

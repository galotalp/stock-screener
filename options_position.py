"""
options_position.py — PART 2: dollar-denominated position calculator.

Given ONE chosen contract (from the screener output) and a dollar budget, this
returns every figure from Part 1 converted from percentages into DOLLARS, plus
a payoff grid.

WHY THIS EXISTS: percentages obscure how small the absolute stakes can be
relative to fixed costs. A "2% round-trip cost" sounds negligible until it is
$68 on a $680 position that also left $320 of a $1,000 budget sitting idle,
and paid $2.80 in commissions to get in and out. This module removes that
ambiguity by refusing to report anything as a percentage without also
reporting it in dollars.

Python 3.9 compatible.
"""
import datetime as dt
import math
import sys

import black_scholes as bs
import options_config as cfg


def _log(msg):
    print(msg)
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Requirement 1 — integer contract sizing with explicit residual
# ---------------------------------------------------------------------------

def size_position(budget, ask):
    """Contracts are integers carrying a 100x multiplier, so a budget will not
    divide cleanly. The leftover is reported explicitly because it materially
    changes return on capital: a $1,000 budget against a $3.40 ask buys 2
    contracts for $680, leaving $320 — a third of the stated budget — doing
    nothing. Returns on 'capital deployed' and returns on 'budget' are then
    different numbers, and both are reported everywhere below.
    """
    cost_per_contract = ask * cfg.CONTRACT_MULTIPLIER
    if cost_per_contract <= 0:
        return {"n_contracts": 0, "capital_deployed": 0.0, "residual": budget,
                "cost_per_contract": 0.0, "residual_pct_of_budget": 100.0}

    n_contracts = int(math.floor(budget / cost_per_contract))
    capital_deployed = n_contracts * cost_per_contract
    residual = budget - capital_deployed

    return {
        "n_contracts": n_contracts,
        "cost_per_contract": cost_per_contract,
        "capital_deployed": capital_deployed,
        "residual": residual,
        "residual_pct_of_budget": 100.0 * residual / budget if budget > 0 else 0.0,
    }


# ---------------------------------------------------------------------------
# Requirement 2 — commissions and fees as first-class inputs
# ---------------------------------------------------------------------------

def transaction_costs(n_contracts):
    """Per-contract commission + regulatory/exchange fees, on BOTH entry and exit.

    These are approximately FIXED per contract, which makes them a large
    percentage of premium on cheap contracts — several percent each way on a
    $0.30 contract. Omitting them flatters cheap contracts precisely where they
    are worst, so they are folded into every drag calculation and every payoff
    grid cell below.
    """
    per_contract_one_way = cfg.COMMISSION_PER_CONTRACT + cfg.REG_FEE_PER_CONTRACT
    entry = n_contracts * per_contract_one_way
    exit_ = n_contracts * per_contract_one_way
    return {
        "per_contract_one_way": per_contract_one_way,
        "entry_fees": entry,
        "exit_fees": exit_,
        "round_trip_fees": entry + exit_,
    }


# ---------------------------------------------------------------------------
# Requirement 3 — non-linear theta
# ---------------------------------------------------------------------------

def decay_schedule(spot, strike, dte, rate, div_yield, sigma, is_call, days_held):
    """Actual decay over a holding period, by RE-PRICING at each forward day.

    DO NOT multiply daily theta by the number of days held. That linear
    approximation is acceptable in percentage terms but misleading in dollars,
    because theta ACCELERATES as expiry approaches — for a ~10-DTE contract the
    linear version can understate a week's decay meaningfully.

    Here the option is re-priced with Black-Scholes at each forward day
    (shrinking T, spot held flat, IV held at the assumed level) and the actual
    day-over-day differences are summed.

    Returns a list of per-day records plus the linear comparison, so the size of
    the error is visible rather than asserted.
    """
    out = []
    prev_price = bs.price(spot, strike, max(dte, 0.5) / 365.0, rate, div_yield,
                          sigma, is_call)
    price_now = prev_price

    for day in range(1, int(days_held) + 1):
        remaining = dte - day
        if remaining <= 0:
            # At/after expiry the option is worth intrinsic value only.
            if is_call:
                value = max(spot - strike, 0.0)
            else:
                value = max(strike - spot, 0.0)
        else:
            value = bs.price(spot, strike, remaining / 365.0, rate, div_yield,
                             sigma, is_call)

        out.append({
            "day": day,
            "dte_remaining": max(remaining, 0),
            "option_price": value,
            "decay_that_day": value - prev_price,
            "cumulative_decay": value - price_now,
        })
        prev_price = value

    return out


# ---------------------------------------------------------------------------
# Requirement 4 + 5 — payoff grid under an adjustable IV assumption
# ---------------------------------------------------------------------------

def payoff_grid(contract, n_contracts, iv_assumed, price_levels=None,
                date_offsets=None, rate=None, div_yield=None):
    """Dollar P/L on the FULL position across price levels and dates.

    Rows = underlying price levels; columns = days from today through expiry;
    cells = dollar profit/loss on n contracts, NET of entry and exit fees.

    Cells use BLACK-SCHOLES RE-PRICING, not intrinsic value at expiry. Most
    positions are closed early rather than held to expiration, so the grid must
    show what the position is worth ON THE WAY THERE, not only at the endpoint.
    Only the final column (expiry) is intrinsic value.

    IV IS AN INPUT, NOT A CONSTANT. Holding IV flat is an OPTIMISTIC
    assumption: post-earnings volatility crush routinely costs more than the
    directional move gains. Callers are expected to run this at least twice —
    once at quoted IV and once lower — which `compare_iv_scenarios` does.

    ENTRY-BASIS CALIBRATION (important, and easy to get wrong)
    ---------------------------------------------------------
    Entry happens at the QUOTED ASK, but the grid values the position with
    Black-Scholes. For liquid near-the-money contracts those nearly agree. For
    cheap out-of-the-money contracts they do not: a $0.09 ask against a $0.054
    model value is 67% above theo, which is normal — the spread is a large
    fraction of the premium at that price, and the model does not price the
    liquidity premium.

    Valuing the exit at raw theo while charging entry at the ask would book
    that entire gap as an INSTANT loss at day 0, so the grid would show a large
    negative P/L at unchanged spot on the very first column. That is wrong: at
    unchanged quotes the true day-0 loss is the round-trip cost, nothing more.

    So the model is anchored to the quoted ask at entry (t=0, spot unchanged)
    and that ratio is carried across the grid. The ask-vs-theo gap remains
    visible where it belongs — `ask_vs_theo_pct` in the screener, as a stale or
    crossed quote flag — instead of being silently double-counted here.
    """
    if rate is None:
        rate = cfg.RISK_FREE_RATE
    if div_yield is None:
        div_yield = cfg.DIVIDEND_YIELD

    spot = contract["spot"]
    strike = contract["strike"]
    dte = contract["dte"]
    ask = contract["ask"]
    is_call = contract["option_type"] == "call"

    # Entry cost is fixed and paid at the quoted ask, plus entry fees.
    fees = transaction_costs(n_contracts)
    entry_outlay = (n_contracts * ask * cfg.CONTRACT_MULTIPLIER) + fees["entry_fees"]

    # Calibration factor: model value at entry conditions vs what was actually
    # paid. Applied multiplicatively to every non-expiry cell.
    theo_at_entry = bs.price(spot, strike, max(dte, 0.5) / 365.0, rate, div_yield,
                             iv_assumed, is_call)
    if theo_at_entry == theo_at_entry and theo_at_entry > 1e-9 and ask > 0:
        calibration = ask / theo_at_entry
    else:
        calibration = 1.0

    # Default price rows: +/-2 SDs of the move implied over the contract's life.
    if price_levels is None:
        sd = contract.get("sd_move_pct")
        if not sd or sd != sd or sd <= 0:
            sd = 0.10
        lo = spot * (1.0 - 2.0 * sd)
        hi = spot * (1.0 + 2.0 * sd)
        lo = max(lo, 0.01)
        steps = 13
        price_levels = [lo + (hi - lo) * i / (steps - 1) for i in range(steps)]

    # Default columns: today, then evenly spaced through expiry.
    if date_offsets is None:
        if dte <= 8:
            date_offsets = list(range(0, dte + 1))
        else:
            n_cols = 9
            date_offsets = sorted(set(
                int(round(dte * i / (n_cols - 1))) for i in range(n_cols)))

    today = dt.date.today()
    columns = []
    for offset in date_offsets:
        columns.append({
            "day_offset": offset,
            "date": (today + dt.timedelta(days=offset)).isoformat(),
            "dte_remaining": max(dte - offset, 0),
            "is_expiry": (dte - offset) <= 0,
        })

    grid = []
    for level in price_levels:
        cells = []
        for col in columns:
            remaining = col["dte_remaining"]
            if remaining <= 0:
                # Final column only: intrinsic value at expiry. NOT calibrated —
                # at expiry the contract is worth exactly its intrinsic value,
                # with no liquidity premium left to justify a markup.
                option_value = max(level - strike, 0.0) if is_call \
                    else max(strike - level, 0.0)
            else:
                option_value = calibration * bs.price(
                    level, strike, remaining / 365.0, rate, div_yield,
                    iv_assumed, is_call)

            gross_exit = n_contracts * option_value * cfg.CONTRACT_MULTIPLIER
            net_pl = gross_exit - entry_outlay - fees["exit_fees"]

            cells.append({
                "option_value": option_value,
                "position_value": gross_exit,
                "pl_dollars": net_pl,
                "pl_pct_of_deployed": (100.0 * net_pl / entry_outlay
                                       if entry_outlay > 0 else None),
            })

        grid.append({
            "underlying": level,
            "underlying_move_pct": 100.0 * (level / spot - 1.0),
            "cells": cells,
        })

    return {
        "columns": columns,
        "rows": grid,
        "entry_outlay": entry_outlay,
        "iv_assumed": iv_assumed,
        "n_contracts": n_contracts,
    }


def compare_iv_scenarios(contract, n_contracts, iv_drop=0.20):
    """Two grids side by side: IV unchanged, and IV down by `iv_drop` (relative).

    The second scenario is the one that matters around earnings. A holder can be
    directionally right and still lose money when the volatility premium
    collapses; showing only the flat-IV grid hides that entirely.
    """
    iv_base = contract["iv"]
    iv_crushed = iv_base * (1.0 - iv_drop)
    return {
        "base": payoff_grid(contract, n_contracts, iv_base),
        "crushed": payoff_grid(contract, n_contracts, iv_crushed),
        "iv_base": iv_base,
        "iv_crushed": iv_crushed,
        "iv_drop_pct": 100.0 * iv_drop,
    }


# ---------------------------------------------------------------------------
# Full analysis
# ---------------------------------------------------------------------------

def analyse(contract, budget, days_held=7, iv_drop=0.20):
    """Everything in dollars for one contract at one budget."""
    ask = contract["ask"]
    sizing = size_position(budget, ask)
    n = sizing["n_contracts"]

    if n < 1:
        return {
            "contract": contract, "budget": budget, "sizing": sizing,
            "error": ("budget ${0:.2f} is below the cost of a single contract "
                      "(${1:.2f})".format(budget, sizing["cost_per_contract"])),
        }

    fees = transaction_costs(n)
    entry_outlay = sizing["capital_deployed"] + fees["entry_fees"]

    # Round-trip spread cost in dollars: bought at ask, sold at bid, unchanged.
    spread_cost_dollars = (contract["ask"] - contract["bid"]) * n * cfg.CONTRACT_MULTIPLIER
    total_round_trip_cost = spread_cost_dollars + fees["round_trip_fees"]

    decay = decay_schedule(
        contract["spot"], contract["strike"], contract["dte"],
        cfg.RISK_FREE_RATE, cfg.DIVIDEND_YIELD, contract["iv"],
        contract["option_type"] == "call", days_held)

    actual_decay_per_contract = decay[-1]["cumulative_decay"] if decay else 0.0
    actual_decay_dollars = actual_decay_per_contract * n * cfg.CONTRACT_MULTIPLIER

    # Linear comparison, purely to show the size of the approximation error.
    #
    # WHICH WAY THE LINEAR ERROR RUNS DEPENDS ON THE CONTRACT, and it is worth
    # being precise rather than repeating a rule of thumb:
    #
    #  - Near the money, theta ACCELERATES into expiry, so multiplying today's
    #    theta by N days UNDERSTATES the true decay. This is the usual case and
    #    the one the spec warns about.
    #  - Deep out of the money and cheap, the opposite happens. Today's theta is
    #    the instantaneous rate, but the contract only has its remaining premium
    #    left to lose, so the linear figure can exceed the option's entire value
    #    — an impossible loss. It is floored below at total premium paid.
    #
    # Either way the re-priced figure is the correct one; this line exists to
    # show HOW WRONG the shortcut is for this specific contract, not to assert
    # a universal direction.
    linear_raw = contract["theta_day"] * days_held * n * cfg.CONTRACT_MULTIPLIER
    max_possible_loss = -(n * contract["ask"] * cfg.CONTRACT_MULTIPLIER)
    linear_decay_dollars = max(linear_raw, max_possible_loss)
    linear_was_floored = linear_raw < max_possible_loss

    scenarios = compare_iv_scenarios(contract, n, iv_drop)

    return {
        "contract": contract,
        "budget": budget,
        "days_held": days_held,
        "sizing": sizing,
        "fees": fees,
        "entry_outlay": entry_outlay,
        "spread_cost_dollars": spread_cost_dollars,
        "total_round_trip_cost": total_round_trip_cost,
        "round_trip_pct_of_deployed": (100.0 * total_round_trip_cost /
                                       sizing["capital_deployed"]
                                       if sizing["capital_deployed"] > 0 else None),
        "round_trip_pct_of_budget": (100.0 * total_round_trip_cost / budget
                                     if budget > 0 else None),
        "decay_schedule": decay,
        "actual_decay_dollars": actual_decay_dollars,
        "linear_decay_dollars": linear_decay_dollars,
        "linear_was_floored": linear_was_floored,
        "decay_understatement": actual_decay_dollars - linear_decay_dollars,
        "breakeven_price": contract["breakeven_price"],
        "scenarios": scenarios,
    }


def print_analysis(result):
    """Human-readable report for one analysed position."""
    if "error" in result:
        _log("  CANNOT SIZE POSITION: {0}".format(result["error"]))
        return

    c = result["contract"]
    s = result["sizing"]
    f = result["fees"]

    _log("")
    _log("=" * 92)
    _log("POSITION ANALYSIS — {0} {1} {2} ${3:.2f} strike, {4} DTE".format(
        c["ticker"], c["expiry"], c["option_type"].upper(), c["strike"], c["dte"]))
    _log("=" * 92)
    _log("  underlying spot          ${0:>12,.2f}".format(c["spot"]))
    _log("  quoted bid / ask         ${0:>6,.2f} / ${1:,.2f}".format(c["bid"], c["ask"]))
    _log("  implied volatility        {0:>12.1%}".format(c["iv"]))

    _log("")
    _log("  SIZING (contracts are integers x{0} multiplier)".format(cfg.CONTRACT_MULTIPLIER))
    _log("    budget                 ${0:>12,.2f}".format(result["budget"]))
    _log("    cost per contract      ${0:>12,.2f}".format(s["cost_per_contract"]))
    _log("    contracts bought        {0:>12d}".format(s["n_contracts"]))
    _log("    capital deployed       ${0:>12,.2f}".format(s["capital_deployed"]))
    _log("    RESIDUAL (idle cash)   ${0:>12,.2f}   = {1:.1f}% of budget".format(
        s["residual"], s["residual_pct_of_budget"]))

    _log("")
    _log("  TRANSACTION COSTS (charged on BOTH entry and exit)")
    _log("    per contract, one way  ${0:>12,.2f}".format(f["per_contract_one_way"]))
    _log("    entry fees             ${0:>12,.2f}".format(f["entry_fees"]))
    _log("    exit fees              ${0:>12,.2f}".format(f["exit_fees"]))
    _log("    total entry outlay     ${0:>12,.2f}".format(result["entry_outlay"]))

    _log("")
    _log("  GUARANTEED ROUND-TRIP COST (at unchanged quotes)")
    _log("    bid-ask spread         ${0:>12,.2f}".format(result["spread_cost_dollars"]))
    _log("    commissions + fees     ${0:>12,.2f}".format(f["round_trip_fees"]))
    _log("    TOTAL                  ${0:>12,.2f}".format(result["total_round_trip_cost"]))
    _log("      = {0:.2f}% of capital deployed".format(
        result["round_trip_pct_of_deployed"]))
    _log("      = {0:.2f}% of stated budget".format(result["round_trip_pct_of_budget"]))
    _log("    This is paid with certainty before the position can work.")

    _log("")
    _log("  TIME DECAY over {0} days (Black-Scholes re-priced each day)".format(
        result["days_held"]))
    _log("    actual (re-priced)     ${0:>12,.2f}".format(result["actual_decay_dollars"]))
    _log("    linear approximation   ${0:>12,.2f}{1}".format(
        result["linear_decay_dollars"],
        "   [floored at total premium]" if result["linear_was_floored"] else ""))
    gap = result["decay_understatement"]
    # gap > 0 means actual decay is SMALLER than linear predicted (linear
    # overstated); gap < 0 means actual is LARGER (linear understated).
    _log("    difference             ${0:>12,.2f}   <- linear {1} true decay by ${2:,.2f}".format(
        gap, "OVERSTATES" if gap > 0 else "UNDERSTATES", abs(gap)))
    if result["linear_was_floored"]:
        _log("    (today's theta rate x {0} days exceeds the option's entire premium —".format(
            result["days_held"]))
        _log("     an impossible loss. This is why the shortcut is not used.)")

    _log("")
    _log("  BREAKEVEN")
    _log("    underlying must reach  ${0:>12,.2f}   ({1:+.2f}% from spot)".format(
        c["breakeven_price"], 100.0 * c["breakeven_move_pct"]))
    if c.get("breakeven_in_sds") is not None:
        _log("    in standard deviations  {0:>12.2f}   [lognormal ruler, not a probability]".format(
            c["breakeven_in_sds"]))
    _log("    NOTE: breakeven above ignores fees; including them, the position")
    _log("    must clear a further ${0:,.2f} to be whole.".format(f["round_trip_fees"]))

    _print_grid(result["scenarios"])


def _print_grid(scenarios):
    """Print the two payoff grids side by side."""
    for key, title in (("base", "IV UNCHANGED at {0:.1%}"),
                       ("crushed", "IV CRUSHED to {0:.1%} (-{1:.0f}%)")):
        grid = scenarios[key]
        if key == "base":
            heading = title.format(scenarios["iv_base"])
        else:
            heading = title.format(scenarios["iv_crushed"], scenarios["iv_drop_pct"])

        _log("")
        _log("  PAYOFF GRID — {0}".format(heading))
        _log("    dollar P/L on the full position, net of entry and exit fees")
        _log("    Black-Scholes re-priced; only the final column is intrinsic value")

        cols = grid["columns"]
        header = "    {0:>10s}".format("UNDERLYING") + "".join(
            "{0:>10s}".format("d+" + str(c["day_offset"])) for c in cols)
        _log(header)

        for row in grid["rows"]:
            cells = "".join("{0:>10,.0f}".format(cell["pl_dollars"])
                            for cell in row["cells"])
            _log("    {0:>10,.2f}{1}".format(row["underlying"], cells))

        _log("    (columns are days from today; last column = expiry)")

    _log("")
    _log("  Holding IV constant is the OPTIMISTIC case. Compare the two grids:")
    _log("  the difference at any cell is what a volatility crush costs you at")
    _log("  that price level, independent of being directionally right.")


def _load_contract(ticker, option_type, expiry, strike):
    """Find one contract in the latest screen snapshot."""
    import json
    with open(cfg.LATEST_JSON, "r") as fh:
        payload = json.load(fh)

    for c in payload["contracts"]:
        if (c["ticker"] == ticker.upper()
                and c["option_type"] == option_type.lower()
                and c["expiry"] == expiry
                and abs(c["strike"] - float(strike)) < 1e-6):
            return c
    return None


if __name__ == "__main__":
    if len(sys.argv) < 6:
        print("usage: python3 options_position.py TICKER call|put YYYY-MM-DD STRIKE BUDGET"
              " [DAYS_HELD] [IV_DROP]")
        print("example: python3 options_position.py AAPL call 2026-09-18 320 1000 7 0.2")
        raise SystemExit(2)

    t, otype, exp, k, budget = sys.argv[1:6]
    held = int(sys.argv[6]) if len(sys.argv) > 6 else 7
    drop = float(sys.argv[7]) if len(sys.argv) > 7 else 0.20

    found = _load_contract(t, otype, exp, k)
    if found is None:
        print("contract not found in {0}".format(cfg.LATEST_JSON))
        print("run options_screener.py first, and check the ticker/expiry/strike")
        raise SystemExit(1)

    print_analysis(analyse(found, float(budget), held, drop))

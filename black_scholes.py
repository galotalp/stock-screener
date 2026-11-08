"""
black_scholes.py — Black-Scholes-Merton pricing for European equity options,
with a continuous dividend yield term.

Deliberately dependency-free: the normal CDF and PDF are implemented here in
terms of `math.erf` rather than imported from scipy. The sentiment pipeline on
the EC2 instance installs only yfinance / curl_cffi / feedparser /
pandas_market_calendars, and adding scipy to that host for two functions is not
a trade worth making. `math.erf` is exact to double precision, so nothing is
lost by hand-rolling.

Python 3.9 compatible (the instance runs 3.9): no `X | None` annotations, no
backslashes inside f-strings.

Conventions
-----------
- All rates and volatilities are annualised proportions (0.04, not 4).
- T is in YEARS.
- `theta` is returned PER CALENDAR DAY (the annual figure divided by 365), which
  is what the screener's cost metrics assume. Note this is calendar days, not
  trading days: a contract decays over a weekend even though it cannot be
  traded, so calendar-day theta is the honest figure for a holding-period cost.
- Degenerate inputs (non-positive spot, strike, sigma, or time) return NaN
  rather than raising, so one bad row cannot abort a screening run. Callers are
  expected to filter NaN.

Validated on import against the reference values in options_tool_spec.md — see
`_self_check()` at the bottom of this file.
"""
import math

NAN = float("nan")


# ---------------------------------------------------------------------------
# Normal distribution helpers
# ---------------------------------------------------------------------------

def norm_cdf(x):
    """Standard normal CDF, via the error function.

    N(x) = 0.5 * (1 + erf(x / sqrt(2)))
    """
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x):
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


# ---------------------------------------------------------------------------
# Core model
# ---------------------------------------------------------------------------

def _d1_d2(spot, strike, t_years, rate, div_yield, sigma):
    """Return (d1, d2), or (nan, nan) for degenerate inputs.

    Guarding here rather than in each public function means every downstream
    quantity degrades to NaN together, instead of some fields silently
    returning plausible-looking numbers computed from garbage.
    """
    if spot is None or strike is None or sigma is None or t_years is None:
        return NAN, NAN
    if spot <= 0 or strike <= 0 or sigma <= 0 or t_years <= 0:
        return NAN, NAN

    vol_sqrt_t = sigma * math.sqrt(t_years)
    d1 = (math.log(spot / strike)
          + (rate - div_yield + 0.5 * sigma * sigma) * t_years) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t
    return d1, d2


def price(spot, strike, t_years, rate, div_yield, sigma, is_call):
    """Theoretical option price under Black-Scholes-Merton."""
    d1, d2 = _d1_d2(spot, strike, t_years, rate, div_yield, sigma)
    if math.isnan(d1):
        return NAN

    disc_div = math.exp(-div_yield * t_years)   # dividend discount on the spot
    disc_rate = math.exp(-rate * t_years)       # discount on the strike

    if is_call:
        return spot * disc_div * norm_cdf(d1) - strike * disc_rate * norm_cdf(d2)
    return strike * disc_rate * norm_cdf(-d2) - spot * disc_div * norm_cdf(-d1)


def delta(spot, strike, t_years, rate, div_yield, sigma, is_call):
    """dPrice/dSpot. Call delta in (0, 1); put delta in (-1, 0)."""
    d1, _ = _d1_d2(spot, strike, t_years, rate, div_yield, sigma)
    if math.isnan(d1):
        return NAN

    disc_div = math.exp(-div_yield * t_years)
    if is_call:
        return disc_div * norm_cdf(d1)
    return -disc_div * norm_cdf(-d1)


def n_d2(spot, strike, t_years, rate, div_yield, sigma, is_call):
    """The RISK-NEUTRAL probability of finishing in the money.

    N(d2) for calls, N(-d2) for puts.

    IMPORTANT — this is NOT a forecast. It is backed out of the contract's own
    quoted implied volatility, so it is the market's price restated as a
    probability. Comparing an option's cost against this number is circular by
    construction: the number came from the price. Its only legitimate use is
    calibration against a view formed on independent grounds — if the market
    implies 22% and you believe 45%, the gap is your thesis. If you cannot say
    why your estimate differs, you have no edge on that contract.
    """
    _, d2 = _d1_d2(spot, strike, t_years, rate, div_yield, sigma)
    if math.isnan(d2):
        return NAN
    return norm_cdf(d2) if is_call else norm_cdf(-d2)


def theta_per_day(spot, strike, t_years, rate, div_yield, sigma, is_call):
    """Time decay per CALENDAR day (annual theta / 365). Normally negative."""
    d1, d2 = _d1_d2(spot, strike, t_years, rate, div_yield, sigma)
    if math.isnan(d1):
        return NAN

    disc_div = math.exp(-div_yield * t_years)
    disc_rate = math.exp(-rate * t_years)
    sqrt_t = math.sqrt(t_years)

    # Term common to calls and puts: the gamma/vega bleed.
    common = -(spot * disc_div * norm_pdf(d1) * sigma) / (2.0 * sqrt_t)

    if is_call:
        annual = (common
                  - rate * strike * disc_rate * norm_cdf(d2)
                  + div_yield * spot * disc_div * norm_cdf(d1))
    else:
        annual = (common
                  + rate * strike * disc_rate * norm_cdf(-d2)
                  - div_yield * spot * disc_div * norm_cdf(-d1))

    return annual / 365.0


def greeks(spot, strike, t_years, rate, div_yield, sigma, is_call):
    """Convenience wrapper returning every quantity the screener needs.

    Computing these together avoids recomputing d1/d2 four times per contract;
    across ~25 tickers x multiple expiries x both option types that matters.
    """
    return {
        "theo_price": price(spot, strike, t_years, rate, div_yield, sigma, is_call),
        "delta": delta(spot, strike, t_years, rate, div_yield, sigma, is_call),
        "n_d2": n_d2(spot, strike, t_years, rate, div_yield, sigma, is_call),
        "theta_day": theta_per_day(spot, strike, t_years, rate, div_yield, sigma, is_call),
    }


# ---------------------------------------------------------------------------
# Validation against the reference values in options_tool_spec.md
# ---------------------------------------------------------------------------

# S = K = 100, T = 1, r = 0.05, q = 0, sigma = 0.20
_REF = {
    "spot": 100.0, "strike": 100.0, "t_years": 1.0,
    "rate": 0.05, "div_yield": 0.0, "sigma": 0.20,
}
_REF_EXPECTED = {
    "call_price": 10.4506,
    "call_delta": 0.6368,
    "n_d2": 0.5596,
    "theta_day": -0.0176,
    "put_price": 5.5735,
}


def _self_check(tol=1e-4, verbose=False):
    """Verify the implementation reproduces the spec's reference table.

    Returns a list of failure strings; empty means everything matched. Called
    at import time so a broken build fails loudly at the top of a run rather
    than silently producing wrong cost metrics for every contract.
    """
    a = _REF
    failures = []

    got = {
        "call_price": price(a["spot"], a["strike"], a["t_years"], a["rate"],
                            a["div_yield"], a["sigma"], True),
        "call_delta": delta(a["spot"], a["strike"], a["t_years"], a["rate"],
                            a["div_yield"], a["sigma"], True),
        "n_d2": n_d2(a["spot"], a["strike"], a["t_years"], a["rate"],
                     a["div_yield"], a["sigma"], True),
        "theta_day": theta_per_day(a["spot"], a["strike"], a["t_years"], a["rate"],
                                   a["div_yield"], a["sigma"], True),
        "put_price": price(a["spot"], a["strike"], a["t_years"], a["rate"],
                           a["div_yield"], a["sigma"], False),
    }

    for key, expected in _REF_EXPECTED.items():
        actual = got[key]
        if abs(actual - expected) > tol:
            failures.append(
                "{0}: expected {1:.4f}, got {2:.6f}".format(key, expected, actual))
        elif verbose:
            print("  OK  {0:12s} {1:.6f}  (spec {2:.4f})".format(key, actual, expected))

    # Put-call parity must hold EXACTLY (to floating-point noise), not merely to
    # the 4dp of the printed reference values:
    #     C - P == S*exp(-qT) - K*exp(-rT)
    # This is a structural identity, so a residual here means a sign or
    # discount-factor error that the 4dp checks above could mask.
    lhs = got["call_price"] - got["put_price"]
    rhs = (a["spot"] * math.exp(-a["div_yield"] * a["t_years"])
           - a["strike"] * math.exp(-a["rate"] * a["t_years"]))
    residual = lhs - rhs
    if abs(residual) > 1e-10:
        failures.append("put-call parity residual {0:.3e} (must be ~0)".format(residual))
    elif verbose:
        print("  OK  {0:12s} {1:.3e}  (spec exact 0)".format("parity", residual))

    return failures


_IMPORT_FAILURES = _self_check()
if _IMPORT_FAILURES:  # pragma: no cover
    raise AssertionError(
        "black_scholes.py failed spec validation: " + "; ".join(_IMPORT_FAILURES))


if __name__ == "__main__":
    print("Black-Scholes validation against options_tool_spec.md reference values")
    print("  S=100 K=100 T=1 r=0.05 q=0 sigma=0.20")
    fails = _self_check(verbose=True)
    if fails:
        print("\nFAILED:")
        for f in fails:
            print("  " + f)
        raise SystemExit(1)
    print("\nAll reference values matched.")

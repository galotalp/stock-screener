"""
sim_config.py — thresholds for the sentiment -> options simulation cascade.

Separate from options_config.py because these are RESEARCH parameters, not
market-data parameters. They encode the selection rule being tested, and the
whole point of the exercise is that they will be revised as evidence
accumulates. Keeping them in one file means a revision is a diff, and the
ledger records which values were in force at the time.

Python 3.9 compatible.
"""

# ---------------------------------------------------------------------------
# STAGE 2 — "does news lead price for this name?"
# ---------------------------------------------------------------------------
# Read from data/session_analysis.json, which cross-correlates hourly sentiment
# against hourly price returns at lags of -24h..+24h.
#
# MIN_SENT_HOURS is the load-bearing threshold and deserves an explanation.
#
# As of 2026-08, the overlap between the options universe and the sentiment
# universe is 9 tickers, and 8 of those have fewer than 35 sentiment-hours.
# Their reported correlations look impressive (|0.48| to |0.91|) but are
# computed on 10-33 observations while scanning 24 candidate lags. With that
# many lags and that few points, the best-fitting lag is near-guaranteed to
# look strong by chance: the expected maximum |r| across 24 draws at n=15 is
# already around 0.5 under the null of no relationship.
#
# The tell is that the sign FLIPS between the all-hours and ex-earnings regimes
# for 6 of the 9 names (AAPL +0.75 vs -0.61; AMZN -0.74 vs +0.86). A real
# lead/lag relationship does not invert when earnings windows are removed.
#
# So this gate is deliberately set where a correlation starts to mean something
# rather than where the current data would pass. Most names will fail it for
# months. That is the correct behaviour: the alternative is a ledger full of
# trades justified by noise, which would teach the wrong lesson precisely
# because it would sometimes be profitable.
MIN_SENT_HOURS = 200          # hourly sentiment observations required
MIN_LEAD_CORR = 0.30          # |corr| at the best lag
REQUIRE_NEGATIVE_LAG = True   # sentiment must LEAD price, not follow it
REQUIRE_SIGN_STABILITY = True # sign must agree between 'all' and 'ex_earn'
USE_EX_EARN_REGIME = True     # prefer the earnings-excluded correlation

# ---------------------------------------------------------------------------
# STAGE 3 — "has a sentiment change NOT yet been reflected in price?"
# ---------------------------------------------------------------------------
# The thesis: sentiment moved, price has not yet followed, and the lead/lag
# relationship from stage 2 says price tends to follow for this name. The gap
# is the opportunity. If price has ALREADY moved with sentiment, the
# information is priced in and there is nothing left to capture.
SENT_LOOKBACK_DAYS = 3        # window over which to measure the sentiment change
SENT_BASELINE_DAYS = 14       # baseline the change is measured against
MIN_SENT_CHANGE = 0.15        # minimum |change| in mean score, on [-1, 1]
MIN_MENTIONS_RECENT = 5       # mentions in the lookback window (avoid 1-post spikes)
MAX_PRICE_MOVE_PCT = 2.0      # price must have moved LESS than this, %
                              # (i.e. the move has not happened yet)
REQUIRE_DIRECTIONAL_GAP = True  # sentiment and price must not already agree

# ---------------------------------------------------------------------------
# STAGE 4 — contract selection
# ---------------------------------------------------------------------------
SIM_BUDGET = 1000.0           # notional dollars per simulated position
TARGET_DTE_MIN = 20           # prefer enough time for the thesis to play out
TARGET_DTE_MAX = 60
MAX_BREAKEVEN_SDS = 1.25      # reject contracts needing an implausible move
MAX_DRAG_7D_PCT = 40.0        # reject high-friction contracts outright
PREFER_MONEYNESS = 0.03       # target near-the-money (|strike/spot - 1|)
MAX_MONEYNESS = 0.10
MAX_POSITIONS_PER_DAY = 1     # "the most recommended option", per the brief
MAX_OPEN_PER_TICKER = 1       # no doubling up on one name

# Quotes pulled outside the regular session have unrepresentative spreads. A
# simulated fill at those prices would corrupt the ledger, so entries are
# refused. Set False only if you accept that the P/L becomes indicative.
REQUIRE_REGULAR_SESSION = True

# ---------------------------------------------------------------------------
# Earnings handling
# ---------------------------------------------------------------------------
# A position held through an earnings release is dominated by the event, not by
# the sentiment signal being tested. Since the point of this exercise is to
# learn whether sentiment predicts direction, contaminated positions are worse
# than no positions.
AVOID_EARNINGS_WITHIN_DAYS = 3   # skip if earnings fall within N days of entry
FLAG_EARNINGS_BEFORE_EXPIRY = True  # record when expiry is past an earnings date

"""
options_config.py — every tunable constant for the options tool.

The spec's rule is "no hidden magic numbers": if a number changes behaviour, it
lives here and nowhere else. Kept in its own module (rather than at the top of
one script) because the screener, the position calculator, the simulation
cascade and the site generator all need the same values, and duplicating them
across four files is how they drift apart.

Python 3.9 compatible.
"""
import os

# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------
# ~25 high-options-volume symbols. HARD-CODED ON PURPOSE: the spec requires a
# stable universe so the history log stays comparable across runs. Scraping a
# "most active" table each run would silently change the denominator of every
# median and make the accumulated IV history meaningless.
#
# Note for the simulation side: SPY/QQQ/IWM are index ETFs and are structurally
# excluded from the sentiment study's universe (see ETF_LIKE in
# build_universe_dynamic.py), so they will screen here but can never pass the
# simulation's sentiment gate. That is intended — the two tools have different
# scopes, and the screener should still show the most-traded instruments.
TICKERS = [
    # index ETFs
    "SPY", "QQQ", "IWM",
    # mega-cap tech
    "NVDA", "TSLA", "AAPL", "AMD", "MSFT", "AMZN", "META", "GOOGL", "NFLX", "AVGO",
    # higher-IV single names
    "MU", "PLTR", "COIN", "MSTR", "SMCI", "HOOD", "GME", "SOFI", "INTC", "F",
    "BAC", "XLF",
]

# ---------------------------------------------------------------------------
# Chain filters
# ---------------------------------------------------------------------------
DTE_MIN = 3                   # days to expiration, lower bound
DTE_MAX = 75                  # upper bound — wide enough to show term structure
MONEYNESS_WINDOW = 0.15       # keep strikes within +/-15% of spot
MIN_OPEN_INTEREST = 100
MIN_VOLUME = 25
MAX_SPREAD_PCT_MID = 0.15
REQUIRE_NONZERO_BID = True    # a zero bid cannot be exited at any price

# ---------------------------------------------------------------------------
# Model inputs
# ---------------------------------------------------------------------------
RISK_FREE_RATE = 0.04         # 3-month T-bill proxy
DIVIDEND_YIELD = 0.012        # blanket assumption; immaterial at short DTE

# IV normalisation: the upstream source sometimes reports IV as a proportion
# (0.28) and sometimes as a percentage (28). Anything above this threshold is
# treated as a percentage and divided by 100.
IV_PERCENT_THRESHOLD = 3.0

# ---------------------------------------------------------------------------
# Costs (Part 2 — dollar calculator)
# ---------------------------------------------------------------------------
# Approximately fixed PER CONTRACT, and charged on BOTH entry and exit. On a
# $0.30 contract these are several percent each way, which is exactly where the
# percentage-only view flatters cheap options.
COMMISSION_PER_CONTRACT = 0.65    # typical retail broker
REG_FEE_PER_CONTRACT = 0.05       # exchange + regulatory, approximate
CONTRACT_MULTIPLIER = 100

# ---------------------------------------------------------------------------
# Ranking / reporting
# ---------------------------------------------------------------------------
DRAG_HORIZON_DAYS = 7         # holding horizon for the least-friction ranking
TOP_N_DISPLAY = 20            # contracts shown in the ranking
CHEAP_CONTRACT_MAX_ASK = 0.50 # the "cheap lottery ticket" cohort breakout
ATM_MONEYNESS = 0.03          # |moneyness| threshold for term-structure ATM IV
IV_RANK_DTE_MIN = 25          # DTE band used for IV rank
IV_RANK_DTE_MAX = 65
IV_RANK_MIN_RUN_DATES = 20    # distinct run dates needed before IV rank is real

# ---------------------------------------------------------------------------
# Network behaviour
# ---------------------------------------------------------------------------
SLEEP_BETWEEN_QUOTES = 0.3    # seconds, per spec
SLEEP_BETWEEN_CHAINS = 0.5    # seconds, per spec
MAX_RETRIES_PER_TICKER = 2

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))

OUTPUT_DIR = os.path.join(_HERE, "data", "options")
SNAPSHOT_DIR = os.path.join(OUTPUT_DIR, "snapshots")
HISTORY_FILE = os.path.join(OUTPUT_DIR, "iv_history.csv")
LATEST_JSON = os.path.join(OUTPUT_DIR, "latest_screen.json")

# Snapshot retention. Each full-universe snapshot is roughly 1 MB, so daily runs
# would accumulate ~300 MB/year on an instance that exists to boot, scrape and
# shut down. The snapshots are a per-run archive for ad-hoc inspection; the
# artefact that actually compounds is iv_history.csv, which is tiny (a few
# hundred bytes per run) and is NEVER pruned.
SNAPSHOT_RETENTION = 60       # keep this many most recent snapshot CSVs; 0 = keep all

# Simulation
SIM_DIR = os.path.join(_HERE, "data", "sim")
SIM_LEDGER = os.path.join(SIM_DIR, "ledger.jsonl")        # one record per decision
SIM_POSITIONS = os.path.join(SIM_DIR, "positions.jsonl")  # open + settled positions

# Sentiment inputs (READ ONLY — this tool never writes to the sentiment store)
MENTIONS_DIR = os.path.join(_HERE, "data", "mentions")
PRICES_DIR = os.path.join(_HERE, "data", "prices")
EARNINGS_DIR = os.path.join(_HERE, "data", "earnings")
SESSION_ANALYSIS = os.path.join(_HERE, "data", "session_analysis.json")

SITE_HTML = os.path.join(_HERE, "options.html")


def ensure_dirs():
    """Create output directories if absent. Safe to call repeatedly."""
    for d in (OUTPUT_DIR, SNAPSHOT_DIR, SIM_DIR):
        if not os.path.isdir(d):
            os.makedirs(d)

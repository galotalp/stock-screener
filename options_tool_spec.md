# Build Spec: Options Cost & Position Analysis Tool

## Context and purpose

Build a two-part local tool for analysing listed equity options.

**Part 1** is a scheduled screener that pulls option chains and computes the
structural cost of holding each contract.

**Part 2** is an interactive position calculator: given one chosen contract and
a dollar budget, it returns every figure in dollars rather than percentages.

**Critical framing — do not build this as a trade-finder.** The tool does not
identify mispriced or underpriced options and must not be described as doing so.
Implied volatility is the market's own volatility estimate, and the probability
of finishing in the money is derived from that same IV — so comparing an
option's price against its own implied probability is circular and will show
every contract as fairly priced. What this tool measures is **cost**: the
guaranteed, quantifiable drag paid on entry and over the holding period. Its use
is to identify the least-costly vehicle for a directional view already held on
other grounds, not to generate the view.

## Environment and conventions

- **Language: your choice.** Any language with a dataframe/tabular abstraction,
  a normal CDF and PDF, and an options data source is suitable. Reasonable
  options include Python (`pandas`, `scipy.stats`, `yfinance`), R
  (`dplyr`/`tidyr`, `quantmod`), Julia, or TypeScript with a numerics library.
  Pick one and state the choice before writing code. Do not split the pipeline
  across languages except for the documented data-source fallback below.
- Target platform: macOS on Apple Silicon. Runs from the command line.
- Style: verbose and explicit. Print row counts to stdout after every data
  transformation and every filtering step, so attrition through the pipeline is
  visible. Prefer readable named intermediate objects over dense one-liners or
  deeply chained expressions.
- No hidden magic numbers — every threshold goes in a config block at the top of
  the script.
- Wrap every network call in the language's error handling so a single failed
  ticker logs a message and the run continues, rather than aborting.

---

# PART 1 — Chain screener

## Configuration block (top of script)

- `TICKERS`: vector of ~25 high-options-volume symbols. Include index ETFs
  (SPY, QQQ, IWM), mega-cap tech (NVDA, TSLA, AAPL, AMD, MSFT, AMZN, META,
  GOOGL, NFLX, AVGO), and higher-IV single names (MU, PLTR, COIN, MSTR, SMCI,
  HOOD, GME, SOFI, INTC, F, BAC, XLF). Hard-code this list rather than scraping
  a "most active" table each run — a stable universe is required for the history
  log to be comparable across runs.
- `DTE_MIN = 3`, `DTE_MAX = 75` — days-to-expiration window. Wide enough to show
  the term structure across expiries, not just the front month.
- `MONEYNESS_WINDOW = 0.15` — keep strikes within ±15% of spot.
- `MIN_OPEN_INTEREST = 100`
- `MIN_VOLUME = 25`
- `MAX_SPREAD_PCT_MID = 0.15`
- `REQUIRE_NONZERO_BID = true` — a zero bid means the position cannot be exited
  at any price; these must be dropped.
- `RISK_FREE_RATE = 0.04` (3-month T-bill proxy)
- `DIVIDEND_YIELD = 0.012` (blanket assumption; immaterial at short DTE)
- `OUTPUT_DIR`, `HISTORY_FILE` paths.

## Data acquisition

1. **Spot prices** — per ticker, try a live quote first, fall back to the most
   recent daily close if that fails. Log which path was used. Sleep ~0.3s
   between requests.
2. **Option chains** — request **all expiries** within the DTE window per
   ticker. Some libraries accept a date range in a single call; others require
   first listing available expiry dates, then iterating over those inside the
   window. Either is fine — the requirement is that the run covers multiple
   expiries per ticker, not just the front month. Sleep ~0.5s between requests.
   Catch errors per ticker, log them, and continue rather than aborting the run.
3. Normalise the returned structure so the parsing loop always sees a uniform
   collection of expiries, including the edge case where a single expiry is
   returned in a flattened or unwrapped shape.
4. **Column mapping must be defensive.** The upstream schema has changed
   historically; map candidate column names (`Strike`/`strike`,
   `Bid`/`bid`, `OI`/`OpenInterest`/`openInterest`,
   `IV`/`ImpliedVolatility`/`impliedVolatility`, etc.) rather than assuming a
   fixed set.
5. **IV normalisation** — the source sometimes reports IV as a proportion
   (0.28) and sometimes as a percentage (28). Normalise to proportion: if the
   value exceeds 3, divide by 100.
6. **Expiry date** — parse from the OCC contract symbol
   (`ROOT` + `YYMMDD` + `C`/`P` + strike×1000), which is more reliable than the
   expiry labels returned by the API. Fall back to the label if the symbol
   fails to parse, and log how many rows needed the fallback.

## Black-Scholes module

Compute theoretical price, delta, N(d2), and theta using each contract's own
quoted IV. Handle the dividend yield term. Return theta **per calendar day**
(divide the annual figure by 365). Guard against NaN from degenerate inputs
(non-positive spot, strike, sigma, or time).

**Validate the implementation against these reference values** for
S = K = 100, T = 1, r = 0.05, q = 0, sigma = 0.20:

| Quantity | Expected |
|---|---|
| Call price | 10.4506 |
| Call delta | 0.6368 |
| N(d2) | 0.5596 |
| Theta per day | −0.0176 |
| Put price | 5.5735 |
| Put-call parity residual | 0 (exact) |

## Per-contract metrics

- `mid` = (bid + ask) / 2
- `entry_cost_per_contract` = ask × 100
- `spread_abs` = ask − bid
- `spread_pct_mid` = (ask − bid) / mid
- **`round_trip_cost_pct`** = (ask − bid) / ask — the immediate unavoidable loss
  on a round trip at unchanged quotes. You buy at the ask and sell at the bid,
  always.
- `moneyness` = strike / spot − 1
- `breakeven_price` = strike + ask for calls, strike − ask for puts
- `breakeven_move_pct` = breakeven_price / spot − 1
- **`theta_pct_of_premium_per_day`** = 100 × theta_day / ask — daily decay as a
  share of what was paid.
- `spread_in_theta_days` = spread_abs / |theta_day| — expresses the bid-ask
  haircut as an equivalent number of days of decay.
- `ask_vs_theo_pct` — quoted ask vs model price on quoted IV; flags stale or
  crossed quotes.
- `sd_move_pct` = IV × √(T in years) — the size of a one-standard-deviation
  move over the remaining life.
- **`breakeven_in_sds`** = |breakeven_move_pct| / sd_move_pct — **how many
  standard deviations of movement are required to break even.** This is the key
  normalising metric: a raw required move of 2% is trivial for a high-IV name
  over 60 days and implausible for a low-IV name over 5 days, so expressing it
  in SDs makes required moves comparable across tickers and horizons. Roughly:
  0.5 = half a typical move; 1.0 = a typical move; 2.0+ = a two-sigma move
  (~5% odds one-sided under a normal assumption), which is where cheap OTM
  contracts live.
- **`p_itm`** = N(d2) for calls, N(−d2) for puts — the **risk-neutral**
  probability of finishing in the money, backed out of the option's own quoted
  IV. Label it clearly in code comments and output as *not a forecast*: it is
  the market's price expressed as a probability, for comparison against the
  user's own view.

**Note in comments why `p_itm` and `breakeven_in_sds` are both needed:** they
measure different things and can disagree usefully. `p_itm` is the chance of
finishing past the strike; `breakeven_in_sds` is the move needed to also recover
the premium paid. A contract can have a 40% chance of finishing ITM while
needing 1.4 SDs to break even — the holder is directionally right and still
loses money. The gap between them is the premium, expressed in movement.

## Ranking

- `drag_7d_pct` = 100 × round_trip_cost_pct + |theta_pct_of_premium_per_day| × 7
- Sort ascending and display the 20 lowest-drag contracts.
- Name and comment this explicitly as a **least-friction ranking, not an
  expected-return ranking.**

## Summary output

Print medians across the screened universe for: round-trip spread cost, theta
burn per day, implied P(ITM), and breakeven move (both in % and in SDs).

Then print a **separate breakout of contracts priced at or below $0.50** — the
"cheap lottery ticket" cohort — with the same medians. Contracts that look
cheapest in dollar terms are typically the worst on all of these measures, and
the output should make that visible rather than leaving it as folklore. If none
survive the liquidity filters, state that explicitly as the finding.

## Term structure

Group surviving contracts by ticker and expiry, filter to near-the-money
(|moneyness| ≤ 0.03), and report mean ATM IV by DTE in a wide table (one row per
ticker, one column per DTE). Comment on why this matters: an inverted term
structure — near-dated IV above far-dated — usually indicates an event or stress
episode priced into the front, and is the classic decay setup.

## History log — the component that compounds in value

Every run appends a compact record (run timestamp, run date, ticker, expiry
date, DTE, ATM IV, contract count) to a persistent CSV.

**Rationale to include in comments:** IV rank requires a distribution of past
IV, which no single snapshot can provide. Accumulating this history is what
makes it possible to compute genuine IV percentiles per ticker, and therefore to
*test* whether volatility mean-reverts in these names rather than assuming it.

Once the log spans **≥20 distinct run dates**, compute and print IV rank per
ticker for the 25–65 DTE band: `(current_IV − min_IV) / (max_IV − min_IV)`,
sorted descending. Below 20 dates, print how many more runs are needed.

Also write a full timestamped snapshot CSV of all screened contracts per run.

## Scheduling

Provide a **macOS `launchd` plist** (not cron — launchd is the supported path on
Apple Silicon), with `StartCalendarInterval`, and `StandardOutPath` /
`StandardErrorPath` pointing into the output directory. Include the
`launchctl load` command.

Invoke the script via an **absolute path to the interpreter** for whichever
language was chosen — launchd does not inherit the interactive shell's `PATH`, so
a bare interpreter name will fail silently. If the language uses a virtual
environment or project-local dependencies, point at the environment's own
interpreter rather than the system one.

**Timing matters:** schedule the run while the US market is open, ideally
mid-afternoon ET. Quotes pulled after the close are stale, and spreads printed
at the open are unrepresentatively wide. Account for the local timezone offset
when setting the hour.

## Known fragility — document this in the script

The free public option endpoint requires a cookie/crumb handshake that library
support for has broken and been patched repeatedly. If chain fetches fail
wholesale, the documented fallbacks are:

1. Install the data library from its development source rather than the language's
   stable package registry — fixes for this typically land upstream well before
   they are released.
2. Swap the data source. As of writing, Python's `yfinance` has been the most
   consistently maintained free option-chain client, so if the chosen language is
   not Python, the fallback is a small Python helper that fetches chains and
   writes CSV for the main script to read back in.
3. For anything intended to be traded on, use a broker API instead of a scraped
   free feed — broker quotes are the ones that can actually be executed
   against; free feeds are delayed and occasionally crossed.

**Therefore: isolate data acquisition behind a single function or module** whose
only contract is to return a normalised table with the columns listed above. Every
metric, filter, and output stage must depend on that table and nothing else, so a
source swap touches one file and requires no changes downstream. This is the most
important structural requirement in the spec — the data source is the part most
likely to break, and the part least worth rewriting the tool over.

---

# PART 2 — Dollar-denominated position calculator

Interactive module. **Input:** one chosen contract (from the screener output)
plus a dollar amount the user is willing to spend. **Output:** every figure from
Part 1 converted from percentages into dollars, plus a payoff grid.

Percentages obscure how small the absolute stakes can be relative to fixed
costs. This module exists to remove that ambiguity.

## Requirement 1 — Integer contract sizing with explicit residual

Contracts are integers carrying a 100× multiplier, so the budget will not divide
cleanly:

```
n_contracts = floor(budget / (ask * 100))
capital_deployed = n_contracts * ask * 100
residual = budget - capital_deployed
```

**Report the residual explicitly.** It materially changes return-on-capital: a
$1,000 budget against a $3.40 ask buys 2 contracts for $680, leaving $320 idle —
a third of the stated budget doing nothing. All return figures should be
reported both on capital deployed and on total budget.

## Requirement 2 — Commissions and fees as first-class inputs

Per-contract commission plus exchange and regulatory fees are approximately
fixed **per contract**, which makes them a large percentage of premium on cheap
contracts — several percent each way on a $0.30 contract. They apply on **both
entry and exit**.

- Put them in a config constant (per-contract commission, per-contract
  regulatory/exchange fee).
- Fold them into all drag calculations and into the payoff grid.
- Without this, the dollar figures will flatter cheap contracts precisely where
  they are worst.

## Requirement 3 — Non-linear theta

**Do not multiply daily theta by the number of days held.** That linear
approximation is acceptable in percentage terms but misleading in dollars,
because theta accelerates as expiry approaches — for a ~10-DTE contract the
linear version can understate a week's decay meaningfully.

Instead: **re-price with Black-Scholes at each forward day** (shrinking T, spot
held flat, IV held at the assumed level) and sum the actual day-over-day
differences.

## Requirement 4 — Payoff grid

The primary output. A table where:

- **Rows** = underlying price levels (a sensible range around spot, e.g. ±2 SDs)
- **Columns** = dates from today through expiry
- **Cells** = dollar profit/loss on the full position (n contracts, net of
  entry and exit fees)

**Use Black-Scholes re-pricing for the cells, not intrinsic value at expiry.**
Most positions are closed early rather than held to expiration, so the grid must
show what the position is worth on the way there, not only at the endpoint. Only
the final column is intrinsic value.

## Requirement 5 — IV must be adjustable, not constant

The payoff grid necessarily assumes an IV path, and **holding IV constant is
optimistic.** Post-earnings volatility crush routinely costs more than the
directional move gains.

Therefore expose IV as an adjustable input (a slider in a UI, or a parameter
that can be swept) rather than a fixed constant, and let the user regenerate the
grid under a lower-IV assumption. Consider showing the grid under two IV
scenarios side by side: IV unchanged, and IV down by a user-set amount.

---

# Caveats to preserve in the code and any UI text

1. **This is not a mispricing detector.** Nothing in a chain snapshot identifies
   underpriced options; the quotes are the market's estimate, set by
   participants with faster data and better execution. Do not add features that
   claim or imply otherwise.
2. **`p_itm` is risk-neutral, not predictive.** It is the market's implied
   probability. Its use is calibration: if the market implies 22% and the user
   believes 45%, the gap is the thesis and should be statable. If the user
   cannot articulate why their estimate differs, that indicates no edge on that
   contract.
3. **SD scaling assumes lognormal returns.** Real equity returns have fat tails,
   so genuine 2-SD moves occur somewhat more often than a normal distribution
   implies — especially to the downside and especially in single names around
   catalysts. `breakeven_in_sds` is a comparability ruler, not a probability.
   Note this wherever the metric is displayed.
4. **Free-feed quotes are delayed and sometimes crossed.** Any figure derived
   from them is indicative, not executable.

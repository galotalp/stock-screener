"""
run_options.py — one-shot entry point for the whole options stage.

Runs, in order:
  1. screen the chains          (options_screener)
  2. settle / mark positions    (settle_positions)
  3. evaluate the cascade       (sim_cascade)
  4. re-mark, now including any position just opened
  5. render the static page     (gen_options_site)

Ordering note: settlement runs BEFORE the cascade so that a position expiring
today is closed out before a new one is considered, and again AFTER so a
position opened this run appears on the page with a mark rather than a blank.

Designed to be called from run_scrape.sh as the final stage, after every
sentiment step has finished. It is deliberately fail-soft: any stage can raise
without preventing the others from running, and a total failure still exits 0
so it can never take down the sentiment pipeline it is appended to.

Python 3.9 compatible.
"""
import datetime as dt
import sys
import traceback


def _log(msg):
    print(msg)
    sys.stdout.flush()


def _stage(name, fn, *args, **kwargs):
    """Run one stage, converting any exception into a logged warning."""
    try:
        return fn(*args, **kwargs), None
    except Exception as exc:
        _log("")
        _log("  !! stage '{0}' failed: {1}: {2}".format(name, type(exc).__name__, exc))
        traceback.print_exc()
        return None, exc


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    tickers = [a.upper() for a in argv if not a.startswith("-")] or None
    today = dt.date.today()

    started = dt.datetime.now()
    _log("=" * 92)
    _log("OPTIONS STAGE  {0}".format(today.isoformat()))
    _log("=" * 92)

    failures = []

    # 1 — screen
    import options_screener
    result, err = _stage("screen", options_screener.run, tickers)
    if err:
        failures.append("screen")
    contracts, meta = (result if result else ([], {}))
    screen_payload = {"meta": meta, "contracts": contracts} if contracts else None

    # 2 — settle expired positions and mark open ones
    import settle_positions
    _, err = _stage("settle", settle_positions.run, today, screen_payload)
    if err:
        failures.append("settle")

    # 3 — the cascade
    if screen_payload:
        import sim_cascade
        _, err = _stage("cascade", sim_cascade.run, today, screen_payload)
        if err:
            failures.append("cascade")
    else:
        _log("")
        _log("  skipping cascade: no screen data this run")

    # 4 — re-mark so a position opened above shows up with a mark
    if screen_payload:
        _stage("remark", settle_positions.run, today, screen_payload)

    # 5 — cumulative performance summary
    _stage("summarise", settle_positions.summarise)

    # 6 — render the page
    import gen_options_site
    _, err = _stage("site", gen_options_site.run)
    if err:
        failures.append("site")

    elapsed = (dt.datetime.now() - started).total_seconds()
    _log("")
    _log("=" * 92)
    if failures:
        _log("OPTIONS STAGE finished in {0:.1f}s WITH FAILURES: {1}".format(
            elapsed, ", ".join(failures)))
    else:
        _log("OPTIONS STAGE finished cleanly in {0:.1f}s".format(elapsed))
    _log("=" * 92)

    # Always exit 0. This stage is appended to the sentiment pipeline and must
    # never be able to fail that pipeline's run.
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
Classify a UTC timestamp into the US-equity market session it falls in, using the
authoritative NYSE calendar (holidays and early-close days handled correctly).

Sessions (US/Eastern), aligned to standard equity trading windows:
  pre_market   04:00–09:30 ET  on a trading day
  regular      09:30–16:00 ET  on a trading day (uses the calendar's actual
                               open/close, so early-close days end at 13:00)
  after_hours  16:00–20:00 ET  on a trading day (on early-close days regular ends
                               at 13:00 and after-hours effectively runs 13:00–20:00)
  overnight    20:00–04:00 ET  between two consecutive TRADING days (weeknights)
  weekend      any time on Saturday or Sunday (equities do not trade)
  holiday      a weekday the market is closed for a holiday (whole-day, outside any
                               overnight tail of the prior trading evening)

The classifier is deterministic and offline once the calendar year is cached.

API:
  session_of(dt_utc) -> 'pre_market'|'regular'|'after_hours'|'overnight'|'weekend'|'holiday'
  is_open(dt_utc)     -> True if regular session
"""
import datetime as dt

try:
    import pandas_market_calendars as mcal
    _NYSE = mcal.get_calendar("NYSE")
except Exception:  # pragma: no cover
    _NYSE = None

import functools

ET = None
try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    pass

# standard extended-hours boundaries (ET), independent of the regular open/close
PRE_START = dt.time(4, 0)
AH_END = dt.time(20, 0)


@functools.lru_cache(maxsize=8)
def _schedule_for_year(year):
    """Return {date: (open_utc, close_utc)} for all NYSE trading days in a year."""
    if _NYSE is None:
        return {}
    sched = _NYSE.schedule(start_date=f"{year}-01-01", end_date=f"{year}-12-31")
    out = {}
    for idx, row in sched.iterrows():
        out[idx.date()] = (row["market_open"].to_pydatetime(),
                           row["market_close"].to_pydatetime())
    return out


def _parse(dt_utc):
    if isinstance(dt_utc, str):
        dt_utc = dt.datetime.fromisoformat(dt_utc.replace("Z", "+00:00"))
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=dt.timezone.utc)
    return dt_utc.astimezone(dt.timezone.utc)


def session_of(dt_utc):
    """Market session for a UTC datetime (or ISO string)."""
    if _NYSE is None or ET is None:
        return "unknown"
    u = _parse(dt_utc)
    et = u.astimezone(ET)
    day = et.date()
    sched = _schedule_for_year(day.year)

    def reg(d):
        return sched.get(d)  # (open_utc, close_utc) or None

    t = et.time()
    prev = day - dt.timedelta(days=1)
    prev_is_trading = reg(prev) is not None

    # "closed" fallback resolves to weekend (Sat/Sun) or holiday (weekday closure).
    def closed_kind(d):
        return "weekend" if d.weekday() >= 5 else "holiday"

    today = reg(day)
    if today is not None:
        o_utc, c_utc = today
        o_et, c_et = o_utc.astimezone(ET), c_utc.astimezone(ET)
        if o_et.time() <= t < c_et.time():
            return "regular"
        if PRE_START <= t < o_et.time():
            return "pre_market"
        if c_et.time() <= t < AH_END:
            return "after_hours"
        if t >= AH_END:                 # 20:00–24:00 on a trading day -> overnight
            return "overnight"
        # 00:00–04:00 on a trading day: overnight tail of the PRIOR trading evening
        # if the prior day traded; otherwise it's the tail of the weekend/holiday
        # that just ended (e.g. Mon 3am after Sunday -> weekend).
        return "overnight" if prev_is_trading else closed_kind(prev)

    # Not a trading day (weekend or weekday holiday).
    # 00:00–04:00 can be the overnight tail of the prior trading evening
    # (Fri 20:00 -> Sat 04:00 is overnight, then weekend); otherwise weekend/holiday.
    if t < dt.time(4, 0) and prev_is_trading:
        return "overnight"
    return closed_kind(day)


def is_open(dt_utc):
    return session_of(dt_utc) == "regular"


def next_regular_open(dt_utc):
    """The UTC datetime of the next REGULAR-session open at or after dt_utc.

    Used to bound the "earnings-contaminated" window: an earnings release at time T
    contaminates [T, next_regular_open(T)) — the after-hours / pre-market gap plus
    the immediate reaction, up to (but not including) the next time the regular
    market opens. Correctly skips weekends/holidays via the NYSE schedule, so a
    Friday-after-hours report bounds through to Monday's open.

    Returns None if the calendar is unavailable or no open is found within a year.
    """
    if _NYSE is None:
        return None
    u = _parse(dt_utc)
    day = u.astimezone(ET).date() if ET else u.date()
    # scan forward day by day; a trading day's open is candidate if it's >= u
    for i in range(0, 400):
        d = day + dt.timedelta(days=i)
        sched = _schedule_for_year(d.year)
        row = sched.get(d)
        if row is not None:
            o_utc = row[0].astimezone(dt.timezone.utc)
            if o_utc >= u:
                return o_utc
    return None


if __name__ == "__main__":
    # (July 2025 is EDT = UTC-4, so ET = UTC - 4h)
    def show(iso, label, expect):
        got = session_of(iso)
        ok = "OK " if got == expect else "XX "
        print(f"  {ok}{iso}  {label:<26} -> {got}  (expect {expect})")
    print("Normal trading day (Mon 2025-07-07):")
    show("2025-07-07T07:00:00Z", "03:00 ET overnight-tail", "weekend")   # Mon 3am, prev=Sun
    show("2025-07-07T08:00:00Z", "04:00 ET pre-market", "pre_market")
    show("2025-07-07T14:00:00Z", "10:00 ET regular", "regular")
    show("2025-07-07T21:00:00Z", "17:00 ET after-hours", "after_hours")
    show("2025-07-08T01:00:00Z", "21:00 ET overnight", "overnight")
    show("2025-07-08T05:00:00Z", "01:00 ET Tue overnight-tail", "overnight")  # prev=Mon trading
    print("Holiday (Fri 2025-07-04):")
    show("2025-07-04T15:00:00Z", "11:00 ET holiday", "holiday")
    print("Weekend (Sat 2025-07-05):")
    show("2025-07-05T15:00:00Z", "11:00 ET weekend", "weekend")
    show("2025-07-05T02:00:00Z", "22:00 Fri ET -> overnight tail", "overnight")  # Fri 10pm
    print("Half day (Thu 2025-07-03, closes 13:00 ET):")
    show("2025-07-03T17:30:00Z", "13:30 ET after early close", "after_hours")
    show("2025-07-03T16:00:00Z", "12:00 ET still regular", "regular")

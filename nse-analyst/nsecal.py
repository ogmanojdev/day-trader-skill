# Vendored from nse-common/nsecal.py by tools/vendor.py. Do not edit here:
# edit nse-common/nsecal.py and re-run the script, or the suite will fail.
#!/usr/bin/env python3
"""The NSE clock and calendar. Standard library only, Python 3.8+.

One canonical copy lives in nse-common/. tools/vendor.py copies it into every
skill folder so a folder stays independently copyable into ~/.claude/skills/,
and tests/test_common.py fails if a copy drifts.

India does not observe daylight saving. IST is UTC+5:30 the year round, so the
whole nth-Sunday apparatus the US original needs collapses to one constant. Do
not reach for zoneinfo: the tools promise Python 3.8 and zoneinfo arrives in
3.9.

The session is 09:15 to 15:30 IST, 375 minutes, which is exactly 75 five-minute
bars. A pre-open call auction runs 09:00 to 09:08 and is not part of the bar
grid; bars before 09:15 are not session bars.

square_off() is 15:15 by default, not the close. Indian brokers square off
intraday (MIS) positions themselves at about 15:20 at market price, so a plan
that waits for 15:30 hands the exit to the broker. Sanyam uses the same 15:15
in backend/app/strategy_engine.py.

HOLIDAYS is the NSE equity (CM) segment trading-holiday list, read from
https://www.nseindia.com/api/holiday-master?type=trading on 2026-11-10.
NSE publishes the next calendar year around December, so the table ends at
HOLIDAYS_THROUGH and anything past it is reported unknown rather than guessed:
treating an unpublished holiday as a trading day is the failure that silently
backtests a day the market never traded. Refresh with:

    python3 tools/refresh_calendar.py

A holiday can still carry a special session. Diwali Muhurat trading is about an
hour on an evening the market is otherwise shut, and in 2026 it fell on a
Sunday (2026-11-08). MUHURAT holds those, so is_trading_day() stays False while
session() still returns the window.
"""

import datetime

__all__ = ["IST_OFFSET", "to_ist", "ist_to_utc", "utc_now", "utc_of", "ist_now",
           "HOLIDAYS", "HOLIDAYS_THROUGH", "MUHURAT", "SESSION_OPEN", "SESSION_CLOSE",
           "PRE_OPEN_START", "PRE_OPEN_END", "SQUARE_OFF", "BAR_MINUTES", "BARS_PER_DAY",
           "CalendarError", "is_trading_day", "covered", "session", "session_or_raise",
           "next_session_day", "prev_session_day", "trading_days", "bar_slots",
           "slot_of", "parse_ist", "fmt_ist"]

IST_OFFSET = datetime.timedelta(hours=5, minutes=30)
EPOCH = datetime.datetime(1970, 1, 1)

SESSION_OPEN = datetime.time(9, 15)
SESSION_CLOSE = datetime.time(15, 30)
PRE_OPEN_START = datetime.time(9, 0)
PRE_OPEN_END = datetime.time(9, 8)
SQUARE_OFF = datetime.time(15, 15)
BAR_MINUTES = 5
BARS_PER_DAY = 75

# NSE equity (CM) segment, from the exchange's holiday-master API, read 2026-11-10.
HOLIDAYS = {
    "2026-01-15": "Municipal Corporation Election - Maharashtra",
    "2026-01-26": "Republic Day",
    "2026-02-15": "Mahashivratri",
    "2026-03-03": "Holi",
    "2026-03-21": "Id-Ul-Fitr (Ramadan Eid)",
    "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Shri Mahavir Jayanti",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram",
    "2026-08-15": "Independence Day",
    "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-08": "Diwali Laxmi Pujan (Muhurat trading)",
    "2026-11-10": "Diwali-Balipratipada",
    "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
    "2026-12-25": "Christmas",
}
HOLIDAYS_THROUGH = "2026-12-31"

# Special sessions on days the market is otherwise closed: {date: (start, end)}.
# The exchange confirms the window by circular a few days ahead; 2026-11-08 ran
# 13:45 to 14:45 IST (a Sunday).
MUHURAT = {
    "2026-11-08": (datetime.time(13, 45), datetime.time(14, 45)),
}


class CalendarError(Exception):
    pass


# ---------------------------------------------------------------- clock

def to_ist(utc):
    """Naive UTC -> naive IST wall clock."""
    return utc.replace(tzinfo=None) + IST_OFFSET


def ist_to_utc(local):
    """Naive IST wall clock -> naive UTC. No DST, so this is exact and total."""
    return local.replace(tzinfo=None) - IST_OFFSET


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)


def ist_now():
    return to_ist(utc_now())


def utc_of(ts):
    """A Yahoo/epoch second -> naive UTC."""
    return EPOCH + datetime.timedelta(seconds=int(ts))


def parse_ist(text, what="a time"):
    """"2026-11-09", "2026-11-09 10:15" or "...Z" for UTC -> naive IST."""
    s = (text or "").strip()
    if not s:
        raise CalendarError("%s is empty" % what)
    z = s.endswith("Z")
    if z:
        s = s[:-1].strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            d = datetime.datetime.strptime(s, fmt)
            return to_ist(d) if z else d
        except ValueError:
            continue
    raise CalendarError('%s wants an IST time like "2026-11-09 10:15", not %r' % (what, text))


def fmt_ist(local, with_date=False):
    return local.strftime("%Y-%m-%d %H:%M IST" if with_date else "%H:%M IST")


# ---------------------------------------------------------------- calendar

def _as_date(d):
    if isinstance(d, datetime.datetime):
        return d.date()
    if isinstance(d, datetime.date):
        return d
    return parse_ist(str(d), "a date").date()


def covered(d):
    """Is this date inside the published holiday table? Past the end, callers must
    not assume the market was open."""
    return _as_date(d).isoformat() <= HOLIDAYS_THROUGH


def is_trading_day(d):
    """A normal session day. A Muhurat day is NOT one: the market is shut and a
    special window is bolted on, which is what session() reports."""
    d = _as_date(d)
    return d.weekday() < 5 and d.isoformat() not in HOLIDAYS


def session(d):
    """(open, close) IST times, or None when the market does not trade that day."""
    d = _as_date(d)
    special = MUHURAT.get(d.isoformat())
    if special:
        return special
    if not is_trading_day(d):
        return None
    return SESSION_OPEN, SESSION_CLOSE


def session_or_raise(d):
    """(open, close), or CalendarError saying why not.

    The uncovered-date check comes FIRST and on purpose. session() answers from
    the weekday plus the table, so a date past HOLIDAYS_THROUGH that happens to
    fall on a Tuesday looks like an ordinary session to it. Asking "is it in the
    table?" only after session() returned None lets exactly the dangerous case
    through: an unpublished holiday, silently backtested as a day that traded.
    """
    d = _as_date(d)
    if not covered(d):
        raise CalendarError(
            "%s is past the published NSE holiday table (ends %s), so this tool will not "
            "guess whether the market traded. Run tools/refresh_calendar.py."
            % (d.isoformat(), HOLIDAYS_THROUGH))
    s = session(d)
    if s:
        return s
    why = HOLIDAYS.get(d.isoformat()) or ("a %s" % d.strftime("%A"))
    raise CalendarError("the NSE equity market was closed on %s (%s)" % (d.isoformat(), why))


def next_session_day(d, limit=40):
    d = _as_date(d)
    for _ in range(limit):
        d += datetime.timedelta(days=1)
        if is_trading_day(d):
            return d
    return None


def prev_session_day(d, limit=40):
    d = _as_date(d)
    for _ in range(limit):
        d -= datetime.timedelta(days=1)
        if is_trading_day(d):
            return d
    return None


def trading_days(start, end):
    """Normal session days in [start, end], oldest first. Muhurat is excluded."""
    a, b = _as_date(start), _as_date(end)
    if not covered(b):
        raise CalendarError(
            "%s is past the published NSE holiday table (ends %s); refusing to invent "
            "session days. Run tools/refresh_calendar.py." % (b.isoformat(), HOLIDAYS_THROUGH))
    out, d = [], a
    while d <= b:
        if is_trading_day(d):
            out.append(d)
        d += datetime.timedelta(days=1)
    return out


# ---------------------------------------------------------------- the bar grid

def bar_slots(d=None, minutes=BAR_MINUTES):
    """Every bar START time of the session, oldest first. 75 of them at 5 minutes."""
    s = session(d) if d is not None else (SESSION_OPEN, SESSION_CLOSE)
    if not s:
        return []
    open_t, close_t = s
    day = _as_date(d) if d is not None else datetime.date(2000, 1, 1)
    cur = datetime.datetime.combine(day, open_t)
    end = datetime.datetime.combine(day, close_t)
    out = []
    while cur + datetime.timedelta(minutes=minutes) <= end:
        out.append(cur.time())
        cur += datetime.timedelta(minutes=minutes)
    return out


def slot_of(local, minutes=BAR_MINUTES):
    """Which bar does this IST instant belong to? 0 at the open, None off the grid
    or outside the session. A bar off the grid is Yahoo's live partial tick."""
    d = local.date()
    s = session(d)
    if not s:
        return None
    open_t, close_t = s
    start = datetime.datetime.combine(d, open_t)
    end = datetime.datetime.combine(d, close_t)
    if local < start or local >= end:
        return None
    delta = int((local - start).total_seconds())
    if delta % (minutes * 60):
        return None
    return delta // (minutes * 60)

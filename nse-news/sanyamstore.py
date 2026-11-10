# Vendored from nse-common/sanyamstore.py by tools/vendor.py. Do not edit here:
# edit nse-common/sanyamstore.py and re-run the script, or the suite will fail.
#!/usr/bin/env python3
"""Read intraday bars from Sanyam's candle store. Standard library only, Python 3.8+.

One canonical copy lives in nse-common/; tools/vendor.py copies it into every
skill folder and tests/test_common.py fails if a copy drifts.

Why this exists. Yahoo's chart endpoint is the only free source of NSE intraday
bars, and it throttles by IP hard: about a dozen chart calls in a burst earns a
429 on every endpoint for hours, which is long enough to miss the session you
were trying to trade. Sanyam already keeps a durable OHLC corpus in SQLite,
fetched from the broker, so the bars are there already and need no network call
at all.

It reads the database file directly with sqlite3 rather than importing Sanyam's
modules. Importing would drag in SQLAlchemy and Sanyam's whole app package, and
a skill folder has to stand on its own to be copied into ~/.claude/skills/.
Read-only, one table, a schema that has a uniqueness constraint on
(symbol, interval, ts) and is not going to move under us.

THE THING TO UNDERSTAND BEFORE TRUSTING A BAR FROM HERE: source.

Sanyam labels every bar with where it came from, because they are not equally
trustworthy, and its own model says so at app/models.py:170. HISTORY and
HISTORY_DB are the broker's own bars with true extremes. LIVE_AGG is aggregated
from a roughly 1 Hz last-traded-price poll, so it MISSES TRUE INTRABAR EXTREMES
and carries no volume.

That distinction is fatal for an opening-range breakout specifically. The range
is the highest high and lowest low of the first three bars, not their closes. On
the real TCS session in this repo's fixtures the 09:15 bar closed at 2456.70 and
reached 2495.60: the high is 39 rupees above the close. A LIVE_AGG bar sampled
once a second can easily miss a spike like that, which makes the range too
narrow, which manufactures a breakout that never happened. So bars carry their
source out of here and callers are expected to refuse LIVE_AGG for a range.
worst_source() is the helper for that.

Sanyam stores 1m as its base interval, so 5m often has to be derived. aggregate()
does that on the IST grid. Deriving from HISTORY 1m bars gives true extremes;
deriving from LIVE_AGG 1m bars does not, and the source propagates as the worst
of its inputs.
"""

import os
import sqlite3

__all__ = ["StoreError", "DEFAULT_DB_CANDIDATES", "TRUSTED_SOURCES", "find_db",
           "sanyam_symbol", "read_bars", "aggregate", "worst_source", "as_series",
           "describe_store"]

DEFAULT_DB_CANDIDATES = (
    "~/Documents/GitHub/sanyam/backend/trading_system.db",
    "../sanyam/backend/trading_system.db",
    "./trading_system.db",
)

# Bars whose extremes are the broker's, not a poll's.
TRUSTED_SOURCES = ("HISTORY", "HISTORY_DB", "DERIVED")
# Worst first, so worst_source() can just take the earliest match.
SOURCE_RANK = ("LIVE_AGG", "DERIVED", "HISTORY_DB", "HISTORY")

# Sanyam names indices its own way and translates at the broker boundary. Equity
# symbols are identical, so only the exceptions are listed.
SYMBOL_MAP = {
    "NIFTY": "NIFTY 50",
    "NIFTY50": "NIFTY 50",
    "^NSEI": "NIFTY 50",
    "NIFTYBANK": "BANKNIFTY",
    "^NSEBANK": "BANKNIFTY",
}


class StoreError(Exception):
    pass


def find_db(path=None):
    """The candle database, or a StoreError naming where it looked."""
    if path:
        p = os.path.expanduser(path)
        if not os.path.exists(p):
            raise StoreError("no candle database at %s" % p)
        return p
    tried = []
    for cand in DEFAULT_DB_CANDIDATES:
        p = os.path.expanduser(cand)
        tried.append(p)
        if os.path.exists(p):
            return p
    raise StoreError("could not find Sanyam's candle database. Looked in:\n  %s\n"
                     "Pass the path explicitly with --sanyam PATH."
                     % "\n  ".join(tried))


def sanyam_symbol(symbol):
    """An NSE symbol as Sanyam spells it."""
    s = (symbol or "").strip().upper()
    return SYMBOL_MAP.get(s, s)


def _connect(db_path):
    try:
        # Read only, so a running Sanyam is never blocked or corrupted by us.
        con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True, timeout=10)
    except sqlite3.Error as e:
        raise StoreError("could not open %s read only: %s" % (db_path, e))
    con.row_factory = sqlite3.Row
    try:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(candles)")}
    except sqlite3.Error as e:
        raise StoreError("%s has no readable candles table: %s" % (db_path, e))
    if not cols:
        raise StoreError("%s has no candles table. Has Sanyam ever run?" % db_path)
    for need in ("symbol", "interval", "ts", "open", "high", "low", "close"):
        if need not in cols:
            raise StoreError("%s's candles table has no %s column; the schema has moved"
                             % (db_path, need))
    return con, cols


def read_bars(db_path, symbol, interval="5m", start_ts=None, end_ts=None,
              complete_only=True):
    """Bars ascending by ts, as plain dicts. Never raises on an empty result.

    complete_only mirrors Sanyam's own default (app/candles/store.py:110). A bar
    still forming has a close that can move, and acting on it is acting on a price
    that has not settled yet. Same rule as dropping Yahoo's partial live tick.
    """
    con, cols = _connect(db_path)
    sym = sanyam_symbol(symbol)
    where = ["symbol = ?", "interval = ?"]
    args = [sym, interval]
    if complete_only and "complete" in cols:
        where.append("complete = 1")
    if start_ts is not None:
        where.append("ts >= ?")
        args.append(int(start_ts))
    if end_ts is not None:
        where.append("ts < ?")
        args.append(int(end_ts))
    picked = ["ts", "open", "high", "low", "close"]
    for extra in ("volume", "source"):
        if extra in cols:
            picked.append(extra)
    sql = "SELECT %s FROM candles WHERE %s ORDER BY ts ASC" % (", ".join(picked),
                                                               " AND ".join(where))
    try:
        rows = [dict(r) for r in con.execute(sql, args)]
    except sqlite3.Error as e:
        raise StoreError("reading %s failed: %s" % (db_path, e))
    finally:
        con.close()
    out = []
    for r in rows:
        out.append({"ts": int(r["ts"]), "o": r["open"], "h": r["high"], "l": r["low"],
                    "c": r["close"], "v": int(r.get("volume") or 0),
                    "source": r.get("source") or "UNKNOWN"})
    return out


def aggregate(bars, seconds=300):
    """Group bars onto a coarser grid. Sanyam's base interval is 1m, so 5m is often
    derived rather than stored.

    Buckets are aligned to the epoch, which lands on the IST five-minute grid
    because IST is a whole number of minutes from UTC: +5:30 is 19800 seconds, a
    multiple of 300. That is only true for intervals that divide 30 minutes, which
    1m, 5m and 15m all do.
    """
    buckets = {}
    order = []
    for b in bars:
        key = (b["ts"] // seconds) * seconds
        if key not in buckets:
            buckets[key] = {"ts": key, "o": b["o"], "h": b["h"], "l": b["l"],
                            "c": b["c"], "v": b["v"], "source": b["source"],
                            "parts": 1}
            order.append(key)
            continue
        agg = buckets[key]
        agg["h"] = max(agg["h"], b["h"]) if b["h"] is not None else agg["h"]
        agg["l"] = min(agg["l"], b["l"]) if b["l"] is not None else agg["l"]
        agg["c"] = b["c"]
        agg["v"] += b["v"]
        agg["source"] = worst_source([agg["source"], b["source"]])
        agg["parts"] += 1
    return [buckets[k] for k in sorted(order)]


def worst_source(sources):
    """The least trustworthy of several, because an aggregate is only as good as
    its worst input."""
    seen = {(s or "UNKNOWN") for s in sources}
    for s in SOURCE_RANK:
        if s in seen:
            return s
    return "UNKNOWN"


def as_series(bars):
    """Bars -> the shape nsedata.series_from_chart returns.

    Deliberate: session_bars, orb_for and the Time Machine's loader all speak that
    shape already, so a Sanyam-backed run goes through exactly the same grid
    checks, completeness checks and arithmetic as a Yahoo-backed one. No second
    code path to keep honest.
    """
    return {
        "t": [b["ts"] for b in bars],
        "o": [b["o"] for b in bars],
        "h": [b["h"] for b in bars],
        "l": [b["l"] for b in bars],
        "c": [b["c"] for b in bars],
        "v": [b["v"] for b in bars],
        "meta": {"currency": "INR", "exchangeTimezoneName": "Asia/Kolkata",
                 "gmtoffset": 19800, "fullExchangeName": "NSE",
                 "_from_sanyam": True,
                 "_sources": sorted({b["source"] for b in bars}),
                 "_worst_source": worst_source([b["source"] for b in bars])},
    }


def describe_store(db_path):
    """What the store actually holds, for an error message worth reading."""
    con, cols = _connect(db_path)
    try:
        total = con.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
        rows = [dict(r) for r in con.execute(
            "SELECT symbol, interval, COUNT(*) AS n, MIN(ts) AS lo, MAX(ts) AS hi "
            "FROM candles GROUP BY symbol, interval ORDER BY n DESC LIMIT 25")]
    except sqlite3.Error as e:
        raise StoreError("reading %s failed: %s" % (db_path, e))
    finally:
        con.close()
    return total, rows


EMPTY_HELP = (
    "Sanyam's candle store has no bars for %s at %s.\n"
    "Fetch them into it first, from the sanyam/backend directory:\n"
    "    python -m app.candles.fetch --symbol \"%s\" --interval 1m --from %s --to %s\n"
    "That needs a live KITE_ACCESS_TOKEN (Zerodha mints one per day) and Kite's\n"
    "historical-data add-on, which is billed separately from Kite Connect. Or pass\n"
    "--source db to read whatever OpenAlgo has already downloaded."
)

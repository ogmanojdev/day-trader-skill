#!/usr/bin/env python3
"""Reading NSE's public data: HTTP, the archive files, the bhavcopy, the universe.
Standard library only, Python 3.8+.

One canonical copy lives in nse-common/; tools/vendor.py copies it into every
skill folder and tests/test_common.py fails if a copy drifts.

Three NSE hosts, and they behave differently:

  nsearchives.nseindia.com   flat files. No cookie, no referer, generous. This
                             is where the universe, the bhavcopy, the price
                             bands and the deal files live, and it is why a
                             whole-market scan here costs one request.
  www.nseindia.com/api       JSON. Cookie gated and picky about headers. Some
                             paths answer a plain request (corporate
                             announcements, reportASM, holiday-master), others
                             refuse it (quote-equity). Treat a 403 as normal
                             and degrade, never as a crash.
  query*.finance.yahoo.com   intraday bars. Throttles by IP, hard: about a
                             dozen chart calls in a burst earns a 429 on every
                             endpoint for minutes. Use it for a short candidate
                             list, never for a universe sweep.

The bhavcopy filename is NOT authoritative, and this is the trap worth knowing.
Ask the archive for a date it has no file for and it answers 200 with the most
recent file it does have. A request for 10 Nov 2026 returns 09 Nov data, same
status, same shape, no warning, and a scan that trusts the filename silently
re-scores yesterday as today. fetch_bhav() checks the DATE1 column inside the
file against the date asked for and refuses a mismatch. Do not route around it.
"""

import csv
import datetime
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

__all__ = ["DataError", "ThrottledError", "UA", "NSE_ARCHIVE", "NSE_API", "YAHOO_HOSTS", "EQUITY_L", "SEC_LIST",
           "BULK_DEALS", "BLOCK_DEALS", "TRADEABLE_SERIES", "http_get", "nse_get", "nse_api",
           "yahoo_get", "to_yahoo", "chart_path", "rows_of", "num", "is_etf", "parse_universe",
           "read_universe", "write_universe", "bhav_date_in", "fetch_bhav", "bhav_ladder",
           "parse_bands", "parse_asm", "series_from_chart"]

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
NSE_ARCHIVE = "https://nsearchives.nseindia.com"
NSE_API = "https://www.nseindia.com"
YAHOO_HOSTS = ("https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com")
EQUITY_L = "/content/equities/EQUITY_L.csv"
SEC_LIST = "/content/equities/sec_list.csv"
BULK_DEALS = "/content/equities/bulk.csv"
BLOCK_DEALS = "/content/equities/block.csv"
BHAV = "/products/content/sec_bhavdata_full_%s.csv"
TRADEABLE_SERIES = ("EQ",)
BACKOFF = 20
SLEEP = time.sleep


class DataError(Exception):
    pass


class ThrottledError(DataError):
    """Yahoo has rate limited this IP.

    Its own class because the right response is to stop, not to retry the next
    symbol. The ban is per IP and lasts minutes to hours, so a loop over twenty
    charts that treats each 429 as that symbol's problem spends twenty backoffs
    discovering the same fact. The first scan written without this took longer
    than the opening range it was trying to measure.
    """
    pass


# ---------------------------------------------------------------- http

def http_get(url, timeout=40, referer=None, binary=False):
    headers = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"}
    if referer:
        headers["Referer"] = referer
        headers["Accept"] = "application/json"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return raw if binary else raw.decode("utf-8", "replace")


def nse_get(path, timeout=40):
    try:
        return http_get(NSE_ARCHIVE + path, timeout)
    except urllib.error.HTTPError as e:
        raise DataError("nseindia.com answered HTTP %d for %s" % (e.code, path))
    except (urllib.error.URLError, OSError) as e:
        raise DataError("could not reach nseindia.com: %s" % getattr(e, "reason", e))


def nse_api(path, timeout=40):
    try:
        return http_get(NSE_API + path, timeout, referer=NSE_API + "/")
    except urllib.error.HTTPError as e:
        raise DataError("nseindia.com answered HTTP %d for %s. Its API is cookie gated and "
                        "refuses some clients; this path may simply be closed to scripts."
                        % (e.code, path))
    except (urllib.error.URLError, OSError) as e:
        raise DataError("could not reach nseindia.com: %s" % getattr(e, "reason", e))


def yahoo_get(path):
    """query2 first, query1 on a 429 or 5xx, one more round after a pause."""
    last = "no answer"
    for attempt in (0, 1):
        saw_429 = False
        for host in YAHOO_HOSTS:
            try:
                return http_get(host + path, timeout=30)
            except urllib.error.HTTPError as e:
                last = "HTTP %d" % e.code
                try:
                    body = e.read().decode("utf-8", "replace") \
                        if 400 <= e.code < 500 and e.code != 429 else ""
                finally:
                    e.close()
                if e.code == 429:
                    saw_429 = True
                    continue
                if e.code >= 500:
                    continue
                if body.strip().startswith("{"):
                    return body
                raise DataError("Yahoo answered %s for %s" % (last, path.split("?")[0]))
            except (urllib.error.URLError, OSError) as e:
                last = str(getattr(e, "reason", e))
                continue
        if attempt == 0 and saw_429:
            SLEEP(BACKOFF)
            continue
        break
    if last == "HTTP 429":
        raise ThrottledError(
            "Yahoo has rate limited this IP (429 on both hosts, after a retry). It bans by "
            "IP for minutes to hours, so the next symbol will fail the same way. Wait, or "
            "pass --offline, or read the opening range off your broker's chart.")
    raise DataError("Yahoo answered %s for %s. It throttles by IP; wait a few minutes "
                    "or pass --offline." % (last, path.split("?")[0]))


def to_yahoo(sym):
    """NSE symbol -> Yahoo symbol. RELIANCE -> RELIANCE.NS; indices keep ^NSEI."""
    s = (sym or "").strip().upper()
    if s.startswith("^") or s.endswith(".NS"):
        return s
    return s + ".NS"


def chart_path(sym, rng, interval):
    return "/v8/finance/chart/%s?range=%s&interval=%s" % (to_yahoo(sym), rng, interval)


def series_from_chart(text_or_doc):
    """A Yahoo v8 chart answer -> plain lists plus meta."""
    doc = json.loads(text_or_doc) if isinstance(text_or_doc, str) else text_or_doc
    chart = (doc or {}).get("chart") or {}
    err = chart.get("error")
    if err:
        raise DataError("Yahoo: %s" % (err.get("description") or err.get("code") or err))
    res = (chart.get("result") or [None])[0]
    if not res:
        raise DataError("Yahoo's answer has no chart result")
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0] or {}
    t = res.get("timestamp") or []
    n = len(t)

    def col(k):
        v = q.get(k)
        return (list(v) + [None] * (n - len(v))) if isinstance(v, list) else [None] * n
    return {"t": list(t), "o": col("open"), "h": col("high"), "l": col("low"),
            "c": col("close"), "v": col("volume"), "meta": res.get("meta") or {}}


# ---------------------------------------------------------------- csv

def rows_of(text):
    """CSV text -> dicts, keys and values stripped. NSE pads both with spaces."""
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        out.append({(k or "").strip(): (v.strip() if isinstance(v, str) else v)
                    for k, v in row.items()})
    return out


def num(x, default=None):
    try:
        s = str(x).strip().replace(",", "")
        if s in ("", "-", "NA", "None"):
            return default
        return float(s)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- universe

# An ETF or index fund trades in series EQ alongside ordinary shares, and nothing in
# the bhavcopy distinguishes one. They have to go, because their delivery percentage
# is structurally near 100%: units are created and redeemed rather than churned, so a
# delivery signal fires on every gold or index fund every single day. The first scan
# written without this filter returned LICMFGOLD and INFRAIETF at 95% and 93%
# delivery, which says nothing about conviction and everything about plumbing. Most
# are caught for free, because NSE lists ETFs outside EQUITY_L.csv entirely.
ETF_WORDS = ("ETF", "EXCHANGE TRADED", "BEES", "INDEX FUND", "MUTUAL FUND",
             "GOLDSHARE", "LIQUIDCASE", " MF ")
ETF_ISIN_PREFIX = ("INF",)   # INF = a mutual fund scheme; INE / IN9 = a company


def is_etf(name, isin=""):
    n = " %s " % (name or "").upper()
    if (isin or "").upper().startswith(ETF_ISIN_PREFIX):
        return True
    return any(w in n for w in ETF_WORDS)


def parse_universe(text):
    """EQUITY_L.csv -> (kept, counts). Keeps SERIES == EQ and drops funds.

    Series is the fact that decides whether a stock can be traded intraday, so it
    is a hard filter: BE and BZ are trade-for-trade with 100% margin and no
    intraday netting, SM and ST are the SME platform, GS and GB are government
    securities.
    """
    rows = rows_of(text)
    if not rows or "SYMBOL" not in rows[0]:
        raise DataError("EQUITY_L.csv is not the shape this tool knows (no SYMBOL column)")
    kept, counts = [], {}
    for r in rows:
        series = (r.get("SERIES") or "").upper()
        counts[series or "blank"] = counts.get(series or "blank", 0) + 1
        if series not in TRADEABLE_SERIES:
            continue
        name = " ".join((r.get("NAME OF COMPANY") or "").split())
        isin = r.get("ISIN NUMBER") or ""
        if is_etf(name, isin):
            counts["fund"] = counts.get("fund", 0) + 1
            continue
        kept.append({"symbol": r["SYMBOL"], "name": name, "series": series, "isin": isin,
                     "lot": int(num(r.get("MARKET LOT"), 1) or 1)})
    kept.sort(key=lambda k: k["symbol"])
    return kept, counts


def write_universe(kept, path):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for k in kept:
            fh.write("%s|%s|%d\n" % (k["symbol"], k["name"], k["lot"]))


def read_universe(path):
    """trader/universe.txt -> {symbol: name}."""
    if not os.path.exists(path):
        raise DataError("no universe at %s. Run: python3 scan.py universe" % path)
    out = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("|")
            if parts and parts[0]:
                out[parts[0]] = parts[1] if len(parts) > 1 else ""
    if not out:
        raise DataError("%s is empty" % path)
    return out


def names_for(offline=None, universe_path="trader/universe.txt", quiet=False):
    """{symbol: company name}, from the cache, the file or the exchange. None if
    nothing is reachable, which callers must treat as "cannot filter funds"."""
    if offline:
        p = os.path.join(offline, "EQUITY_L.csv")
        if os.path.exists(p):
            with open(p, encoding="utf-8", errors="replace") as fh:
                kept, _ = parse_universe(fh.read())
            return {k["symbol"]: k["name"] for k in kept}
        return None
    if universe_path and os.path.exists(universe_path):
        return read_universe(universe_path)
    try:
        kept, _ = parse_universe(nse_get(EQUITY_L, timeout=60))
        return {k["symbol"]: k["name"] for k in kept}
    except DataError as e:
        if not quiet:
            print("note: no universe available, so ETFs cannot be filtered out (%s)" % e,
                  file=sys.stderr)
        return None


# ---------------------------------------------------------------- bhavcopy

def bhav_date_in(text):
    """The DATE1 the file itself claims, as a date, or None."""
    for r in rows_of(text)[:1]:
        raw = (r.get("DATE1") or "").strip()
        for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
            try:
                return datetime.datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
    return None


def fetch_bhav(day, cache_dir, pause=0.0):
    """One session's bhavcopy, cached. Returns (rows, from_cache).

    See the module docstring: the archive answers 200 with its newest file when
    the date asked for does not exist, so the DATE1 check is the only thing
    standing between a scan and silently reusing yesterday's prices as today's.
    """
    iso = day.isoformat()
    path = os.path.join(cache_dir, "bhav-%s.csv" % iso)
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as fh:
            return rows_of(fh.read()), True
    text = nse_get(BHAV % day.strftime("%d%m%Y"), timeout=60)
    got = bhav_date_in(text)
    if got != day:
        raise DataError(
            "asked the archive for %s and it answered with %s data. The archive serves its "
            "newest file for a date it does not have, so this is not that session's "
            "bhavcopy. Either %s was not a session, or it is not published yet (it lands "
            "after about 18:00 IST)."
            % (iso, got.isoformat() if got else "an unreadable date", iso))
    os.makedirs(cache_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    if pause:
        SLEEP(pause)
    return rows_of(text), False


def bhav_ladder(end_day, sessions, cache_dir, is_trading_day, pause=0.0):
    """The last `sessions` sessions up to end_day, oldest first.
    Returns (days_kept, {symbol: {index: row}}, misses)."""
    days, d = [], end_day
    while len(days) < sessions:
        if is_trading_day(d):
            days.append(d)
        d -= datetime.timedelta(days=1)
        if (end_day - d).days > sessions * 3 + 40:
            break
    days.reverse()
    series, misses, kept = {}, [], []
    for day in days:
        try:
            rows, _ = fetch_bhav(day, cache_dir, pause)
        except DataError as e:
            misses.append((day.isoformat(), str(e)))
            continue
        kept.append(day)
        idx = len(kept) - 1
        for r in rows:
            if (r.get("SERIES") or "").upper() not in TRADEABLE_SERIES:
                continue
            series.setdefault(r["SYMBOL"], {})[idx] = r
    return kept, series, misses


# ---------------------------------------------------------------- bands and surveillance

STANDARD_BAND = 20.0


def parse_bands(text):
    """sec_list.csv -> {symbol: band percent}.

    A band is the furthest price may move from the previous close before the
    exchange stops accepting orders through it. This has no US analogue and it
    decides whether a stop can fill at all: a stop placed past the band is not a
    wide stop, it is an order that cannot execute. On a 2% band name an intraday
    stop has almost no room.
    """
    out = {}
    for r in rows_of(text):
        sym = (r.get("Symbol") or "").strip().upper()
        if not sym or (r.get("Series") or "").upper() not in TRADEABLE_SERIES:
            continue
        b = num(r.get("Band"))
        if b is not None:
            out[sym] = b
    return out


def band_flag(band):
    """Only a band tighter than the standard 20% is worth printing: reporting
    "band 20%" on 260 of 424 hits hides the 5% names that matter."""
    return band if (band is not None and band < STANDARD_BAND) else None


def parse_asm(doc):
    """reportASM -> {symbol: "longterm" or "shortterm"}. An ASM name can carry
    100% margin, which makes it a poor intraday candidate whatever the chart says."""
    if isinstance(doc, str):
        doc = json.loads(doc)
    out = {}
    for stage in ("longterm", "shortterm"):
        block = (doc or {}).get(stage) or {}
        rows = block.get("data") if isinstance(block, dict) else block
        for r in rows or []:
            sym = (r.get("symbol") or r.get("Symbol") or "").strip().upper()
            if sym:
                out[sym] = stage
    return out

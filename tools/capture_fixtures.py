#!/usr/bin/env python3
"""Capture small, real fixtures from the public NSE sources for the test suite.

The suite has to run offline and deterministically, so every tool takes
--offline and every network answer used by a test is a file in
tests/fixtures/. These are real published documents, trimmed to keep the
repository small: a bhavcopy is 400 KB and the ladder needs twenty-five of
them, so each is cut to the symbols the tests actually name.

    python3 tools/capture_fixtures.py
    python3 tools/capture_fixtures.py --sessions 25

Yahoo is deliberately not captured here. It throttles by IP hard enough that a
capture cannot be relied on, so the Time Machine's bars come from
tools/make_fixture.py instead and are labelled synthetic.
"""

import argparse
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "nse-common"))
import nsecal   # noqa: E402
import nsedata  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures")
KEEP = ["RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "SBIN", "INFY", "LT", "HINDALCO",
        "DIVISLAB", "BSE", "LAURUSLABS", "KPIGREEN", "AARTIDRUGS", "DATAPATTNS",
        "SAILIFE", "FINCABLES", "TITAGARH", "COHANCE", "V2RETAIL", "HUBTOWN"]


def out(skill, name):
    d = os.path.join(FIX, skill)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def trim_csv(text, keep, symbol_col):
    """Header plus the rows whose symbol is in keep."""
    lines = text.splitlines()
    if not lines:
        return text
    header = lines[0]
    cols = [c.strip() for c in header.split(",")]
    try:
        idx = cols.index(symbol_col)
    except ValueError:
        return text
    want = set(keep)
    rows = [header]
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) > idx and parts[idx].strip().strip('"').upper() in want:
            rows.append(line)
    return "\n".join(rows) + "\n"


def save(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("  %-62s %7d bytes" % (os.path.relpath(path, ROOT), len(text.encode("utf-8"))))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sessions", type=int, default=25)
    ap.add_argument("--end", default=None, help="last session (IST date); default the latest")
    a = ap.parse_args(argv)
    end = nsecal.parse_ist(a.end, "--end").date() if a.end else nsecal.ist_now().date()
    if not nsecal.is_trading_day(end):
        end = nsecal.prev_session_day(end)

    print("universe and bands:")
    eq = nsedata.nse_get(nsedata.EQUITY_L, timeout=60)
    save(out("nse-scanner", "EQUITY_L.csv"), trim_csv(eq, KEEP, "SYMBOL"))
    bands = nsedata.nse_get(nsedata.SEC_LIST, timeout=60)
    save(out("nse-scanner", "sec_list.csv"), trim_csv(bands, KEEP, "Symbol"))
    try:
        asm = nsedata.nse_api("/api/reportASM")
        save(out("nse-scanner", "reportASM.json"), asm)
    except nsedata.DataError as e:
        print("  reportASM unavailable: %s" % e)

    print("bhavcopy ladder, %d sessions to %s:" % (a.sessions, end.isoformat()))
    day, got = end, 0
    while got < a.sessions:
        if nsecal.is_trading_day(day):
            try:
                text = nsedata.nse_get(nsedata.BHAV % day.strftime("%d%m%Y"), timeout=60)
                if nsedata.bhav_date_in(text) == day:
                    save(out("nse-scanner", "bhav-%s.csv" % day.isoformat()),
                         trim_csv(text, KEEP, "SYMBOL"))
                    got += 1
                else:
                    print("  %s skipped (archive served another date)" % day.isoformat())
            except nsedata.DataError as e:
                print("  %s skipped (%s)" % (day.isoformat(), str(e)[:60]))
        day -= datetime.timedelta(days=1)
        if (end - day).days > a.sessions * 3 + 40:
            break

    print("deals:")
    for path, name in ((nsedata.BULK_DEALS, "bulk.csv"), (nsedata.BLOCK_DEALS, "block.csv")):
        try:
            save(out("nse-deals", name), nsedata.nse_get(path, timeout=40))
        except nsedata.DataError as e:
            print("  %s unavailable: %s" % (name, e))

    print("news and analyst:")
    try:
        save(out("nse-news", "corporate-announcements.json"),
             nsedata.nse_api("/api/corporate-announcements?index=equities"))
    except nsedata.DataError as e:
        print("  announcements unavailable: %s" % e)
    since = (end - datetime.timedelta(days=3)).strftime("%d-%m-%Y")
    until = end.strftime("%d-%m-%Y")
    try:
        doc = json.loads(nsedata.nse_api(
            "/api/corporate-announcements?index=equities&from_date=%s&to_date=%s" % (since, until)))
        rows = doc if isinstance(doc, list) else (doc or {}).get("data") or []
        keep = [r for r in rows if (r.get("symbol") or "").upper() in set(KEEP)][:120]
        save(out("nse-analyst", "corporate-announcements-range.json"),
             json.dumps(keep, indent=1))
    except (nsedata.DataError, ValueError) as e:
        print("  dated announcements unavailable: %s" % e)
    for sym in ("RELIANCE", "TCS"):
        try:
            save(out("nse-analyst", "top-corp-info-%s.json" % sym),
                 nsedata.nse_api("/api/top-corp-info?symbol=%s&market=equities" % sym))
        except nsedata.DataError as e:
            print("  top-corp-info %s unavailable: %s" % (sym, e))
    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

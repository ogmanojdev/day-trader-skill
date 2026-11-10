#!/usr/bin/env python3
"""Refresh nse-common/nsecal.py's holiday table from the exchange.

NSE publishes the next year's trading holidays around December, so the table in
nsecal.py is dated and ends at HOLIDAYS_THROUGH. Anything past that is reported
as unknown instead of guessed. Run this when the table runs out.

    python3 tools/refresh_calendar.py            # show what the exchange says
    python3 tools/refresh_calendar.py --write    # rewrite the table in place

Muhurat windows are NOT set from here. The exchange confirms that session's
start and end by circular a few days ahead, and the API marks the day only with
an asterisk, so the window goes into MUHURAT by hand with the circular's times.
"""

import argparse
import datetime
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "nse-common", "nsecal.py")
URL = "https://www.nseindia.com/api/holiday-master?type=trading"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def fetch():
    req = urllib.request.Request(URL, headers={
        "User-Agent": UA, "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.nseindia.com/"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def equity_rows(doc):
    """The CM segment is the equity cash market. Other keys are derivatives, currency,
    commodities and so on, and their holiday lists differ."""
    rows = doc.get("CM")
    if not isinstance(rows, list) or not rows:
        raise SystemExit("the API answer has no CM (equity cash) segment; keys: %s"
                         % ", ".join(sorted(doc)) if isinstance(doc, dict) else "none")
    out = []
    for r in rows:
        try:
            d = datetime.datetime.strptime(r["tradingDate"], "%d-%b-%Y").date()
        except (KeyError, ValueError):
            continue
        desc = " ".join((r.get("description") or "").split())
        special = bool(r.get("morning_session") or r.get("evening_session")) or desc.endswith("*")
        out.append((d, desc, special))
    return sorted(out)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true", help="rewrite the table in nsecal.py")
    a = ap.parse_args(argv)
    rows = equity_rows(fetch())
    years = sorted({d.year for d, _, _ in rows})
    through = "%d-12-31" % max(years)
    print("NSE equity (CM) holidays for %s: %d days" % (", ".join(str(y) for y in years), len(rows)))
    lines = []
    for d, desc, special in rows:
        label = desc + (" (Muhurat trading)" if special and "Muhurat" not in desc else "")
        lines.append('    "%s": "%s",' % (d.isoformat(), label.replace('"', "'")))
        print("  %s  %s%s" % (d.isoformat(), label, "   <- special session, set MUHURAT by hand"
                              if special else ""))
    if not a.write:
        print("\nnothing written. Re-run with --write to replace the table.")
        return 0
    with open(TARGET, encoding="utf-8") as fh:
        src = fh.read()
    today = datetime.date.today().isoformat()
    block = ("HOLIDAYS = {\n" + "\n".join(lines) + "\n}\n"
             'HOLIDAYS_THROUGH = "%s"' % through)
    new, n = re.subn(r"HOLIDAYS = \{.*?\n\}\nHOLIDAYS_THROUGH = \"[^\"]*\"",
                     block.replace("\\", "\\\\"), src, count=1, flags=re.S)
    if not n:
        raise SystemExit("could not find the HOLIDAYS block in %s" % TARGET)
    new = re.sub(r"holiday-master\?type=trading on \d{4}-\d{2}-\d{2}",
                 "holiday-master?type=trading on %s" % today, new)
    new = re.sub(r"(from the exchange's holiday-master API, read )\d{4}-\d{2}-\d{2}",
                 r"\g<1>" + today, new)
    with open(TARGET, "w", encoding="utf-8") as fh:
        fh.write(new)
    print("\nrewrote the table in %s (through %s)." % (os.path.relpath(TARGET, ROOT), through))
    print("Now run: python3 tools/vendor.py")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""Earnings Analyst for nse-analyst: what the last few quarters actually say.
Standard library only, Python 3.8+.

    python3 facts.py RELIANCE
    python3 facts.py TCS --quarters 8 --json
    python3 facts.py --calendar --days 7          # who reports in the next week
    python3 facts.py RELIANCE --announcements 10

Reads the exchange's own filings, not a data vendor:

  top-corp-info          per symbol: filed quarterly results, board meetings,
                         corporate actions and shareholding pattern
  corporate-announcements the filing feed, with a date range. This is the live
                         source and the only one of the three that is current.

Do not reach for /api/corporates-financial-results. It answers 200 with 3,816
rows and looks like the market-wide results feed, and it is a frozen snapshot:
2,538 of those rows were broadcast in February 2025 and RELIANCE's newest entry
is dated 16 January 2025, about twenty-two months stale as this was written.
Passing it a date range returns one row. A calendar built on it would report
that nobody is reporting this quarter, confidently and wrongly. The
announcements feed with from_date and to_date is current and is what --calendar
reads.

The US original reads SEC XBRL company facts. There is no EDGAR here. SEBI's
LODR regulations put results on the exchange instead, and the exchange's JSON
is the primary source.

Two units traps, both of which silently produce answers that are wrong by
orders of magnitude:

  NSE publishes these figures in LAKH of rupees. A published income of 17856700
  is 17,856,700 lakh, which is 1,78,567 crore. Guessing the unit is not safe
  and guessing wrong is not obvious: read as thousands, the same filing makes
  RELIANCE a 1,786 crore-revenue company with a 154 crore profit, which is
  wrong by a factor of a hundred and still looks like a real company.

  A uniform scale error cannot be caught from inside one filing. EPS is
  published in rupees whatever unit the totals use, and income over PAT is
  scale-free, so no ratio in the document changes when every total is multiplied
  by a hundred. implied_shares() divides PAT in crore by diluted EPS, prints the
  result and warns outside a plausible range, which is a floor against nonsense
  rather than a proof: 1,354 crore shares is RELIANCE, the hundredfold error
  implies 13.5 crore, which is ordinary for a small cap and passes quietly.

  So the unit is held by a test pinned to a known filing
  (tests/test_analyst.py): RELIANCE's September 2026 quarter must come out at
  1,78,567 crore of income and 15,354 crore of profit, the figures the exchange
  actually published. If the unit changes, the suite fails rather than every
  answer quietly rescaling.

  Consolidated and standalone filings sit in the same list with no ordering
  guarantee. Comparing a consolidated quarter against a standalone one
  manufactures growth that did not happen, so this tool groups by that flag
  and never mixes the two in one growth number.

Growth is quarter on quarter and year on year, computed only between filings of
the same basis. A quarter with no comparable predecessor prints as a dash
rather than a guess.

Not financial advice. These are filed figures with arithmetic on top. A results
filing is also the single most dangerous thing to hold an intraday position
through, which is the main reason to look it up before the open.
"""

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import charges  # noqa: E402  (vendored; see tools/vendor.py)
import nsecal   # noqa: E402
import nsedata  # noqa: E402

AnalystError = nsedata.DataError
CORP_INFO = "/api/top-corp-info?symbol=%s&market=equities"
ANNOUNCE_RANGE = "/api/corporate-announcements?index=equities&from_date=%s&to_date=%s"
# Categories that mean "numbers are coming or have just landed".
RESULT_CATEGORIES = ("financial result", "board meeting", "outcome of board")
ANNOUNCE = "/api/corporate-announcements?index=equities&symbol=%s"

# NSE publishes results in lakh of rupees. One crore is one hundred lakh, so a
# published figure becomes crore by dividing by 100. Verified against RELIANCE's
# September 2026 filing: income 17856700 and PAT 1535400 give 1,78,567 crore and
# 15,354 crore, and 15,354 / 11.34 EPS implies 1,354 crore shares, which is the
# share count RELIANCE actually has. The thousands reading implies 13.5 crore.
LAKH_PER_CRORE = 100.0

# A listed Indian company's share count, in crore. Outside this range the unit
# is more likely wrong than the company unusual.
PLAUSIBLE_SHARES_CR = (1.0, 50000.0)


def to_crore(x):
    v = nsedata.num(x)
    return None if v is None else v / LAKH_PER_CRORE


def implied_shares(pat_cr, eps):
    """Share count in crore, from the filing's own two numbers.

    A coarse unit check, not a proof: it flags an implied count that no listed
    company could have. A hundredfold error can still land inside the plausible
    range, which is why the authoritative check is the pinned-value test.
    """
    if pat_cr is None or not eps:
        return None
    return pat_cr / eps


def parse_date(raw):
    """The exchange uses at least five date spellings across these endpoints:
    "30 Sep 2026", "30-Oct-2026", "30-Jul-2026 17:17:53" and "...17:17". Parsing
    only some of them fails silently as an empty result set, which reads like
    "nobody filed" rather than "this tool cannot read the date"."""
    text = (raw or "").strip()
    for fmt in ("%d %b %Y", "%d-%b-%Y", "%Y-%m-%d",
                "%d-%b-%Y %H:%M", "%d-%b-%Y %H:%M:%S",
                "%d %b %Y %H:%M", "%d %b %Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def fetch_json(path, offline, fixture):
    if offline:
        p = os.path.join(offline, fixture)
        if not os.path.exists(p):
            raise AnalystError("no fixture at %s" % p)
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    return json.loads(nsedata.nse_api(path))


def quarters_for(doc):
    """top-corp-info -> filed quarters, newest first, grouped nowhere: the basis
    flag rides along on each row so the caller can keep the two apart."""
    fr = (doc or {}).get("financial_results") or {}
    rows = fr.get("data") if isinstance(fr, dict) else fr
    out = []
    for r in rows or []:
        end = parse_date(r.get("to_date"))
        basis = "consolidated" if "non" not in (r.get("consolidated") or "").lower() \
            else "standalone"
        out.append({
            "period_end": end.isoformat() if end else None,
            "basis": basis,
            "audited": (r.get("audited") or "").strip() or None,
            "income_cr": to_crore(r.get("income")),
            "expenditure_cr": to_crore(r.get("expenditure")),
            "pbt_cr": to_crore(r.get("reProLossBefTax")),
            "pat_cr": to_crore(r.get("proLossAftTax")),
            "eps_diluted": nsedata.num(r.get("reDilEPS")),
            "filed": (r.get("re_broadcast_timestamp") or "").strip() or None,
            "xbrl": (r.get("xbrl_attachment") or "").strip() or None,
        })
    out = [q for q in out if q["period_end"]]
    out.sort(key=lambda q: q["period_end"], reverse=True)
    return out


def growth(quarters):
    """Attach QoQ and YoY to each quarter, within its own basis only."""
    by_basis = {}
    for q in quarters:
        by_basis.setdefault(q["basis"], []).append(q)
    for basis, rows in by_basis.items():
        rows.sort(key=lambda q: q["period_end"], reverse=True)
        by_end = {q["period_end"]: q for q in rows}
        for i, q in enumerate(rows):
            prev = rows[i + 1] if i + 1 < len(rows) else None
            q["qoq_income_pct"] = pct(q["income_cr"], prev["income_cr"]) if prev else None
            q["qoq_pat_pct"] = pct(q["pat_cr"], prev["pat_cr"]) if prev else None
            end = datetime.date.fromisoformat(q["period_end"])
            ago = None
            for cand in rows:
                ce = datetime.date.fromisoformat(cand["period_end"])
                if 360 <= (end - ce).days <= 372:
                    ago = cand
                    break
            q["yoy_income_pct"] = pct(q["income_cr"], ago["income_cr"]) if ago else None
            q["yoy_pat_pct"] = pct(q["pat_cr"], ago["pat_cr"]) if ago else None
            q["margin_pct"] = (100.0 * q["pat_cr"] / q["income_cr"]) \
                if (q["pat_cr"] is not None and q["income_cr"]) else None
        _ = by_end
    return quarters


def pct(now, before):
    if now is None or before is None or before == 0:
        return None
    return (now - before) / abs(before) * 100.0


def crore(x):
    if x is None:
        return "        -"
    return "%s cr" % charges.indian_group(int(round(x)))


def show_pct(x):
    return "     -" if x is None else "%+5.1f%%" % x


def cmd_symbol(a):
    sym = a.symbol.strip().upper()
    doc = fetch_json(CORP_INFO % sym, a.offline, "top-corp-info-%s.json" % sym)
    quarters = growth(quarters_for(doc))
    if not quarters:
        raise AnalystError("the exchange returned no filed quarterly results for %s" % sym)
    keep = quarters[:a.quarters]
    checks = [implied_shares(q["pat_cr"], q["eps_diluted"]) for q in keep]
    checks = [c for c in checks if c is not None]
    lo, hi = PLAUSIBLE_SHARES_CR
    off = [c for c in checks if not (lo <= c <= hi)]
    board = (doc.get("borad_meeting") or {})       # the exchange spells it this way
    board_rows = board.get("data") if isinstance(board, dict) else board
    actions = (doc.get("corporate_actions") or {})
    actions_rows = actions.get("data") if isinstance(actions, dict) else actions
    announcements = []
    if a.announcements:
        try:
            feed = fetch_json(ANNOUNCE % sym, a.offline, "announcements-%s.json" % sym)
            rows = feed if isinstance(feed, list) else (feed or {}).get("data") or []
            for r in rows[:a.announcements]:
                announcements.append({
                    "time": (r.get("an_dt") or "").strip(),
                    "category": " ".join((r.get("desc") or "").split()),
                    "text": " ".join((r.get("attchmntText") or "").split())})
        except AnalystError as e:
            print("note: announcements unavailable (%s)" % e, file=sys.stderr)

    out = {"symbol": sym, "as_of": nsecal.ist_now().isoformat(timespec="seconds"),
           "units": "crore of rupees, converted from the lakh NSE publishes",
           "implied_shares_cr": round(sum(checks) / len(checks), 1) if checks else None,
           "unit_check_ok": not off,
           "quarters": keep, "announcements": announcements,
           "board_meetings": [b for b in
                              [{"date": (r.get("meetingdate") or "").strip(),
                                "purpose": " ".join((r.get("purpose") or "").split())}
                               for r in (board_rows or [])]
                              if b["date"] or b["purpose"]][:5],
           "corporate_actions": [c for c in
                                 [{"ex_date": (r.get("exdate") or "").strip(),
                                   "purpose": " ".join((r.get("purpose") or "").split())}
                                  for r in (actions_rows or [])]
                                 if c["ex_date"]][:5]}
    if not a.no_write:
        d = os.path.dirname(a.out)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2)
    if a.json:
        print(json.dumps(out, indent=2))
        return 0

    bases = sorted({q["basis"] for q in keep})
    print("%s, filed quarterly results. Figures in crore, converted from the lakh"
          % sym)
    print("the exchange publishes. Basis shown per row and never mixed in a growth number.\n")
    print("  %-12s %-13s %12s %12s %8s %7s %7s %7s"
          % ("period end", "basis", "income", "PAT", "margin", "QoQ PAT", "YoY PAT", "EPS"))
    for q in keep:
        print("  %-12s %-13s %12s %12s %7s %7s %7s %7s"
              % (q["period_end"], q["basis"], crore(q["income_cr"]), crore(q["pat_cr"]),
                 ("%.1f%%" % q["margin_pct"]) if q["margin_pct"] is not None else "    -",
                 show_pct(q["qoq_pat_pct"]), show_pct(q["yoy_pat_pct"]),
                 ("%.2f" % q["eps_diluted"]) if q["eps_diluted"] is not None else "   -"))
    if checks:
        print("\n  unit check: PAT divided by EPS implies about %s crore shares outstanding."
              % charges.indian_group(int(round(sum(checks) / len(checks)))))
        if off:
            print("  WARNING: that is outside the plausible range %g to %g crore shares for a"
                  % (lo, hi))
            print("  listed company, so the exchange's published unit may have changed. Treat"
                  " every figure above as suspect.")
    if len(bases) > 1:
        print("\n  Two bases are filed (%s). Rows are only compared with their own."
              % ", ".join(bases))
    if out["board_meetings"]:
        print("\n  board meetings:")
        for b in out["board_meetings"]:
            print("    %-14s %s" % (b["date"], b["purpose"][:70]))
    if out["corporate_actions"]:
        print("\n  corporate actions (ex-date):")
        for c in out["corporate_actions"]:
            print("    %-14s %s" % (c["ex_date"], c["purpose"][:70]))
    if announcements:
        print("\n  latest filings:")
        for an in announcements[:6]:
            print("    %-20s %s" % (an["time"][:20], an["category"][:60]))
    if not a.no_write:
        print("\nWrote %s." % a.out)
    return 0


def cmd_calendar(a):
    """Who just filed, or intimated a board meeting, in the window.

    An Indian board-meeting intimation is the forward signal: a company must tell
    the exchange before the meeting that approves results, so the intimation
    arrives days ahead of the numbers.
    """
    today = nsecal.ist_now().date()
    until = today + datetime.timedelta(days=a.days)
    since = today - datetime.timedelta(days=a.back)
    doc = fetch_json(ANNOUNCE_RANGE % (since.strftime("%d-%m-%Y"), until.strftime("%d-%m-%Y")),
                     a.offline, "corporate-announcements-range.json")
    rows = doc if isinstance(doc, list) else (doc or {}).get("data") or []
    want = set(s.strip().upper() for s in a.symbols.split(",")) if a.symbols else None
    items, seen = [], set()
    for r in rows:
        sym = (r.get("symbol") or "").strip().upper()
        if want and sym not in want:
            continue
        cat = " ".join((r.get("desc") or "").split())
        low = cat.lower()
        if not any(k in low for k in RESULT_CATEGORIES):
            continue
        when = parse_date(r.get("an_dt") or r.get("sort_date"))
        if not when:
            continue
        key = (sym, when.isoformat(), low[:40])
        if key in seen:
            continue
        seen.add(key)
        items.append({"symbol": sym, "company": " ".join((r.get("sm_name") or "").split()),
                      "date": when.isoformat(), "category": cat,
                      "text": " ".join((r.get("attchmntText") or "").split())})
    items.sort(key=lambda i: (i["date"], i["symbol"]), reverse=True)
    if a.json:
        print(json.dumps({"from": since.isoformat(), "to": until.isoformat(),
                          "count": len(items), "items": items}, indent=2))
        return 0
    print("Results and board meetings filed, %s to %s: %d."
          % (since.isoformat(), until.isoformat(), len(items)))
    print("A results filing is the most dangerous thing to hold an intraday position")
    print("through. A day trade is flat by 15:15, so it is not exposed to one.\n")
    for i in items[:a.show]:
        print("  %-11s %-13s %s" % (i["date"], i["symbol"], i["category"][:56]))
        if i["text"]:
            print("              %s" % i["text"][:84])
    if len(items) > a.show:
        print("  ... and %d more" % (len(items) - a.show))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="facts.py", description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("symbol", nargs="?")
    ap.add_argument("--calendar", action="store_true", help="who reports, market wide")
    ap.add_argument("--symbols", help="calendar: only these")
    ap.add_argument("--days", type=int, default=7, help="calendar: days ahead")
    ap.add_argument("--back", type=int, default=5, help="calendar: days behind")
    ap.add_argument("--quarters", type=int, default=6)
    ap.add_argument("--announcements", type=int, default=0, metavar="N")
    ap.add_argument("--show", type=int, default=25)
    ap.add_argument("--out", default="trader/analyst.json")
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.calendar:
            return cmd_calendar(a)
        if not a.symbol:
            ap.print_help()
            return 1
        return cmd_symbol(a)
    except (AnalystError, charges.ChargeError, nsecal.CalendarError) as e:
        print("facts.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

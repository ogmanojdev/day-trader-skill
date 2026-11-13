#!/usr/bin/env python3
"""Price Alerts for nse-alerts: ring on a stop, a target, or the square-off.
Standard library only, Python 3.8+.

    python3 alerts.py add --from-plan                       # stop, target, square-off
    python3 alerts.py add --symbol RELIANCE --below 1318.5 --label "stop"
    python3 alerts.py add --at 15:15 --label "square off" --square-off
    python3 alerts.py list
    python3 alerts.py check                                 # rings what hit, once
    python3 alerts.py check --price RELIANCE=1331.2         # price supplied by hand
    python3 alerts.py clear --fired

The square-off alert is the one that matters most and the one the US original
cannot have. An Indian broker flattens intraday (MIS) positions itself at about
15:20 IST at market price, so the default rings at 15:15 to leave five minutes
to exit on your own terms. Past that the exit is the broker's, at whatever the
market pays.

Time alerts are calendar aware. 15:15 on a Sunday or on Diwali-Balipratipada is
not a square-off, and an alert that fires on a day the market never opened is
worse than no alert: it teaches you to ignore it. A Muhurat session has its own
short window, so the square-off is pinned to that window's end instead.

Price alerts need a live price, and that is the honest limit here. Yahoo's chart
endpoint is the only free intraday source for NSE and it throttles by IP hard,
so `check` tries it, caches what it gets, and degrades to the last settled
bhavcopy close with the staleness printed. --price lets you supply the number
from your broker's terminal, which is the one you should trust anyway.

State is trader/alerts.json. A rule fires once unless --repeat, and `check`
prints only what changed, so it is safe to run on a loop.

Not financial advice. An alert is a reminder, not an order. Nothing here can
place one: no order leaves this machine without nse-risk passing it and you
typing yes.
"""

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import charges      # noqa: E402  (vendored; see tools/vendor.py)
import nsecal       # noqa: E402
import nsedata      # noqa: E402
import sanyamstore  # noqa: E402

AlertError = nsedata.DataError
KINDS = ("above", "below", "at_time")


# ---------------------------------------------------------------- store

def load(path):
    if not os.path.exists(path):
        return {"rules": []}
    try:
        with open(path, encoding="utf-8") as fh:
            st = json.load(fh)
    except (OSError, ValueError) as e:
        raise AlertError("could not read %s: %s" % (path, e))
    st.setdefault("rules", [])
    return st


def save(path, st):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(st, fh, indent=2)


def next_id(st):
    used = {r.get("id") for r in st["rules"]}
    n = 1
    while "a%d" % n in used:
        n += 1
    return "a%d" % n


def describe(r):
    if r["kind"] == "at_time":
        return "%s at %s IST%s" % (r.get("label") or "time", r["value"],
                                   " (square-off)" if r.get("square_off") else "")
    arrow = "at or above" if r["kind"] == "above" else "at or below"
    return "%s %s %s %s" % (r["symbol"], arrow, charges.inr(r["value"]),
                            ("(" + r["label"] + ")") if r.get("label") else "")


# ---------------------------------------------------------------- prices

def live_prices(symbols, cache_dir, offline=None, pause=1.0, sanyam=None):
    """{symbol: (price, source, as_of)}. Yahoo when it answers, else the last
    settled close, with the source named so a stale number is never silently
    treated as live."""
    out = {}
    store_db = None
    if sanyam is not None:
        store_db = sanyamstore.find_db(sanyam or None)
    for k, sym in enumerate(sorted(set(symbols))):
        got = False
        if store_db:
            bars = sanyamstore.read_bars(store_db, sym, "5m") or \
                sanyamstore.read_bars(store_db, sym, "1m")
            if bars:
                last = bars[-1]
                out[sym] = (last["c"], "Sanyam %s bar" % last["source"],
                            nsecal.to_ist(nsecal.utc_of(last["ts"])))
                got = True
        elif offline:
            p = os.path.join(offline, "chart-%s-1d-5m.json" % nsedata.to_yahoo(sym))
            if os.path.exists(p):
                with open(p, encoding="utf-8") as fh:
                    ser = nsedata.series_from_chart(fh.read())
                closes = [c for c in ser["c"] if c is not None]
                if closes:
                    out[sym] = (closes[-1], "saved chart", None)
                    got = True
        else:
            try:
                if k:
                    nsedata.SLEEP(pause)
                ser = nsedata.series_from_chart(
                    nsedata.yahoo_get(nsedata.chart_path(sym, "1d", "5m")))
                px = (ser.get("meta") or {}).get("regularMarketPrice")
                if px is None:
                    closes = [c for c in ser["c"] if c is not None]
                    px = closes[-1] if closes else None
                if px is not None:
                    out[sym] = (float(px), "Yahoo 5m", nsecal.ist_now())
                    got = True
            except nsedata.ThrottledError as e:
                # One message, then fall through to the settled close for everything.
                print("note: %s" % e, file=sys.stderr)
                break
            except nsedata.DataError:
                pass
        if not got:
            out[sym] = (None, None, None)
    missing = [s for s, v in out.items() if v[0] is None]
    if missing:
        day = nsecal.ist_now().date()
        if not nsecal.is_trading_day(day):
            day = nsecal.prev_session_day(day)
        for _ in range(5):
            try:
                rows, _c = nsedata.fetch_bhav(day, offline or cache_dir)
                break
            except nsedata.DataError:
                day = nsecal.prev_session_day(day)
                if day is None:
                    rows = []
                    break
        else:
            rows = []
        want = set(missing)
        for r in rows:
            sym = (r.get("SYMBOL") or "").upper()
            if sym in want and (r.get("SERIES") or "").upper() in nsedata.TRADEABLE_SERIES:
                c = nsedata.num(r.get("CLOSE_PRICE"))
                if c is not None:
                    out[sym] = (c, "settled close %s" % day.isoformat(), None)
    return out


# ---------------------------------------------------------------- commands

def square_off_time(day, default):
    """The IST time to flatten by. A Muhurat window ends early, so the square-off
    moves with it rather than sitting past the close."""
    s = nsecal.session(day)
    if not s:
        return None
    open_t, close_t = s
    if close_t < default:                     # a short special session
        base = datetime.datetime.combine(day, close_t) - datetime.timedelta(minutes=15)
        return base.time()
    return default


def cmd_add(a):
    st = load(a.store)
    added = []
    if a.from_plan:
        if not os.path.exists(a.plan):
            raise AlertError("no plan at %s" % a.plan)
        with open(a.plan, encoding="utf-8") as fh:
            plan = json.load(fh)
        sym = (plan.get("symbol") or "").upper()
        stop = nsedata.num(plan.get("stop"))
        target = nsedata.num(plan.get("target"))
        if not sym or stop is None or target is None:
            raise AlertError("%s needs symbol, stop and target" % a.plan)
        short = (plan.get("side") or "").upper() == "SELL"
        added.append({"id": next_id(st), "kind": "above" if short else "below",
                      "symbol": sym, "value": stop, "label": "stop", "once": True})
        st["rules"].append(added[-1])
        added.append({"id": next_id(st), "kind": "below" if short else "above",
                      "symbol": sym, "value": target, "label": "target", "once": True})
        st["rules"].append(added[-1])
        flat = plan.get("flat_by") or nsecal.SQUARE_OFF.strftime("%H:%M")
        added.append({"id": next_id(st), "kind": "at_time", "value": flat,
                      "label": "square off %s" % sym, "square_off": True, "once": True})
        st["rules"].append(added[-1])
    else:
        if a.at:
            nsecal.parse_ist("2026-01-01 %s" % a.at, "--at")
            added.append({"id": next_id(st), "kind": "at_time", "value": a.at,
                          "label": a.label or "time", "square_off": bool(a.square_off),
                          "once": not a.repeat})
        elif a.above is not None or a.below is not None:
            if not a.symbol:
                raise AlertError("a price alert needs --symbol")
            kind = "above" if a.above is not None else "below"
            added.append({"id": next_id(st), "kind": kind, "symbol": a.symbol.upper(),
                          "value": a.above if a.above is not None else a.below,
                          "label": a.label or kind, "once": not a.repeat})
        else:
            raise AlertError("nothing to add. Use --from-plan, or --above/--below, or --at.")
        st["rules"].extend(added)
    save(a.store, st)
    for r in added:
        print("added %s: %s" % (r["id"], describe(r)))
    return 0


def cmd_list(a):
    st = load(a.store)
    if a.json:
        print(json.dumps(st, indent=2))
        return 0
    if not st["rules"]:
        print("no alerts. Try: python3 alerts.py add --from-plan")
        return 0
    print("%d alerts:" % len(st["rules"]))
    for r in st["rules"]:
        mark = "fired %s" % r["fired_at"] if r.get("fired_at") else "armed"
        print("  %-4s %-58s %s" % (r["id"], describe(r), mark))
    return 0


def cmd_clear(a):
    st = load(a.store)
    before = len(st["rules"])
    if a.id:
        st["rules"] = [r for r in st["rules"] if r.get("id") not in set(a.id.split(","))]
    elif a.fired:
        st["rules"] = [r for r in st["rules"] if not r.get("fired_at")]
    elif a.all:
        st["rules"] = []
    else:
        raise AlertError("clear what? --id, --fired or --all")
    save(a.store, st)
    print("removed %d, %d left." % (before - len(st["rules"]), len(st["rules"])))
    return 0


def cmd_check(a):
    st = load(a.store)
    now = nsecal.parse_ist(a.now, "--now") if a.now else nsecal.ist_now()
    day = now.date()
    session = nsecal.session(day)
    manual = {}
    for pair in (a.price or []):
        if "=" not in pair:
            raise AlertError('--price wants SYMBOL=PRICE, not %r' % pair)
        k, v = pair.split("=", 1)
        manual[k.strip().upper()] = nsedata.num(v)

    need = sorted({r["symbol"] for r in st["rules"]
                   if r["kind"] in ("above", "below") and not (r.get("fired_at") and r.get("once"))
                   and r["symbol"] not in manual})
    prices = live_prices(need, a.cache, a.offline, a.pause, a.sanyam) \
        if need and not a.no_fetch else {}
    for sym, px in manual.items():
        prices[sym] = (px, "given on the command line", now)

    rang, skipped = [], []
    for r in st["rules"]:
        if r.get("fired_at") and r.get("once"):
            continue
        if r["kind"] == "at_time":
            if not session:
                why = nsecal.HOLIDAYS.get(day.isoformat()) or day.strftime("%A")
                skipped.append((r, "%s is not a session (%s)" % (day.isoformat(), why)))
                continue
            want = r["value"]
            if r.get("square_off"):
                t = square_off_time(day, nsecal.parse_ist("2026-01-01 %s" % want, "value").time())
                if t is None:
                    skipped.append((r, "no session today"))
                    continue
                want = t.strftime("%H:%M")
            due = nsecal.parse_ist("%s %s" % (day.isoformat(), want), "an alert time")
            if now >= due:
                rang.append((r, None, "%s IST reached" % want))
        else:
            px, source, _as_of = prices.get(r["symbol"], (None, None, None))
            if px is None:
                skipped.append((r, "no price for %s" % r["symbol"]))
                continue
            hit = px >= r["value"] if r["kind"] == "above" else px <= r["value"]
            if hit:
                rang.append((r, px, "%s at %s (%s)" % (r["symbol"], charges.inr(px), source)))

    stamp = now.isoformat(timespec="minutes")
    for r, _px, _why in rang:
        r["fired_at"] = stamp
    if not a.no_write:
        save(a.store, st)

    if a.json:
        print(json.dumps({"now": stamp, "rang": [{"id": r["id"], "what": describe(r),
                                                  "why": why, "price": px}
                                                 for r, px, why in rang],
                          "skipped": [{"id": r["id"], "why": why} for r, why in skipped]},
                         indent=2))
        return 0
    if not rang:
        print("%s IST: nothing rang." % now.strftime("%H:%M"))
    for r, _px, why in rang:
        print("RING  %-4s %s" % (r["id"], describe(r)))
        print("      %s" % why)
        if r.get("square_off"):
            print("      Your broker squares off MIS positions itself at about 15:20 at market. "
                  "Exit now and the price is yours.")
    for r, why in skipped[:6]:
        print("  skip %-4s %s" % (r["id"], why), file=sys.stderr)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="alerts.py", description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", default="trader/alerts.json")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("add")
    p.add_argument("--from-plan", action="store_true")
    p.add_argument("--plan", default="trader/plan.json")
    p.add_argument("--symbol")
    p.add_argument("--above", type=float)
    p.add_argument("--below", type=float)
    p.add_argument("--at", metavar="HH:MM")
    p.add_argument("--square-off", action="store_true")
    p.add_argument("--label")
    p.add_argument("--repeat", action="store_true", help="ring every time, not once")

    p = sub.add_parser("list")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("clear")
    p.add_argument("--id")
    p.add_argument("--fired", action="store_true")
    p.add_argument("--all", action="store_true")

    p = sub.add_parser("check")
    p.add_argument("--price", action="append", metavar="SYM=PRICE")
    p.add_argument("--now", help='pretend it is this IST time')
    p.add_argument("--cache", default="trader/cache")
    p.add_argument("--pause", type=float, default=1.0)
    p.add_argument("--no-fetch", action="store_true", help="only --price and time alerts")
    p.add_argument("--sanyam", nargs="?", const="", metavar="DB",
                   help="take prices from Sanyam's candle store instead of Yahoo")
    p.add_argument("--offline", metavar="DIR")
    p.add_argument("--no-write", action="store_true")
    p.add_argument("--json", action="store_true")

    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"add": cmd_add, "list": cmd_list, "check": cmd_check, "clear": cmd_clear}[a.cmd](a)
    except (AlertError, charges.ChargeError, nsecal.CalendarError) as e:
        print("alerts.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

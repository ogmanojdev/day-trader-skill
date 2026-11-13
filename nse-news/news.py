#!/usr/bin/env python3
"""News Monitor for nse-news: what moved an NSE name, and when, in IST.
Standard library only, Python 3.8+.

    python3 news.py --symbols RELIANCE,TCS --hours 12
    python3 news.py --watchlist trader/watchlist.json --hours 6
    python3 news.py announcements --hours 24          # the exchange filing feed
    python3 news.py --symbols SBIN --json

Two very different kinds of news, and the difference matters more here than in
the US:

  the exchange feed    NSE's corporate-announcements API. This is the primary
                       source, the thing the company actually filed, with the
                       exchange's own dissemination timestamp. A results
                       filing, an order win, a pledge, a board meeting: it
                       lands here first and the press reports it second.
  the press            Google News (localised to India), Moneycontrol and
                       Economic Times RSS. Fast, broad, and not authoritative.

The US original reads Yahoo's RSS headline feed and SEC 8-K filings. Neither
works here: Yahoo's RSS endpoint answers 429 for NSE symbols, and there is no
8-K. The exchange feed is the better primary anyway, because SEBI's disclosure
rules put price-sensitive information on the exchange first by law.

Headlines are matched to a symbol by company name, not ticker. An Indian ticker
is a poor search term (SBIN, LT, BSE, M&M) and "LT" in particular matches
almost anything, so the tool resolves the symbol to its registered name from
EQUITY_L.csv and searches that, then requires the name to appear in the result.

TAGS mark what the Indian market actually reacts to: results, order wins,
promoter pledges, QIPs and fundraising, block and bulk deals, SEBI and
regulatory action, rating moves, bonuses and splits, management change.

Times are IST throughout. Google News publishes in GMT and the exchange
publishes in IST already, which is a reliable way to double-count a 5.5 hour
offset if you are not careful, so both are normalised on the way in.

State: trader/state/news.json remembers what has been reported, so a repeated
run shows what is new rather than the same list again. --all ignores it.

Not financial advice. A headline is not a reason to trade, and nothing here can
place an order: no order leaves this machine without nse-risk passing it and
you typing yes.
"""

import argparse
import datetime
import json
import os
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ElementTree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nsecal   # noqa: E402  (vendored; see tools/vendor.py)
import nsedata  # noqa: E402

NewsError = nsedata.DataError

# Without from_date/to_date the feed returns only the latest 20 filings market
# wide, which on a busy evening is about four minutes of news and silently
# answers "nothing happened" for every symbol you actually asked about. With the
# range it returned 3,775 rows for six days.
ANNOUNCEMENTS = "/api/corporate-announcements?index=equities"
ANNOUNCEMENTS_RANGE = "/api/corporate-announcements?index=equities&from_date=%s&to_date=%s"
GOOGLE_NEWS = "https://news.google.com/rss/search?q=%s&hl=en-IN&gl=IN&ceid=IN:en"
PRESS_FEEDS = [
    ("Moneycontrol", "https://www.moneycontrol.com/rss/marketreports.xml"),
    ("Economic Times", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
]

# What the Indian market reacts to. Order matters: the first match wins, so the
# specific patterns come before the general ones.
TAGS = [
    # "revenue" and "profit" on their own are too loose: a GST tribunal piece headed
    # "Dismisses 8 Revenue Appeals" is not an earnings story. They need an earnings
    # neighbour, or a quarter.
    ("results", r"\b(results?|earnings|q[1-4]\s*fy|fy\d{2}\s*q[1-4]|quarterly|ebitda|pat\b|"
                r"topline|bottomline|net\s+profit|(revenue|profit|margin)s?\s+"
                r"(rose|fell|up|down|grew|decline|jump|surge|beat|miss))\b"),
    ("order_win", r"\b(order\s*(win|book|inflow)|bags?\s+(an?\s+)?order|contract\s+(win|worth)|"
                  r"letter\s+of\s+(award|intent)|loa\b|wins?\s+.{0,20}\border)\b"),
    ("fundraise", r"\b(qip|rights\s+issue|preferential\s+(allotment|issue)|fund\s*rais|"
                  r"ncd\b|debenture|warrants?\b|fpo\b|ofs\b)\b"),
    ("pledge", r"\b(pledg\w*|encumbr\w*|invocation\s+of\s+pledge)\b"),
    ("deal", r"\b(block\s+deal|bulk\s+deal|stake\s+(sale|buy)|acquir\w*|acquisition|merger|"
             r"amalgamation|divest\w*|slump\s+sale)\b"),
    ("regulatory", r"\b(sebi|rbi\b|cci\b|nclt|show\s*cause|penalt\w*|adjudicat\w*|"
                   r"investigat\w*|insider\s+trading|gstat|gst\s+(notice|demand|appeal)|"
                   r"itc\s+refund|revenue\s+appeals?|tribunal|income\s*tax\s+"
                   r"(notice|demand|raid)|search\s+and\s+seiz\w*|ed\s+(raid|summons))\b"),
    ("rating", r"\b(upgrad\w*|downgrad\w*|target\s+price|rating|crisil|icra\b|care\s+ratings|"
               r"outlook\s+(revis|chang))\b"),
    ("corp_action", r"\b(dividend|bonus\s+issue|stock\s+split|sub\s*division|buy\s*back|"
                    r"record\s+date|ex[-\s]?date)\b"),
    ("management", r"\b(resign\w*|appoint\w*|cessation|managing\s+director|\bmd\b|\bceo\b|"
                   r"\bcfo\b|chairman|board\s+of\s+directors|key\s+managerial)\b"),
    ("board_meeting", r"\b(board\s+meeting|intimation\s+of\s+(board\s+)?meeting|outcome\s+of\s+"
                      r"(the\s+)?board)\b"),
    ("capacity", r"\b(capex|capacity\s+(expansion|addition)|commission\w*|new\s+plant|"
                 r"greenfield|brownfield)\b"),
    ("call", r"\b(analysts?|institutional\s+investor|con\.?\s*call|conference\s+call|"
             r"earnings\s+call|transcript|investor\s+(meet|presentation))\b"),
]
TAGS = [(name, re.compile(pat, re.I)) for name, pat in TAGS]


def tags_for(text):
    t = text or ""
    out = [name for name, rx in TAGS if rx.search(t)]
    return out or ["other"]


# ---------------------------------------------------------------- time

def parse_rss_time(raw):
    """RFC 822 (Google News, ET, Moneycontrol) -> naive IST. Returns None if unreadable.

    The offset is honoured, not assumed. Google News says GMT, Moneycontrol says
    +0530, and treating either as the other is a 5.5 hour error in a tool whose
    whole job is telling you when something happened.
    """
    s = (raw or "").strip()
    if not s:
        return None
    s = re.sub(r"^[A-Za-z]{3},\s*", "", s)
    m = re.match(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})\s+(\d{2}):(\d{2})(?::(\d{2}))?\s*(.*)$", s)
    if not m:
        return None
    day, mon, year, hh, mm, ss, zone = m.groups()
    months = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
              "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
    mnum = months.get(mon.lower())
    if not mnum:
        return None
    try:
        naive = datetime.datetime(int(year), mnum, int(day), int(hh), int(mm), int(ss or 0))
    except ValueError:
        return None
    zone = (zone or "").strip().upper()
    offset = datetime.timedelta(0)
    zm = re.match(r"([+-])(\d{2}):?(\d{2})$", zone)
    if zm:
        sign = 1 if zm.group(1) == "+" else -1
        offset = sign * datetime.timedelta(hours=int(zm.group(2)), minutes=int(zm.group(3)))
    elif zone in ("IST",):
        offset = nsecal.IST_OFFSET
    return nsecal.to_ist(naive - offset)


def parse_nse_time(raw):
    """"10-Nov-2026 23:48:55" -> naive IST (the exchange publishes in IST)."""
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.datetime.strptime((raw or "").strip(), fmt)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------- the exchange feed

def fetch_announcements(offline=None, hours=12):
    if offline:
        p = os.path.join(offline, "corporate-announcements.json")
        if not os.path.exists(p):
            raise NewsError("no fixture at %s" % p)
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    end = nsecal.ist_now().date()
    start = (nsecal.ist_now() - datetime.timedelta(hours=max(hours, 24))).date()
    try:
        return json.loads(nsedata.nse_api(
            ANNOUNCEMENTS_RANGE % (start.strftime("%d-%m-%Y"), end.strftime("%d-%m-%Y"))))
    except nsedata.DataError:
        return json.loads(nsedata.nse_api(ANNOUNCEMENTS))   # the latest 20, better than nothing


def announcements_for(doc, symbols=None, since=None):
    """The feed -> normalised items, newest first."""
    want = set(s.upper() for s in symbols) if symbols else None
    out = []
    rows = doc if isinstance(doc, list) else (doc or {}).get("data") or []
    for r in rows:
        sym = (r.get("symbol") or "").strip().upper()
        if want and sym not in want:
            continue
        when = parse_nse_time(r.get("an_dt") or r.get("sort_date") or "")
        if since and when and when < since:
            continue
        desc = " ".join((r.get("desc") or "").split())
        body = " ".join((r.get("attchmntText") or "").split())
        out.append({"symbol": sym, "name": " ".join((r.get("sm_name") or "").split()),
                    "time": when, "category": desc, "text": body,
                    "industry": (r.get("smIndustry") or "").strip() or None,
                    "url": (r.get("attchmntFile") or "").strip() or None,
                    "tags": tags_for(desc + " " + body),
                    "source": "NSE filing"})
    out.sort(key=lambda i: i["time"] or datetime.datetime.min, reverse=True)
    # The exchange feed repeats a filing: the same CGCL con-call intimation came
    # back three times in one 20 row answer, same timestamp, same text. Dedupe on
    # symbol plus category plus text, keeping the newest.
    seen, uniq = set(), []
    for i in out:
        key = (i["symbol"], i["category"].lower(), i["text"].lower()[:120])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(i)
    return uniq


# ---------------------------------------------------------------- the press

def rss_items(xml_bytes):
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError as e:
        raise NewsError("could not parse an RSS answer: %s" % e)
    out = []
    for item in root.iter("item"):
        def text(tag):
            el = item.find(tag)
            return " ".join((el.text or "").split()) if el is not None and el.text else ""
        src = item.find("source")
        out.append({"title": text("title"), "link": text("link"),
                    "when": parse_rss_time(text("pubDate")),
                    "publisher": (" ".join((src.text or "").split())
                                  if src is not None and src.text else "")})
    return out


def split_google_title(title, publisher):
    """Google News writes "Headline - Publisher". Keep the headline, recover the
    publisher when the <source> element is missing."""
    if publisher:
        return title, publisher
    m = re.match(r"^(.*)\s+-\s+([^-]{2,40})$", title)
    return (m.group(1), m.group(2)) if m else (title, "")


def press_for(symbol, name, hours, offline=None, feeds=True):
    """Headlines that mention this company, newest first.

    The name must actually appear in the headline. Searching Google News for a
    company returns plenty of market-wrap pieces that merely list it, but a
    keyword search alone also returns pieces about a different company whose
    name shares a word, so the check is a substring of the registered name
    rather than the query matching at all.
    """
    since = nsecal.ist_now() - datetime.timedelta(hours=hours)
    query = name or symbol
    short = re.sub(r"\b(limited|ltd\.?|industries|corporation|company|india)\b", "",
                   query, flags=re.I).strip()
    needle = (short or query).lower()
    items, errors = [], []
    sources = []
    if offline:
        p = os.path.join(offline, "google-news-%s.xml" % symbol)
        if os.path.exists(p):
            with open(p, "rb") as fh:
                sources.append(("Google News", fh.read()))
    else:
        url = GOOGLE_NEWS % urllib.parse.quote('"%s"' % query)
        try:
            sources.append(("Google News", nsedata.http_get(url, timeout=30).encode("utf-8")))
        except Exception as e:
            errors.append("Google News: %s" % e)
        if feeds:
            for label, feed in PRESS_FEEDS:
                try:
                    sources.append((label, nsedata.http_get(feed, timeout=25).encode("utf-8")))
                except Exception as e:
                    errors.append("%s: %s" % (label, e))
    for label, raw in sources:
        try:
            for it in rss_items(raw):
                title, publisher = split_google_title(it["title"], it["publisher"])
                if needle not in title.lower():
                    continue
                if it["when"] and it["when"] < since:
                    continue
                items.append({"symbol": symbol, "title": title, "time": it["when"],
                              "publisher": publisher or label, "url": it["link"],
                              "tags": tags_for(title), "source": label})
        except NewsError as e:
            errors.append("%s: %s" % (label, e))
    seen, uniq = set(), []
    for it in sorted(items, key=lambda i: i["time"] or datetime.datetime.min, reverse=True):
        key = it["title"].lower()[:90]
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)
    return uniq, errors


# ---------------------------------------------------------------- the move

def move_for(symbols, cache_dir, offline=None):
    """Each symbol's last settled move, from the bhavcopy. Intraday would need
    Yahoo, which throttles; the settled close is the honest number before the open."""
    day = nsecal.ist_now().date()
    if not nsecal.is_trading_day(day):
        day = nsecal.prev_session_day(day)
    for _ in range(5):
        try:
            rows, _cached = nsedata.fetch_bhav(day, offline or cache_dir)
            break
        except nsedata.DataError:
            day = nsecal.prev_session_day(day)
            if day is None:
                return {}, None
    else:
        return {}, None
    want = set(s.upper() for s in symbols)
    out = {}
    for r in rows:
        sym = (r.get("SYMBOL") or "").upper()
        if sym in want and (r.get("SERIES") or "").upper() in nsedata.TRADEABLE_SERIES:
            close = nsedata.num(r.get("CLOSE_PRICE"))
            prev = nsedata.num(r.get("PREV_CLOSE"))
            out[sym] = {"close": close, "prev_close": prev,
                        "pct": ((close - prev) / prev * 100.0) if (close and prev) else None,
                        "delivery_pct": nsedata.num(r.get("DELIV_PER"))}
    return out, day


# ---------------------------------------------------------------- state

def load_state(path):
    if not os.path.exists(path):
        return {"seen": []}
    try:
        with open(path, encoding="utf-8") as fh:
            st = json.load(fh)
        st.setdefault("seen", [])
        return st
    except (OSError, ValueError):
        return {"seen": []}


def save_state(path, st, keep=800):
    st["seen"] = st["seen"][-keep:]
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(st, fh, indent=2)


def key_of(item):
    base = item.get("title") or (item.get("category", "") + "|" + item.get("text", ""))
    return "%s|%s" % (item.get("symbol") or "", base.lower()[:110])


# ---------------------------------------------------------------- commands

def symbols_from(a):
    if a.symbols:
        return [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    if os.path.exists(a.watchlist):
        with open(a.watchlist, encoding="utf-8") as fh:
            wl = json.load(fh)
        return [h["symbol"] for h in (wl.get("hits") or [])][:a.max_symbols]
    raise NewsError("nothing to watch. Pass --symbols, or run nse-scanner's daily scan first.")


def cmd_announcements(a):
    since = nsecal.ist_now() - datetime.timedelta(hours=a.hours)
    items = announcements_for(fetch_announcements(a.offline, a.hours), None, since)
    st = load_state(a.state)
    fresh = [i for i in items if a.all or key_of(i) not in st["seen"]]
    if a.json:
        print(json.dumps({"as_of": nsecal.ist_now().isoformat(timespec="seconds"),
                          "hours": a.hours, "count": len(fresh),
                          "items": [dict(i, time=i["time"].isoformat() if i["time"] else None)
                                    for i in fresh]}, indent=2))
    else:
        print("NSE corporate announcements, last %dh: %d filings%s."
              % (a.hours, len(fresh), "" if a.all else " not seen before"))
        for i in fresh[:a.show]:
            when = i["time"].strftime("%d %b %H:%M") if i["time"] else "time unknown"
            print("  %-12s %s  %s" % (i["symbol"], when, i["category"][:64]))
            if i["text"]:
                print("               %s" % i["text"][:96])
            print("               tags: %s" % ",".join(i["tags"]))
        if len(fresh) > a.show:
            print("  ... and %d more" % (len(fresh) - a.show))
    if not a.no_state:
        st["seen"].extend(key_of(i) for i in fresh)
        save_state(a.state, st)
    return 0


def cmd_watch(a):
    syms = symbols_from(a)
    names = nsedata.names_for(offline=a.offline, universe_path=a.universe, quiet=True) or {}
    since = nsecal.ist_now() - datetime.timedelta(hours=a.hours)
    st = load_state(a.state)

    filings = []
    try:
        filings = announcements_for(fetch_announcements(a.offline, a.hours), syms, since)
    except NewsError as e:
        print("note: the exchange feed is unavailable (%s)" % e, file=sys.stderr)

    moves, move_day = ({}, None)
    if not a.no_move:
        try:
            moves, move_day = move_for(syms, a.cache, a.offline)
        except (nsedata.DataError, OSError) as e:
            print("note: no settled move available (%s)" % e, file=sys.stderr)

    by_symbol, errors = {}, []
    for sym in syms:
        items = [f for f in filings if f["symbol"] == sym]
        if not a.no_press:
            press, errs = press_for(sym, names.get(sym, ""), a.hours, a.offline, not a.fast)
            items += press
            errors += errs
        items.sort(key=lambda i: i["time"] or datetime.datetime.min, reverse=True)
        if not a.all:
            items = [i for i in items if key_of(i) not in st["seen"]]
        by_symbol[sym] = items

    total = sum(len(v) for v in by_symbol.values())
    if a.json:
        print(json.dumps({
            "as_of": nsecal.ist_now().isoformat(timespec="seconds"), "hours": a.hours,
            "move_session": move_day.isoformat() if move_day else None,
            "count": total,
            "symbols": {s: {"move": moves.get(s),
                            "items": [dict(i, time=i["time"].isoformat() if i["time"] else None)
                                      for i in v]}
                        for s, v in by_symbol.items()}}, indent=2))
    else:
        print("Last %dh in IST, %d symbols, %d items%s."
              % (a.hours, len(syms), total, "" if a.all else " not seen before"))
        if move_day:
            print("Moves are the settled close of %s." % move_day.isoformat())
        for sym in syms:
            mv = moves.get(sym)
            head = sym if not names.get(sym) else "%s (%s)" % (sym, names[sym])
            if mv and mv.get("pct") is not None:
                head += "  %+.2f%% to %.2f" % (mv["pct"], mv["close"])
                if mv.get("delivery_pct") is not None:
                    head += ", delivery %.0f%%" % mv["delivery_pct"]
            print("\n%s" % head)
            items = by_symbol[sym]
            if not items:
                print("  nothing new.")
            for i in items[:a.show]:
                when = i["time"].strftime("%d %b %H:%M") if i["time"] else "time unknown"
                label = i.get("title") or i.get("category") or ""
                print("  %s  %-13s %s" % (when, "[" + ",".join(i["tags"][:2]) + "]", label[:88]))
                if i["source"] == "NSE filing":
                    print("      filed with the exchange%s"
                          % ((": " + i["text"][:72]) if i.get("text") else ""))
            if len(items) > a.show:
                print("  ... and %d more" % (len(items) - a.show))
        for e in errors[:4]:
            print("note: %s" % e, file=sys.stderr)
    if not a.no_state:
        for v in by_symbol.values():
            st["seen"].extend(key_of(i) for i in v)
        save_state(a.state, st)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="news.py", description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    common = [
        (("--hours",), {"type": int, "default": 12}),
        (("--state",), {"default": "trader/state/news.json"}),
        (("--no-state",), {"action": "store_true"}),
        (("--all",), {"action": "store_true", "help": "ignore what has been seen"}),
        (("--show",), {"type": int, "default": 8}),
        (("--offline",), {"metavar": "DIR"}),
        (("--json",), {"action": "store_true"}),
    ]
    for args, kw in common:
        ap.add_argument(*args, **kw)
    ap.add_argument("--symbols")
    ap.add_argument("--watchlist", default="trader/watchlist.json")
    ap.add_argument("--universe", default="trader/universe.txt")
    ap.add_argument("--cache", default="trader/cache")
    ap.add_argument("--max-symbols", type=int, default=12)
    ap.add_argument("--no-press", action="store_true", help="exchange filings only")
    ap.add_argument("--no-move", action="store_true")
    ap.add_argument("--fast", action="store_true", help="skip the Moneycontrol and ET feeds")
    ap.add_argument("mode", nargs="?", default="watch", choices=["watch", "announcements"])
    a = ap.parse_args(argv)
    try:
        return cmd_announcements(a) if a.mode == "announcements" else cmd_watch(a)
    except (NewsError, nsecal.CalendarError) as e:
        print("news.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

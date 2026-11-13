#!/usr/bin/env python3
"""Deals Tracker for nse-deals: who actually bought, by name, yesterday.
Standard library only, Python 3.8+.

    python3 deals.py                              # today's bulk and block deals
    python3 deals.py --symbols RELIANCE,SBIN
    python3 deals.py --min-value 1000             # only deals above 10 crore
    python3 deals.py insider --days 30            # SEBI PIT disclosures
    python3 deals.py --json

This replaces the US original's Congress tracker, which has no Indian
counterpart: there is no STOCK Act here and no register of politicians' trades.
What India has instead is better for a trader, because it is daily rather than
45 days late and it names the buyer:

  bulk deals    Every trade where a single client moved more than 0.5% of a
                company's listed shares in a day, published by the exchange
                after the close with the client's name, side, quantity and
                weighted average price.
  block deals   Trades done in the block window, minimum 10 crore, same
                disclosure.
  PIT           Disclosures under SEBI's Prohibition of Insider Trading
                regulations: promoters, directors and designated employees must
                report their own dealings within two trading days.

A bulk deal is not a tip and the disclosure is deliberately after the fact. The
useful reading is accumulation: the same fund appearing on the buy side of the
same name across several sessions, or a promoter selling into a rally. A single
day's print is mostly noise, which is why the default view aggregates by
symbol and client and shows net, not individual trades.

Both sides of a bulk deal are reported, so a crossed trade appears twice, once
as a buy and once as a sell. Net is net of both.

Not financial advice. These are disclosures of what someone else did, for their
own reasons, at their own size and horizon. Nothing here can place an order: no
order leaves this machine without nse-risk passing it and you typing yes.
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

DealError = nsedata.DataError
PIT = "/api/corporates-pit?index=equities&from_date=%s&to_date=%s"


def parse_deals(text, kind):
    """bulk.csv or block.csv -> normalised deals."""
    out = []
    for r in nsedata.rows_of(text):
        sym = (r.get("Symbol") or "").strip().upper()
        if not sym:
            continue
        qty = nsedata.num(r.get("Quantity Traded"))
        px = nsedata.num(r.get("Trade Price / Wght. Avg. Price") or r.get("Trade Price"))
        side = (r.get("Buy/Sell") or "").strip().upper()
        when = None
        for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
            try:
                when = datetime.datetime.strptime((r.get("Date") or "").strip(), fmt).date()
                break
            except ValueError:
                continue
        out.append({"date": when.isoformat() if when else None, "symbol": sym,
                    "name": " ".join((r.get("Security Name") or "").split()),
                    "client": " ".join((r.get("Client Name") or "").split()),
                    "side": "BUY" if side.startswith("B") else "SELL",
                    "quantity": int(qty) if qty else None, "price": px,
                    "value_lacs": round(qty * px / 100000.0, 2) if (qty and px) else None,
                    "remarks": " ".join((r.get("Remarks") or "").split()) or None,
                    "kind": kind})
    return out


def fetch_deals(offline=None):
    deals = []
    for path, kind, fname in ((nsedata.BULK_DEALS, "bulk", "bulk.csv"),
                              (nsedata.BLOCK_DEALS, "block", "block.csv")):
        try:
            if offline:
                p = os.path.join(offline, fname)
                if not os.path.exists(p):
                    continue
                with open(p, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            else:
                text = nsedata.nse_get(path, timeout=40)
            deals += parse_deals(text, kind)
        except nsedata.DataError as e:
            print("note: %s deals unavailable (%s)" % (kind, e), file=sys.stderr)
    return deals


def aggregate(deals):
    """(symbol, client) -> net quantity and value. Both sides of a crossed trade
    are published, so a net of zero means the shares changed hands between two
    reported clients, not that nothing happened."""
    agg = {}
    for d in deals:
        if d["quantity"] is None:
            continue
        key = (d["symbol"], d["client"])
        a = agg.setdefault(key, {"symbol": d["symbol"], "client": d["client"],
                                 "buy_qty": 0, "sell_qty": 0, "buy_value": 0.0,
                                 "sell_value": 0.0, "kinds": set(), "dates": set()})
        side = "buy" if d["side"] == "BUY" else "sell"
        a[side + "_qty"] += d["quantity"]
        a[side + "_value"] += d["value_lacs"] or 0.0
        a["kinds"].add(d["kind"])
        if d["date"]:
            a["dates"].add(d["date"])
    out = []
    for a in agg.values():
        net_qty = a["buy_qty"] - a["sell_qty"]
        net_val = a["buy_value"] - a["sell_value"]
        out.append(dict(a, net_qty=net_qty, net_value_lacs=round(net_val, 2),
                        kinds=sorted(a["kinds"]), dates=sorted(a["dates"])))
    out.sort(key=lambda a: -abs(a["net_value_lacs"]))
    return out


def deal_lines(x):
    """The rows printed for one client's position in one symbol.

    Shares and cash carry their own signs and can disagree: a market maker who buys
    and sells the same size all day ends a few shares long while taking cash out.
    Reporting the share sign alone turned that round trip into a "buy", which is the
    opposite of what the position says.
    """
    gross = max(x["buy_qty"], x["sell_qty"])
    both = x["buy_qty"] > 0 and x["sell_qty"] > 0
    crossed = both and abs(x["net_qty"]) <= 0.1 * gross
    arrow = "crossed" if (crossed or x["net_qty"] == 0) else (
        "bought" if x["net_qty"] > 0 else "sold")
    cash = x["net_value_lacs"]
    word = "paid" if cash > 0 else ("received" if cash < 0 else "flat")
    rows = ["  %-13s %-42s %-7s %12s shares  %s %s"
            % (x["symbol"], x["client"][:42], arrow,
               charges.indian_group(abs(x["net_qty"])),
               word, charges.inr(abs(cash) * 100000, paise=False))]
    if both:
        rows.append("                bought %s, sold %s: %s of the larger side kept"
                    % (charges.indian_group(x["buy_qty"]), charges.indian_group(x["sell_qty"]),
                       ("%.1f%%" % (100.0 * abs(x["net_qty"]) / gross)) if gross else "0%"))
    rows.append("                %s%s" % (",".join(x["kinds"]),
                                          ("  " + ", ".join(x["dates"])) if x["dates"] else ""))
    return rows


def cmd_deals(a):
    deals = fetch_deals(a.offline)
    if not deals:
        print("no bulk or block deals published for the latest session.")
        return 0
    want = set(s.strip().upper() for s in a.symbols.split(",")) if a.symbols else None
    if want:
        deals = [d for d in deals if d["symbol"] in want]
    if a.min_value:
        deals = [d for d in deals if (d["value_lacs"] or 0) >= a.min_value]
    agg = aggregate(deals)
    days = sorted({d["date"] for d in deals if d["date"]})
    out = {"as_of": nsecal.ist_now().isoformat(timespec="seconds"),
           "sessions": days, "deals": len(deals), "parties": len(agg),
           "aggregated": agg, "raw": deals if a.raw else None}
    if not a.no_write:
        d = os.path.dirname(a.out)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2, default=str)
    if a.json:
        print(json.dumps(out, indent=2, default=str))
        return 0
    print("Bulk and block deals%s: %d prints, %d client positions."
          % ((" for " + ", ".join(days)) if days else "", len(deals), len(agg)))
    print("Figures in rupees. The exchange publishes value in lakh.\n")
    for x in agg[:a.show]:
        for line in deal_lines(x):
            print(line)
    if len(agg) > a.show:
        print("  ... and %d more" % (len(agg) - a.show))
    if not a.no_write:
        print("\nWrote %s." % a.out)
    return 0


def cmd_insider(a):
    end = nsecal.ist_now().date()
    start = end - datetime.timedelta(days=a.days)
    if a.offline:
        p = os.path.join(a.offline, "corporates-pit.json")
        if not os.path.exists(p):
            raise DealError("no fixture at %s" % p)
        with open(p, encoding="utf-8") as fh:
            doc = json.load(fh)
    else:
        doc = json.loads(nsedata.nse_api(PIT % (start.strftime("%d-%m-%Y"),
                                                end.strftime("%d-%m-%Y"))))
    rows = (doc or {}).get("data") or []
    want = set(s.strip().upper() for s in a.symbols.split(",")) if a.symbols else None
    items = []
    for r in rows:
        sym = (r.get("symbol") or "").strip().upper()
        if want and sym not in want:
            continue
        qty = nsedata.num(r.get("secAcq"))
        val = nsedata.num(r.get("acqfromVal")) or nsedata.num(r.get("secVal"))
        items.append({
            "symbol": sym, "company": " ".join((r.get("company") or "").split()),
            "who": " ".join((r.get("acqName") or "").split()),
            "role": (r.get("personCategory") or "").strip(),
            "mode": (r.get("acqMode") or "").strip(),
            "transaction": (r.get("tdpTransactionType") or "").strip(),
            "quantity": int(qty) if qty else None, "value": val,
            "period": "%s to %s" % ((r.get("afterAcqfromDt") or "").strip(),
                                    (r.get("afterAcqtoDt") or "").strip()),
            "filed": (r.get("date") or r.get("broadcasteDate") or "").strip()})
    if a.json:
        print(json.dumps({"from": start.isoformat(), "to": end.isoformat(),
                          "count": len(items), "items": items}, indent=2))
        return 0
    print("SEBI PIT disclosures, %s to %s: %d." % (start.isoformat(), end.isoformat(), len(items)))
    if not items:
        print("Nothing in the window. The exchange's PIT endpoint is often empty for a short\n"
              "range and is cookie gated, so an empty answer is not proof that nobody filed.")
        return 0
    for i in items[:a.show]:
        print("  %-13s %-34s %-16s %s" % (i["symbol"], i["who"][:34], i["role"][:16],
                                          i["transaction"][:22]))
        if i["quantity"]:
            print("                %s shares, mode %s, %s"
                  % (charges.indian_group(i["quantity"]), i["mode"] or "n/a", i["period"]))
    if len(items) > a.show:
        print("  ... and %d more" % (len(items) - a.show))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="deals.py", description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", nargs="?", default="deals", choices=["deals", "insider"])
    ap.add_argument("--symbols")
    ap.add_argument("--min-value", type=float, default=0.0, help="lakh (1000 = 10 crore)")
    ap.add_argument("--days", type=int, default=30, help="insider: how far back")
    ap.add_argument("--show", type=int, default=15)
    ap.add_argument("--raw", action="store_true", help="keep every print in the output file")
    ap.add_argument("--out", default="trader/deals.json")
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        return cmd_insider(a) if a.mode == "insider" else cmd_deals(a)
    except (DealError, charges.ChargeError, nsecal.CalendarError) as e:
        print("deals.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

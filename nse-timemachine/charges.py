# Vendored from nse-common/charges.py by tools/vendor.py. Do not edit here:
# edit nse-common/charges.py and re-run the script, or the suite will fail.
#!/usr/bin/env python3
"""Indian transaction costs, to the paisa. Standard library only, Python 3.8+.

One canonical copy lives in nse-common/; tools/vendor.py copies it into every
skill folder and tests/test_common.py fails if a copy drifts.

Why this file exists at all. The US original computes a target as "1R", one
times the distance to the stop, and ignores costs: most US brokers charge no
commission and there is no transaction tax on an equity trade. Neither holds
here. An Indian intraday round trip pays STT, an exchange transaction charge,
a SEBI turnover fee, an investor-protection fee, stamp duty, brokerage and 18%
GST on several of those. So a "1R" target that ignores them is not 1R, it is
less, every single time, and the gap is widest on exactly the small-stop trades
that look most attractive. Sanyam's own backtester documents the same hole at
backend/app/backtest/engine.py:22 ("no-costs"). This closes it in one place
both can import.

The asymmetry matters as much as the size. STT lands on the SELL leg only for
intraday, stamp duty on the BUY leg only, and delivery adds a flat per-scrip DP
charge on the sell. Costs are therefore not a percentage you can subtract from
a return; they depend on direction, product and how many orders filled.

RATES, read 2026-11-10 from the NSE charge schedule and a discount broker's
published calculator. They move with exchange and government circulars, so
treat them the way the holiday table is treated: dated, and refreshed on
purpose. Every one is overridable from trader/config.json under "charges", and
brokerage especially is yours, not a law. Verify against a real contract note
before you trust a number to the rupee.
"""

import json
import os
from decimal import Decimal, ROUND_HALF_UP

__all__ = ["ChargeError", "RATES", "DEFAULT_BROKERAGE", "load_rates", "inr", "indian_group",
           "leg_cost", "round_trip", "breakeven_move", "net_reward_risk", "explain"]

P = Decimal("0.01")


class ChargeError(Exception):
    pass


def _d(x, what="a number"):
    try:
        if isinstance(x, float):
            return Decimal(repr(x))
        return Decimal(str(x).strip().replace(",", "").lstrip("₹"))
    except Exception:
        raise ChargeError("%s is not a number: %r" % (what, x))


# Percentages of turnover unless the name says otherwise. buy/sell keys mean the
# charge applies to that leg only.
RATES = {
    "equity_intraday": {
        "stt": {"sell": "0.025"},
        "txn": {"buy": "0.00297", "sell": "0.00297"},      # NSE exchange transaction charge
        "sebi": {"buy": "0.0001", "sell": "0.0001"},       # 10 rupees per crore
        "ipft": {"buy": "0.0001", "sell": "0.0001"},       # NSE investor protection fund
        "stamp": {"buy": "0.003"},
        "gst_pct": "18",            # on brokerage + txn + sebi + ipft
        "dp_per_sell": "0",
    },
    "equity_delivery": {
        "stt": {"buy": "0.1", "sell": "0.1"},
        "txn": {"buy": "0.00297", "sell": "0.00297"},
        "sebi": {"buy": "0.0001", "sell": "0.0001"},
        "ipft": {"buy": "0.0001", "sell": "0.0001"},
        "stamp": {"buy": "0.015"},
        "gst_pct": "18",
        "dp_per_sell": "15.34",     # flat per scrip per day on the sell, not a percentage
    },
    "futures": {
        "stt": {"sell": "0.02"},
        "txn": {"buy": "0.00173", "sell": "0.00173"},
        "sebi": {"buy": "0.0001", "sell": "0.0001"},
        "ipft": {"buy": "0.0001", "sell": "0.0001"},
        "stamp": {"buy": "0.002"},
        "gst_pct": "18",
        "dp_per_sell": "0",
    },
    "options": {
        # every rate here is on PREMIUM turnover, not contract value
        "stt": {"sell": "0.1"},
        "txn": {"buy": "0.03503", "sell": "0.03503"},
        "sebi": {"buy": "0.0001", "sell": "0.0001"},
        "ipft": {"buy": "0.0005", "sell": "0.0005"},
        "stamp": {"buy": "0.003"},
        "gst_pct": "18",
        "dp_per_sell": "0",
    },
}

# A discount broker's shape: the lower of a percentage and a flat cap, per order.
DEFAULT_BROKERAGE = {
    "equity_intraday": {"pct": "0.03", "cap": "20", "flat": None},
    "equity_delivery": {"pct": "0", "cap": "0", "flat": "0"},
    "futures": {"pct": "0.03", "cap": "20", "flat": None},
    "options": {"pct": "0", "cap": None, "flat": "20"},
}

PRODUCTS = tuple(RATES)


def load_rates(config_path="trader/config.json", product="equity_intraday"):
    """Merge trader/config.json's "charges" over the defaults. A config may set
    just one key (say its own brokerage cap) and inherit the rest."""
    if product not in RATES:
        raise ChargeError("unknown product %r; try one of %s" % (product, ", ".join(PRODUCTS)))
    rates = json.loads(json.dumps(RATES[product]))
    brok = json.loads(json.dumps(DEFAULT_BROKERAGE[product]))
    if config_path and os.path.exists(config_path):
        try:
            with open(config_path, encoding="utf-8") as fh:
                cfg = json.load(fh)
        except (OSError, ValueError) as e:
            raise ChargeError("could not read %s: %s" % (config_path, e))
        block = (cfg.get("charges") or {})
        for key in ("all", product):
            over = block.get(key) or {}
            for k, v in over.items():
                if k == "brokerage":
                    brok.update(v or {})
                elif isinstance(v, dict) and isinstance(rates.get(k), dict):
                    rates[k].update(v)
                else:
                    rates[k] = v
    rates["brokerage"] = brok
    return rates


def _pct(rates, key, side, turnover):
    table = rates.get(key) or {}
    if side not in table:
        return Decimal("0")
    return turnover * _d(table[side], key) / Decimal("100")


def _brokerage(rates, turnover):
    b = rates.get("brokerage") or {}
    if b.get("flat") is not None:
        return _d(b["flat"], "brokerage flat")
    pct = turnover * _d(b.get("pct") or 0, "brokerage pct") / Decimal("100")
    cap = b.get("cap")
    return min(pct, _d(cap, "brokerage cap")) if cap is not None else pct


def leg_cost(side, quantity, price, product="equity_intraday", rates=None, orders=1):
    """Cost of one leg. side is "buy" or "sell". Returns a dict of components and
    "total", every value a Decimal rounded to the paisa.

    orders is how many separate orders filled this leg: brokerage is charged per
    order, so a position scaled out in three exits pays it three times.
    """
    side = (side or "").strip().lower()
    if side not in ("buy", "sell"):
        raise ChargeError('side wants "buy" or "sell", not %r' % side)
    q = _d(quantity, "quantity")
    p = _d(price, "price")
    if q <= 0:
        raise ChargeError("quantity must be above zero, not %s" % q)
    if p < 0:
        raise ChargeError("price cannot be negative")
    n = int(orders or 1)
    if n < 1:
        raise ChargeError("orders must be at least 1")
    r = rates or load_rates(None, product)
    r.setdefault("brokerage", DEFAULT_BROKERAGE.get(product, {}))
    turnover = q * p

    brokerage = _brokerage(r, turnover) * n
    txn = _pct(r, "txn", side, turnover)
    sebi = _pct(r, "sebi", side, turnover)
    ipft = _pct(r, "ipft", side, turnover)
    stt = _pct(r, "stt", side, turnover)
    stamp = _pct(r, "stamp", side, turnover)
    gst = (brokerage + txn + sebi + ipft) * _d(r.get("gst_pct") or 0, "gst_pct") / Decimal("100")
    dp = _d(r.get("dp_per_sell") or 0, "dp_per_sell") if side == "sell" else Decimal("0")

    out = {"turnover": turnover, "brokerage": brokerage, "txn": txn, "sebi": sebi,
           "ipft": ipft, "stt": stt, "stamp": stamp, "gst": gst, "dp": dp}
    out = {k: v.quantize(P, ROUND_HALF_UP) for k, v in out.items()}
    out["total"] = sum((out[k] for k in ("brokerage", "txn", "sebi", "ipft", "stt",
                                         "stamp", "gst", "dp")), Decimal("0"))
    return out


def round_trip(entry, exit_price, quantity, product="equity_intraday", rates=None,
               short=False, entry_orders=1, exit_orders=1):
    """Both legs of a trade. short=True sells first, which moves STT onto the entry
    and stamp duty onto the exit."""
    r = rates or load_rates(None, product)
    first = "sell" if short else "buy"
    second = "buy" if short else "sell"
    a = leg_cost(first, quantity, entry, product, r, entry_orders)
    b = leg_cost(second, quantity, exit_price, product, r, exit_orders)
    return {"entry": a, "exit": b, "total": a["total"] + b["total"]}


def breakeven_move(entry, quantity, product="equity_intraday", rates=None, short=False):
    """How far price must move your way, per share, just to cover both legs.

    Solved at the entry price for both legs. The true cost at the exit price is
    slightly different, so this is an estimate, and a close one: the percentage
    charges on the exit leg scale with a price that has barely moved.
    """
    rt = round_trip(entry, entry, quantity, product, rates, short)
    q = _d(quantity, "quantity")
    return (rt["total"] / q).quantize(Decimal("0.0001"), ROUND_HALF_UP)


def net_reward_risk(entry, stop, target, quantity, product="equity_intraday",
                    rates=None, short=False):
    """Reward:risk after costs, which is the number that should drive the decision.

    Gross R treats the stop and target as symmetric around the entry. They are
    not, once costs land: the winner pays costs out of the reward and the loser
    pays them on top of the loss. Both move the ratio the same way, down.
    """
    e, s, t = _d(entry, "entry"), _d(stop, "stop"), _d(target, "target")
    q = _d(quantity, "quantity")
    if q <= 0:
        raise ChargeError("quantity must be above zero")
    sign = Decimal("-1") if short else Decimal("1")
    risk_ps = (e - s) * sign
    reward_ps = (t - e) * sign
    if risk_ps <= 0:
        raise ChargeError("a %s stop must sit %s the entry (entry %s, stop %s)"
                          % ("short" if short else "long",
                             "above" if short else "below", e, s))
    if reward_ps <= 0:
        raise ChargeError("a %s target must sit %s the entry (entry %s, target %s)"
                          % ("short" if short else "long",
                             "below" if short else "above", e, t))
    r = rates or load_rates(None, product)
    win = round_trip(e, t, q, product, r, short)["total"]
    lose = round_trip(e, s, q, product, r, short)["total"]
    gross_win = reward_ps * q
    gross_lose = risk_ps * q
    net_win = gross_win - win
    net_lose = gross_lose + lose
    return {
        "gross_reward": gross_win.quantize(P, ROUND_HALF_UP),
        "gross_risk": gross_lose.quantize(P, ROUND_HALF_UP),
        "gross_rr": (gross_win / gross_lose).quantize(Decimal("0.01"), ROUND_HALF_UP),
        "costs_on_win": win, "costs_on_loss": lose,
        "net_reward": net_win.quantize(P, ROUND_HALF_UP),
        "net_risk": net_lose.quantize(P, ROUND_HALF_UP),
        "net_rr": (net_win / net_lose).quantize(Decimal("0.01"), ROUND_HALF_UP),
        "breakeven_per_share": breakeven_move(e, q, product, r, short),
    }


# ---------------------------------------------------------------- money, written the Indian way

def indian_group(digits):
    """"1234567" -> "12,34,567". Last three digits, then pairs."""
    s = str(digits)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts + [tail])


def inr(amount, paise=True):
    """A rupee amount in Indian digit grouping: inr(1234567.5) -> "₹12,34,567.50"."""
    v = _d(amount, "an amount").quantize(P, ROUND_HALF_UP)
    sign = "-" if v < 0 else ""
    v = abs(v)
    whole = int(v)
    frac = (v - whole).quantize(P, ROUND_HALF_UP)
    body = indian_group(whole)
    if paise:
        body += ("%.2f" % frac)[1:]
    return "%s₹%s" % (sign, body)


def explain(side, quantity, price, product="equity_intraday", rates=None, orders=1):
    """Human-readable breakdown of one leg, for a SKILL.md or a ticket."""
    c = leg_cost(side, quantity, price, product, rates, orders)
    lines = ["%s %s at %s  turnover %s" % (side.upper(), indian_group(int(_d(quantity))),
                                           inr(price), inr(c["turnover"]))]
    names = [("brokerage", "brokerage"), ("stt", "STT"), ("txn", "exchange txn"),
             ("sebi", "SEBI fee"), ("ipft", "IPFT"), ("stamp", "stamp duty"),
             ("gst", "GST 18%"), ("dp", "DP charge")]
    for key, label in names:
        if c[key]:
            lines.append("  %-14s %s" % (label, inr(c[key])))
    lines.append("  %-14s %s" % ("total", inr(c["total"])))
    return "\n".join(lines)

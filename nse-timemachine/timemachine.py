#!/usr/bin/env python3
"""Time Machine for nse-timemachine: replay the rest of the session before you
risk a rupee. Standard library only, Python 3.8+.

    python3 timemachine.py run --symbol RELIANCE --entry 1324.40 --stop 1318.50 \
        --target 1330.30 --qty 100 --at 10:00
    python3 timemachine.py run --plan trader/plan.json --trials 10000 --seed 19
    python3 timemachine.py run --offline tests/fixtures/nse-timemachine --symbol DEMO ...

What it does, in two independent passes over the same bars:

  bootstrap   Draws whole 5-minute bars from the symbol's own recent history,
              matched BY TIME OF DAY. The 11:05 bar of a simulated session is
              drawn from real 11:05 bars, because an Indian session is not
              stationary: the first and last half hours carry most of the range
              and the 12:30 lull carries almost none. Sampling uniformly across
              the day would quietly import lunchtime volatility into the open.
              Each drawn bar keeps its own open/high/low/close shape, so the
              intrabar range that decides a stop survives the draw.
  history     Re-runs the identical rule on every real past session: same entry
              time of day, same stop and target as percentage distances. No
              simulation, just what happened.

Both report first touch: target first, stop first, or neither by square-off.

Square-off is 15:15 IST, not the 15:30 close, because Indian brokers flatten
intraday positions themselves at about 15:20 at market. A replay that holds to
15:30 is scoring a position the broker would already have closed.

Costs are applied. A gross expectancy is close to meaningless here: an intraday
round trip pays STT on the sell, stamp duty on the buy, exchange and SEBI fees,
brokerage and GST, and those land hardest on the small-stop trades that look
best gross. The verdict line is net.

Ambiguous bars are counted, not hidden. When one bar's range contains both the
stop and the target, OHLC cannot say which came first, and no amount of
arithmetic recovers it. Those bars resolve to the STOP, the pessimistic
reading, and the count is printed so you can see how much of the answer rests
on that assumption. If it is a large share of outcomes, the honest conclusion
is that this stop and target are too close together to call from 5-minute bars.

It is a simulation, not a prediction. It is built from recent bars, so it
cannot produce what those bars never did: a results announcement, a circuit
halt, a block deal, a gap. Treat the odds as a reality check, never a promise.

Not financial advice. Nothing here can place an order: no order leaves this
machine without nse-risk passing it and you typing yes.
"""

import argparse
import datetime
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import charges  # noqa: E402  (vendored; see tools/vendor.py)
import nsecal   # noqa: E402
import nsedata  # noqa: E402

TimeMachineError = nsedata.DataError


# ---------------------------------------------------------------- bars

def load_bars(symbol, offline=None, days=60, interval="5m"):
    """{session date: [bar, ...]} with bars on the IST grid, oldest first."""
    if offline:
        name = "chart-%s-%dd-%s.json" % (nsedata.to_yahoo(symbol).replace("^", "idx-"),
                                         days, interval)
        path = os.path.join(offline, name)
        if not os.path.exists(path):
            alt = [f for f in sorted(os.listdir(offline))
                   if f.startswith("chart-%s-" % nsedata.to_yahoo(symbol).replace("^", "idx-"))]
            if not alt:
                raise TimeMachineError("no saved chart for %s in %s" % (symbol, offline))
            path = os.path.join(offline, alt[-1])
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    else:
        text = nsedata.yahoo_get(nsedata.chart_path(symbol, "%dd" % days, interval))
    ser = nsedata.series_from_chart(text)
    minutes = int(interval.rstrip("m")) if interval.endswith("m") else 5
    by_day = {}
    for i, ts in enumerate(ser["t"]):
        if ser["c"][i] is None or ser["o"][i] is None:
            continue
        local = nsecal.to_ist(nsedata_utc(ts))
        slot = nsecal.slot_of(local, minutes)
        if slot is None:
            continue  # off the grid: Yahoo's partial live tick, or outside the session
        by_day.setdefault(local.date(), []).append({
            "slot": slot, "time": local, "o": ser["o"][i], "h": ser["h"][i],
            "l": ser["l"][i], "c": ser["c"][i]})
    for d in by_day:
        by_day[d].sort(key=lambda b: b["slot"])
    return by_day, ser.get("meta") or {}


def nsedata_utc(ts):
    return nsecal.utc_of(ts)


def shapes_by_slot(by_day, exclude=None):
    """{slot: [(h_rel, l_rel, c_rel), ...]} from every session but `exclude`.

    Each bar is normalised to its own open, so a draw transplants the bar's
    shape and range onto whatever price the walk has reached, rather than its
    absolute level.
    """
    pool = {}
    for day, bars in by_day.items():
        if exclude and day == exclude:
            continue
        for b in bars:
            o = b["o"]
            if not o or b["h"] is None or b["l"] is None or b["c"] is None:
                continue
            pool.setdefault(b["slot"], []).append(
                ((b["h"] - o) / o, (b["l"] - o) / o, (b["c"] - o) / o))
    return pool


# ---------------------------------------------------------------- first touch

def first_touch(price_open, shape, entry, stop, target, short=False):
    """One bar -> "target", "stop", "both" or None, plus the bar's close.

    "both" is the honest answer when the bar's range spans the stop and the
    target: an OHLC bar records no sequence, so which came first is simply not
    in the data.
    """
    h_rel, l_rel, c_rel = shape
    hi = price_open * (1.0 + h_rel)
    lo = price_open * (1.0 + l_rel)
    close = price_open * (1.0 + c_rel)
    if short:
        hit_target = lo <= target
        hit_stop = hi >= stop
    else:
        hit_target = hi >= target
        hit_stop = lo <= stop
    if hit_target and hit_stop:
        return "both", close
    if hit_target:
        return "target", close
    if hit_stop:
        return "stop", close
    return None, close


def walk(entry, stop, target, start_slot, end_slot, draw, short=False):
    """One simulated remainder of a session. draw(slot) -> a bar shape."""
    price = entry
    ambiguous = 0
    for slot in range(start_slot, end_slot + 1):
        shape = draw(slot)
        if shape is None:
            continue
        hit, close = first_touch(price, shape, entry, stop, target, short)
        if hit == "both":
            ambiguous += 1
            return "stop", slot, ambiguous      # documented pessimistic resolution
        if hit:
            return hit, slot, ambiguous
        price = close
    return "neither", end_slot, ambiguous


# ---------------------------------------------------------------- the two passes

def bootstrap(by_day, entry, stop, target, start_slot, end_slot, trials, seed, short=False):
    pool = shapes_by_slot(by_day)
    if not pool:
        raise TimeMachineError("no usable bars to draw from")
    rng = random.Random(seed)
    slots = sorted(pool)

    def draw(slot):
        bag = pool.get(slot)
        if not bag:
            # A slot with no history (a halt across every session) falls back to the
            # nearest slot that has one, rather than skipping and shortening the day.
            near = min(slots, key=lambda s: abs(s - slot))
            bag = pool.get(near)
        return rng.choice(bag) if bag else None

    counts = {"target": 0, "stop": 0, "neither": 0}
    ambiguous = 0
    for _ in range(trials):
        outcome, _slot, amb = walk(entry, stop, target, start_slot, end_slot, draw, short)
        counts[outcome] += 1
        ambiguous += amb
    return counts, ambiguous, len(pool)


def history(by_day, entry, stop, target, start_slot, end_slot, short=False):
    """Re-run the rule on real sessions. The stop and target are carried across as
    percentage distances from the entry, since a price from six weeks ago means
    nothing in rupees."""
    stop_pct = (stop - entry) / entry
    target_pct = (target - entry) / entry
    counts = {"target": 0, "stop": 0, "neither": 0}
    ambiguous, fired = 0, []
    for day in sorted(by_day):
        bars = {b["slot"]: b for b in by_day[day]}
        start = bars.get(start_slot)
        if not start or not start.get("c"):
            continue
        e = start["c"]
        s, t = e * (1.0 + stop_pct), e * (1.0 + target_pct)
        price = e
        outcome = "neither"
        for slot in range(start_slot + 1, end_slot + 1):
            b = bars.get(slot)
            if not b or b["o"] is None:
                continue
            shape = ((b["h"] - b["o"]) / b["o"], (b["l"] - b["o"]) / b["o"],
                     (b["c"] - b["o"]) / b["o"])
            hit, close = first_touch(price, shape, e, s, t, short)
            if hit == "both":
                ambiguous += 1
                outcome = "stop"
                break
            if hit:
                outcome = hit
                break
            price = close
        counts[outcome] += 1
        fired.append((day.isoformat(), outcome))
    return counts, ambiguous, fired


# ---------------------------------------------------------------- expectancy

def expectancy(counts, entry, stop, target, qty, product, rates, short=False):
    """Net rupees per trade, costs included on every path.

    A gross expectancy flatters a tight stop: the costs are near enough fixed per
    round trip, so the smaller the stop the larger the share of the move they eat.
    """
    total = sum(counts.values()) or 1
    win_costs = charges.round_trip(entry, target, qty, product, rates, short)["total"]
    lose_costs = charges.round_trip(entry, stop, qty, product, rates, short)["total"]
    flat_costs = charges.round_trip(entry, entry, qty, product, rates, short)["total"]
    sign = -1.0 if short else 1.0
    gross_win = (target - entry) * sign * qty
    gross_lose = (entry - stop) * sign * qty
    net_win = gross_win - float(win_costs)
    net_lose = gross_lose + float(lose_costs)
    net_flat = -float(flat_costs)
    p = {k: counts[k] / total for k in counts}
    gross = p["target"] * gross_win - p["stop"] * gross_lose
    net = p["target"] * net_win - p["stop"] * net_lose + p["neither"] * net_flat
    return {"p": p, "gross_per_trade": gross, "net_per_trade": net,
            "net_win": net_win, "net_loss": net_lose, "net_flat": net_flat,
            "costs_win": float(win_costs), "costs_loss": float(lose_costs),
            "costs_flat": float(flat_costs)}


# ---------------------------------------------------------------- command

def read_plan(path):
    if not os.path.exists(path):
        raise TimeMachineError("no plan at %s" % path)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def cmd_run(a):
    plan = read_plan(a.plan) if a.plan else {}
    symbol = (a.symbol or plan.get("symbol") or "").strip().upper()
    if not symbol:
        raise TimeMachineError("which symbol? Pass --symbol or --plan.")
    entry = a.entry if a.entry is not None else nsedata.num(plan.get("entry"))
    stop = a.stop if a.stop is not None else nsedata.num(plan.get("stop"))
    target = a.target if a.target is not None else nsedata.num(plan.get("target"))
    qty = a.qty or int(nsedata.num(plan.get("quantity"), 0) or 0)
    if None in (entry, stop, target) or not qty:
        raise TimeMachineError("need --entry, --stop, --target and --qty (or a plan carrying them)")
    short = bool(a.short or plan.get("side", "").upper() == "SELL")
    if not short and not (stop < entry < target):
        raise TimeMachineError("a long wants stop < entry < target, got %s / %s / %s"
                               % (stop, entry, target))
    if short and not (target < entry < stop):
        raise TimeMachineError("a short wants target < entry < stop, got %s / %s / %s"
                               % (target, entry, stop))

    now = nsecal.parse_ist(a.now, "--now") if a.now else nsecal.ist_now()
    day = now.date()
    at = a.at or plan.get("at")
    if at:
        now = nsecal.parse_ist("%s %s" % (day.isoformat(), at), "--at")
    entry_slot = nsecal.slot_of(now)
    if entry_slot is None:
        base = datetime.datetime.combine(day, nsecal.SESSION_OPEN)
        mins = int((now - base).total_seconds() // 60)
        entry_slot = max(0, min(nsecal.BARS_PER_DAY - 1, mins // nsecal.BAR_MINUTES))
    square_off = datetime.datetime.combine(day, a.flat_by and
                                           nsecal.parse_ist("%s %s" % (day.isoformat(), a.flat_by),
                                                            "--flat-by").time() or nsecal.SQUARE_OFF)
    end_slot = max(entry_slot, (int((square_off - datetime.datetime.combine(
        day, nsecal.SESSION_OPEN)).total_seconds()) // 60) // nsecal.BAR_MINUTES - 1)

    by_day, meta = load_bars(symbol, a.offline, a.days)
    synthetic = bool(meta.get("_synthetic"))
    by_day = {d: b for d, b in by_day.items() if d != day}
    if not by_day:
        raise TimeMachineError("no past sessions of 5-minute bars for %s" % symbol)
    rates = charges.load_rates(a.config, a.product)

    mc, mc_amb, slots_seen = bootstrap(by_day, entry, stop, target, entry_slot, end_slot,
                                       a.trials, a.seed, short)
    hist, hist_amb, fired = history(by_day, entry, stop, target, entry_slot, end_slot, short)
    mc_exp = expectancy(mc, entry, stop, target, qty, a.product, rates, short)
    hist_exp = expectancy(hist, entry, stop, target, qty, a.product, rates, short)
    rr = charges.net_reward_risk(entry, stop, target, qty, a.product, rates, short)

    out = {
        "symbol": symbol, "day": day.isoformat(), "side": "SELL" if short else "BUY",
        "entry": entry, "stop": stop, "target": target, "quantity": qty,
        "entry_at": now.strftime("%H:%M"), "square_off": square_off.strftime("%H:%M"),
        "bars_to_square_off": end_slot - entry_slot + 1,
        "sessions": len(by_day), "slots_with_history": slots_seen,
        "synthetic_bars": synthetic,
        "trials": a.trials, "seed": a.seed,
        "bootstrap": {"counts": mc, "p": mc_exp["p"], "ambiguous_bars": mc_amb,
                      "gross_per_trade": round(mc_exp["gross_per_trade"], 2),
                      "net_per_trade": round(mc_exp["net_per_trade"], 2)},
        "history": {"counts": hist, "p": hist_exp["p"], "ambiguous_bars": hist_amb,
                    "sessions_fired": sum(hist.values()),
                    "gross_per_trade": round(hist_exp["gross_per_trade"], 2),
                    "net_per_trade": round(hist_exp["net_per_trade"], 2),
                    "days": fired if a.days_detail else None},
        "costs": {"on_win": mc_exp["costs_win"], "on_loss": mc_exp["costs_loss"],
                  "on_flat": mc_exp["costs_flat"],
                  "breakeven_per_share": float(rr["breakeven_per_share"])},
        "reward_risk": {"gross": float(rr["gross_rr"]), "net": float(rr["net_rr"])},
    }
    if not a.no_write:
        d = os.path.dirname(a.out)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2)
    if a.json:
        print(json.dumps(out, indent=2))
        return 0

    if synthetic:
        print("NOTE: these bars are SYNTHETIC (%s). The numbers below exercise the method, "
              "they say nothing about this symbol."
              % (meta.get("_note") or "generated fixture"))
    print("%s %s  %d sessions of 5-minute bars, entry slot %s, square-off %s (%d bars)."
          % (symbol, out["side"], len(by_day), now.strftime("%H:%M"),
             square_off.strftime("%H:%M"), out["bars_to_square_off"]))
    print("  entry %s  stop %s  target %s  qty %s"
          % (charges.inr(entry), charges.inr(stop), charges.inr(target),
             charges.indian_group(qty)))
    print("  reward:risk  %.2f gross, %.2f after costs" % (out["reward_risk"]["gross"],
                                                           out["reward_risk"]["net"]))
    print("  price must move %s a share just to cover both legs."
          % charges.inr(out["costs"]["breakeven_per_share"]))
    print("\n  bootstrap, %s trials, seed %d:" % (charges.indian_group(a.trials), a.seed))
    _print_pass(mc, mc_exp, mc_amb, a.trials)
    print("\n  history, the same rule on %d past sessions:" % sum(hist.values()))
    _print_pass(hist, hist_exp, hist_amb, sum(hist.values()) or 1)
    print("\n  %s" % verdict(mc_exp, hist_exp, mc_amb, a.trials, out["bars_to_square_off"]))
    if not a.no_write:
        print("  wrote %s." % a.out)
    return 0


def _print_pass(counts, exp, ambiguous, total):
    print("    target first %5.1f%%   stop first %5.1f%%   open at square-off %5.1f%%"
          % (100.0 * exp["p"]["target"], 100.0 * exp["p"]["stop"], 100.0 * exp["p"]["neither"]))
    print("    per trade    %s gross, %s net"
          % (charges.inr(exp["gross_per_trade"]), charges.inr(exp["net_per_trade"])))
    if ambiguous:
        print("    %d of %d outcomes rested on a bar holding both the stop and the target, "
              "resolved to the stop." % (ambiguous, total))


def verdict(mc_exp, hist_exp, ambiguous, trials, bars):
    net = mc_exp["net_per_trade"]
    hnet = hist_exp["net_per_trade"]
    agree = (net > 0) == (hnet > 0)
    if ambiguous > 0.25 * trials:
        return ("Too close to call. More than a quarter of simulated outcomes hinged on a bar "
                "that held both the stop and the target, which 5-minute bars cannot resolve. "
                "Widen the gap or drop to 1-minute bars before trusting any of these numbers.")
    if not agree:
        return ("The simulation and the real sessions disagree on the sign of the edge "
                "(%s simulated against %s historical). That is a reason to pass, not to pick "
                "the friendlier one." % (charges.inr(net), charges.inr(hnet)))
    if net > 0 and hnet > 0:
        return ("Both passes are net positive after costs (%s simulated, %s historical over the "
                "same rule). That is an edge in this sample, not a promise about tomorrow."
                % (charges.inr(net), charges.inr(hnet)))
    return ("Net negative after costs on both passes (%s simulated, %s historical). The gross "
            "numbers may look fine; the costs are what turn this one down."
            % (charges.inr(net), charges.inr(hnet)))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="timemachine.py", description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("run", help="replay the rest of the session")
    p.add_argument("--symbol")
    p.add_argument("--plan", help="trader/plan.json, for entry/stop/target/qty")
    p.add_argument("--entry", type=float)
    p.add_argument("--stop", type=float)
    p.add_argument("--target", type=float)
    p.add_argument("--qty", type=int)
    p.add_argument("--short", action="store_true")
    p.add_argument("--at", metavar="HH:MM", help="entry time of day, IST")
    p.add_argument("--flat-by", metavar="HH:MM", help="square-off (default 15:15 IST)")
    p.add_argument("--now", help='pretend it is this IST time')
    p.add_argument("--days", type=int, default=60, help="sessions of history to draw from")
    p.add_argument("--trials", type=int, default=10000)
    p.add_argument("--seed", type=int, default=19)
    p.add_argument("--product", default="equity_intraday", choices=list(charges.RATES))
    p.add_argument("--config", default="trader/config.json")
    p.add_argument("--out", default="trader/timemachine.json")
    p.add_argument("--days-detail", action="store_true", help="list every historical session")
    p.add_argument("--offline", metavar="DIR")
    p.add_argument("--no-write", action="store_true")
    p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return cmd_run(a)
    except (TimeMachineError, charges.ChargeError, nsecal.CalendarError) as e:
        print("timemachine.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

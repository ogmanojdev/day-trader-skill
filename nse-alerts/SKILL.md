---
name: nse-alerts
description: Ring on a stop, a target or the 15:15 IST intraday square-off for NSE positions. Builds all three from a plan in one command, is calendar aware so an alert never fires on a holiday or a weekend, shortens the square-off to fit a Muhurat session, and names the source of every price so a stale settled close is never mistaken for a live one. Use when the user wants alerts on an Indian stock, a reminder to exit before the broker squares off, or to be told when a stop or target is hit.
---

# NSE Price Alerts

Three alerts matter for an Indian day trade: the stop, the target, and getting
flat before someone else does it for you.

## The tool

```bash
python3 alerts.py add --from-plan                      # all three at once
python3 alerts.py add --symbol RELIANCE --below 1318.5 --label "stop"
python3 alerts.py add --at 15:15 --label "square off" --square-off
python3 alerts.py list
python3 alerts.py check                                # rings what hit, once
python3 alerts.py check --price RELIANCE=1331.2        # price from your terminal
python3 alerts.py clear --fired
```

`check` prints only what changed and marks a rule fired, so it is safe to run
on a loop.

## The square-off alert

This is the one the US original cannot have. An Indian broker flattens
intraday (MIS) positions itself at about 15:20 IST, at market, whatever the
market happens to be paying at that moment. The default alert rings at **15:15**
to leave five minutes to exit on your own terms. Past that the exit is the
broker's.

```
RING  a3   square off RELIANCE at 15:15 IST (square-off)
      15:15 IST reached
      Your broker squares off MIS positions itself at about 15:20 at market.
      Exit now and the price is yours.
```

## The calendar matters

15:15 on a Sunday is not a square-off. Neither is 15:15 on
Diwali-Balipratipada, when the market never opened. An alert that fires on a
day the market was shut is worse than no alert, because it teaches you to
ignore the thing you installed to protect you. Time alerts check the exchange
calendar and skip with a reason.

A Muhurat session is the interesting case: the market is closed for the
holiday but a special hour-long window trades anyway, so the square-off moves
to fifteen minutes before *that* window's end rather than sitting two hours
past it.

## The honest limit on prices

Price alerts need a live price, and the only free intraday source for NSE is
Yahoo's chart endpoint, which throttles by IP. So `check` tries Yahoo, and when
that fails it falls back to the last settled bhavcopy close **and names the
source**:

```
RELIANCE at 1,331.00 (Yahoo 5m)
RELIANCE at 1,324.40 (settled close 2026-11-09)
```

The second is a yesterday's number and the output says so. A stale price
silently presented as live is how an alert system becomes actively harmful.

`--sanyam` takes the price from Sanyam's candle store instead, which needs no
network call and names the bar's source:

```bash
python3 alerts.py check --sanyam
```

`--price SYMBOL=PRICE` takes the number from your broker's terminal, which is
the one you should trust anyway, and `--no-fetch` runs time alerts only.

## Not a stop order

An alert is a reminder. It does not protect a position: your actual stop
belongs with your broker, where it executes without you watching. And a stop
is a plan, not a guarantee. In a fast market, or across a gap, or when a name
hits its circuit band, a stop can fill well past its price or not fill at all.

**Not financial advice.** Nothing in this skill can place an order: no order
reaches a broker without the risk engine passing that exact order and you
typing yes for it.

---
name: nse-timemachine
description: Replay an NSE intraday trade thousands of times before risking a rupee, net of Indian transaction costs. Bootstraps whole five-minute bars from the symbol's own history matched by time of day, re-runs the identical rule on every past session, and reports target-first, stop-first and still-open odds plus net expectancy after STT, stamp duty, exchange and SEBI fees, brokerage and GST. Squares off at 15:15 IST because brokers flatten MIS positions at about 15:20. Use when the user wants the odds on an Indian day trade, asks whether a setup is worth taking, or needs to know if costs kill an edge.
---

# NSE Time Machine

The question it answers: given this entry, this stop and this target, what
actually happened the last sixty times, and what would happen across ten
thousand simulated remainders of the session? And then the question that
matters more in India: does any of it survive costs?

## The tool

```bash
python3 timemachine.py run --symbol RELIANCE --entry 1324.40 --stop 1318.50 \
    --target 1330.30 --qty 100 --at 10:00
python3 timemachine.py run --plan trader/plan.json --trials 10000 --seed 19
python3 timemachine.py run --offline tests/fixtures/nse-timemachine --symbol DEMO ...
```

## Two independent passes

**bootstrap** draws whole five-minute bars from the symbol's own recent
history, matched *by time of day*. The 11:05 bar of a simulated session is
drawn from real 11:05 bars. This is not fussiness: an Indian session is not
stationary. The first and last half hours carry most of the range and the 12:30
lull carries almost none, so sampling uniformly across the day imports
lunchtime volatility into the open and open volatility into the lunch hour.
Each drawn bar keeps its own open, high, low and close shape, so the intrabar
range that actually decides whether a stop fills survives the draw.

**history** re-runs the identical rule on every real past session: same entry
time of day, same stop and target as percentage distances from the entry. No
simulation. Just what happened.

When the two disagree on the sign of the edge, that is a reason to pass, not a
reason to pick the friendlier number.

## Costs are the point

This is the single biggest difference from the US original, which computes a
target as "1R" and ignores costs because most US brokers charge no commission
and there is no transaction tax on an equity trade. Here an intraday round trip
pays STT on the sell leg, stamp duty on the buy leg, an exchange transaction
charge, a SEBI turnover fee, an investor-protection fee, brokerage, and 18% GST
on several of those.

Worked, on a 100-share position in a 1,324 rupee stock with a 5.90 rupee stop
and a 1:1 target:

```
reward:risk   1.00 gross,  0.72 after costs
price must move 0.94 a share just to cover both legs
bootstrap     target first 50.3%  stop first 49.5%
per trade     +4.54 gross,  -89.64 net
```

A coin flip at 1:1 is not a marginal trade here. It is a reliable loser. And
notice the asymmetry: STT lands on the sell, stamp duty on the buy, so costs
are not a percentage you can subtract from a return. They depend on direction,
product and how many orders filled.

The drag shrinks as the target widens. The same trade at 2R reads 2.00 gross
and 1.82 net, because costs are near enough fixed per round trip while the
reward is not. That is an argument for wider targets that gross arithmetic
cannot make.

## Square-off is 15:15, not the close

Indian brokers flatten intraday (MIS) positions themselves at about 15:20 IST
at market price. A replay that holds to the 15:30 close is scoring a position
the broker would already have closed, at a price you did not choose. Sanyam
uses the same 15:15 in its strategy engine.

## Ambiguous bars are counted, not hidden

When one bar's range contains both the stop and the target, OHLC records no
sequence and which came first is simply not in the data. Those resolve to the
**stop**, the pessimistic reading, and the count is printed. If a large share of
outcomes rest on that assumption the verdict says so outright, because the
honest conclusion is that the stop and target are too close together to call
from five-minute bars at all.

## It is a simulation

Built from recent bars, so it cannot produce what those bars never did: a
results filing, a circuit halt, a block deal, an overnight gap. Treat the odds
as a reality check, never a promise. The seed is printed so any run can be
reproduced exactly.

## Data

Needs five-minute bars, which means Yahoo, which throttles by IP hard. Use
`--offline` with saved charts for repeatable work. The bundled fixture is
clearly marked synthetic in its own metadata and the tool prints a warning when
it is reading one, because numbers from generated bars exercise the method and
say nothing whatever about the symbol.

**Not financial advice.** Most people who day trade lose money, and a
favourable simulation does not change that. Nothing in this skill can place an
order: no order reaches a broker without the risk engine passing that exact
order and you typing yes for it.

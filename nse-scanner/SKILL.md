---
name: nse-scanner
description: Scan the whole NSE cash market for intraday setups from the exchange's own data. Builds the tradeable universe from EQUITY_L.csv (series EQ only), finds daily setups across roughly 2,400 names from one bhavcopy request including delivery-percentage surges that no US feed can produce, flags price bands and ASM surveillance names whose stops cannot fill, and reports opening-range breakouts on the 09:15 to 15:30 IST five-minute grid. Use when the user wants to find Indian stocks setting up for a day trade, scan NSE for breakouts, check which names are at circuit, or build a watchlist before the open.
---

# NSE Chart Scanner

Finds candidates. Decides nothing. Every number here comes from the exchange or
from Yahoo's chart endpoint, and both can be wrong or late.

## The tool

```bash
python3 scan.py universe                    # EQUITY_L.csv -> trader/universe.txt
python3 scan.py daily                       # whole market -> trader/watchlist.json
python3 scan.py daily --min-turnover 500 --limit 40
python3 scan.py orb --at 10:00              # opening-range breakouts so far
python3 scan.py bands                       # price bands + ASM -> trader/bands.json
```

Run `universe` once a week, `daily` after about 18:30 IST when the bhavcopy
lands or before the next open, `orb` during the session, `bands` whenever you
want the current surveillance picture.

## Why the daily scan reads the bhavcopy, not a chart API

The US original fetches Yahoo's spark endpoint twenty symbols at a time, which
is about 140 requests for a market this size, and Yahoo throttles by IP hard
enough that the scan can simply fail. NSE publishes `sec_bhavdata_full`, the
settled end-of-day record for every security, as one 400 KB file. One request
covers roughly 2,700 EQ names. It is official rather than unofficial, it is the
price that actually settled, and it carries two columns no US feed has:

```
DELIV_QTY   shares actually delivered
DELIV_PER   that as a percentage of volume
```

Delivery percentage is the signal to understand here. Indian intraday volume is
heavily churned, so a breakout on 70% delivery is being bought by people taking
the shares home, while the same breakout on 12% delivery is churn that tends to
give the move back. `delivery_surge` fires when delivery is at least 1.5 times
its own 20-day average and above 50% on an up close.

The scan caches each day's bhavcopy under `trader/cache/`, so the first run
costs 25 requests and every later run costs one.

## What it refuses to scan, and why

**Series.** Only `EQ` survives. `BE` and `BZ` are trade-for-trade with 100%
margin and no intraday netting, so there is no day trade to be had. `SM` and
`ST` are the SME platform, `GS` is government securities.

**ETFs and index funds.** They sit in series EQ and nothing in the bhavcopy
marks them, but their delivery percentage is structurally near 100% because
units are created and redeemed rather than churned. Left in, `delivery_surge`
fires on every gold and index fund every day. Most are excluded for free
because NSE lists them outside EQUITY_L.csv, which is why the daily scan loads
the universe at all.

**Band-locked and ASM names.** A price band is the furthest price may move from
the previous close before the exchange stops accepting orders through it, 2%,
5%, 10% or the standard 20%. This has no US analogue and it decides whether
your stop can fill: a stop beyond the band is not a wide stop, it is an order
that cannot execute. On a 2% band name an intraday stop has almost no room. An
ASM name can carry 100% margin. Both are dropped from `daily` and reported.

## The opening range

NSE trades 09:15 to 15:30 IST, which is 375 minutes, exactly 75 five-minute
bars. The opening range is the first `--or-minutes` (15) of that, so 09:15 to
09:30. A breakout is the first *completed* bar to close above the range high.

Three kinds of bar are dropped, each for a reason that bites in live data: a
null close (a halt or no trades), a bar off the five-minute grid (Yahoo's
partial live tick, like 10:49:45), and a bar whose window has not closed yet.
If the range itself has a gap the symbol is skipped, because the range is not
knowable.

### Where the bars come from

`orb` has three sources and they all go through the same grid checks, so the
answer does not depend on which one replied:

```bash
python3 scan.py orb --sanyam                 # Sanyam's candle store, no network
python3 scan.py orb --sanyam /path/to/db     # or point at it explicitly
python3 scan.py orb                          # Yahoo, the fallback
python3 scan.py orb --offline DIR            # saved charts
```

**Prefer `--sanyam`.** Yahoo is the only free source of NSE intraday bars and it
throttles by IP hard: about a dozen chart calls in a burst earns a 429 on every
endpoint for hours, which is long enough to miss the session you were trying to
trade. Sanyam already holds the broker's own bars in SQLite, so there is no
network call to throttle. Read-only, via `sqlite3`, so a running Sanyam is never
blocked. On the real TCS session in the fixtures, Sanyam's HISTORY bars give a
byte-identical opening range to the Yahoo chart.

Sanyam stores `1m` as its base interval, so `5m` is derived when absent, on the
IST grid.

### One source of bars is refused on purpose

Sanyam labels every bar with where it came from, and they are not equally
trustworthy. `HISTORY` and `HISTORY_DB` are the broker's own bars with true
extremes. `LIVE_AGG` is aggregated from a roughly 1 Hz last-traded-price poll,
so it **misses true intrabar extremes**.

That is fatal for an opening range specifically, because the range is the
highest high and lowest low of the first three bars, not their closes. On that
TCS session the 09:15 bar closed at 2456.70 and reached 2495.60: the high is 39
rupees above the close. A once-a-second poll misses a spike like that, the range
comes out **36% narrower**, and a close that is actually inside the real range
reads as a breakout. So `LIVE_AGG` bars are refused with an explanation.
`--allow-live-agg` overrides it if you know what you are accepting.

When no store is available, `orb` falls back to Yahoo and fails fast on a
throttle: one backoff and a clear message, not one per symbol. Keep
`--max-charts` small and let `--pause` do its work.

## The calendar is the exchange's

A holiday is a holiday. 2026-11-10 is Diwali-Balipratipada and the market was
shut, so the scanner says so and scans the previous session instead. The table
ends at a published date and anything past it is reported unknown rather than
guessed, because treating an unpublished holiday as a trading day silently
scores a day that never traded.

## A trap worth knowing

The bhavcopy filename is not authoritative. Ask the archive for a date it has
no file for and it answers `200 OK` with the most recent file it does have, so
a request for 10 Nov returns 09 Nov data with no warning at all. Every fetch
checks the `DATE1` column inside the file against the date requested and
refuses a mismatch. Do not route around that check.

## Reading the output

```
HDFCBANK    798.15  breakout_20d          deliv 63%  RSI 77
SAILIFE    1836.80  breakout_20d          deliv 14%  RSI 79
```

Same setup, different quality: 63% delivery against 14%. The scanner will not
rank that for you.

## Then what

`nse-news` on the names you like, to see whether something explains the move
and whether results are due. `nse-timemachine` before you commit, because a
setup with a 1.0 reward-to-risk is a losing trade here once costs land. Then
Sanyam's risk engine, which sizes and blocks, and which this skill never
bypasses.

**Not financial advice.** A setup is a chart that looks a certain way. Most
people who day trade lose money. Nothing in this skill can place an order:
no order reaches a broker without the risk engine passing that exact order and
you typing yes for it.

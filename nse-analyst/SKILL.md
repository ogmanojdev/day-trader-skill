---
name: nse-analyst
description: Read an NSE company's filed quarterly results from the exchange and show what changed. Converts the lakh figures NSE publishes into crore, checks that unit against the filing's own EPS so a hundredfold error cannot pass silently, never mixes consolidated and standalone filings in a growth number, and builds a current results calendar from board-meeting intimations rather than the exchange's stale results endpoint. Use when the user asks about an Indian company's earnings, revenue or profit growth, or whether results are due before an intraday trade.
---

# NSE Earnings Analyst

Filed figures with arithmetic on top. No estimates, no vendor data.

## The tool

```bash
python3 facts.py RELIANCE
python3 facts.py TCS --quarters 8 --json
python3 facts.py RELIANCE --announcements 10
python3 facts.py --calendar --back 5 --days 7     # who just filed or has a board meeting
```

## What it reads

The US original reads SEC XBRL company facts. There is no EDGAR here. SEBI's
LODR regulations put results on the exchange, so `top-corp-info` is the primary
source: filed quarterly results, board meetings, corporate actions and the
shareholding pattern, per symbol.

```
period end   basis            income          PAT   margin QoQ PAT YoY PAT     EPS
2026-09-30   standalone  1,78,567 cr    15,354 cr    8.6%  +15.7%  +68.2%   11.34
2026-06-30   standalone  1,70,121 cr    13,272 cr    7.8%  +78.8%       -    9.81

unit check: PAT divided by EPS implies about 1,354 crore shares outstanding.
```

## Two traps that produce confidently wrong answers

**Units.** NSE publishes these figures in **lakh of rupees**. Read as thousands,
the same RELIANCE filing makes it a 1,786 crore-revenue company earning 154
crore, which is wrong by a factor of a hundred and still looks like a perfectly
plausible mid-cap. Two things guard against that, and they do different jobs.

Every run prints an implied share count: profit in crore divided by diluted
EPS. RELIANCE comes out at 1,354 crore shares, which is what it has. That is a
floor against nonsense, not a proof, and the reason is worth knowing: a uniform
scale error cannot be caught from inside a single filing. EPS is published in
rupees whatever unit the totals use, and income over profit is scale-free, so
nothing in the document changes when every total is multiplied by a hundred.
The hundredfold error implies 13.5 crore shares, which is ordinary for a small
cap and passes quietly.

The real guard is a test pinned to a known filing, asserting that RELIANCE's
September 2026 quarter reads 1,78,567 crore of income and 15,354 crore of
profit. Those are the published numbers. If the exchange changes its unit, the
suite fails rather than every answer quietly rescaling.

**Basis.** Consolidated and standalone filings sit in the same list with no
ordering guarantee. Comparing a consolidated quarter against a standalone one
manufactures growth that never happened. Rows are grouped by basis and a growth
number is only ever computed within one. A quarter with no comparable
predecessor prints a dash rather than a guess.

## The calendar reads intimations, not the results feed

`/api/corporates-financial-results` looks exactly like the market-wide results
feed. It answers `200 OK` with 3,816 rows. It is a frozen snapshot: 2,538 of
those were broadcast in February 2025 and RELIANCE's newest entry is dated
16 January 2025, roughly twenty-two months stale. Pass it a date range and it
returns one row. A calendar built on it reports that nobody is reporting this
quarter, confidently and wrongly.

So `--calendar` reads the corporate-announcements feed with a date range, which
is current, and filters for board meetings and results. An Indian
board-meeting intimation is the forward signal worth having: a company must
tell the exchange before the meeting that approves results, so the intimation
arrives days ahead of the numbers.

## Why a day trader cares

A results filing is the most dangerous thing to hold an intraday position
through. The move is a gap, not a trend, and no stop protects against it. A day
trade that is flat by 15:15 is not exposed to one, which is the main reason to
check before the open rather than after.

The corporate-actions list matters for the same reason: an ex-dividend or
bonus date changes the price without anything happening to the company, and a
chart that does not know about a 1:1 bonus shows a 50% crash.

**Not financial advice.** These are filed figures and arithmetic. They say
nothing about what the stock will do. Nothing in this skill can place an order:
no order reaches a broker without the risk engine passing that exact order and
you typing yes for it.

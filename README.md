# The NSE Day Trader skill

Six Claude skills for day trading Indian equities: scan the whole NSE cash
market from the exchange's own data, read the filing feed that moves a stock,
replay a trade before you risk a rupee, and see what it costs after STT.

An adaptation of [the Day Trader skill](https://github.com/Jakeschincariol/day-trader-skill)
for NSE. The original is excellent and entirely US-shaped: a New York clock
with daylight saving, Nasdaq Trader's symbol lists, SEC EDGAR, the House Clerk,
a 15:55 flat-by, the pattern-day-trader rule, and targets computed as "1R"
because a US equity trade is close to free. None of that is true here, and the
last one is not a detail. It is the difference between a winning rule and a
losing one.

Free, MIT, no signup, no API key. Standard-library Python, 3.8 or newer.

## Install

```bash
git clone https://github.com/ogmanojdev/day-trader-skill.git
cp -r day-trader-skill/nse-* ~/.claude/skills/
```

Project-local instead of global: copy the same folders into your repo's
`.claude/skills/`. Each folder stands alone, so one skill can be copied without
the others. No Claude Code at all? Paste any single `SKILL.md` at the top of a
chat and it runs as a mode; you lose the Python tools and keep the method.

## The six

| command | job | what it does |
| --- | --- | --- |
| `/nse-scanner` | Chart Scanner | Scans ~2,400 EQ names from one bhavcopy request, including delivery-percentage surges, and refuses band-locked and ASM names. |
| `/nse-news` | News Monitor | The exchange filing feed plus India-localised press, tagged by what this market reacts to, timed in IST. |
| `/nse-timemachine` | Time Machine | Replays the rest of the session 10,000 times from the symbol's own bars, and tells you the odds after costs. |
| `/nse-alerts` | Price Alerts | Stop, target, and the 15:15 square-off before your broker does it for you. |
| `/nse-analyst` | Earnings Analyst | Filed quarterly results in crore, basis kept separate, results calendar from board-meeting intimations. |
| `/nse-deals` | Deals Tracker | Who bought, by name: bulk and block deals daily, plus SEBI insider filings. |

Five of the original eleven are deliberately absent. `trader-risk`,
`trader-budget`, `trader-broker`, `trader-exit` and `trader-entry` are already
implemented, better and in one place, inside [Sanyam](https://github.com/ogmanojdev/sanyam):
a risk engine with a kill switch and a cooling-off lock that no order can
bypass, protective stops, paper execution, Zerodha and Upstox adapters, and an
opening-range strategy. Porting them would have built a second, weaker risk
path beside the one that was made unbypassable on purpose. These six are the
research surface that feeds it; none of them can place an order.

`trader-pelosi` has no Indian counterpart at all: there is no STOCK Act, no
register of politicians' trades. `nse-deals` replaces it with something better
for a trader, because bulk and block deals are published daily rather than six
weeks late and they name the buyer.

## What actually had to change

**Costs, and this is the big one.** An Indian intraday round trip pays STT on
the sell leg, stamp duty on the buy leg, an exchange transaction charge, a SEBI
turnover fee, an investor-protection fee, brokerage, and 18% GST on several of
those. So:

```
100 shares at 1,324.40, stop 1,318.50, target 1,330.30
reward:risk   1.00 gross,  0.72 after costs
breakeven     0.94 a share, which is 16% of the risk
expectancy    +4.54 gross,  -89.64 net
```

A 1:1 coin flip is not marginal here. It is a reliable loser. The original's
`1R` default is wrong in India in a way that no amount of discipline fixes, and
`nse-common/charges.py` is the module that says so in rupees. Sanyam's
backtester documents the same hole at `backend/app/backtest/engine.py:22`
("no-costs"), which this closes in one place both can import.

**The clock.** 09:15 to 15:30 IST, 375 minutes, exactly 75 five-minute bars.
India has no daylight saving, so the original's nth-Sunday apparatus (copied
into nine files, two of which had already drifted) collapses to one constant.
Square-off is **15:15**, not 15:55, because brokers flatten MIS positions
themselves at about 15:20 at market.

**The calendar.** The exchange's own holiday list, including the case the US
has no shape for: Diwali Muhurat trading, an hour-long session on a day the
market is otherwise shut, which in 2026 fell on a Sunday. The table is dated
and ends where NSE's published list ends; past that, dates are reported unknown
rather than guessed.

**The data.** `EQUITY_L.csv` for the universe, `sec_bhavdata_full` for the
whole market's settled prices in one request, `sec_list.csv` for price bands,
`reportASM` for surveillance, `bulk.csv` and `block.csv` for deals,
`top-corp-info` for results, the corporate-announcements feed for filings, and
Yahoo only where intraday bars are unavoidable.

**Things with no US analogue.** Price bands, where a stop beyond the band is
not a wide stop but an order that cannot execute. ASM and trade-for-trade
series, where there is no intraday trade to be had. Delivery percentage, which
separates a breakout being bought from one being churned. Lot sizes. And no
pattern-day-trader rule, so that guard is simply gone.

## The tools

```bash
python3 nse-scanner/scan.py universe                      # EQUITY_L.csv -> trader/universe.txt
python3 nse-scanner/scan.py daily                         # whole market -> trader/watchlist.json
python3 nse-scanner/scan.py orb --at 10:00                # opening-range breakouts
python3 nse-scanner/scan.py bands                         # price bands + ASM
python3 nse-news/news.py --symbols RELIANCE --hours 12
python3 nse-news/news.py announcements --hours 24
python3 nse-timemachine/timemachine.py run --plan trader/plan.json
python3 nse-alerts/alerts.py add --from-plan
python3 nse-alerts/alerts.py check
python3 nse-analyst/facts.py RELIANCE
python3 nse-analyst/facts.py --calendar --back 5
python3 nse-deals/deals.py
python3 nse-deals/deals.py insider --days 30
```

Shared code lives in `nse-common/` and is copied into each skill folder by
`tools/vendor.py`, because a skill folder has to stand alone. The suite fails
if a copy drifts.

```bash
python3 -m unittest discover -s tests -v   # offline, on fixtures cut from real data
python3 tools/vendor.py --check
python3 tools/refresh_calendar.py          # when the holiday table runs out
python3 tools/capture_fixtures.py          # refresh the fixtures
```

## Traps found while building this

Each of these answers `200 OK` and is wrong, which is why each has a test.

**The bhavcopy filename is not authoritative.** Ask the archive for a date it
has no file for and it serves the most recent file it does have. A request for
10 Nov 2026 returned 09 Nov data, same status, same shape, no warning. Every
fetch checks the `DATE1` column inside the file.

**`/api/corporates-financial-results` is frozen.** It returns 3,816 rows and
looks like the market-wide results feed. 2,538 of them were broadcast in
February 2025 and RELIANCE's newest entry is dated 16 January 2025. A calendar
built on it reports that nobody is reporting this quarter.

**The announcements feed without a date range returns 20 rows.** Market wide.
On a busy evening that is about four minutes of news, and it will answer
"nothing happened" for whatever symbol you asked about. With the range: 3,775
rows for six days.

**NSE publishes results in lakh.** Read as thousands, RELIANCE becomes a 1,786
crore-revenue company earning 154 crore: wrong by a hundred and still
plausible. A uniform scale error cannot be detected from inside one filing, so
the unit is held by a test pinned to real published figures.

**ETFs trade in series EQ.** Nothing in the bhavcopy marks them and their
delivery percentage is structurally near 100%, so a delivery signal fires on
every gold and index fund every day.

**A holiday can still have a session.** Muhurat trading, so `is_trading_day`
and `session` have to disagree.

## Fine print

**Not financial advice.** This is software that reads public data and does
arithmetic. It does not know your situation and cannot see the future. Most
people who day trade lose money. You can lose money. Talk to a SEBI-registered
investment adviser if you need advice.

**Nothing here can place an order.** None of these six skills talks to a
broker. An order reaches one only through Sanyam's risk engine, which has to
pass that exact order, with you typing yes. Paper trading is the default and
live execution is off on every start.

**The cost rates are dated, not legislated.** They were read on 2026-11-10 from
NSE's schedule and a discount broker's calculator, and they move with exchange
and government circulars. Brokerage especially is yours, not a law. Everything
is overridable in `trader/config.json`. Verify against a real contract note
before trusting a number to the rupee.

**A stop is a plan, not a guarantee.** In a fast market, across a gap, or when
a name is at its circuit band, a stop can fill well past its price or not fill
at all. The band case is specific to India and it means a stop can be an order
the exchange will not accept.

**The Time Machine is a simulation.** Built from recent bars, so it cannot
produce what those bars never did: a results filing, a halt, a block deal, a
gap. Its odds are a reality check, never a promise.

**The data is free and partly unofficial.** The NSE archive files are the
exchange's own. Its JSON API is cookie gated and refuses some clients, so a
403 is normal and the tools degrade rather than crash. Yahoo's chart endpoint
is not an official API and throttles by IP hard. Check anything that matters
against your broker before acting on it.

**Bulk deals and insider filings are disclosures of the past.** Published after
the fact, by someone whose size and horizon are not yours. Not a tip, and
copying a fund is not a strategy.

**Taxes.** Intraday equity gains are speculative business income in India, not
capital gains, and the treatment differs from delivery trades. Nothing here is
tax advice.

MIT, as the original is. Adapted from Jake Schincariol's
[day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill).

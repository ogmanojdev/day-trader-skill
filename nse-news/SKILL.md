---
name: nse-news
description: Track what moved an NSE stock and when, in IST, from the exchange's own filing feed plus India-localised press. Reads NSE corporate announcements with a date range as the primary source, adds Google News India, Moneycontrol and Economic Times, resolves tickers to registered company names so searches actually match, tags filings by what the Indian market reacts to (results, order wins, promoter pledges, QIP, SEBI action, bonus and splits), and shows the settled move with delivery percentage. Use when the user asks why an Indian stock moved, wants news on NSE names, or needs to know if results are due before taking an intraday position.
---

# NSE News Monitor

Two kinds of news, and the difference matters more here than in the US.

## The tool

```bash
python3 news.py --symbols RELIANCE,SBIN --hours 12
python3 news.py --watchlist trader/watchlist.json --hours 6
python3 news.py announcements --hours 24        # the filing feed, market wide
python3 news.py --symbols SBIN --no-press       # filings only
```

## The exchange feed is the primary source

SEBI's disclosure rules put price-sensitive information on the exchange first,
by law. A results filing, an order win, a pledge, a board meeting outcome: it
lands in NSE's corporate-announcements feed and the press reports it second. So
that feed, not a news site, is what this skill reads first, with the exchange's
own dissemination timestamp.

The US original reads Yahoo's RSS headline feed and SEC 8-K filings. Neither
works here. Yahoo's RSS endpoint answers 429 for NSE symbols, and there is no
8-K to read.

One thing to get right: without `from_date` and `to_date` the feed returns only
the latest 20 filings market wide. On a busy evening that is about four minutes
of news, and it will cheerfully answer "nothing happened" for whatever symbol
you asked about. With the range it returns thousands. This skill always passes
the range.

## Tickers are bad search terms here

Searching the press for `LT`, `BSE`, `SBIN` or `M&M` returns noise. So the
skill resolves the symbol to its registered name from `EQUITY_L.csv`, searches
that, and then requires the name to appear in the headline. `RELIANCE` becomes
"Reliance Industries Limited" and the match is checked, not assumed.

## The tags

What the Indian market actually reacts to:

```
results        order_win      fundraise     pledge
deal           regulatory     rating        corp_action
management     board_meeting  capacity      call
```

`pledge` is worth its own tag: a promoter pledging shares, and especially a
pledge invocation, is an Indian-specific signal with no clean US equivalent.
`regulatory` covers SEBI, RBI, CCI, NCLT, GST and tax action.

Tags are deliberately narrow. "Revenue appeals dismissed" in a GST tribunal
story is `regulatory`, not `results`, because a bare mention of revenue is not
an earnings event. Getting that wrong fills the `results` tag with tax news.

## Time

The exchange publishes in IST. Google News publishes in GMT. Moneycontrol
publishes `+0530`. Treating any of them as another is a five and a half hour
error in a tool whose entire job is telling you when something happened, so
every timestamp is normalised on the way in and every one printed is IST.

## The move

The percentage shown is the last settled bhavcopy close against its previous
close, with delivery percentage alongside. It is not intraday. Intraday would
mean Yahoo, which throttles, and before the open the settled close is the
honest number anyway.

## State

`trader/state/news.json` remembers what has been reported, so running again
shows what is new rather than the same list. `--all` ignores it.

The exchange feed repeats filings: the same con-call intimation came back three
times in one answer, same timestamp, same text. Those are deduplicated on
symbol, category and text.

## Results are the thing to check

The single most useful question this skill answers before an intraday entry is
whether the company reports today. An intraday position held through a results
filing is a different trade from the one you planned. `nse-analyst --calendar`
reads board-meeting intimations, which arrive days before the numbers.

**Not financial advice.** A headline is not a reason to trade, and a filing is
a fact about the past. Nothing in this skill can place an order: no order
reaches a broker without the risk engine passing that exact order and you
typing yes for it.

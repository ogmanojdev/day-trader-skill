---
name: nse-deals
description: Read who actually bought and sold NSE stocks by name, from the exchange's daily bulk and block deal disclosures plus SEBI insider-trading filings. Aggregates by symbol and client so accumulation is visible rather than single prints, nets both sides of crossed trades, and reports values in Indian digit grouping. Replaces the US Congress-trading tracker, which has no Indian counterpart. Use when the user asks who is buying an Indian stock, wants bulk or block deal data, promoter or insider activity, or institutional accumulation.
---

# NSE Deals Tracker

The US original tracks Congress's stock disclosures under the STOCK Act. India
has no equivalent: no register of politicians' trades, no 45-day filing rule.
What India has instead is better for a trader, because it is daily rather than
six weeks late and it names the buyer.

## The tool

```bash
python3 deals.py                           # the latest session's deals
python3 deals.py --symbols RELIANCE,SBIN
python3 deals.py --min-value 1000          # only deals above 10 crore
python3 deals.py insider --days 30         # SEBI PIT disclosures
```

## Three disclosures

**Bulk deals.** Any trade where one client moved more than 0.5% of a company's
listed shares in a day. Published after the close with the client's name, side,
quantity and weighted average price.

**Block deals.** Trades done in the exchange's block window, minimum 10 crore,
same disclosure.

**PIT.** Disclosures under SEBI's Prohibition of Insider Trading regulations:
promoters, directors and designated employees must report their own dealings
within two trading days.

## How to read it

A bulk deal is not a tip. The disclosure is deliberately after the fact, and
whoever filed it has a size and a horizon that are not yours. A single day's
print is mostly noise.

What is worth something is accumulation: the same fund on the buy side of the
same name across several sessions, or a promoter selling into a rally. That is
why the default view aggregates by symbol and client and shows net rather than
listing individual trades.

```
COHANCE    JASUB PROPERTY HOLDINGS LLP    bought  60,00,000 shares  2,58,00,00,000
COHANCE    JASTI FAMILY TRUST             sold    60,00,000 shares  2,58,00,00,000
```

That pair is the thing to understand before reading any of this. Both sides of
a bulk deal are reported, so a crossed trade appears twice, once as a buy and
once as a sell. Here two entities of the same family moved 60 lakh shares
between themselves. Nothing was accumulated and nobody formed a view: it is a
restructuring. Read either line alone and it looks like conviction.

A genuine signal looks different: one named institution on the buy side, no
matching seller of the same size, repeated across sessions.

## PIT comes back empty often

The endpoint is cookie gated and frequently returns nothing for a short window.
An empty answer is not proof that nobody filed, and the tool says so rather
than reporting zero as a fact.

## Numbers are written the Indian way

`60,00,000` is sixty lakh, not six million. Values print in lakh as the
exchange publishes them, and rupee amounts use Indian digit grouping
throughout, because reading `2,58,00,00,000` as `258,000,000` is a factor of
ten error.

**Not financial advice.** These are disclosures of what someone else did, for
their own reasons, after they did it. Congress-tracking strategies are not a
model here and neither is copying a fund. Nothing in this skill can place an
order: no order reaches a broker without the risk engine passing that exact
order and you typing yes for it.

# Contributing

Contributions are welcome, particularly from people who trade Indian markets and
can tell when a number is wrong.

## What this project is

Six Claude skills that read NSE's own published data and refuse to guess. Two
rules shape almost every decision here:

1. **Every number traces to a source.** The exchange, or arithmetic on the
   exchange. No vendor data, no estimates, no "approximately". If a figure
   cannot be traced, it does not get printed.
2. **Nothing places an order.** These are research tools. The risk engine and
   broker adapters live in [Sanyam](https://github.com/ogmanojdev/sanyam), where
   there is exactly one unbypassable risk path. A pull request that adds an
   order path here will be declined, however well written.

## Getting set up

No dependencies, no virtualenv, no build step. Standard-library Python 3.8+.

```bash
git clone https://github.com/ogmanojdev/nse-trader-skill.git
cd nse-trader-skill
python3 -m unittest discover -s tests
```

166 tests, about a third of a second, no network. Every test runs against
captured fixtures in `tests/fixtures/`, so the suite works on a plane and gives
the same answer every time.

To run a skill against live NSE data, run its script from a working directory of
your choosing. Output lands in `trader/` relative to where you ran it:

```bash
mkdir ~/trading && cd ~/trading
python3 /path/to/nse-trader-skill/nse-scanner/scan.py daily --show 10
```

## The layout, and the one surprising thing

Each skill folder stands alone and **vendors** its shared modules, so
`nse-scanner/charges.py` and `nse-deals/charges.py` are byte-identical copies of
`nse-common/charges.py`. That is deliberate: a user can copy a single skill
folder and it works, with no package to install.

**Edit `nse-common/`, never the copies.** Then run:

```bash
python3 tools/vendor.py
```

`tests/test_common.py` fails if the copies have drifted, so CI catches it if you
forget. The upstream US project this was adapted from had two drifted copies of
the same file; that is the failure this guards against.

## Adding or changing a cost

`nse-common/charges.py` is the module the whole project leans on, because Indian
intraday costs are large enough to flip a winning rule into a losing one. STT,
stamp duty, exchange transaction charges, SEBI turnover fees, IPF, brokerage and
GST on several of those.

Rates move with exchange and government circulars. If you change one:

- cite the circular or the broker's published calculator in the commit message,
  with the date you read it
- verify the result against a real contract note before trusting it to the rupee
- add a test with the figures from that contract note

A wrong rate here is worse than a missing feature, because it silently changes
every expectancy number the project reports.

## Tests

Write one for anything that reads data or formats a number. The useful tests in
this repo are the ones that encode a mistake somebody actually made:

- `test_a_round_trip_is_not_reported_as_a_buy` exists because a crossed trade
  printed as accumulation
- `test_above_and_below_together_add_both` exists because a stop was silently
  dropped, leaving a position unprotected
- `test_value_is_quantity_times_price_in_lakh` exists because the exchange
  publishes lakh and everyone thinks in crore

Name the test after the behaviour, not the function.

Need a fixture? `tools/capture_fixtures.py` and `tools/make_fixture.py` record
one from live data. Keep them small, and never commit real personal data.

## What gets merged quickly

- A number that is wrong, with the source showing why
- A failure mode handled with a message that says what to do next, rather than a
  traceback
- An NSE endpoint that changed shape
- Tests for existing behaviour that is currently untested

## What to discuss first

- A new skill. Six is already a lot of always-on context; the bar is that it
  does something the others cannot.
- Anything that adds a dependency. The no-install promise is load bearing.
- Anything touching order placement. See rule 2.

## Pull requests

Branch off `main`, keep the diff focused, and say in the description what you
checked against live data and when. Indian market data is seasonal: a thing that
works in November may break on a settlement holiday or during a Muhurat session,
and the calendar code in `nsecal.py` is where that lives.

Run the suite before pushing. If you changed a skill's behaviour, update its
`SKILL.md` in the same commit, because that file is what Claude actually reads.

Shipping note: this repo is also a Claude Code plugin. A merged fix only reaches
installed users once `version` in `.claude-plugin/plugin.json` is raised, so
maintainers should bump it on release.

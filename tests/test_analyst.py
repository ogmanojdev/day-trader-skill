import io
import json
import os
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import FIX, load

facts = load("nse-analyst", "facts")
FIXD = os.path.join(FIX, "nse-analyst")


class Units(unittest.TestCase):
    def test_nse_publishes_in_lakh(self):
        # 100 lakh to the crore. Read as thousands, RELIANCE becomes a 1,786 crore
        # company instead of a 1,78,567 crore one: wrong by a hundred and still
        # plausible-looking, which is exactly why this is pinned by a test.
        self.assertEqual(facts.LAKH_PER_CRORE, 100.0)
        self.assertEqual(facts.to_crore(17856700), 178567.0)
        self.assertEqual(facts.to_crore(1535400), 15354.0)
        self.assertIsNone(facts.to_crore(None))

    def test_the_published_figures_are_pinned_to_a_known_filing(self):
        """The authoritative unit guard.

        RELIANCE's September 2026 quarter was filed as income 17856700 and PAT
        1535400, which in lakh is 1,78,567 crore and 15,354 crore. Pinning the real
        numbers means a change in the exchange's unit fails this test instead of
        quietly rescaling every answer the skill gives.
        """
        self.assertEqual(facts.to_crore(17856700), 178567.0)
        self.assertEqual(facts.to_crore(1535400), 15354.0)

    def test_the_implied_share_count_catches_only_a_gross_error(self):
        """Worth stating plainly, because the opposite is easy to assume.

        A uniform scale error cannot be detected from one filing's internals. EPS is
        published in rupees whatever unit the totals use, and income over PAT is
        scale-free, so there is no ratio inside the document that changes when every
        total is multiplied by a hundred. The implied share count needs an external
        anchor to mean anything, and it has none.

        What it does catch is an implied count that no listed company could have.
        1,354 crore shares is RELIANCE. 13.5 crore (the hundredfold error) is
        ordinary for a small cap and passes. 0.135 crore, 13.5 lakh shares, does not.
        So this is a floor against nonsense, and the pinned-value test above is the
        guard that actually holds the unit.
        """
        lo, hi = facts.PLAUSIBLE_SHARES_CR
        self.assertTrue(lo <= facts.implied_shares(15354.0, 11.34) <= hi)     # right
        self.assertTrue(lo <= facts.implied_shares(153.54, 11.34) <= hi)      # 100x, missed
        self.assertFalse(lo <= facts.implied_shares(1.5354, 11.34) <= hi)     # 10000x, caught
        self.assertIsNone(facts.implied_shares(100.0, 0))


class Dates(unittest.TestCase):
    def test_every_spelling_the_exchange_uses_parses(self):
        # Parsing only some of them fails as an empty result set, which reads like
        # "nobody filed" rather than "this tool cannot read the date".
        for raw in ("30 Sep 2026", "30-Oct-2026", "2026-09-30",
                    "30-Jul-2026 17:17", "30-Jul-2026 17:17:53"):
            self.assertIsNotNone(facts.parse_date(raw), raw)
        self.assertIsNone(facts.parse_date("last quarter"))


class Quarters(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(FIXD, "top-corp-info-RELIANCE.json"), encoding="utf-8") as fh:
            self.doc = json.load(fh)
        self.q = facts.growth(facts.quarters_for(self.doc))

    def test_quarters_come_back_newest_first(self):
        ends = [x["period_end"] for x in self.q]
        self.assertEqual(ends, sorted(ends, reverse=True))

    def test_the_figures_are_in_crore_and_sane(self):
        top = self.q[0]
        self.assertGreater(top["income_cr"], 1000)        # a large cap, in crore
        self.assertLess(top["income_cr"], 10000000)
        shares = facts.implied_shares(top["pat_cr"], top["eps_diluted"])
        lo, hi = facts.PLAUSIBLE_SHARES_CR
        self.assertTrue(lo <= shares <= hi, shares)

    def test_growth_never_crosses_the_basis(self):
        """Consolidated and standalone sit in one list with no ordering guarantee.

        Comparing one against the other manufactures growth that never happened.
        """
        by_basis = {}
        for x in self.q:
            by_basis.setdefault(x["basis"], []).append(x)
        for basis, rows in by_basis.items():
            rows.sort(key=lambda r: r["period_end"], reverse=True)
            for i, row in enumerate(rows):
                if row["qoq_pat_pct"] is None or i + 1 >= len(rows):
                    continue
                prev = rows[i + 1]
                self.assertEqual(prev["basis"], basis)
                want = (row["pat_cr"] - prev["pat_cr"]) / abs(prev["pat_cr"]) * 100.0
                self.assertAlmostEqual(row["qoq_pat_pct"], want, places=4)

    def test_a_quarter_with_no_comparable_prints_nothing_rather_than_guessing(self):
        self.assertIsNone(self.q[-1]["qoq_pat_pct"])


class EndToEnd(unittest.TestCase):
    def test_a_symbol_runs_offline(self):
        buf, err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            rc = facts.main(["RELIANCE", "--offline", FIXD, "--no-write", "--quarters", "4"])
        out = buf.getvalue()
        self.assertEqual(rc, 0, out + err.getvalue())
        self.assertIn("converted from the lakh", out)
        self.assertIn("unit check", out)
        self.assertNotIn("WARNING", out)

    def test_the_calendar_reads_the_dated_announcements_feed(self):
        buf, err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            rc = facts.main(["--calendar", "--offline", FIXD, "--back", "3650", "--show", "3"])
        self.assertEqual(rc, 0, buf.getvalue() + err.getvalue())
        self.assertIn("board meetings", buf.getvalue())


if __name__ == "__main__":
    unittest.main()

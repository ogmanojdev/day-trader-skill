import datetime
import io
import os
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import FIX, load

news = load("nse-news", "news")
FIXD = os.path.join(FIX, "nse-news")


class Tags(unittest.TestCase):
    def test_the_indian_events_are_tagged(self):
        cases = {
            "Q2 FY27 results: net profit rose 18%": "results",
            "Company bags order worth Rs 1,200 crore": "order_win",
            "Promoter pledges 2% of holding": "pledge",
            "SEBI penalty for insider trading": "regulatory",
            "Board approves bonus issue 1:1": "corp_action",
            "QIP of Rs 2,000 crore approved": "fundraise",
            "CFO resigns with immediate effect": "management",
        }
        for text, want in cases.items():
            self.assertIn(want, news.tags_for(text), text)

    def test_a_bare_revenue_mention_is_not_an_earnings_event(self):
        # The regression: a GST tribunal story was landing in the results tag, which
        # fills the one tag a day trader checks with tax news.
        tags = news.tags_for("GSTAT Rajkot Dismisses 8 Revenue Appeals, Upholds ITC Refund Relief")
        self.assertIn("regulatory", tags)
        self.assertNotIn("results", tags)

    def test_unmatched_text_is_other(self):
        self.assertEqual(news.tags_for("the stock went up a bit"), ["other"])


class Time(unittest.TestCase):
    def test_gmt_and_ist_feeds_both_land_in_ist(self):
        # Google News publishes GMT, Moneycontrol publishes +0530. Treating either as
        # the other is a five and a half hour error in a tool about when things happen.
        gmt = news.parse_rss_time("Tue, 10 Nov 2026 10:35:11 GMT")
        ist = news.parse_rss_time("Tue, 10 Nov 2026 16:05:11 +0530")
        self.assertEqual(gmt, datetime.datetime(2026, 11, 10, 16, 5, 11))
        self.assertEqual(gmt, ist)

    def test_the_exchange_timestamp_is_already_ist(self):
        self.assertEqual(news.parse_nse_time("10-Nov-2026 23:48:55"),
                         datetime.datetime(2026, 11, 10, 23, 48, 55))

    def test_unreadable_times_are_none_not_now(self):
        self.assertIsNone(news.parse_rss_time("sometime yesterday"))
        self.assertIsNone(news.parse_nse_time(""))


class Announcements(unittest.TestCase):
    def setUp(self):
        self.doc = news.fetch_announcements(FIXD)

    def test_the_feed_parses_and_carries_ist_times(self):
        items = news.announcements_for(self.doc)
        self.assertTrue(items)
        self.assertTrue(all(i["source"] == "NSE filing" for i in items))
        self.assertTrue(any(i["time"] for i in items))

    def test_repeated_filings_are_deduplicated(self):
        # The live feed returned the same CGCL con-call intimation three times in one
        # 20 row answer: same symbol, same timestamp, same text.
        items = news.announcements_for(self.doc)
        keys = [(i["symbol"], i["category"], i["text"][:120]) for i in items]
        self.assertEqual(len(keys), len(set(keys)))

    def test_newest_first(self):
        times = [i["time"] for i in news.announcements_for(self.doc) if i["time"]]
        self.assertEqual(times, sorted(times, reverse=True))

    def test_a_symbol_filter_narrows_it(self):
        items = news.announcements_for(self.doc)
        if not items:
            self.skipTest("empty fixture")
        sym = items[0]["symbol"]
        only = news.announcements_for(self.doc, [sym])
        self.assertTrue(all(i["symbol"] == sym for i in only))


class Headlines(unittest.TestCase):
    def test_google_titles_split_off_the_publisher(self):
        t, p = news.split_google_title("Reliance wins offshore block - TradingView", "")
        self.assertEqual(t, "Reliance wins offshore block")
        self.assertEqual(p, "TradingView")

    def test_an_explicit_publisher_wins(self):
        t, p = news.split_google_title("Headline - Something", "Mint")
        self.assertEqual((t, p), ("Headline - Something", "Mint"))


class EndToEnd(unittest.TestCase):
    def test_announcements_mode_runs_offline(self):
        buf, err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            rc = news.main(["announcements", "--offline", FIXD, "--hours", "720",
                            "--no-state", "--all", "--show", "3"])
        self.assertEqual(rc, 0, buf.getvalue() + err.getvalue())
        self.assertIn("corporate announcements", buf.getvalue())


if __name__ == "__main__":
    unittest.main()

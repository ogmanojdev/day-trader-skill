import datetime
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import FIX, load

scan = load("nse-scanner", "scan")
nsedata = load("nse-common", "nsedata")
FIXD = os.path.join(FIX, "nse-scanner")


class Universe(unittest.TestCase):
    def test_only_series_eq_survives(self):
        with open(os.path.join(FIXD, "EQUITY_L.csv"), encoding="utf-8") as fh:
            kept, counts = nsedata.parse_universe(fh.read())
        self.assertTrue(kept)
        self.assertTrue(all(k["series"] == "EQ" for k in kept))
        self.assertIn("RELIANCE", [k["symbol"] for k in kept])

    def test_funds_are_dropped_by_name_or_isin(self):
        # An ETF trades in series EQ and the bhavcopy cannot tell you so. Left in, its
        # structurally near-100% delivery fires delivery_surge every single day.
        self.assertTrue(nsedata.is_etf("Nippon India ETF Nifty 50 BeES"))
        self.assertTrue(nsedata.is_etf("Whatever Fund", isin="INF204KB14I2"))
        self.assertFalse(nsedata.is_etf("Reliance Industries Limited", isin="INE002A01018"))

    def test_universe_file_round_trips(self):
        with open(os.path.join(FIXD, "EQUITY_L.csv"), encoding="utf-8") as fh:
            kept, _ = nsedata.parse_universe(fh.read())
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "u.txt")
            nsedata.write_universe(kept, p)
            back = nsedata.read_universe(p)
        self.assertEqual(sorted(back), sorted(k["symbol"] for k in kept))
        self.assertEqual(back["RELIANCE"], "Reliance Industries Limited")


class BhavcopyTrap(unittest.TestCase):
    def test_a_mismatched_date_is_refused(self):
        """The archive answers 200 with its newest file for a date it does not have.

        Asking for 10 Nov 2026 returned 09 Nov data, same status, same shape. Without
        this check a scan silently re-scores yesterday as today.
        """
        served = ("SYMBOL, SERIES, DATE1, PREV_CLOSE, CLOSE_PRICE\n"
                  "RELIANCE, EQ, 09-Nov-2026, 1324.00, 1324.40\n")
        real = nsedata.nse_get
        nsedata.nse_get = lambda path, timeout=40: served
        try:
            with tempfile.TemporaryDirectory() as d:
                with self.assertRaises(nsedata.DataError) as cm:
                    nsedata.fetch_bhav(datetime.date(2026, 11, 10), d)
                self.assertIn("answered with 2026-11-09 data", str(cm.exception))
                # nothing was cached, so a later correct fetch is not poisoned
                self.assertEqual(os.listdir(d), [])
        finally:
            nsedata.nse_get = real

    def test_the_matching_date_is_accepted_and_cached(self):
        served = ("SYMBOL, SERIES, DATE1, PREV_CLOSE, CLOSE_PRICE\n"
                  "RELIANCE, EQ, 10-Nov-2026, 1324.00, 1324.40\n")
        real = nsedata.nse_get
        nsedata.nse_get = lambda path, timeout=40: served
        try:
            with tempfile.TemporaryDirectory() as d:
                rows, cached = nsedata.fetch_bhav(datetime.date(2026, 11, 10), d)
                self.assertFalse(cached)
                self.assertEqual(rows[0]["SYMBOL"], "RELIANCE")
                rows2, cached2 = nsedata.fetch_bhav(datetime.date(2026, 11, 10), d)
                self.assertTrue(cached2)
                self.assertEqual(rows, rows2)
        finally:
            nsedata.nse_get = real

    def test_date_in_file_is_read_from_either_spelling(self):
        self.assertEqual(
            nsedata.bhav_date_in("SYMBOL, DATE1\nX, 09-Nov-2026\n"),
            datetime.date(2026, 11, 9))
        self.assertIsNone(nsedata.bhav_date_in("SYMBOL, DATE1\nX, not a date\n"))


class Indicators(unittest.TestCase):
    def test_rsi_of_a_pure_uptrend_is_100(self):
        self.assertEqual(scan.rsi([float(i) for i in range(1, 30)]), 100.0)

    def test_rsi_needs_enough_history(self):
        self.assertIsNone(scan.rsi([1.0, 2.0, 3.0]))

    def test_breakout_20d_wants_a_new_high_over_the_prior_20(self):
        closes = [100.0] * 20 + [101.0]
        names, _rsi, _d = scan.setups_for(closes, [None] * 21, {"DELIV_PER": "40"})
        self.assertIn("breakout_20d", names)
        flat = [100.0] * 21
        names, _r, _d = scan.setups_for(flat, [None] * 21, {"DELIV_PER": "40"})
        self.assertNotIn("breakout_20d", names)

    def test_delivery_surge_needs_an_up_close_and_a_real_jump(self):
        closes = [100.0] * 20 + [101.0]
        deliv = [20.0] * 20 + [80.0]
        names, _r, d_now = scan.setups_for(closes, deliv, {"DELIV_PER": "80"})
        self.assertIn("delivery_surge", names)
        self.assertEqual(d_now, 80.0)
        # same delivery but a down close is not a surge
        down = [100.0] * 20 + [99.0]
        names2, _r2, _d2 = scan.setups_for(down, deliv, {"DELIV_PER": "80"})
        self.assertNotIn("delivery_surge", names2)
        # high delivery that is normal for the name is not a surge either
        normal = [70.0] * 20 + [75.0]
        names3, _r3, _d3 = scan.setups_for(closes, normal, {"DELIV_PER": "75"})
        self.assertNotIn("delivery_surge", names3)


class Bands(unittest.TestCase):
    def test_the_standard_band_is_not_flagged(self):
        # Reporting "band 20%" on 260 of 424 hits hides the 5% names that matter.
        self.assertIsNone(nsedata.band_flag(20.0))
        self.assertEqual(nsedata.band_flag(5.0), 5.0)
        self.assertIsNone(nsedata.band_flag(None))

    def test_bands_parse_from_the_exchange_file(self):
        with open(os.path.join(FIXD, "sec_list.csv"), encoding="utf-8") as fh:
            bands = nsedata.parse_bands(fh.read())
        self.assertTrue(all(isinstance(v, float) for v in bands.values()))

    def test_an_asm_name_is_blocked_whatever_the_chart_says(self):
        self.assertEqual(scan.band_block("X", {}, {}, {"X": "shortterm"}), "ASM shortterm")
        self.assertIsNone(scan.band_block("Y", {"PREV_CLOSE": "100", "CLOSE_PRICE": "101"},
                                          {"Y": 10.0}, {}))
        # a name sitting on its band cannot take a stop through it
        self.assertIsNotNone(scan.band_block("Z", {"PREV_CLOSE": "100", "CLOSE_PRICE": "110"},
                                             {"Z": 10.0}, {}))


class OpeningRange(unittest.TestCase):
    def bars(self, closes, highs=None, lows=None):
        day = datetime.date(2026, 11, 9)
        out = []
        for i, c in enumerate(closes):
            t = datetime.datetime.combine(day, datetime.time(9, 15)) + \
                datetime.timedelta(minutes=5 * i)
            out.append({"slot": i, "time": t, "o": c, "c": c,
                        "h": (highs or closes)[i], "l": (lows or closes)[i], "v": 1000})
        return out

    def test_the_first_close_above_the_range_high_is_the_hit(self):
        o = scan.orb_for(self.bars([100.0, 101.0, 102.0, 103.0]), 15)
        self.assertEqual(o["range_high"], 102.0)
        self.assertTrue(o["hit"])
        self.assertEqual(o["close"], 103.0)
        self.assertEqual(o["at"], "09:30")

    def test_no_close_above_the_range_is_not_a_hit(self):
        o = scan.orb_for(self.bars([100.0, 101.0, 102.0, 101.5]), 15)
        self.assertFalse(o["hit"])

    def test_a_gap_inside_the_range_makes_it_unknowable(self):
        bars = self.bars([100.0, 101.0, 102.0, 103.0])
        del bars[1]                       # a halt: no 09:20 bar
        for i, b in enumerate(bars):      # slots no longer 0,1,2
            b["slot"] = [0, 2, 3][i]
        self.assertIsNone(scan.orb_for(bars, 15))

    def test_session_bars_drop_incomplete_and_off_grid_bars(self):
        day = datetime.date(2026, 11, 9)
        base = datetime.datetime(2026, 11, 9, 3, 45)        # 09:15 IST in UTC
        epoch = datetime.datetime(1970, 1, 1)
        times = [base, base + datetime.timedelta(minutes=5),
                 base + datetime.timedelta(minutes=10, seconds=42),   # off the grid
                 base + datetime.timedelta(minutes=15)]
        ser = {"t": [int((t - epoch).total_seconds()) for t in times],
               "o": [1, 2, 3, 4], "h": [1, 2, 3, 4], "l": [1, 2, 3, 4],
               "c": [1, 2, 3, 4], "v": [1, 1, 1, 1], "meta": {}}
        # clock at 09:28: the 09:25 bar has not closed, the 09:25:42 tick is off grid
        got = scan.session_bars(ser, day, datetime.datetime(2026, 11, 9, 9, 28))
        self.assertEqual([b["slot"] for b in got], [0, 1])


class RealSession(unittest.TestCase):
    """One real NSE session, TCS on 2026-11-09, straight from Yahoo's chart endpoint.

    This is the fixture that proves the grid against the exchange rather than against
    a generator: 75 slots on the five-minute grid, 09:15 to 15:25, which is exactly
    what a 375 minute session should produce, and one of them arrived empty.
    """

    def setUp(self):
        with open(os.path.join(FIXD, "chart-TCS.NS-1d-5m.json"), encoding="utf-8") as fh:
            self.ser = nsedata.series_from_chart(fh.read())
        self.day = datetime.date(2026, 11, 9)

    def test_the_real_session_fills_the_grid_and_one_bar_is_empty(self):
        """75 slots, 74 usable, and the missing one is the point.

        Yahoo emitted the 15:20 timestamp for TCS with open, high, low, close and
        volume all null. Not a gap in the array: a present slot carrying nothing.
        TCS is among the most liquid stocks on the exchange, so if it happens there
        it happens anywhere, and a tool that trusted the array length would read a
        null close as a price. The 15:15 bar either side of it traded 10 shares and
        the 15:25 bar traded 176,784, which is the closing auction.
        """
        self.assertEqual(len(self.ser["t"]), 75)                      # the full grid
        self.assertEqual(sum(1 for c in self.ser["c"] if c is None), 1)
        bars = scan.session_bars(self.ser, self.day,
                                 datetime.datetime(2026, 11, 9, 15, 30))
        self.assertEqual(len(bars), 74)                               # the usable ones
        self.assertEqual([b["slot"] for b in bars],
                         [s for s in range(75) if s != 73])
        self.assertEqual(bars[0]["time"].time(), datetime.time(9, 15))
        self.assertEqual(bars[-1]["time"].time(), datetime.time(15, 25))
        self.assertTrue(all(b["c"] is not None for b in bars))

    def test_the_meta_says_what_we_assumed(self):
        meta = self.ser["meta"]
        self.assertEqual(meta["exchangeTimezoneName"], "Asia/Kolkata")
        self.assertEqual(meta["gmtoffset"], 19800)        # +5:30, no daylight saving
        self.assertEqual(meta["currency"], "INR")

    def test_the_clock_cuts_the_session_where_asked(self):
        bars = scan.session_bars(self.ser, self.day,
                                 datetime.datetime(2026, 11, 9, 10, 0))
        # 09:15 through 09:50 have closed by 10:00; the 09:55 bar has not
        self.assertEqual([b["slot"] for b in bars], list(range(9)))

    def test_the_opening_range_on_real_bars(self):
        bars = scan.session_bars(self.ser, self.day,
                                 datetime.datetime(2026, 11, 9, 15, 30))
        o = scan.orb_for(bars, 15)
        self.assertIsNotNone(o)
        self.assertGreater(o["range_high"], o["range_low"])
        self.assertAlmostEqual(o["range_mid"], (o["range_high"] + o["range_low"]) / 2.0)
        first_three = bars[:3]
        self.assertEqual(o["range_high"], max(b["h"] for b in first_three))
        self.assertEqual(o["range_low"], min(b["l"] for b in first_three))
        if o["hit"]:
            self.assertGreater(o["close"], o["range_high"])

    def test_the_orb_command_runs_on_the_real_fixture(self):
        buf, err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            rc = scan.main(["orb", "--offline", FIXD, "--symbols", "TCS",
                            "--now", "2026-11-09 15:30", "--no-write"])
        out = buf.getvalue()
        self.assertEqual(rc, 0, out + err.getvalue())
        self.assertIn("Opening range 09:15 to 09:30 IST", out)


class EndToEnd(unittest.TestCase):
    def test_the_daily_scan_runs_on_the_fixtures(self):
        buf, err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            rc = scan.main(["daily", "--offline", FIXD, "--now", "2026-11-09 18:30",
                            "--no-write", "--min-turnover", "0", "--show", "5"])
        out = buf.getvalue()
        self.assertEqual(rc, 0, out + err.getvalue())
        self.assertIn("NSE bhavcopy", out)
        self.assertIn("setups", out)

    def test_the_scan_refuses_a_closed_day_cleanly(self):
        buf, err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            rc = scan.main(["orb", "--symbols", "RELIANCE", "--now", "2026-11-10 10:00",
                            "--no-write"])
        self.assertEqual(rc, 1)
        self.assertIn("closed", err.getvalue())


if __name__ == "__main__":
    unittest.main()

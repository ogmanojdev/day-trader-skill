import io
import os
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import FIX, load

tm = load("nse-timemachine", "timemachine")
FIXD = os.path.join(FIX, "nse-timemachine")
BARS = {"symbol": "DEMO", "entry": 1324.40, "stop": 1318.50, "target": 1330.30, "qty": 100}


class FirstTouch(unittest.TestCase):
    def test_a_bar_that_reaches_the_target_only(self):
        hit, close = tm.first_touch(100.0, (0.02, -0.001, 0.015), 100.0, 99.0, 101.0)
        self.assertEqual(hit, "target")
        self.assertAlmostEqual(close, 101.5)

    def test_a_bar_that_reaches_the_stop_only(self):
        hit, _c = tm.first_touch(100.0, (0.001, -0.02, -0.015), 100.0, 99.0, 101.0)
        self.assertEqual(hit, "stop")

    def test_a_bar_holding_both_is_ambiguous_not_a_coin_flip(self):
        # OHLC records no sequence, so which came first is not in the data at all.
        hit, _c = tm.first_touch(100.0, (0.02, -0.02, 0.0), 100.0, 99.0, 101.0)
        self.assertEqual(hit, "both")

    def test_a_quiet_bar_touches_nothing(self):
        hit, _c = tm.first_touch(100.0, (0.002, -0.002, 0.001), 100.0, 99.0, 101.0)
        self.assertIsNone(hit)

    def test_a_short_reads_the_sides_the_other_way(self):
        hit, _c = tm.first_touch(100.0, (0.001, -0.02, -0.015), 100.0, 101.0, 99.0, short=True)
        self.assertEqual(hit, "target")
        hit2, _c2 = tm.first_touch(100.0, (0.02, -0.001, 0.015), 100.0, 101.0, 99.0, short=True)
        self.assertEqual(hit2, "stop")


class Ambiguity(unittest.TestCase):
    def test_an_ambiguous_bar_resolves_to_the_stop(self):
        # Documented and pessimistic: the walk stops and counts it.
        outcome, _slot, amb = tm.walk(100.0, 99.0, 101.0, 0, 5,
                                      lambda s: (0.02, -0.02, 0.0))
        self.assertEqual(outcome, "stop")
        self.assertEqual(amb, 1)


class Bars(unittest.TestCase):
    def setUp(self):
        self.by_day, self.meta = tm.load_bars("DEMO", FIXD, 60)

    def test_the_fixture_is_labelled_synthetic(self):
        # Numbers from generated bars must never be reported as a symbol's real odds.
        self.assertTrue(self.meta.get("_synthetic"))

    def test_sixty_sessions_of_seventy_five_bars(self):
        self.assertEqual(len(self.by_day), 60)
        self.assertTrue(all(len(v) == 75 for v in self.by_day.values()))

    def test_shapes_are_pooled_by_time_of_day(self):
        pool = tm.shapes_by_slot(self.by_day)
        self.assertEqual(len(pool), 75)
        self.assertTrue(all(len(v) == 60 for v in pool.values()))

    def test_excluding_a_day_removes_exactly_that_day(self):
        day = sorted(self.by_day)[0]
        pool = tm.shapes_by_slot(self.by_day, exclude=day)
        self.assertTrue(all(len(v) == 59 for v in pool.values()))

    def test_the_session_is_u_shaped_which_is_why_slots_matter(self):
        pool = tm.shapes_by_slot(self.by_day)

        def spread(slot):
            return sum(h - l for h, l, _c in pool[slot]) / len(pool[slot])
        self.assertGreater(spread(0), spread(37) * 1.5)     # the open against the lull
        self.assertGreater(spread(74), spread(37) * 1.5)    # and the close


class Reproducible(unittest.TestCase):
    def setUp(self):
        self.by_day, _m = tm.load_bars("DEMO", FIXD, 60)

    def test_the_same_seed_gives_the_same_answer(self):
        a = tm.bootstrap(self.by_day, 1324.40, 1318.50, 1330.30, 9, 71, 500, 19)
        b = tm.bootstrap(self.by_day, 1324.40, 1318.50, 1330.30, 9, 71, 500, 19)
        self.assertEqual(a[0], b[0])

    def test_a_different_seed_gives_a_different_answer(self):
        a = tm.bootstrap(self.by_day, 1324.40, 1318.50, 1330.30, 9, 71, 500, 19)
        b = tm.bootstrap(self.by_day, 1324.40, 1318.50, 1330.30, 9, 71, 500, 7)
        self.assertNotEqual(a[0], b[0])

    def test_the_trials_add_up(self):
        counts, _amb, _slots = tm.bootstrap(self.by_day, 1324.40, 1318.50, 1330.30,
                                            9, 71, 400, 19)
        self.assertEqual(sum(counts.values()), 400)

    def test_history_only_counts_sessions_that_had_the_entry_bar(self):
        counts, _amb, fired = tm.history(self.by_day, 1324.40, 1318.50, 1330.30, 9, 71)
        self.assertEqual(sum(counts.values()), len(fired))
        self.assertLessEqual(sum(counts.values()), len(self.by_day))


class Costs(unittest.TestCase):
    def test_a_coin_flip_at_1R_is_net_negative(self):
        # The point of the whole skill: gross positive, net negative, because an
        # Indian intraday round trip is not free.
        counts = {"target": 500, "stop": 495, "neither": 5}
        exp = tm.expectancy(counts, 1324.40, 1318.50, 1330.30, 100, "equity_intraday", None)
        self.assertGreater(exp["gross_per_trade"], 0)
        self.assertLess(exp["net_per_trade"], 0)

    def test_costs_are_charged_on_a_flat_exit_too(self):
        counts = {"target": 0, "stop": 0, "neither": 100}
        exp = tm.expectancy(counts, 1324.40, 1318.50, 1330.30, 100, "equity_intraday", None)
        self.assertEqual(exp["gross_per_trade"], 0)
        self.assertLess(exp["net_per_trade"], 0)


class EndToEnd(unittest.TestCase):
    def run_it(self, extra=None):
        args = ["run", "--offline", FIXD, "--symbol", "DEMO",
                "--entry", "1324.40", "--stop", "1318.50", "--target", "1330.30",
                "--qty", "100", "--at", "10:00", "--now", "2026-11-10 10:00",
                "--trials", "400", "--no-write"] + (extra or [])
        buf, err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            rc = tm.main(args)
        return rc, buf.getvalue(), err.getvalue()

    def test_it_runs_and_warns_about_synthetic_bars(self):
        rc, out, err = self.run_it()
        self.assertEqual(rc, 0, out + err)
        self.assertIn("SYNTHETIC", out)
        self.assertIn("reward:risk", out)
        self.assertIn("1.00 gross", out)
        self.assertIn("0.72 after costs", out)

    def test_square_off_is_15_15_by_default(self):
        _rc, out, _err = self.run_it()
        self.assertIn("square-off 15:15", out)

    def test_a_stop_on_the_wrong_side_is_refused(self):
        buf, err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            rc = tm.main(["run", "--offline", FIXD, "--symbol", "DEMO", "--entry", "100",
                          "--stop", "110", "--target", "120", "--qty", "10", "--no-write"])
        self.assertEqual(rc, 1)
        self.assertIn("stop < entry < target", err.getvalue())


if __name__ == "__main__":
    unittest.main()

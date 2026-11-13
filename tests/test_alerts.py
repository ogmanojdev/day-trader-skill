import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load

alerts = load("nse-alerts", "alerts")


class Store(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = os.path.join(self.dir.name, "alerts.json")
        self.plan = os.path.join(self.dir.name, "plan.json")
        with open(self.plan, "w") as fh:
            json.dump({"symbol": "RELIANCE", "side": "BUY", "entry": 1324.40,
                       "stop": 1318.50, "target": 1330.30, "quantity": 100,
                       "flat_by": "15:15"}, fh)

    def tearDown(self):
        self.dir.cleanup()

    def run_it(self, args):
        buf, err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            rc = alerts.main(["--store", self.store] + args)
        return rc, buf.getvalue(), err.getvalue()

    def test_from_plan_adds_stop_target_and_square_off(self):
        rc, out, _e = self.run_it(["add", "--from-plan", "--plan", self.plan])
        self.assertEqual(rc, 0)
        with open(self.store) as fh:
            rules = json.load(fh)["rules"]
        self.assertEqual(len(rules), 3)
        kinds = {r["kind"] for r in rules}
        self.assertEqual(kinds, {"below", "above", "at_time"})
        self.assertTrue(any(r.get("square_off") for r in rules))

    def test_a_long_stop_is_a_below_and_the_target_an_above(self):
        self.run_it(["add", "--from-plan", "--plan", self.plan])
        with open(self.store) as fh:
            rules = {r["label"]: r for r in json.load(fh)["rules"]}
        self.assertEqual(rules["stop"]["kind"], "below")
        self.assertEqual(rules["target"]["kind"], "above")

    def test_a_short_flips_both(self):
        with open(self.plan, "w") as fh:
            json.dump({"symbol": "X", "side": "SELL", "stop": 110.0, "target": 90.0}, fh)
        self.run_it(["add", "--from-plan", "--plan", self.plan])
        with open(self.store) as fh:
            rules = {r["label"]: r for r in json.load(fh)["rules"]}
        self.assertEqual(rules["stop"]["kind"], "above")
        self.assertEqual(rules["target"]["kind"], "below")

    def test_a_price_alert_rings_once_then_stays_quiet(self):
        self.run_it(["add", "--symbol", "RELIANCE", "--above", "1330.30", "--label", "target"])
        rc, out, _e = self.run_it(["check", "--price", "RELIANCE=1331.0", "--no-fetch",
                                   "--now", "2026-11-09 11:00"])
        self.assertEqual(rc, 0)
        self.assertIn("RING", out)
        _rc2, out2, _e2 = self.run_it(["check", "--price", "RELIANCE=1331.0", "--no-fetch",
                                       "--now", "2026-11-09 11:05"])
        self.assertNotIn("RING", out2)

    def test_a_price_short_of_the_level_does_not_ring(self):
        self.run_it(["add", "--symbol", "RELIANCE", "--above", "1330.30"])
        _rc, out, _e = self.run_it(["check", "--price", "RELIANCE=1329.9", "--no-fetch",
                                    "--now", "2026-11-09 11:00"])
        self.assertNotIn("RING", out)

    def test_the_square_off_rings_on_a_session_day(self):
        self.run_it(["add", "--at", "15:15", "--square-off", "--label", "square off"])
        _rc, out, _e = self.run_it(["check", "--no-fetch", "--now", "2026-11-09 15:16"])
        self.assertIn("RING", out)
        self.assertIn("15:20", out)          # the warning about the broker's own

    def test_the_square_off_is_skipped_on_a_holiday(self):
        # An alert that fires on a day the market never opened teaches you to ignore it.
        self.run_it(["add", "--at", "15:15", "--square-off", "--label", "square off"])
        _rc, out, err = self.run_it(["check", "--no-fetch", "--now", "2026-11-10 15:16"])
        self.assertNotIn("RING", out)
        self.assertIn("not a session", err)

    def test_the_square_off_moves_inside_a_muhurat_window(self):
        # 2026-11-08: the market is shut for Diwali but trades 13:45 to 14:45.
        self.run_it(["add", "--at", "15:15", "--square-off", "--label", "square off"])
        _rc, out, _e = self.run_it(["check", "--no-fetch", "--now", "2026-11-08 14:31"])
        self.assertIn("RING", out)          # 14:30, fifteen minutes before 14:45
        self.assertIn("14:30", out)

    def test_clear_fired_keeps_the_armed_ones(self):
        self.run_it(["add", "--symbol", "A", "--above", "10"])
        self.run_it(["add", "--symbol", "B", "--above", "1000000"])
        self.run_it(["check", "--price", "A=11", "--price", "B=1", "--no-fetch",
                     "--now", "2026-11-09 11:00"])
        self.run_it(["clear", "--fired"])
        with open(self.store) as fh:
            rules = json.load(fh)["rules"]
        self.assertEqual([r["symbol"] for r in rules], ["B"])

    def test_a_bad_price_argument_is_refused(self):
        self.run_it(["add", "--symbol", "A", "--above", "10"])
        rc, _o, err = self.run_it(["check", "--price", "nonsense", "--no-fetch"])
        self.assertEqual(rc, 1)
        self.assertIn("SYMBOL=PRICE", err)

    def test_adding_nothing_is_an_error_not_a_silent_noop(self):
        rc, _o, err = self.run_it(["add"])
        self.assertEqual(rc, 1)
        self.assertIn("nothing to add", err)


if __name__ == "__main__":
    unittest.main()

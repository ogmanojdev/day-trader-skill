import io
import os
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import FIX, load

deals = load("nse-deals", "deals")
FIXD = os.path.join(FIX, "nse-deals")


class Parsing(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(FIXD, "bulk.csv"), encoding="utf-8") as fh:
            self.bulk = deals.parse_deals(fh.read(), "bulk")

    def test_the_exchange_file_parses(self):
        self.assertTrue(self.bulk)
        d = self.bulk[0]
        for k in ("date", "symbol", "client", "side", "quantity", "price", "value_lacs"):
            self.assertIn(k, d)
        self.assertIn(d["side"], ("BUY", "SELL"))

    def test_value_is_quantity_times_price_in_lakh(self):
        for d in self.bulk[:20]:
            if d["quantity"] and d["price"]:
                self.assertAlmostEqual(d["value_lacs"], d["quantity"] * d["price"] / 100000.0,
                                       places=1)

    def test_both_files_load_through_one_entry_point(self):
        got = deals.fetch_deals(FIXD)
        self.assertTrue(got)
        self.assertEqual({d["kind"] for d in got}, {"bulk", "block"})


class Aggregation(unittest.TestCase):
    def test_a_crossed_trade_nets_to_zero_across_the_two_parties(self):
        """Both sides of a bulk deal are published, so the same shares appear twice.

        COHANCE in the captured session: JASUB PROPERTY HOLDINGS LLP bought 60 lakh
        shares and JASTI FAMILY TRUST sold 60 lakh, same day, same value. Read either
        line alone and it looks like conviction; it is a family restructuring.
        """
        raw = [
            {"symbol": "X", "client": "A", "side": "BUY", "quantity": 100,
             "price": 10.0, "value_lacs": 0.01, "kind": "bulk", "date": "2026-11-09"},
            {"symbol": "X", "client": "B", "side": "SELL", "quantity": 100,
             "price": 10.0, "value_lacs": 0.01, "kind": "bulk", "date": "2026-11-09"},
        ]
        agg = deals.aggregate(raw)
        self.assertEqual(len(agg), 2)
        self.assertEqual(sum(a["net_qty"] for a in agg), 0)

    def test_repeat_buying_by_one_client_accumulates(self):
        raw = [{"symbol": "X", "client": "FUND", "side": "BUY", "quantity": 100,
                "price": 10.0, "value_lacs": 0.01, "kind": "bulk", "date": d}
               for d in ("2026-11-05", "2026-11-06", "2026-11-09")]
        agg = deals.aggregate(raw)
        self.assertEqual(len(agg), 1)
        self.assertEqual(agg[0]["net_qty"], 300)
        self.assertEqual(agg[0]["dates"], ["2026-11-05", "2026-11-06", "2026-11-09"])

    def test_a_buy_and_a_sell_by_the_same_client_net_off(self):
        raw = [{"symbol": "X", "client": "A", "side": "BUY", "quantity": 300,
                "price": 10.0, "value_lacs": 0.03, "kind": "bulk", "date": "2026-11-09"},
               {"symbol": "X", "client": "A", "side": "SELL", "quantity": 100,
                "price": 10.0, "value_lacs": 0.01, "kind": "bulk", "date": "2026-11-09"}]
        agg = deals.aggregate(raw)
        self.assertEqual(agg[0]["net_qty"], 200)

    def test_sorted_by_absolute_value_so_sells_are_not_buried(self):
        values = [abs(a["net_value_lacs"]) for a in deals.aggregate(deals.fetch_deals(FIXD))]
        self.assertEqual(values, sorted(values, reverse=True))


class EndToEnd(unittest.TestCase):
    def test_it_runs_offline_and_writes_indian_numbers(self):
        buf, err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            rc = deals.main(["--offline", FIXD, "--no-write", "--show", "4"])
        out = buf.getvalue()
        self.assertEqual(rc, 0, out + err.getvalue())
        self.assertIn("Bulk and block deals", out)
        self.assertIn("lakh", out)

    def test_a_symbol_filter_works(self):
        buf, err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            rc = deals.main(["--offline", FIXD, "--symbols", "COHANCE", "--no-write"])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        if "0 prints" not in out:
            self.assertIn("COHANCE", out)


if __name__ == "__main__":
    unittest.main()

import unittest
from decimal import Decimal

from _load import common

ch = common("charges")


class Legs(unittest.TestCase):
    def test_stt_is_sell_side_only_for_intraday(self):
        buy = ch.leg_cost("buy", 100, 1324.40)
        sell = ch.leg_cost("sell", 100, 1324.40)
        self.assertEqual(buy["stt"], Decimal("0.00"))
        self.assertGreater(sell["stt"], Decimal("0"))
        # and stamp duty is buy side only, which is why the legs are not symmetric
        self.assertGreater(buy["stamp"], Decimal("0"))
        self.assertEqual(sell["stamp"], Decimal("0.00"))
        self.assertNotEqual(buy["total"], sell["total"])

    def test_delivery_pays_more_stt_and_a_dp_charge(self):
        intra = ch.leg_cost("sell", 100, 1324.40, "equity_intraday")
        deliv = ch.leg_cost("sell", 100, 1324.40, "equity_delivery")
        self.assertGreater(deliv["stt"], intra["stt"] * 3)
        self.assertGreater(deliv["dp"], Decimal("0"))
        self.assertEqual(intra["dp"], Decimal("0.00"))

    def test_brokerage_is_capped_and_charged_per_order(self):
        one = ch.leg_cost("buy", 100, 1324.40, orders=1)
        three = ch.leg_cost("buy", 100, 1324.40, orders=3)
        self.assertEqual(one["brokerage"], Decimal("20.00"))     # the cap, not 0.03%
        self.assertEqual(three["brokerage"], Decimal("60.00"))
        small = ch.leg_cost("buy", 1, 100.0)
        self.assertLess(small["brokerage"], Decimal("20.00"))    # under the cap, so the pct

    def test_a_bad_side_is_refused(self):
        with self.assertRaises(ch.ChargeError):
            ch.leg_cost("hold", 10, 100.0)
        with self.assertRaises(ch.ChargeError):
            ch.leg_cost("buy", 0, 100.0)


class RewardRisk(unittest.TestCase):
    def test_a_1R_trade_is_not_1R_after_costs(self):
        # The whole reason this module exists. 100 shares at 1324.40, 5.90 stop,
        # 1:1 target: gross 1.00, net 0.72.
        rr = ch.net_reward_risk(1324.40, 1318.50, 1330.30, 100)
        self.assertEqual(rr["gross_rr"], Decimal("1.00"))
        self.assertEqual(rr["net_rr"], Decimal("0.72"))
        self.assertLess(rr["net_reward"], rr["gross_reward"])
        self.assertGreater(rr["net_risk"], rr["gross_risk"])

    def test_costs_bite_less_as_the_target_widens(self):
        near = ch.net_reward_risk(1324.40, 1318.50, 1330.30, 100)   # 1R
        far = ch.net_reward_risk(1324.40, 1318.50, 1348.00, 100)    # about 4R
        self.assertLess(near["net_rr"] / near["gross_rr"], far["net_rr"] / far["gross_rr"])

    def test_breakeven_is_a_real_hurdle(self):
        be = ch.breakeven_move(1324.40, 100)
        self.assertGreater(be, Decimal("0.5"))
        self.assertLess(be, Decimal("2.0"))

    def test_a_stop_on_the_wrong_side_is_refused(self):
        with self.assertRaises(ch.ChargeError):
            ch.net_reward_risk(100.0, 110.0, 120.0, 10)             # long, stop above
        with self.assertRaises(ch.ChargeError):
            ch.net_reward_risk(100.0, 110.0, 120.0, 10, short=True)  # short, target above

    def test_a_short_moves_stt_to_the_entry(self):
        rr = ch.net_reward_risk(1324.40, 1330.30, 1318.50, 100, short=True)
        self.assertEqual(rr["gross_rr"], Decimal("1.00"))
        self.assertLess(rr["net_rr"], Decimal("1.00"))


class Money(unittest.TestCase):
    def test_indian_digit_grouping(self):
        self.assertEqual(ch.indian_group(100), "100")
        self.assertEqual(ch.indian_group(1000), "1,000")
        self.assertEqual(ch.indian_group(100000), "1,00,000")
        self.assertEqual(ch.indian_group(12345678), "1,23,45,678")

    def test_inr(self):
        self.assertEqual(ch.inr(1234.5), "₹1,234.50")
        self.assertEqual(ch.inr(12345678.5), "₹1,23,45,678.50")
        self.assertEqual(ch.inr(-500), "-₹500.00")

    def test_config_overrides_merge_over_the_defaults(self):
        import json
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.json")
            with open(p, "w") as fh:
                json.dump({"charges": {"equity_intraday":
                                       {"brokerage": {"flat": "0", "pct": "0", "cap": None}}}}, fh)
            rates = ch.load_rates(p, "equity_intraday")
            self.assertEqual(ch.leg_cost("buy", 100, 1000.0, rates=rates)["brokerage"],
                             Decimal("0.00"))
            # an untouched rate still comes from the defaults
            self.assertEqual(rates["stt"]["sell"], "0.025")


if __name__ == "__main__":
    unittest.main()

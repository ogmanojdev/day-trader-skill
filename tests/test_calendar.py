import datetime
import unittest

from _load import common

cal = common("nsecal")


class Clock(unittest.TestCase):
    def test_ist_is_a_constant_offset(self):
        # India has no daylight saving, so the US nth-Sunday machinery is not needed
        # and a round trip must be exact in both directions at every date.
        for month in range(1, 13):
            utc = datetime.datetime(2026, month, 15, 6, 0)
            self.assertEqual(cal.ist_to_utc(cal.to_ist(utc)), utc)
        self.assertEqual(cal.IST_OFFSET, datetime.timedelta(hours=5, minutes=30))

    def test_parse_ist_handles_z_as_utc(self):
        self.assertEqual(cal.parse_ist("2026-11-09 10:15"), datetime.datetime(2026, 11, 9, 10, 15))
        self.assertEqual(cal.parse_ist("2026-11-09T04:45:00Z"),
                         datetime.datetime(2026, 11, 9, 10, 15))
        with self.assertRaises(cal.CalendarError):
            cal.parse_ist("tuesday morning")


class Calendar(unittest.TestCase):
    def test_the_session_is_exactly_75_five_minute_bars(self):
        slots = cal.bar_slots()
        self.assertEqual(len(slots), 75)
        self.assertEqual(cal.BARS_PER_DAY, 75)
        self.assertEqual(slots[0], datetime.time(9, 15))
        self.assertEqual(slots[-1], datetime.time(15, 25))

    def test_a_holiday_is_closed(self):
        # 2026-11-10 was Diwali-Balipratipada: the market did not trade.
        self.assertFalse(cal.is_trading_day("2026-11-10"))
        self.assertIsNone(cal.session("2026-11-10"))
        with self.assertRaises(cal.CalendarError) as cm:
            cal.session_or_raise("2026-11-10")
        self.assertIn("Balipratipada", str(cm.exception))

    def test_a_weekend_is_closed(self):
        self.assertFalse(cal.is_trading_day("2026-11-07"))  # Saturday
        self.assertFalse(cal.is_trading_day("2026-11-08"))  # Sunday

    def test_muhurat_is_a_session_on_a_day_that_is_not_a_trading_day(self):
        # 2026-11-08 was a Sunday AND a listed holiday, and still traded for an hour.
        self.assertFalse(cal.is_trading_day("2026-11-08"))
        self.assertEqual(cal.session("2026-11-08"),
                         (datetime.time(13, 45), datetime.time(14, 45)))
        self.assertEqual(len(cal.bar_slots("2026-11-08")), 12)

    def test_a_date_past_the_table_is_refused_not_guessed(self):
        # The regression this exists for: 2027-03-02 is a Tuesday, so weekday-plus-table
        # logic calls it an ordinary session. NSE has not published 2027 yet, so an
        # unpublished holiday would be silently backtested as a day that traded.
        far = "2027-03-02"
        self.assertFalse(cal.covered(far))
        self.assertTrue(cal.is_trading_day(far))          # the lenient answer
        with self.assertRaises(cal.CalendarError) as cm:  # the one callers must use
            cal.session_or_raise(far)
        self.assertIn("past the published", str(cm.exception))
        with self.assertRaises(cal.CalendarError):
            cal.trading_days("2026-12-20", "2027-01-10")

    def test_next_and_previous_skip_the_holiday(self):
        self.assertEqual(cal.next_session_day("2026-11-09"), datetime.date(2026, 11, 11))
        self.assertEqual(cal.prev_session_day("2026-11-11"), datetime.date(2026, 11, 9))

    def test_december_2026_has_22_sessions(self):
        self.assertEqual(len(cal.trading_days("2026-12-01", "2026-12-31")), 22)


class Grid(unittest.TestCase):
    def test_off_grid_times_are_rejected(self):
        day = datetime.date(2026, 11, 9)
        self.assertEqual(cal.slot_of(datetime.datetime.combine(day, datetime.time(9, 15))), 0)
        self.assertEqual(cal.slot_of(datetime.datetime.combine(day, datetime.time(10, 0))), 9)
        # Yahoo's live partial tick lands off the grid and must not count as a bar.
        self.assertIsNone(cal.slot_of(datetime.datetime.combine(day, datetime.time(10, 12))))
        # Before the open and at or after the close there is no slot.
        self.assertIsNone(cal.slot_of(datetime.datetime.combine(day, datetime.time(9, 5))))
        self.assertIsNone(cal.slot_of(datetime.datetime.combine(day, datetime.time(15, 30))))

    def test_square_off_is_before_the_brokers_own(self):
        # Brokers flatten MIS at about 15:20, so the default must be earlier.
        self.assertLess(cal.SQUARE_OFF, datetime.time(15, 20))
        self.assertEqual(cal.SQUARE_OFF, datetime.time(15, 15))


if __name__ == "__main__":
    unittest.main()

"""Dates written by people, and by VPX.

Both day-first and month-first orders turn up, plenty are ambiguous, and the tail runs
to other languages, Roman numerals and `xx/xx/2019`.
"""

from __future__ import annotations

import unittest

from common.timestamps import (
    epoch_to_iso,
    iso_from_asctime,
    iso_from_authored_date,
    iso_to_epoch,
    utc_now_iso,
)


class AuthoredDateTests(unittest.TestCase):
    def test_an_unambiguous_day_resolves_either_way_round(self):
        for text, expected in (("22.06.2019", "2019-06-22"),     # day > 12
                               ("27/05/2017", "2017-05-27"),
                               ("8/31/2018", "2018-08-31"),      # month slot > 12
                               ("9/13/2019", "2019-09-13")):
            with self.subTest(text=text):
                self.assertEqual(iso_from_authored_date(text), expected)

    def test_an_ambiguous_pair_falls_back_to_the_year(self):
        """Both orders occur in real games, so 01/04/2017 has no answer. The year is
        true; a guess would be a coin flip between January and April."""
        for text in ("01/04/2017", "9-4-2012", "02.06.2019", "8/1/16"):
            with self.subTest(text=text):
                self.assertEqual(len(iso_from_authored_date(text)), 4)

    def test_the_same_number_twice_is_not_ambiguous(self):
        """02.02.2019 reads the same both ways, so the day survives."""
        self.assertEqual(iso_from_authored_date("02.02.2019"), "2019-02-02")

    def test_a_month_name_gives_a_month(self):
        for text, expected in (("August 2016", "2016-08"),
                               ("may 2017", "2017-05"),          # lowercase happens
                               ("June - 2017", "2017-06"),
                               ("1 July 2019", "2019-07-01"),
                               ("Sept 2018", "2018-09")):
            with self.subTest(text=text):
                self.assertEqual(iso_from_authored_date(text), expected)

    def test_a_month_and_year_with_no_day(self):
        for text, expected in (("08/2019", "2019-08"), ("07-2018", "2018-07"),
                               ("5-2017", "2017-05"), ("2019-08", "2019-08")):
            with self.subTest(text=text):
                self.assertEqual(iso_from_authored_date(text), expected)

    def test_an_iso_value_survives_whatever_separator_it_used(self):
        for text in ("2015-04-05", "2017_09_08", "2015/04/05"):
            with self.subTest(text=text):
                self.assertTrue(iso_from_authored_date(text).startswith("201"))
        self.assertEqual(iso_from_authored_date("2017_09_08"), "2017-09-08")

    def test_a_two_digit_year_is_this_century(self):
        """No VPX table was released in 1916."""
        self.assertEqual(iso_from_authored_date("8/1/16"), "2016")

    def test_a_year_we_can_see_beats_giving_up(self):
        """Spanish, Roman numerals, placeholders. The month is unreadable; the year is
        right there, and losing it too would help nobody."""
        for text, expected in (("8 septiembre 2018", "2018"),
                               ("22 X 2017", "2017"),
                               ("xx/xx/2019", "2019")):
            with self.subTest(text=text):
                self.assertEqual(iso_from_authored_date(text), expected)

    def test_an_impossible_date_degrades_rather_than_raising(self):
        self.assertEqual(iso_from_authored_date("31.02.2019"), "2019")

    def test_nothing_in_means_nothing_out(self):
        for text in ("", None, "   ", "not a date at all"):
            with self.subTest(text=text):
                self.assertEqual(iso_from_authored_date(text), "")


class SaveDateTests(unittest.TestCase):
    def test_asctime_becomes_naive_iso(self):
        """VPX writes one format. No Z: asctime has no timezone, so claiming UTC would
        be inventing one."""
        self.assertEqual(iso_from_asctime("Tue Dec 13 16:03:21 2022"), "2022-12-13T16:03:21")
        self.assertEqual(iso_from_asctime("Sun Sep  3 09:09:40 2023"), "2023-09-03T09:09:40")

    def test_anything_else_is_refused(self):
        for text in ("", None, "2022-12-13", "13/12/2022"):
            with self.subTest(text=text):
                self.assertEqual(iso_from_asctime(text), "")


class UtcTests(unittest.TestCase):
    def test_now_is_iso_with_a_z(self):
        stamp = utc_now_iso()
        self.assertTrue(stamp.endswith("Z"), stamp)
        self.assertEqual(len(stamp), 20, stamp)

    def test_an_epoch_reads_back_as_utc(self):
        self.assertEqual(epoch_to_iso(1671033801), "2022-12-14T16:03:21Z")
        self.assertEqual(epoch_to_iso("not a number"), "")

    def test_a_stamp_reads_back_as_the_epoch_it_came_from(self):
        """A time that crossed the wire has to sort as the number it started as."""
        self.assertEqual(iso_to_epoch("2022-12-14T16:03:21Z"), 1671033801)
        for value in (0, 300, 1671033801):
            self.assertEqual(iso_to_epoch(epoch_to_iso(value)), value)

    def test_a_stamp_that_is_not_one_is_absent_rather_than_zero(self):
        """Zero is a real moment and sorts as oldest; "no answer" must not become it."""
        for value in (None, "", "not a stamp"):
            self.assertIsNone(iso_to_epoch(value))


if __name__ == "__main__":
    unittest.main()

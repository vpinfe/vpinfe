"""A day, written the way this install is set to write one."""

from __future__ import annotations

import configparser
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from common import i18n
from common.config import config_schema
from manager.kit import when

DAY = datetime(2026, 4, 29, 11, 26, 53, tzinfo=UTC)
MANAGER = Path(__file__).resolve().parents[2] / "manager"


def _set(**values: str):
    return patch("manager.kit.when._setting", lambda key, default: values.get(key, default))


class DateStyle(unittest.TestCase):
    def test_iso_writes_the_day_as_iso(self) -> None:
        with _set(dates="iso"):
            self.assertEqual("2026-04-29", when.day(DAY))

    def test_language_writes_it_as_the_catalog_does(self) -> None:
        with _set(dates="language"):
            self.assertEqual(i18n.date(DAY), when.day(DAY))

    def test_each_numeric_format_writes_the_day_its_way(self) -> None:
        for dates, expected in (("mm/dd/yyyy", "04/29/2026"), ("dd/mm/yyyy", "29/04/2026"),
                                ("dd.mm.yyyy", "29.04.2026"), ("yyyy/mm/dd", "2026/04/29")):
            with self.subTest(dates=dates), _set(dates=dates):
                self.assertEqual(expected, when.day(DAY))

    def test_every_format_offered_can_be_written(self) -> None:
        option = config_schema.option("manager", "dates")
        assert option is not None
        self.assertEqual(set(option.choices) - {"language"}, set(when.DATE_SHAPES))

    def test_recent_says_how_long_ago_unless_that_is_turned_off(self) -> None:
        stamp = (datetime.now(UTC) - timedelta(days=3)).isoformat()
        with _set(relative_dates="true"):
            self.assertEqual(i18n.t("date.days_ago", count=3), when.ago(stamp))
        with _set(relative_dates="false", dates="iso"):
            self.assertRegex(when.ago(stamp), r"^\d{4}-\d{2}-\d{2}$")

    def test_relative_wins_over_any_format(self) -> None:
        stamp = (datetime.now(UTC) - timedelta(days=3)).isoformat()
        for dates in ("iso", "dd/mm/yyyy"):
            with self.subTest(dates=dates), _set(relative_dates="true", dates=dates):
                self.assertEqual(i18n.t("date.days_ago", count=3), when.ago(stamp))

    def test_timed_keeps_the_time_of_day_where_it_falls_back(self) -> None:
        with _set(relative_dates="true", dates="iso", times="24h"):
            self.assertRegex(when.ago(DAY.isoformat(), timed=True),
                             r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")


class TimeFormat(unittest.TestCase):
    def test_24_hour_is_the_default(self) -> None:
        with _set():
            self.assertEqual("14:05", when.clock(datetime(2026, 4, 29, 14, 5)))

    def test_12_hour_names_the_half_of_the_day(self) -> None:
        with _set(times="12h"):
            self.assertEqual("2:05 PM", when.clock(datetime(2026, 4, 29, 14, 5)))
            self.assertEqual("12:30 AM", when.clock(datetime(2026, 4, 29, 0, 30)))

    def test_the_exact_time_follows_both_settings(self) -> None:
        with _set(dates="dd.mm.yyyy", times="24h"):
            self.assertRegex(when.local(DAY.isoformat()),
                             r"^\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}$")

    def test_nothing_else_in_the_manager_writes_a_day(self) -> None:
        found = sorted(path.name for path in MANAGER.rglob("*.py")
                       if path.name != "when.py"
                       and "i18n.date(" in path.read_text(encoding="utf-8"))
        self.assertEqual([], found)


class TheSettingsFile(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.file = Path(folder.name) / "vpinfe.json"
        self.file.write_text("{}", encoding="utf-8")
        self.reads = 0
        self.dates = "iso"
        for one in (patch("common.config.paths.VPINFE_INI_PATH", self.file.with_suffix(".ini")),
                    patch("common.config.paths.get_ini_config", self._read),
                    patch.object(when, "_settings", None)):
            one.start()
            self.addCleanup(one.stop)

    def _read(self) -> configparser.ConfigParser:
        self.reads += 1
        parser = configparser.ConfigParser()
        parser.read_dict({"manager": {"dates": self.dates}})
        return parser

    def test_a_list_of_dates_reads_the_file_once(self) -> None:
        for _ in range(100):
            self.assertEqual("iso", when._setting("dates", "language"))

        self.assertEqual(1, self.reads)

    def test_a_change_to_the_file_is_read_on_the_next_date(self) -> None:
        when._setting("dates", "language")
        self.dates = "dd.mm.yyyy"
        later = self.file.stat().st_mtime_ns + 1_000_000
        os.utime(self.file, ns=(later, later))

        self.assertEqual(("dd.mm.yyyy", 2), (when._setting("dates", "language"), self.reads))


class ChoiceLabels(unittest.TestCase):
    def _labels(self, section: str, key: str) -> dict[str, str]:
        option = config_schema.option(section, key)
        assert option is not None
        return option.choice_labels

    def test_a_choice_the_catalog_names_carries_its_name(self) -> None:
        labels = self._labels("manager", "dates")
        self.assertEqual("2026-09-22 (ISO)", labels["iso"])
        self.assertEqual(6, len(labels))

    def test_a_choice_it_does_not_name_carries_none(self) -> None:
        option = config_schema.option("updates", "download_spreadsheet")
        assert option is not None
        self.assertEqual({}, replace(option, key="no_such_setting").choice_labels)


if __name__ == "__main__":
    unittest.main()

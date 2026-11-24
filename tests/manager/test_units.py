"""Whether a length is written in centimeters or inches."""

from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from common import i18n
from common.config import config_schema
from manager.kit import units


class ResolvedTests(unittest.TestCase):
    def test_a_choice_is_kept_wherever_the_device_is(self) -> None:
        self.assertEqual(units.resolved("cm", "US"), "cm")
        self.assertEqual(units.resolved("in", "DE"), "in")

    def test_by_region_follows_the_region(self) -> None:
        for region, expected in (("US", "in"), ("LR", "in"), ("MM", "in"), ("us", "in"),
                                 ("DE", "cm"), ("GB", "cm"), ("", "cm")):
            with self.subTest(region=region):
                self.assertEqual(units.resolved("region", region), expected)

    def test_nothing_chosen_is_by_region(self) -> None:
        self.assertEqual(units.resolved(None, "US"), "in")
        self.assertEqual(units.resolved("", "FR"), "cm")

    def test_a_value_nobody_offers_is_by_region(self) -> None:
        self.assertEqual(units.resolved("furlongs", "US"), "in")

    def test_the_devices_own_region_is_asked_when_none_is_given(self) -> None:
        with mock.patch("common.i18n.region", return_value="US"):
            self.assertEqual(units.resolved("region"), "in")
        with mock.patch("common.i18n.region", return_value="NL"):
            self.assertEqual(units.resolved("region"), "cm")


class ArithmeticTests(unittest.TestCase):
    def test_centimeters_are_shown_as_they_are(self) -> None:
        self.assertEqual(units.shown(95.89, "cm"), 95.9)

    def test_inches_are_shown_to_a_tenth(self) -> None:
        self.assertEqual(units.shown(95.89, "in"), 37.8)
        self.assertEqual(units.shown(53.94, "in"), 21.2)

    def test_what_is_typed_goes_back_to_centimeters(self) -> None:
        self.assertAlmostEqual(units.to_cm(37.8, "in"), 96.012)
        self.assertEqual(units.to_cm(96, "cm"), 96)


class TheSettingTests(unittest.TestCase):
    def test_the_setting_sits_beside_dates_and_times_and_defaults_by_region(self) -> None:
        option = config_schema.option("manager", "units")
        assert option is not None
        self.assertEqual(option.default, "region")
        self.assertEqual(set(option.choices), {"region", "cm", "in"})
        self.assertEqual(option.group, "measurements")

    def test_every_choice_but_by_region_is_a_unit_the_page_draws(self) -> None:
        option = config_schema.option("manager", "units")
        assert option is not None
        self.assertEqual(set(option.choices) - {units.BY_REGION}, {units.CM, units.INCH})


class RegionTests(unittest.TestCase):
    def named(self, system: str, language: str = "", platform: str = ""):
        return mock.patch.multiple("common.i18n", _from_the_system=lambda: system,
                                   _platform_locale=lambda: platform, _language=language)

    def test_the_region_is_the_country_in_the_locale(self) -> None:
        with self.named("en-US"):
            self.assertEqual(i18n.region(), "US")

    def test_a_script_is_not_a_region(self) -> None:
        with self.named("zh-Hans-CN"):
            self.assertEqual(i18n.region(), "CN")

    def test_a_language_alone_names_no_place(self) -> None:
        with self.named("en"):
            self.assertEqual(i18n.region(), "")

    def test_the_chosen_language_answers_where_the_system_does_not(self) -> None:
        with self.named("en", "de-AT"):
            self.assertEqual(i18n.region(), "AT")

    def test_the_operating_systems_locale_comes_before_the_chosen_language(self) -> None:
        with self.named("en", "de-AT", platform="en_US"):
            self.assertEqual(i18n.region(), "US")


class RegionFromTheMachineTests(unittest.TestCase):
    """The environment, `locale.getlocale()` and the operating system's own locale, each as
    a machine gives them."""

    def machine(self, *, env: dict[str, str], getlocale: tuple, platform: str,
                language: str = "") -> mock.Mock:
        self.enterContext(mock.patch.dict("os.environ", env, clear=True))
        self.enterContext(mock.patch("locale.getlocale", return_value=getlocale))
        self.enterContext(mock.patch("common.i18n._language", language))
        asked = mock.Mock(return_value=platform)
        self.enterContext(mock.patch("common.i18n._platform_locale", asked))
        return asked

    def test_windows_names_its_language_in_words_and_the_system_gives_the_region(self) -> None:
        self.machine(env={}, getlocale=("English_United States", "1252"), platform="en-US")

        self.assertEqual(i18n.region(), "US")

    def test_a_mac_with_no_lang_asks_the_system(self) -> None:
        self.machine(env={}, getlocale=("C", "UTF-8"), platform="en_US")

        self.assertEqual(i18n.region(), "US")

    def test_a_region_override_names_the_country(self) -> None:
        self.machine(env={}, getlocale=("C", "UTF-8"), platform="en_US@rg=gbzzzz")

        self.assertEqual(i18n.region(), "GB")

    def test_the_environment_wins_and_the_system_is_not_asked(self) -> None:
        asked = self.machine(env={"LANG": "de_DE.UTF-8"}, getlocale=("C", "UTF-8"),
                             platform="en_US")

        self.assertEqual(i18n.region(), "DE")
        asked.assert_not_called()

    def test_every_source_empty_names_no_place(self) -> None:
        self.machine(env={}, getlocale=(None, None), platform="")

        self.assertEqual(i18n.region(), "")


class PlatformLocaleTests(unittest.TestCase):
    def setUp(self) -> None:
        i18n._platform_locale.cache_clear()
        self.addCleanup(i18n._platform_locale.cache_clear)

    def test_a_mac_reads_apple_locale_once(self) -> None:
        done = subprocess.CompletedProcess([], 0, stdout="en_US\n", stderr="")
        with mock.patch("common.i18n.sys.platform", "darwin"), \
                mock.patch("common.i18n.subprocess.run", return_value=done) as run:
            self.assertEqual(i18n._platform_locale(), "en_US")
            self.assertEqual(i18n._platform_locale(), "en_US")

        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][-2:], ["-g", "AppleLocale"])

    def test_a_lookup_that_fails_is_nothing(self) -> None:
        for failure in (OSError("no defaults"), subprocess.TimeoutExpired("defaults", 2)):
            with self.subTest(failure=type(failure).__name__):
                i18n._platform_locale.cache_clear()
                with mock.patch("common.i18n.sys.platform", "darwin"), \
                        mock.patch("common.i18n.subprocess.run", side_effect=failure):
                    self.assertEqual(i18n._platform_locale(), "")

    def test_windows_asks_for_the_users_default_locale_name(self) -> None:
        def fill(name, size) -> None:
            name.value = "en-US"

        with mock.patch("common.i18n.sys.platform", "win32"), \
                mock.patch("ctypes.windll", create=True) as windll:
            windll.kernel32.GetUserDefaultLocaleName.side_effect = fill
            self.assertEqual(i18n._platform_locale(), "en-US")

    def test_another_platform_has_no_lookup(self) -> None:
        with mock.patch("common.i18n.sys.platform", "linux"), \
                mock.patch("common.i18n.subprocess.run") as run:
            self.assertEqual(i18n._platform_locale(), "")

        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()

"""Settings > Frontend > Browser: the flags a window opens with, as the three settings give them."""

from __future__ import annotations

import unittest

from frontend.chromium_manager import CHROMIUM_BASE_FLAGS
from manager.settings import chrome_options


def texts(held: dict) -> list[str]:
    return [line.text for line in chrome_options.lines(held)]


def by_name(held: dict, name: str) -> list:
    return [line for line in chrome_options.lines(held) if line.text.split("=")[0] == name]


class EffectiveFlags(unittest.TestCase):
    def test_the_built_in_flags_are_there_when_nothing_is_set(self) -> None:
        flags = texts({})

        self.assertTrue(set(CHROMIUM_BASE_FLAGS) <= set(flags))
        self.assertTrue(any(flag.startswith("--app=") for flag in flags))

    def test_what_is_added_comes_last_and_keeps_its_quoting(self) -> None:
        flags = texts(
            {"options": '--first\n--name="two words"'})

        self.assertEqual(flags[-2:], ["--first", "--name=two words"])

    def test_a_dropped_flag_leaves_the_rest(self) -> None:
        dropped = CHROMIUM_BASE_FLAGS[0]

        flags = texts({"options_exclude": dropped})

        self.assertNotIn(dropped, flags)
        self.assertIn(CHROMIUM_BASE_FLAGS[-1], flags)

    def test_turning_the_defaults_off_leaves_only_what_the_window_needs(self) -> None:
        for off in (False, "false", "False"):
            flags = texts({"defaults": off, "options": "--mine"})

            self.assertFalse(set(CHROMIUM_BASE_FLAGS) & set(flags), off)
            self.assertIn("--mine", flags)

    def test_a_switch_that_is_on_or_blank_keeps_the_defaults(self) -> None:
        for on in (True, "true", ""):
            self.assertTrue(set(CHROMIUM_BASE_FLAGS) <= set(
                texts({"defaults": on})), on)

    def test_a_flag_with_a_value_is_dropped_by_its_name(self) -> None:
        valued = next(flag for flag in CHROMIUM_BASE_FLAGS if "=" in flag)

        flags = texts({"options_exclude": valued.split("=")[0]})

        self.assertNotIn(valued, flags)

    def test_a_line_that_will_not_parse_adds_nothing(self) -> None:
        flags = texts({"options": '--bad="unclosed'})

        self.assertFalse(any("unclosed" in flag for flag in flags))


class Breakdown(unittest.TestCase):
    def test_each_line_says_where_it_came_from(self) -> None:
        sources = {line.text.split("=")[0]: line.source
                   for line in chrome_options.lines({"options": "--mine"})}

        self.assertEqual(sources["--window-size"], "window")
        self.assertEqual(sources["--kiosk"], "default")
        self.assertEqual(sources["--mine"], "yours")

    def test_a_default_an_added_option_replaces_is_still_listed_and_marked(self) -> None:
        (line,) = by_name({"options": "--log-level=0"}, "--log-level")[:1]
        self.assertEqual((line.text, line.replaced), ("--log-level=3", True))
        self.assertEqual(by_name({"options": "--log-level=0"}, "--log-level")[1].text,
                         "--log-level=0")

    def test_an_added_feature_list_is_merged_into_vpinfes_not_set_beside_it(self) -> None:
        (line,) = by_name({"options": "--disable-features=WebUSB,CalculateNativeWindowOcclusion"},
                          "--disable-features")

        self.assertTrue(line.text.endswith(",WebUSB"))
        self.assertEqual(line.merged, ("WebUSB",))
        self.assertFalse(line.replaced)

    def test_added_feature_lists_are_all_merged_once(self) -> None:
        (line,) = by_name({"options": "--disable-features=A\n--disable-features=B,A"},
                          "--disable-features")

        self.assertEqual(line.merged, ("A", "B"))

    def test_with_vpinfes_feature_list_left_out_the_added_one_stands_alone(self) -> None:
        shown = by_name({"options_exclude": "--disable-features",
                         "options": "--disable-features=WebUSB"}, "--disable-features")

        self.assertEqual([(line.text, line.source) for line in shown],
                         [("--disable-features=WebUSB", "yours")])

    def test_a_window_option_that_is_replaced_is_named(self) -> None:
        held = {"options": "--window-size=1,1\n--ozone-platform=x11"}

        self.assertEqual(chrome_options.replaced_window_options(chrome_options.lines(held)),
                         ["--window-size"])
        self.assertEqual(chrome_options.replaced_window_options(chrome_options.lines({})), [])

    def test_replacing_the_language_is_not_a_placement_warning(self) -> None:
        shown = chrome_options.lines({"options": "--lang=de"})

        self.assertEqual(chrome_options.replaced_window_options(shown), [])

    def test_an_unmatched_quote_is_named_and_adds_nothing(self) -> None:
        self.assertTrue(chrome_options.unmatched_quote({"options": '--bad="x'}))
        self.assertFalse(chrome_options.unmatched_quote({"options": '--ok="x y"'}))


class PreviewAfterASave(unittest.IsolatedAsyncioTestCase):
    async def test_a_saved_value_is_what_the_next_reading_uses(self) -> None:
        async def accepted(_value: object) -> bool:
            return True

        preview = chrome_options.Preview({"chromium": {"options": "--old"}})
        await preview.after("options", accepted)("--new")

        self.assertEqual(preview.held["options"], "--new")

    async def test_a_refused_save_leaves_the_reading_alone(self) -> None:
        async def refused(_value: object) -> bool:
            return False

        preview = chrome_options.Preview({"chromium": {"options": "--old"}})
        self.assertFalse(await preview.after("options", refused)("--new"))

        self.assertEqual(preview.held["options"], "--old")


class LeaveOutEntries(unittest.TestCase):
    NAMES = ["--kiosk", "--log-level", "--disable-features"]

    def test_a_known_option_is_listed_by_its_name(self) -> None:
        self.assertEqual(
            chrome_options._entries("--kiosk\n--log-level=3", self.NAMES),
            ["--kiosk", "--log-level"])

    def test_an_entry_that_is_not_a_default_is_kept_as_written(self) -> None:
        self.assertEqual(
            chrome_options._entries("--kiosk --mine=1", self.NAMES), ["--kiosk", "--mine=1"])

    def test_a_quoted_entry_is_read_as_the_window_launcher_reads_it(self) -> None:
        self.assertEqual(
            chrome_options._entries('"--kiosk" "--log-level=3"', self.NAMES),
            ["--kiosk", "--log-level"])
        self.assertEqual(
            chrome_options._entries('--kiosk "--unclosed', self.NAMES),
            ["--kiosk", '"--unclosed'])

    def test_the_same_option_twice_is_listed_once(self) -> None:
        self.assertEqual(
            chrome_options._entries("--kiosk\n--kiosk", self.NAMES), ["--kiosk"])

    def test_nothing_stored_leaves_nothing_out(self) -> None:
        self.assertEqual(chrome_options._entries("", self.NAMES), [])
        self.assertEqual(chrome_options._entries(None, self.NAMES), [])

    def test_the_names_offered_are_the_ones_a_window_starts_with(self) -> None:
        from common.devices import device_client

        names = device_client.local().browser_default_option_names()

        self.assertEqual(names, list(dict.fromkeys(flag.split("=")[0]
                                                   for flag in CHROMIUM_BASE_FLAGS)))
        self.assertIn("--disable-features", names)
        self.assertNotIn("--disable-features=CalculateNativeWindowOcclusion", names)


if __name__ == "__main__":
    unittest.main()

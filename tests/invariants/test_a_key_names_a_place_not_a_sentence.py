"""A key's last part names the thing, not the words of its own English."""

from __future__ import annotations

import re
import unittest

from tests.support.catalogs import served

KINDS = ("label", "help", "description", "blank")
LONGEST = 3

NAMED_BY_DATA = (
    "config.", "filter.", "media.kind.", "app.vpx.field.", "app.vpx.group.",
    "ext.library_importer.media.kind.",
)
NAMED_BY_AN_OUTSIDE_CONTRACT = ("frontend.theme.",)
EXEMPT_PREFIXES = NAMED_BY_DATA + NAMED_BY_AN_OUTSIDE_CONTRACT

# The id is what the VPX app declares for the value, or what a stored tip is filed under.
EXEMPT_KEYS = frozenset({
    "app.vpx.from_the_screen.help",
    "app.vpx.match_the_display",
    "manager.vpx_displays.in_the_playfield_window",
})


def _name(key: str) -> list[str]:
    parts = key.split(".")
    if len(parts) > 2 and parts[-1] in KINDS:
        parts = parts[:-1]
    return parts[-1].split("_")


def _words(value: object) -> list[str]:
    forms = value.values() if isinstance(value, dict) else [value]
    return re.findall(r"[a-z0-9]+", " ".join(str(form) for form in forms).lower())


def _in_order_in(name: list[str], words: list[str]) -> bool:
    at = iter(words)
    return all(part in at for part in name)


def _is_exempt(key: str) -> bool:
    return key in EXEMPT_KEYS or key.startswith(EXEMPT_PREFIXES)


def _checked() -> dict[str, object]:
    return {key: value for key, value in served().items() if not _is_exempt(key)}


class KeysNameAPlace(unittest.TestCase):
    def test_a_name_is_at_most_three_words(self) -> None:
        long = sorted(key for key in _checked() if len(_name(key)) > LONGEST)

        self.assertEqual(long, [])

    def test_a_three_word_name_is_not_the_words_of_its_own_text(self) -> None:
        copied = sorted(key for key, value in _checked().items()
                        if len(_name(key)) == LONGEST and _in_order_in(_name(key), _words(value)))

        self.assertEqual(copied, [])

    def test_each_exemption_still_names_a_key(self) -> None:
        keys = list(served())
        gone = [prefix for prefix in EXEMPT_PREFIXES if not any(k.startswith(prefix) for k in keys)]
        gone += [key for key in EXEMPT_KEYS if key not in keys]

        self.assertEqual(gone, [])

    def test_each_exempt_key_would_otherwise_fail(self) -> None:
        values = served()
        spared = [key for key in EXEMPT_KEYS
                  if len(_name(key)) <= LONGEST
                  and not (len(_name(key)) == LONGEST
                           and _in_order_in(_name(key), _words(values[key])))]

        self.assertEqual(spared, [])


class TheCopyCheck(unittest.TestCase):
    def test_words_of_the_text_in_order_are_a_copy(self) -> None:
        self.assertTrue(_in_order_in(["not", "in", "library"], _words("Not in this library")))

    def test_a_name_that_says_something_else_is_not(self) -> None:
        said = _words("Add these tables to a collection")
        self.assertFalse(_in_order_in(["parent", "tables", "now"], said))

    def test_a_leaf_with_a_kind_suffix_is_named_by_what_precedes_it(self) -> None:
        self.assertEqual(_name("manager.assets.reason.help"), ["reason"])
        self.assertEqual(_name("manager.assets.title"), ["title"])

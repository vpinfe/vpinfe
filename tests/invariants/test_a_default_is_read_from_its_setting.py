"""A default named on screen is read from the setting, never typed: `docs/conventions.md`,
"A default is read from its setting".

Choices and switches are left out: their descriptions say what each one does by name.
"""

from __future__ import annotations

import re
import unittest
from collections.abc import Callable, Iterable

from common.config import config_schema


def typed_defaults(options: Iterable[config_schema.ConfigOption],
                   described: Callable[[config_schema.ConfigOption], str]) -> list[str]:
    """Each setting whose description holds its own default, as a whole word."""
    found = []
    for option in options:
        default = str(option.default).strip()
        said = described(option)
        if not default or not said or option.type == "bool":
            continue
        if re.search(rf"(?<![\w.]){re.escape(default)}(?![\w.])", said):
            found.append(f"{option.section}.{option.key}: {said}")
    return found


class TypedDefaultTests(unittest.TestCase):
    def test_no_settings_description_holds_its_default(self) -> None:
        self.assertEqual(typed_defaults(config_schema.CONFIG_OPTIONS,
                                        lambda option: option.description), [])

    def test_the_check_finds_one_and_not_a_number_inside_another(self) -> None:
        length = config_schema.option("capture", "length")
        assert length is not None

        self.assertEqual(len(typed_defaults([length], lambda _: "Seconds to record, 20 by "
                                                               "default")), 1)
        self.assertEqual(typed_defaults([length], lambda _: "Stored 1920 wide, or 20.5"), [])

    def test_whose_default_it_is_and_its_unit_are_declared_words(self) -> None:
        for option in config_schema.CONFIG_OPTIONS:
            with self.subTest(f"{option.section}.{option.key}"):
                self.assertIn(option.default_is, ("", *config_schema.DEFAULTS_ARE))
                self.assertIn(option.unit, ("", *config_schema.UNITS))


if __name__ == "__main__":
    unittest.main()

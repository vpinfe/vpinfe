"""Config names follow the convention, and no old name stops resolving.

`docs/conventions.md` mandates snake_case for JSON, and the settings file is JSON. It
drifted anyway: every key was snake_case while ten of seventeen sections were not, because
nothing checked. A one-time tidy decays the same way, so the rule is asserted here rather
than remembered.

The second half matters more. Every rename keeps its old spelling working forever, which
is only true while the alias exists - and an alias is exactly the kind of line that looks
dead to whoever is cleaning up. Dropping one should fail here, not on a user's upgrade.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from common.config import config_schema
from common.config.config_access import cfg_get

SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")

# The outside witness: every spelling that has to keep resolving, frozen so that
# deleting an alias fails here instead of on somebody's upgrade.
FROZEN_NAMES = Path(__file__).resolve().parent.parent / "fixtures" / "config_legacy_names.json"
# Every section a 2.x install could have written, from the v2.6.1 defaults. A closed set:
# 2.6.1 shipped, so nothing is ever added to it.
TWO_X_SECTIONS = ("DOF", "Displays", "Input", "Logger", "Media", "Mobile", "Network",
                  "Settings", "State", "VPSdb", "libdmdutil", "pinmame-score-parser",
                  "vpinplay")


class NamingTests(unittest.TestCase):
    def test_every_section_is_snake_case(self) -> None:
        """A nested section is snake_case at every level: `windows.playfield`."""
        bad = sorted({section for section in {o.section for o in config_schema.options()}
                      if not all(SNAKE.match(part) for part in section.split("."))})
        self.assertEqual(bad, [], "sections are snake_case - see docs/conventions.md")

    def test_every_key_is_snake_case(self) -> None:
        bad = sorted(f"{o.section}.{o.key}" for o in config_schema.options()
                     if not SNAKE.match(o.key))
        self.assertEqual(bad, [])

    def test_no_section_is_named_for_the_envelope_around_it(self) -> None:
        """`settings.settings` reads as a mistake, which is why Settings became general."""
        self.assertNotIn("settings", {o.section for o in config_schema.options()})


class CompatibilityTests(unittest.TestCase):
    """Every spelling we have ever written still resolves to the setting it named.

    Checked against a frozen list rather than against the schema, because the schema is
    where an alias would be deleted from - iterating it would drop the assertion along
    with the thing it guards, and pass. Same reason `config_defaults.json` exists.
    """

    def _parser(self):
        from configparser import ConfigParser
        return ConfigParser()

    def test_every_name_we_have_ever_written_still_resolves(self) -> None:
        frozen = json.loads(FROZEN_NAMES.read_text(encoding="utf-8"))
        gone = [(s, k) for s, k, ws, wk in frozen
                if config_schema.locate(s, k) != (ws, wk)]
        self.assertEqual(gone, [], "an alias is a promise to whoever upgrades - restore "
                                   "it, or drop it from the fixture deliberately")

    def test_a_typed_accessor_reads_every_spelling_its_setting_has_had(self) -> None:
        """`config_schema.locate` resolving is not enough: the typed views ask by a
        literal section and key, so one written against the *new* name stops seeing a
        file that uses an old one. Nothing else catches that - `locate` still says the
        names map, and every test that builds config by hand uses the current spelling.
        """
        from common.config.config_access import NetworkConfig

        for section, key, expected in (
            ("Network", "manageruiport", 9999),      # pre-PAR-44 section and key
            ("network", "manageruiport", 9998),      # migrated section, oldest key
            ("network", "manager_ui_port", 8888),    # the PAR-44 spelling
            ("network", "http_port", 7777),           # what it is called now
        ):
            with self.subTest(section=section, key=key):
                parser = self._parser()
                parser.add_section(section)
                parser.set(section, key, str(expected))

                self.assertEqual(NetworkConfig.from_config(parser).http_port, expected)

    def test_the_frozen_list_covers_every_alias_declared_today(self) -> None:
        """So adding a rename without recording it is also a failure, not a gap."""
        frozen = {(s, k) for s, k, _, _ in
                  json.loads(FROZEN_NAMES.read_text(encoding="utf-8"))}
        missing = sorted({(e.section, a) for e in config_schema.options() for a in e.aliases
                          if (e.section, a) not in frozen}
                         | {pair for e in config_schema.options() for pair in e.legacy
                            if pair not in frozen})
        self.assertEqual(missing, [], "new aliases go in tests/fixtures/config_legacy_names.json")

    def test_every_2x_section_still_names_a_section_the_schema_has(self) -> None:
        """Every section a 2.x file can hold resolves to one the schema still declares.

        `Displays` and `Input` are exempt: their keys moved one at a time and carry
        their own `legacy` pairs, so neither section name outlives the move.
        `pinmame-score-parser` held only cache stamps, and `State` and `VPSdb` held only
        what an install keeps about itself, which is on its row in `devices.json`.
        """
        moved_per_key = {"Displays", "Input"}
        left_with_the_cache = {"pinmame-score-parser", "State", "VPSdb"}
        declared = {option.section for option in config_schema.options()}

        stale = sorted(section for section in TWO_X_SECTIONS
                       if section not in moved_per_key | left_with_the_cache
                       and config_schema.canonical_section(section) not in declared)

        self.assertEqual(stale, [], "add it to SECTION_RENAMES, or move its keys one at "
                                    "a time with a legacy pair each")

    def test_a_current_section_is_left_alone(self) -> None:
        for section in {o.section for o in config_schema.options()}:
            self.assertEqual(config_schema.canonical_section(section), section)

    def test_a_setting_reads_through_the_section_it_used_to_be_in(self) -> None:
        """End to end rather than through `locate` alone: a caller written against the
        old section name goes on working without being touched."""
        parser = self._parser()
        for old_section, old_key, section, key in json.loads(
                FROZEN_NAMES.read_text(encoding="utf-8")):
            if not parser.has_section(section):
                parser.add_section(section)
            parser.set(section, key, "sentinel")
            self.assertEqual(cfg_get(parser, old_section, old_key), "sentinel",
                             f"reading {old_section}.{old_key} stopped working")


if __name__ == "__main__":
    unittest.main()

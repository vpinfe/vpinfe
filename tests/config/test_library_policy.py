"""What an install collects is the `library` section of its settings.

Four lists of what is switched off. Nothing else holds them: no file of their own, no
route of their own.
"""

from __future__ import annotations

import json
import unittest

from common.config.config_store import SETTINGS_KEY, ConfigStore
from common.games.library_policy import LibraryPolicy
from tests.config.test_config_store import ConfigStoreTests


class LibraryPolicyTests(ConfigStoreTests):
    def setUp(self) -> None:
        super().setUp()
        self.store = ConfigStore(str(self.ini))
        self.policy = LibraryPolicy(self.store)

    def _saved(self) -> dict:
        return self._payload()[SETTINGS_KEY]["library"]

    def test_a_library_nobody_has_narrowed_collects_everything(self) -> None:
        """Empty in all of them, so a kind added in a later version arrives switched on."""
        self.assertEqual(self.policy.values(),
                         {"hidden_media_kinds": [], "hidden_asset_kinds": [],
                          "hidden_sources": [], "hidden_checks": []})

    def test_what_is_set_is_what_is_read_back(self) -> None:
        self.policy.set("hidden_media_kinds", ["loading", "audio"])

        self.assertEqual(self.policy.get("hidden_media_kinds"), ["loading", "audio"])

    def test_it_is_written_to_the_library_section_of_the_settings(self) -> None:
        self.policy.set("hidden_sources", ["vpuniverse"])

        self.assertEqual(self._saved()["hidden_sources"], ["vpuniverse"])

    def test_setting_one_leaves_the_others(self) -> None:
        self.policy.set("hidden_media_kinds", ["loading"])
        self.policy.set("hidden_sources", ["vpuniverse"])

        self.assertEqual(self.policy.get("hidden_media_kinds"), ["loading"])
        self.assertEqual(self.policy.get("hidden_sources"), ["vpuniverse"])

    def test_clearing_one_is_an_answer_rather_than_a_missing_one(self) -> None:
        """Empty means everything, so storing empty is how "collect it all" is said."""
        self.policy.set("hidden_asset_kinds", ["rom"])
        self.policy.set("hidden_asset_kinds", [])

        self.assertEqual(self.policy.get("hidden_asset_kinds"), [])
        self.assertEqual(self._saved()["hidden_asset_kinds"], [])

    def test_a_comma_string_reads_as_a_list(self) -> None:
        self.policy.set("hidden_media_kinds", "loading, audio")

        self.assertEqual(self.policy.get("hidden_media_kinds"), ["loading", "audio"])

    def test_it_refuses_a_key_that_is_not_a_library_policy(self) -> None:
        with self.assertRaises(ValueError):
            self.policy.set("game_root_dir", "/tables")

    def test_it_survives_being_reopened(self) -> None:
        self.policy.set("hidden_sources", ["vpsdb"])

        self.assertEqual(LibraryPolicy(ConfigStore(str(self.ini))).get("hidden_sources"),
                         ["vpsdb"])

    def test_no_file_of_its_own_is_written(self) -> None:
        self.policy.set("hidden_sources", ["vpsdb"])

        self.assertEqual(sorted(path.name for path in self.root.iterdir()),
                         ["vpinfe.json"])


class MovedFromGeneralTests(ConfigStoreTests):
    """The two lists a file written before this section still holds under `general`."""

    def _file_with(self, **held: list[str]) -> LibraryPolicy:
        ConfigStore(str(self.ini)).save()
        payload = self._payload()
        payload[SETTINGS_KEY]["general"].update(held)
        self.json.write_text(json.dumps(payload), encoding="utf-8")
        return LibraryPolicy(ConfigStore(str(self.ini)))

    def test_what_general_held_is_still_what_is_collected(self) -> None:
        policy = self._file_with(hidden_media_kinds=["loading", "audio"],
                                 hidden_asset_kinds=["rom"])

        self.assertEqual(policy.get("hidden_media_kinds"), ["loading", "audio"])
        self.assertEqual(policy.get("hidden_asset_kinds"), ["rom"])

    def test_the_next_save_writes_them_to_the_library_section(self) -> None:
        policy = self._file_with(hidden_media_kinds=["loading"])

        policy.set("hidden_sources", ["vpsdb"])

        saved = self._payload()[SETTINGS_KEY]
        self.assertEqual(saved["library"]["hidden_media_kinds"], ["loading"])
        self.assertNotIn("hidden_media_kinds", saved["general"])


if __name__ == "__main__":
    unittest.main()

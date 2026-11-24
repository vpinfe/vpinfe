"""One installation's identity: an id that survives, a name that addresses nothing.

The id is what a device registry keys on, so the properties that matter are that reading
never mints, that minting reaches disk, and that a second start adopts what the first
wrote rather than quietly becoming a different install.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from common.config.config_access import cfg_get, cfg_set
from common.config.config_store import ConfigStore
from common.devices import device_registry, install_identity


class InstallIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "state", "devices.json")
        self.addCleanup(device_registry.reset_for_tests)
        device_registry.reset_for_tests(Path(self.path))

    def _on_disk(self) -> dict:
        with open(self.path, encoding="utf-8") as handle:
            return json.load(handle)

    def test_a_fresh_install_has_no_id_until_one_is_minted(self) -> None:
        self.assertEqual(install_identity.install_id(), "")
        self.assertFalse(os.path.exists(self.path),
                         "reading an identity must never write one")

    def test_minting_persists_so_the_next_start_finds_it(self) -> None:
        minted = install_identity.ensure_id()

        self.assertEqual(self._on_disk()["self"], minted,
                         "an id that is not on disk is not an identity")

    def test_a_second_start_adopts_the_id_rather_than_minting_again(self) -> None:
        first = install_identity.ensure_id()
        device_registry.reset_for_tests(Path(self.path))

        self.assertEqual(install_identity.ensure_id(), first)

    def test_an_id_is_opaque_and_readable_aloud(self) -> None:
        minted = install_identity.ensure_id()

        self.assertEqual(len(minted), 10)
        self.assertFalse(set(minted) & set("0OIl"), "these are misread out loud")

    def test_minting_writes_the_registry_and_nothing_else(self) -> None:
        install_identity.ensure_id()

        self.assertEqual(sorted(os.listdir(self.tmp.name)), ["state"])
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ["devices.json"])

    def test_however_often_it_starts_there_is_one_id_and_one_row(self) -> None:
        ids = {install_identity.ensure_id() for _ in range(3)}

        self.assertEqual(len(ids), 1)
        self.assertEqual([row["device_id"] for row in self._on_disk()["devices"]], list(ids))


class DisplayNameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ConfigStore(os.path.join(self.tmp.name, "vpinfe.ini"))

    def test_it_falls_back_to_the_hostname(self) -> None:
        """So a single-machine user never meets the concept."""
        self.assertTrue(install_identity.display_name(self.store))

    def test_what_was_configured_wins(self) -> None:
        cfg_set(self.store, "device", "name", "basement cab")

        self.assertEqual(install_identity.display_name(self.store), "basement cab")

    def test_reading_the_default_does_not_write_it_down(self) -> None:
        """A machine that is renamed should follow, which freezing the first hostname
        this install ever saw would prevent."""
        install_identity.display_name(self.store)

        self.assertEqual(cfg_get(self.store, "device", "name"), "")


class FeatureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ConfigStore(os.path.join(self.tmp.name, "vpinfe.ini"))

    def _features(self, value: str) -> list[str]:
        cfg_set(self.store, "device", "features", value)
        return install_identity.features(self.store)

    def test_an_install_has_every_feature_by_default(self) -> None:
        """So nobody has to set it, and an install that wrote its own list keeps that."""
        self.assertEqual(install_identity.features(self.store),
                         ["core", "library", "frontend", "devices", "overview"])

    def test_one_feature_can_be_declared_on_its_own(self) -> None:
        self.assertEqual(self._features("frontend"), ["core", "frontend"])
        self.assertEqual(self._features("library"), ["core", "library"])
        self.assertEqual(self._features("devices"), ["core", "devices"])

    def test_features_read_the_same_however_they_were_written(self) -> None:
        for written in ("frontend,library", " library , frontend ", "LIBRARY,Frontend"):
            with self.subTest(written=written):
                self.assertEqual(self._features(written),
                                 ["core", "library", "frontend"])

    def test_a_typo_leaves_the_install_doing_what_it_did(self) -> None:
        """Falling back to everything, not to nothing: a misspelling must not decide that
        this machine has stopped launching games."""
        self.assertEqual(self._features("wat"),
                         ["core", "library", "frontend", "devices", "overview"])

    def test_a_recognized_feature_survives_an_unrecognized_one(self) -> None:
        self.assertEqual(self._features("library,wat"), ["core", "library"])

    def test_an_install_can_be_for_nothing_yet(self) -> None:
        """The setting is there and empty, which is somebody saying "none of them". It
        is a legal state because `core` keeps the settings that switch one back on."""
        self.assertEqual(self._features(""), ["core"])

    def test_a_setting_that_was_never_written_has_not_said_anything(self) -> None:
        """Apart from one written and emptied. A 2.x file names no features and is every
        bit as much a working install as it was."""
        import configparser

        bare = configparser.ConfigParser()

        self.assertEqual(install_identity.features(bare),  # type: ignore[arg-type]
                         ["core", "library", "frontend", "devices", "overview"])

    def test_core_is_not_a_value_the_file_decides(self) -> None:
        """It is synthesized either way, so naming it says nothing about the optional
        ones - a list holding only it has not said, exactly like a typo."""
        self.assertEqual(self._features("core"),
                         ["core", "library", "frontend", "devices", "overview"])
        self.assertEqual(self._features("core,library"), ["core", "library"])

    def test_has_feature_answers_for_one(self) -> None:
        cfg_set(self.store, "device", "features", "library")

        self.assertTrue(install_identity.has_feature(self.store, "library"))
        self.assertFalse(install_identity.has_feature(self.store, "frontend"))


if __name__ == "__main__":
    unittest.main()

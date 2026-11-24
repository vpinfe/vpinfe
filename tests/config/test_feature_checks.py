"""A feature is enabled and its configuration does not let it work."""

from __future__ import annotations

import configparser
import os
import unittest
from tempfile import TemporaryDirectory

from common.config import path_state
from common.devices import install_identity
from common.games.launchers import Launcher
from common.games.library_folders import Location, state_of
from common.settings import feature_checks


class RequirementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.games = os.path.join(self.tmp.name, "games")
        os.mkdir(self.games)
        self.launcher = os.path.join(self.tmp.name, "VPinballX")
        with open(self.launcher, "w", encoding="utf-8"):
            pass
        os.chmod(self.launcher, 0o755)

    def _config(self, *, library_url: str = "", **general: str):
        parser = configparser.ConfigParser()
        parser.add_section("general")
        for key, value in general.items():
            parser.set("general", key, value)
        parser.add_section("network")
        parser.set("network", "library_url", library_url)
        return parser

    def _frontend_config(self, **general: str):
        """A frontend that has been told which library to read, so a test about a path is
        about that path and not about the library as well."""
        return self._config(library_url="http://elsewhere:8001", **general)

    def _locations(self, *paths: str):
        """The locations the install has, resolved the way the caller resolves them."""
        held = [Location(location_id=f"loc{n}", path=path)
                for n, path in enumerate(paths)]
        return [(one, state_of(one)) for one in held]

    def _launcher_for(self, bin_path: str | None):
        """What the install would launch with. None means it has no launcher at all."""
        if bin_path is None:
            return None
        return Launcher(launcher_id="l1", app="vpx", display_name="Visual Pinball X",
                        settings={"bin_path": bin_path})

    def _unmet(self, config, features, bin_path="__ok__", locations="__ok__"):
        found = self._launcher_for(self.launcher if bin_path == "__ok__" else bin_path)
        held = self._locations(self.games) if locations == "__ok__" else locations
        return [(u.feature, u.key or u.where, u.state)
                for u in feature_checks.unmet(config, features, launcher=found,
                                              locations=held)]

    def test_a_fully_configured_install_reports_nothing(self) -> None:
        config = self._config(game_root_dir=self.games)

        self.assertEqual(feature_checks.unmet(config, install_identity.FEATURES,
                                              launcher=self._launcher_for(self.launcher)),
                         [])

    def test_the_frontend_needs_a_launcher(self) -> None:
        """An install with no launcher at all. It leads to the Launchers list rather than
        to a setting, because adding one is not something a settings field does."""
        config = self._frontend_config(game_root_dir=self.games)

        self.assertEqual(self._unmet(config, ["frontend"], bin_path=None),
                         [("frontend", feature_checks.WHERE_LAUNCHERS,
                           path_state.UNSET)])

    def test_the_library_does_not_need_a_launcher(self) -> None:
        """It curates games; it never starts one. Requiring it would mark a catalog
        install broken for lacking something it has no use for."""
        config = self._config(game_root_dir=self.games)

        self.assertEqual(feature_checks.unmet(config, ["library"]), [])

    def test_a_requirement_two_features_need_is_reported_against_each(self) -> None:
        """Somebody is looking at one feature's page and needs to know that page is
        affected - not that some other feature is also unhappy about the same thing."""
        config = self._config()

        self.assertEqual(self._unmet(config, ["library", "frontend"], locations=[]),
                         [("library", feature_checks.WHERE_SETTINGS, path_state.UNSET),
                          ("frontend", feature_checks.WHERE_SETTINGS,
                           path_state.UNSET)])

    def test_one_location_among_several_being_unreachable_is_not_a_fault(self) -> None:
        """That row's business, not the install's: the library still has the others."""
        held = self._locations(self.games, os.path.join(self.tmp.name, "never-mounted"))

        self.assertEqual(self._unmet(self._config(), ["library"], locations=held), [])

    def test_nothing_reachable_at_all_is_a_fault(self) -> None:
        held = self._locations(os.path.join(self.tmp.name, "never-mounted"))

        self.assertEqual(self._unmet(self._config(), ["library"], locations=held),
                         [("library", feature_checks.WHERE_SETTINGS,
                           path_state.UNSET)])

    def test_no_folder_is_filed_against_the_folders_page_not_a_setting(self) -> None:
        """The fix is the Folders page, which is not a config key: the finding carries no
        key, and its section is how the Manager finds the page."""
        found = feature_checks.unmet(self._config(), ["library"],
                                     launcher=self._launcher_for(self.launcher),
                                     locations=[])

        self.assertEqual([(one.where, one.section, one.key) for one in found],
                         [(feature_checks.WHERE_SETTINGS, feature_checks.FOLDERS, "")])

    def test_a_path_that_is_set_and_wrong_is_not_the_same_as_unset(self) -> None:
        """The reason differs, and so does the fix: one is 'fill this in', the other is
        'what you filled in is not there'."""
        config = self._frontend_config(game_root_dir=self.games)

        self.assertEqual(self._unmet(config, ["frontend"], bin_path="/nope/vpx"),
                         [("frontend", feature_checks.WHERE_LAUNCHERS,
                           path_state.MISSING)])

    def test_a_frontend_with_no_library_has_to_be_told_which_one(self) -> None:
        """No silent picking, not even when exactly one is on the network."""
        config = self._config(game_root_dir=self.games)

        self.assertEqual(self._unmet(config, ["frontend"]),
                         [("frontend", "library_url", path_state.UNSET)])

    def test_a_frontend_that_holds_its_own_library_needs_no_picker(self) -> None:
        """The single-machine case, which is the common one."""
        config = self._config(game_root_dir=self.games)

        self.assertEqual(feature_checks.unmet(config, ["library", "frontend"],
                                              launcher=self._launcher_for(self.launcher)),
                         [])

    def test_a_library_chosen_settles_it(self) -> None:
        config = self._frontend_config(game_root_dir=self.games)

        self.assertEqual(feature_checks.unmet(config, ["frontend"],
                                              launcher=self._launcher_for(self.launcher)),
                         [])

    def test_managing_devices_requires_nothing_of_its_own(self) -> None:
        """It reaches other installs over the network. An address that does not answer is
        that device's row to report, not a setting on this one."""
        self.assertEqual(feature_checks.unmet(self._config(), ["devices"]), [])

    def test_a_feature_that_is_off_is_not_checked(self) -> None:
        """Only what the operator asked for. An install that does not launch games is not
        misconfigured for having no launcher."""
        self.assertEqual(feature_checks.unmet(self._config(), ["devices"]), [])
        self.assertNotEqual(feature_checks.unmet(self._config(), ["frontend"]), [])

    def test_every_reason_says_what_is_wrong(self) -> None:
        """It is shown to the person who has to fix it, so 'no' on its own is not enough."""
        config = self._config()

        for item in feature_checks.unmet(config, install_identity.FEATURES):
            with self.subTest(setting=item.key):
                self.assertTrue(item.reason.strip(), f"{item.key} gave no reason")

    def test_features_in_trouble_names_them_once(self) -> None:
        """A frontend with no library to read and no launcher is in trouble twice, and
        is named once."""
        config = self._config()

        self.assertEqual(feature_checks.features_in_trouble(config, ["frontend"]),
                         {"frontend"})


if __name__ == "__main__":
    unittest.main()

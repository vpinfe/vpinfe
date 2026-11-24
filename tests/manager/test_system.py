"""System is always reachable, and everything beside it is not.

The bootstrap case is the one worth pinning: an install with nothing switched on still
has to be able to switch something on, and every other section in the rail is gone by
then.
"""

import re
import unittest
from unittest import mock
from urllib.parse import parse_qs

from common.config import path_state
from common.devices import install_identity
from common.i18n import t
from common.settings import feature_checks
from manager import page
from manager.kit import appearance, deeplink, panel
from manager.settings import settings as settings_page


def _rail(features) -> list[str]:
    return [key for _parent, items in page.nav_for(features) for key, *_rest in items]


def _pages(features) -> list[str]:
    return [item[0] for _group, item in settings_page.system_pages(features)]


class NavTests(unittest.TestCase):
    """What the rail holds, for the features an install declares."""

    def test_every_section_is_there_when_every_feature_is(self) -> None:
        rail = _rail(install_identity.FEATURES)

        self.assertEqual(rail, [key for _parent, items in page.NAV_GROUPS
                                for key, *_rest in items])

    def test_no_row_is_drawn_deeper_than_a_page_under_its_group(self) -> None:
        drawn = set(re.findall(r"\.manager-nav-row--([a-z-]+)", appearance.base_css()))

        self.assertEqual({"nested", "out"}, drawn)

    def test_system_survives_an_install_that_is_for_nothing(self) -> None:
        """The bootstrap case: features are switched on from in here, so what System
        holds is the only rail an install for nothing has."""
        for features in (["nonsense"], [install_identity.CORE]):
            with self.subTest(features=features):
                self.assertEqual(_rail(features),
                                 ["settings", "extensions", "metrics", "logs", "jobs", "backups",
                                  "about"])

    def test_a_frontend_only_install_opens_on_what_it_has(self) -> None:
        """Players, because that is the first real place such an install holds. It has
        no library, so there are no Games to open on."""
        self.assertEqual(page.landing_for(_rail([install_identity.FRONTEND])),
                         "players")

    def test_an_install_for_nothing_opens_on_settings(self) -> None:
        """Settings is `core` and first in System, so it is always present and always
        what the rail falls back to."""
        self.assertEqual(page.landing_for(_rail([install_identity.CORE])), "settings")

    def test_an_install_that_never_chose_features_opens_on_overview(self) -> None:
        self.assertEqual(page.landing_for(_rail(install_identity.DEFAULT_FEATURES)),
                         "overview")

    def test_an_install_without_overview_opens_on_games(self) -> None:
        self.assertEqual(page.landing_for(_rail([install_identity.LIBRARY,
                                                 install_identity.FRONTEND,
                                                 install_identity.DEVICES])), "games")

    def test_a_front_door_is_always_in_the_rail_whatever_is_switched_on(self) -> None:
        """Exhaustive, because an ordering has to hold for every combination."""
        import itertools

        switchable = list(install_identity.FEATURES)
        for size in range(len(switchable) + 1):
            for chosen in itertools.combinations(switchable, size):
                rail = _rail([install_identity.CORE, *chosen])
                with self.subTest(features=chosen):
                    self.assertIn(page.landing_for(rail), rail)

    def test_where_you_left_off_wins_when_the_rail_still_holds_it(self) -> None:
        rail = _rail(install_identity.DEFAULT_FEATURES)
        self.assertIn("media", rail)
        self.assertEqual("media", page.landing_for(rail, "media"))

    def test_a_section_the_install_no_longer_has_is_dropped(self) -> None:
        rail = _rail([install_identity.FRONTEND])
        self.assertNotIn("media", rail)
        self.assertEqual(page.landing_for(rail), page.landing_for(rail, "media"))

    def test_remembering_nothing_lands_where_it_always_did(self) -> None:
        rail = _rail(install_identity.DEFAULT_FEATURES)
        self.assertEqual(page.landing_for(rail), page.landing_for(rail, ""))

    def test_reporting_nothing_is_not_the_same_as_being_for_nothing(self) -> None:
        """An install that has not answered gets the ordinary rail. One that answered
        with an empty list is for nothing - as `install_identity.features` reads an empty
        setting - and has System alone."""
        self.assertIn("games", _rail(None))
        self.assertNotIn("games", _rail([]))
        self.assertEqual(_rail([]), _rail([install_identity.CORE]))

    def test_system_is_a_container(self) -> None:
        """It collapsed into its first child while `build_system` drew the index
        directly. Records are places; configuration is a setting."""
        under_system = [items for parent, items in page.nav_for(install_identity.FEATURES)
                        if parent == page.NAV_SYSTEM]

        self.assertEqual([key for key, *_rest in under_system[0]],
                         ["settings", "devices", "extensions", "metrics", "logs", "jobs", "backups",
                          "about"])

    def test_what_the_frontend_owns_has_a_container_of_its_own(self) -> None:
        """The rule Library and this one make together: a feature with more than one
        subject gets a container. Launchers sat under System and never fitted - System's
        three are configuration and records, and a launcher is neither."""
        under_frontend = [items for parent, items
                          in page.nav_for(install_identity.FEATURES)
                          if parent == page.NAV_FRONTEND]

        self.assertEqual([key for key, *_rest in under_frontend[0]],
                         ["players", "launchers", "themes"])

    def test_launchers_goes_with_the_feature_that_launches(self) -> None:
        """An install that curates a library and never starts a game has nothing to run
        a table with and no reason to be offered one - and with nothing left under it,
        the container goes too."""
        self.assertNotIn("launchers", _rail([install_identity.LIBRARY]))
        self.assertNotIn(page.NAV_FRONTEND,
                         [parent for parent, _items
                          in page.nav_for([install_identity.LIBRARY])])
        self.assertIn("launchers", _rail([install_identity.FRONTEND]))

    def test_overview_is_on_by_default_and_can_be_switched_off(self) -> None:
        self.assertIn(install_identity.OVERVIEW, install_identity.DEFAULT_FEATURES)
        self.assertIn("overview", _rail(install_identity.DEFAULT_FEATURES))
        self.assertNotIn("overview", _rail([install_identity.LIBRARY,
                                            install_identity.FRONTEND,
                                            install_identity.DEVICES]))

    def test_core_is_never_one_of_the_switchable_features(self) -> None:
        """Synthesized on every read and never stored, so it is not in the set a person
        chooses from and cannot be edited out of a list."""
        self.assertNotIn(install_identity.CORE, install_identity.FEATURES)
        self.assertNotIn(install_identity.CORE, install_identity.DEFAULT_FEATURES)

    def test_an_unreadable_setting_falls_back_to_the_defaults(self) -> None:
        import configparser

        config = configparser.ConfigParser()
        config.add_section("device")
        config.set("device", "features", "libary, frontnd")

        self.assertEqual([install_identity.CORE, *install_identity.DEFAULT_FEATURES],
                         install_identity.features(config))  # type: ignore[arg-type]

    def test_features_written_without_overview_stay_without_it(self) -> None:
        import configparser

        config = configparser.ConfigParser()
        config.add_section("device")
        config.set("device", "features", "library, frontend, devices")

        self.assertNotIn(install_identity.OVERVIEW,
                         install_identity.features(config))  # type: ignore[arg-type]

    def test_no_library_takes_the_library_sections_with_it(self) -> None:
        rail = _rail([install_identity.FRONTEND])

        self.assertNotIn("games", rail)
        self.assertNotIn("collections", rail)
        self.assertIn("settings", rail)

    def test_a_group_with_nothing_left_in_it_goes_too(self) -> None:
        """A disclosure with no entries under it is a control that does nothing."""
        parents = [parent for parent, _items in page.nav_for([install_identity.DEVICES])]

        self.assertNotIn(page.NAV_PARENT, parents)

    def test_devices_needs_the_feature_that_manages_them(self) -> None:
        self.assertNotIn("devices", _rail([install_identity.LIBRARY]))
        self.assertIn("devices", _rail([install_identity.DEVICES]))


class SystemIndexTests(unittest.TestCase):
    """What System offers, which is identity plus whatever the features can answer."""

    def test_identity_leads_and_is_not_feature_derived(self) -> None:
        for features in ([], [install_identity.LIBRARY], install_identity.FEATURES):
            with self.subTest(features=features):
                self.assertEqual(_pages(features)[0], settings_page.IDENTITY)

    def test_a_page_belonging_to_a_feature_goes_with_it(self) -> None:
        held = _pages([install_identity.DEVICES])

        self.assertNotIn("library.media", held)
        self.assertNotIn("hardware.displays", held)
        self.assertIn("library.media", _pages([install_identity.LIBRARY]))

    def test_the_install_wide_pages_are_always_offered(self) -> None:
        """They name `core`, which is how a page says it is here on every install
        rather than leaving that to an empty string a reader has to know about."""
        held = _pages([install_identity.DEVICES])

        self.assertIn("vpinfe.manager", held)
        self.assertIn("vpinfe.network", held)
        self.assertIn("vpinfe.logger", held)

    def test_every_page_names_a_feature(self) -> None:
        """`core` where it belongs to the install as a whole. An unnamed one would be
        filtered out rather than always offered, which is the failure worth pinning."""
        for group, pages in settings_page.DEVICE_INDEX:
            for page_item in pages:
                with self.subTest(group=group, page=page_item[0]):
                    named = {page_item[4]} if isinstance(page_item[4], str) \
                        else set(page_item[4])
                    self.assertLessEqual(
                        named, {install_identity.CORE, *install_identity.FEATURES})
                    self.assertTrue(named)

    def test_a_page_two_features_share_is_there_for_either(self) -> None:
        """The folders are read by a library and by a frontend, and neither needs the
        other to have them."""
        for features in ([install_identity.LIBRARY], [install_identity.FRONTEND],
                         [install_identity.LIBRARY, install_identity.FRONTEND]):
            with self.subTest(features=features):
                self.assertIn("library.folders", _pages(features))

        self.assertNotIn("library.folders", _pages([install_identity.DEVICES]))
        self.assertNotIn("library.folders", _pages([install_identity.CORE]))

    def test_the_folders_page_leads_the_library_group(self) -> None:
        library = [one for group, one in settings_page.system_pages(
            [install_identity.LIBRARY]) if group == "manager.settings.group_library"]

        self.assertEqual(library[0][0], "library.folders")

    def test_the_library_rail_reads_folders_media_assets_checks_automatic_discovery(self) -> None:
        library = [one for group, one in settings_page.system_pages(
            [install_identity.LIBRARY]) if group == "manager.settings.group_library"]

        self.assertEqual(["Folders", "Media", "Assets", "Checks", "Automatic", "Discovery"],
                         [t(one[1]) for one in library])
        self.assertEqual(["library.folders", "library.media", "library.assets",
                          "library.checks", "library.updates", "library.discovery"],
                         [one[0] for one in library])

    def test_the_metadata_page_is_not_on_the_rail(self) -> None:
        offered = [one[0] for _group, one in settings_page.system_pages(
            install_identity.FEATURES)]

        self.assertNotIn("library.metadata", offered)
        self.assertNotIn("library.metadata", settings_page.PAGES)
        self.assertNotIn("library.metadata", settings_page.PAGE_NOTES)

    def test_the_discovery_page_is_in_the_library_group_and_drawn(self) -> None:
        """Opening Settings at a page that is not in the index falls back to the first
        page, so a mistyped key would open the wrong screen without failing."""
        library = [one[0] for group, one in settings_page.system_pages(
            [install_identity.LIBRARY]) if group == "manager.settings.group_library"]

        self.assertIn(settings_page.DISCOVERY_PAGE, library)
        self.assertIn(settings_page.DISCOVERY_PAGE, settings_page.PAGES)
        self.assertIn(settings_page.DISCOVERY_PAGE, settings_page.PAGE_NOTES)

    def test_identity_survives_an_install_that_is_for_nothing(self) -> None:
        """The whole point of `core`: the screen that switches a feature back on is the
        one screen an install with everything off still has."""
        self.assertEqual(_pages([install_identity.CORE]),
                         [settings_page.IDENTITY, "vpinfe.network", "vpinfe.security",
                          "vpinfe.logger",
                          "vpinfe.backups", "vpinfe.manager", "vpinfe.commands",
                          "vpinfe.tools"])


class AddressTests(unittest.TestCase):
    """A page of System has to survive being written down and read back."""

    def test_the_page_is_named_in_the_address(self) -> None:
        address = parse_qs(deeplink.query({"view": "settings",
                                           "settings_page": "network"}))

        self.assertEqual(address["page"], ["network"])

    def test_a_page_name_is_noise_anywhere_else(self) -> None:
        address = parse_qs(deeplink.query({"view": "games",
                                           "settings_page": "network"}))

        self.assertNotIn("page", address)


class TroubleTests(unittest.TestCase):
    """A configuration error has to lead from the nav down to the field that fixes it."""

    def _unmet(self, section: str, key: str, state: str = path_state.MISSING):
        return feature_checks.Unmet(feature=install_identity.FRONTEND, section=section,
                                    key=key, state=state, reason="Nothing is there.")

    def test_a_requirement_is_keyed_by_the_page_that_holds_it(self) -> None:
        held = settings_page.pages_in_trouble([self._unmet("network", "library_url")])

        self.assertEqual(list(held), ["vpinfe.network"])

    def test_no_folder_is_keyed_to_the_folders_page(self) -> None:
        """Its section names no settings page, so it is keyed by name rather than found
        by the section's owner."""
        found = feature_checks.Unmet(
            feature=install_identity.LIBRARY, section=feature_checks.FOLDERS, key="",
            state=path_state.UNSET, reason="Nothing is there.")

        held = settings_page.pages_in_trouble([found])

        self.assertEqual(list(held), ["library.folders"])

    def test_a_backups_folder_that_cannot_be_written_to_marks_the_backups_page(self) -> None:
        with mock.patch.object(settings_page.backups_folder, "refused",
                               return_value=("Not mounted.", path_state.NOT_CONNECTED)):
            found = settings_page._backups_folder_trouble()

        self.assertEqual([one.reason for one in found], ["Not mounted."])
        self.assertEqual(list(settings_page.pages_in_trouble(found)), ["vpinfe.backups"])

    def test_a_missing_backups_folder_keeps_the_state_the_disk_reported(self) -> None:
        """The field beside the page's mark draws from this state, and `unusable` is the
        amber of a program that does not run, not the red of a place that is not there."""
        with mock.patch.object(settings_page.backups_folder, "refused",
                               return_value=("Gone.", path_state.MISSING)):
            found = settings_page._backups_folder_trouble()

        self.assertEqual([one.state for one in found], [path_state.MISSING])

    def test_a_backups_folder_with_nothing_wrong_marks_nothing(self) -> None:
        with mock.patch.object(settings_page.backups_folder, "refused",
                               return_value=("", path_state.OK)):
            self.assertEqual(settings_page._backups_folder_trouble(), [])

    def test_local_trouble_carries_the_backups_folder(self) -> None:
        from common.games import launchers, library_folders
        mine = mock.sentinel.backups_folder_finding
        with mock.patch.object(launchers, "get_launcher_store"), \
                mock.patch.object(launchers, "default_for"), \
                mock.patch.object(library_folders, "configured", return_value=[]), \
                mock.patch.object(library_folders, "states_of", return_value={}), \
                mock.patch.object(settings_page.feature_checks, "unmet", return_value=[]), \
                mock.patch.object(settings_page, "_extension_trouble", return_value=[]), \
                mock.patch.object(settings_page, "_backups_folder_trouble",
                                  return_value=[mine]):
            self.assertEqual(settings_page.local_trouble(), [mine])

    def test_a_section_is_drawn_by_no_more_than_one_page(self) -> None:
        """`_page_holding` answers with the first page that draws a section, so a
        section on two pages sends every badge for it to whichever comes first.

        A page drawing several sections is the other direction and is fine: Displays
        draws four, and each of the four still names Displays back.
        """
        seen: dict[str, str] = {}
        twice = []
        for _group, pages in settings_page.DEVICE_INDEX:
            for entry in pages:
                for section in entry[3]:
                    if section in seen:
                        twice.append(f"{section} is on {seen[section]} and {entry[0]}")
                    seen[section] = entry[0]

        self.assertEqual(sorted(twice), [],
                         "a page names a whole section, so two pages drawing one puts "
                         "its badge on the wrong page")

    def test_a_setting_on_no_page_is_dropped_rather_than_counted(self) -> None:
        """A badge that leads nowhere is worse than no badge."""
        with self.assertLogs("vpinfe.manager.settings", level="WARNING"):
            held = settings_page.pages_in_trouble([self._unmet("nowhere", "thing")])

        self.assertEqual(held, {})

    def test_the_disk_answers_until_a_feature_says_otherwise(self) -> None:
        checks = [{"section": "general", "key": "vpx_bin_path", "state": path_state.OK,
                   "reason": ""}]
        marks = settings_page.field_marks([self._unmet("general", "vpx_bin_path")],
                                          checks)

        self.assertEqual(marks[("general", "vpx_bin_path")]["reason"],
                         "Nothing is there.")

    def test_blank_where_blank_is_not_allowed_draws_something(self) -> None:
        """An optional path left empty draws nothing, and this is not one of those."""
        marks = settings_page.field_marks(
            [self._unmet("general", "vpx_bin_path", path_state.UNSET)], [])

        self.assertEqual(marks[("general", "vpx_bin_path")]["state"], panel.REQUIRED)
        self.assertIn(panel.REQUIRED, panel._VALUE_STATES)


class EveryPathStateDrawsAMark(unittest.TestCase):
    def test_a_setting_can_be_in_each_state_a_look_gives_it(self) -> None:
        """A state with no entry draws nothing, tooltip included, and reads as fine. Blank
        draws nothing on purpose, and a setting never asks to be writable."""
        asked = (path_state.OK, path_state.NOT_CONNECTED, path_state.MISSING,
                 path_state.WRONG_KIND, path_state.NOT_EXECUTABLE, path_state.NOT_ANSWERING)

        for state in asked:
            self.assertIn(state, panel._VALUE_STATES, state)


class TheInstallYouAreSittingAtReportsItsOwnUpdate(unittest.TestCase):
    """A caller asks every device whether a newer build is published, and gets the local
    client back for the one it is running in.

    That client does not offer the call - it is this process, and asking it to reach the
    network for a version it already knows would be the install phoning itself. So the
    call raised, the caller logged that a device could not be asked, and every machine on
    the network reported correctly except the one in front of you. Silent, because a
    device that cannot be reached is an ordinary thing.
    """

    def test_the_local_device_is_asked_through_this_install_s_own_api(self) -> None:
        from manager.devices import devices as devices_page

        ask = devices_page.update_checker(True, object())

        self.assertIsNotNone(ask)
        self.assertEqual(getattr(ask, "__name__", ""), "update_check")

    def test_another_machine_is_still_asked_directly(self) -> None:
        from manager.devices import devices as devices_page

        class Remote:
            def update_check(self):
                return {"update_available": True}

        client = Remote()
        ask = devices_page.update_checker(False, client)

        self.assertEqual(ask(), {"update_available": True})

    def test_a_device_that_cannot_be_asked_answers_with_nothing(self) -> None:
        """A phone speaks a different protocol entirely and has no such call."""
        from manager.devices import devices as devices_page

        self.assertIsNone(devices_page.update_checker(False, object()))

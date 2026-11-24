"""Launchers: the store, and the one function that says which one plays a table.

The resolution order is the part worth pinning. It is what the grid's column and the
launch path both read, and them disagreeing means what will actually happen at launch
is not visible anywhere before it happens.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from common import apps
from common.config.config_store import ConfigStore
from common.games import launchers


def _launcher(launcher_id: str, **kwargs) -> launchers.Launcher:
    return launchers.Launcher(launcher_id=launcher_id,
                              app=kwargs.pop("app", "vpx"),
                              display_name=kwargs.pop("display_name", launcher_id),
                              **kwargs)


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = launchers.LauncherStore(self.tmp.name)
        self.store.save([], {})

    def test_a_first_run_has_the_shipped_launcher_and_no_assignments(self) -> None:
        fresh = launchers.LauncherStore(Path(self.tmp.name) / "fresh")

        self.assertEqual([one.app for one in fresh.launchers()], ["vpx"])
        self.assertEqual(fresh.mappings(), {})

    def test_a_launcher_survives_being_written_and_read(self) -> None:
        one = _launcher("a", settings={"bin_path": "/usr/bin/vpx"}, owns_ini=True)

        self.store.put(one)

        got = self.store.get("a")
        assert got is not None
        self.assertEqual(got.settings["bin_path"], "/usr/bin/vpx")
        self.assertTrue(got.owns_ini)

    def test_putting_the_same_id_replaces_in_place(self) -> None:
        """Order is what says which launcher is the default, so a replacement that moved
        to the end would silently change which one plays every unassigned table."""
        self.store.put(_launcher("a"))
        self.store.put(_launcher("b"))

        self.store.put(_launcher("a", display_name="renamed"))

        self.assertEqual([one.launcher_id for one in self.store.launchers()],
                         ["a", "b"])
        renamed = self.store.get("a")
        assert renamed is not None
        self.assertEqual(renamed.display_name, "renamed")

    def test_a_table_moves_off_a_launcher_whose_app_is_gone(self) -> None:
        """Moving a table changes only which launcher lists it, so a launcher this install
        can no longer name does not block it."""
        import json

        self.store.put(_launcher("a"))
        self.store.put(_launcher("b"))
        self.store.assign("T1", "b")
        path = Path(self.tmp.name) / "vpinfe.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        for item in data["launchers"]:
            if item["id"] == "b":
                item["app"] = "gone"
        path.write_text(json.dumps(data), encoding="utf-8")

        self.store.assign("T1", "a")

        self.assertEqual(self.store.mapped("T1"), "a")

    def test_removing_takes_its_mappings_with_it(self) -> None:
        """A table pointing at a launcher that was deleted is not a state anybody chose,
        so it goes back to the default rather than being left dangling."""
        self.store.put(_launcher("a"))
        self.store.put(_launcher("b"))
        self.store.assign("table-1", "a")
        self.store.assign("table-2", "b")

        self.assertTrue(self.store.remove("a"))

        self.assertEqual(self.store.mappings(), {"table-2": "b"})

    def test_removing_something_that_is_not_there_says_so(self) -> None:
        self.assertFalse(self.store.remove("nope"))

    def test_to_front_keeps_the_rest_in_order_and_their_mappings(self) -> None:
        for one in "abc":
            self.store.put(_launcher(one))
        self.store.assign("table-1", "b")

        self.assertTrue(self.store.to_front("c"))

        self.assertEqual([one.launcher_id for one in self.store.launchers()],
                         ["c", "a", "b"])
        self.assertEqual(self.store.mappings(), {"table-1": "b"})

    def test_to_front_of_something_that_is_not_there_says_so(self) -> None:
        self.store.put(_launcher("a"))
        self.assertFalse(self.store.to_front("nope"))

    def test_clearing_an_assignment_drops_the_row(self) -> None:
        """An absent mapping already means "the default", so a blank one would be a
        second spelling of one state."""
        self.store.put(_launcher("a"))
        self.store.assign("table-1", "a")

        self.store.assign("table-1", "")

        self.assertEqual(self.store.mappings(), {})

    def test_a_table_listed_on_two_launchers_belongs_to_the_first(self) -> None:
        """Hand-edited. Two launchers claiming one table would make which one plays it
        depend on which was read last."""
        self.store.put(_launcher("a"))
        self.store.put(_launcher("b"))
        config = ConfigStore(str(Path(self.tmp.name) / "vpinfe.json"))
        config.set_list("launchers", [
            {**item, "tables": ["table-1"]} for item in config.get_list("launchers")])
        config.save()

        self.assertEqual(launchers.LauncherStore(self.tmp.name).mappings(), {"table-1": "a"})

    def test_a_blank_name_is_its_apps(self) -> None:
        self.store.put(_launcher("a", display_name=""))

        named = self.store.get("a")
        assert named is not None
        self.assertEqual(named.display_name, "Visual Pinball X")

    def test_a_name_another_launcher_has_is_refused(self) -> None:
        self.store.put(_launcher("a", display_name="Cab"))

        with self.assertRaises(launchers.config_schema.ConfigListError):
            self.store.put(_launcher("b", display_name=" cab"))

    def test_an_app_this_install_lacks_is_refused(self) -> None:
        with self.assertRaises(launchers.config_schema.ConfigListError):
            self.store.put(_launcher("a", app="nope"))


class ValueTests(unittest.TestCase):
    def test_a_field_it_does_not_carry_answers_the_app_default(self) -> None:
        """A launcher missing a field is running on the default, so blank says the
        opposite of what is true."""
        one = _launcher("a")

        self.assertEqual(one.value("log_delete_on_start"), "false")

    def test_a_field_it_carries_answers_itself(self) -> None:
        one = _launcher("a", settings={"log_delete_on_start": True})

        self.assertIs(one.value("log_delete_on_start"), True)

    def test_an_app_nobody_declares_offers_nothing_of_its_own(self) -> None:
        """A launcher whose app this build does not know is still a launcher: it is
        listed, and the only fields it has are the ones every launcher has."""
        one = _launcher("a", app="future-pinball")

        self.assertEqual(one.fields(), apps.OWN_FIELDS)
        self.assertEqual(one.value("bin_path"), "")


class OneToALineTests(unittest.TestCase):
    def test_the_environment_field_takes_several_lines(self) -> None:
        """A one-line input cannot take the second NAME=value its help text promises."""
        for app_id in ("vpx", "generic"):
            with self.subTest(app=app_id):
                declared = apps.launcher_fields(app_id)
                assert declared is not None
                field = next(f for f in declared if f.key == "launch_env")

                self.assertEqual(field.type, "text")
                self.assertGreater(field.lines, 1)


class ResolutionTests(unittest.TestCase):
    """Explicit assignment, then is it enabled, then the default for the app."""

    def setUp(self) -> None:
        self.default = _launcher("d", display_name="Visual Pinball X")
        self.other = _launcher("o", display_name="VPX (4K)")
        self.held = [self.default, self.other]

    def _for(self, table_id: str, mappings=None, held=None):
        return launchers.launcher_for_table("Table.vpx", table_id,
                                            self.held if held is None else held,
                                            mappings or {})

    def test_a_table_that_names_nothing_gets_the_default(self) -> None:
        self.assertEqual(self._for("t1"), self.default)

    def test_a_table_that_names_one_gets_it(self) -> None:
        self.assertEqual(self._for("t1", {"t1": "o"}), self.other)

    def test_a_table_naming_a_disabled_launcher_falls_back(self) -> None:
        """Falling back rather than refusing to launch. The caller says so - the
        fallback is honest and silence about it is what makes it a mystery."""
        held = [self.default, _launcher("o", enabled=False)]

        self.assertEqual(self._for("t1", {"t1": "o"}, held), self.default)

    def test_the_default_is_the_first_enabled_one(self) -> None:
        held = [_launcher("off", enabled=False), self.other]

        self.assertEqual(self._for("t1", {}, held), self.other)

    def test_a_file_no_app_claims_has_no_launcher(self) -> None:
        """Not a table, so there is nothing to answer. A launcher here would be a
        promise to run something we cannot."""
        self.assertIsNone(launchers.launcher_for_table("notes.txt", "t1",
                                                       self.held, {}))

    def test_a_library_with_no_launchers_resolves_to_nothing(self) -> None:
        self.assertIsNone(self._for("t1", {}, []))

    def test_the_default_for_another_app_is_not_borrowed(self) -> None:
        """An app's launcher runs that app's files. A Future Pinball launcher standing
        in for a .vpx would launch the wrong program with a straight face."""
        held = [_launcher("fp", app="future-pinball")]

        self.assertIsNone(self._for("t1", {}, held))


class InEffectTests(unittest.TestCase):
    def test_an_empty_settings_file_is_visual_pinballs_own(self) -> None:
        one = launchers.Launcher(launcher_id="a", app="vpx", settings={"ini_path": ""})

        own = Path("/prefs/VPinballX.ini")
        with patch("apps.vpx.config.own_file", return_value=own):
            self.assertEqual(one.in_effect("ini_path"), str(own))
            self.assertEqual(one.value("ini_path"), "")

    def test_a_set_one_is_itself(self) -> None:
        one = launchers.Launcher(launcher_id="a", app="vpx",
                                 settings={"ini_path": "/cfg/other.ini"})

        self.assertEqual(one.in_effect("ini_path"), "/cfg/other.ini")

    def test_an_app_with_nothing_to_say_leaves_it_empty(self) -> None:
        one = launchers.Launcher(launcher_id="a", app="generic", settings={})

        self.assertEqual(one.in_effect("bin_path"), "")


class NameTests(unittest.TestCase):
    def test_two_names_that_differ_only_in_case_and_spaces_are_one(self) -> None:
        self.assertTrue(launchers.same_name(" Visual Pinball X", "visual pinball x "))

    def test_a_free_name_is_kept(self) -> None:
        self.assertEqual(launchers.free_name("VPX", ["Other"]), "VPX")

    def test_a_taken_one_is_numbered_from_two(self) -> None:
        self.assertEqual(launchers.free_name("VPX", ["vpx", "VPX 2"]), "VPX 3")


if __name__ == "__main__":
    unittest.main()

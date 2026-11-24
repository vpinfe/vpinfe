"""What the disk says about a path, asked one way for everything that names one.

A path is the one setting that can be perfectly well-formed and still wrong, and it
fails much later - at launch, as a file-not-found, on the machine nobody is at.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

from common import mounts, service_errors
from common.apps.contract import Field
from common.config import config_schema, path_state
from common.config.config_schema import ConfigList, ListVariant
from tests.config.test_config_lists import declared
from tests.support.hung_folder import never_answering
from tests.support.library import TempTree
from tests.support.skips import needs_posix_permissions

GADGETS = ConfigList(
    "gadgets", (Field("kind"), Field("home", path="dir")),
    variant=ListVariant("kind", lambda: ("plain", "runs"),
                        lambda kind: (Field("exe", path="exe"),) if kind == "runs" else
                        (() if kind == "plain" else None)))


class ItemPathTests(unittest.TestCase):
    def test_a_path_field_of_an_item_and_of_its_kind_is_found(self) -> None:
        items = {"gadgets": [
            {"id": "a", "kind": "runs", "home": "/h", "settings": {"exe": "/bin/x"}},
            {"id": "b", "kind": "plain", "home": "", "settings": {}}]}

        with declared(GADGETS):
            found = path_state.item_paths(items)

        self.assertEqual([(one.list_key, one.id, one.field, one.kind, one.value)
                          for one in found],
                         [("gadgets", "a", "home", "dir", "/h"),
                          ("gadgets", "a", "exe", "exe", "/bin/x"),
                          ("gadgets", "b", "home", "dir", "")])

    def test_a_kind_this_install_lacks_has_no_settings_to_check(self) -> None:
        items = {"gadgets": [{"id": "a", "kind": "gone", "home": "",
                              "settings": {"exe": "/bin/x"}}]}

        with declared(GADGETS):
            found = path_state.item_paths(items)

        self.assertEqual([one.field for one in found], ["home"])

    def test_a_list_that_is_not_declared_is_skipped(self) -> None:
        self.assertEqual(path_state.item_paths({"gadgets": [{"id": "a"}]}), ())


class LookTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.file = self.root / "a-file.ini"
        self.file.write_text("x", encoding="utf-8")
        self.folder = self.root / "a-folder"
        self.folder.mkdir()

    def test_a_blank_value_is_not_a_failure(self) -> None:
        """Most of these are optional, and blank means the default."""
        self.assertEqual(path_state.look("", path_state.FILE),
                         path_state.PathState(path_state.UNSET))
        self.assertEqual(path_state.look("   ", path_state.DIR).state, path_state.UNSET)

    def test_a_setting_that_names_nothing_on_disk_is_not_checked(self) -> None:
        self.assertEqual(path_state.look("/nowhere", "").state, path_state.UNSET)

    def test_a_file_that_is_there(self) -> None:
        found = path_state.look(str(self.file), path_state.FILE)

        self.assertEqual(found.state, path_state.OK)
        self.assertEqual(found.reason, "")

    def test_a_folder_that_is_there(self) -> None:
        self.assertEqual(path_state.look(str(self.folder)).state, path_state.OK)

    def test_nothing_at_that_path(self) -> None:
        found = path_state.look(str(self.root / "nope.ini"), path_state.FILE)

        self.assertEqual(found.state, path_state.MISSING)
        self.assertIn("Nothing", found.reason)

    def test_a_folder_where_a_file_was_wanted(self) -> None:
        found = path_state.look(str(self.folder), path_state.FILE)

        self.assertEqual(found.state, path_state.WRONG_KIND)
        self.assertIn("folder", found.reason)

    def test_a_file_where_a_folder_was_wanted(self) -> None:
        found = path_state.look(str(self.file))

        self.assertEqual(found.state, path_state.WRONG_KIND)
        self.assertIn("file", found.reason)

    @needs_posix_permissions
    def test_a_program_that_cannot_be_run_is_its_own_state(self) -> None:
        """The path is right and the file is there. That is a permissions problem, not
        a typo, and the two are fixed differently."""
        found = path_state.look(str(self.file), path_state.EXE)

        self.assertEqual(found.state, path_state.NOT_EXECUTABLE)
        self.assertIn("executable", found.reason)

    def test_a_program_that_can_be_run(self) -> None:
        os.chmod(self.file, 0o755)

        self.assertEqual(path_state.look(str(self.file), path_state.EXE).state,
                         path_state.OK)

    @unittest.skipUnless(sys.platform == "darwin", "macOS bundle layout")
    def test_a_mac_app_bundle_is_a_program_not_a_folder(self) -> None:
        """What a person picks is the .app, which is a directory - and reporting the one
        right answer as a mistake is worse than not checking at all."""
        bundle = self.root / "VPinballX.app"
        inner = bundle / "Contents" / "MacOS"
        inner.mkdir(parents=True)
        binary = inner / "VPinballX"
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        os.chmod(binary, 0o755)

        self.assertEqual(path_state.look(str(bundle), path_state.EXE).state,
                         path_state.OK)

    def test_a_home_relative_path_is_expanded(self) -> None:
        # `expanduser` reads HOME on POSIX and USERPROFILE on Windows, so a fixture
        # that sets only one of them redirects `~` on one platform and not the other.
        with mock.patch.dict(os.environ, {"HOME": str(self.root),
                                          "USERPROFILE": str(self.root)}):
            self.assertEqual(path_state.look("~/a-file.ini", path_state.FILE).state,
                             path_state.OK)


NAS = mounts.Origin(mounts.NFS, "nas.lan", "/export/pinball", "/mnt/nas")


class ShareTests(TempTree):
    """The answers a plain stat cannot give: the path is on a share, and the share is
    not there, or not answering, or not writable."""

    def test_a_path_on_a_share_that_is_not_mounted_says_so(self) -> None:
        """Reads "not connected" and not "missing": the first is fixed by mounting the
        share, the second by correcting the path."""
        with mock.patch.object(mounts, "where",
                               return_value=mounts.Where(NAS, connected=False)):
            found = path_state.look(str(self.root / "gone"), path_state.FILE)

        self.assertEqual(found.state, path_state.NOT_CONNECTED)
        self.assertEqual(found.origin, NAS)

    def test_a_path_on_a_mounted_share_names_it(self) -> None:
        with mock.patch.object(mounts, "where", return_value=mounts.Where(NAS)):
            found = path_state.look(str(self.root))

        self.assertEqual((found.state, found.origin), (path_state.OK, NAS))

    def test_a_dead_mount_is_not_answering_rather_than_a_hang(self) -> None:
        """Nothing cancels a stat on a dead mount. The question is left to finish on its
        own thread and the caller is told it is not answering."""
        with never_answering(str(self.root)):
            found = path_state.look(str(self.root), wait=0.05)

        self.assertEqual(found.state, path_state.NOT_ANSWERING)
        self.assertFalse(found.reachable)

    def test_the_same_question_asked_twice_costs_one_thread(self) -> None:
        with never_answering(str(self.root)) as looked:
            path_state.look(str(self.root), wait=0.05)
            path_state.look(str(self.root), wait=0.05)

        self.assertEqual(looked.call_count, 1)

    @needs_posix_permissions
    def test_a_folder_that_cannot_be_written_is_read_only_when_writing_is_asked(self) -> None:
        shut = self.root / "shut"
        shut.mkdir()
        os.chmod(shut, 0o555)
        self.addCleanup(os.chmod, shut, 0o755)

        asked = path_state.look(str(shut), writable=True)
        plain = path_state.look(str(shut))

        self.assertEqual(asked.state, path_state.READ_ONLY)
        self.assertTrue(asked.reachable)
        self.assertFalse(asked.writable)
        self.assertEqual(plain.state, path_state.OK)

    def test_every_path_is_answered_in_the_order_given(self) -> None:
        here = self.root / "here"
        here.mkdir()

        found = path_state.look_all([
            path_state.Ask(str(here)),
            path_state.Ask(""),
            path_state.Ask(str(self.root / "nope")),
        ])

        self.assertEqual([one.state for one in found],
                         [path_state.OK, path_state.UNSET, path_state.MISSING])


class RequireTests(TempTree):
    """A writer asks before the work, so a dropped share ends in a sentence a person can
    act on and not an errno."""

    def test_a_usable_path_comes_back_as_its_state(self) -> None:
        self.assertEqual(path_state.require(str(self.root)).state, path_state.OK)

    def test_a_path_that_cannot_be_used_is_refused_with_its_name_and_why(self) -> None:
        with self.assertRaises(service_errors.BlockedError) as raised:
            path_state.require(str(self.root / "gone"), name="Backups")

        self.assertIn("Backups", str(raised.exception))
        self.assertIn("Nothing is at that path", str(raised.exception))

    def test_a_blank_path_is_refused_too(self) -> None:
        """A writer has nowhere to write to; unset is only acceptable to a reader that
        has a default."""
        with self.assertRaises(service_errors.BlockedError):
            path_state.require("", name="Backups")

    def test_refusal_is_empty_for_a_path_that_can_be_used(self) -> None:
        self.assertEqual(path_state.refusal("x", path_state.PathState(path_state.OK)), "")

    def test_a_read_only_path_is_refused_by_name(self) -> None:
        said = path_state.refusal("Tables", path_state.PathState(path_state.READ_ONLY))

        self.assertIn("Tables", said)
        self.assertIn("read-only", said)


class CanonicalTests(TempTree):
    def test_blank_stays_blank(self) -> None:
        self.assertEqual(path_state.canonical("  "), "")

    def test_a_folder_reached_two_ways_has_one_spelling(self) -> None:
        folder = self.root / "a"
        folder.mkdir()

        self.assertEqual(path_state.canonical(str(folder)),
                         path_state.canonical(f"{self.root}/./a/"))


class PathOptionTests(unittest.TestCase):
    def test_the_settings_that_name_something_on_disk(self) -> None:
        """Declared on the option rather than matched on the key: the logos folder is a
        directory and `tools.rar_path` is a program."""
        found = {(o.section, o.key): o.path for o in path_state.path_options()}

        self.assertEqual(found.get(("media", "manufacturer_logos_dir")), "dir")
        self.assertEqual(found.get(("tools", "rar_path")), "exe")

    def test_the_library_root_is_not_one_of_them(self) -> None:
        """It is a location now, and internal - so it is not a setting anybody is
        offered, and a check that follows a field has no field to follow."""
        found = {(o.section, o.key) for o in path_state.path_options()}

        self.assertNotIn(("general", "game_root_dir"), found)

    def test_a_launchers_paths_are_not_config_settings(self) -> None:
        """They are fields of a launcher now, so this list must not still be offering to
        check them - it walks the config schema, and they left it."""
        found = {key for _section, key in
                 ((o.section, o.key) for o in path_state.path_options())}

        self.assertNotIn("vpx_bin_path", found)
        self.assertNotIn("vpx_ini_path", found)

    def test_every_declared_kind_is_one_the_checker_knows(self) -> None:
        """A kind with no checker silently passes everything it is given."""
        for option in path_state.path_options():
            self.assertIn(option.path, config_schema.PATH_KINDS,
                          f"{option.section}.{option.key}")

    def test_nothing_internal_is_offered(self) -> None:
        self.assertTrue(all(not o.internal for o in path_state.path_options()))


if __name__ == "__main__":
    unittest.main()

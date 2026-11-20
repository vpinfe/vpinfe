"""Protects: (b, e) official means VPinFE's own files, never a changed one or a copy.

An extension is official only when its files are the ones VPinFE put there, and a folder
that takes an official name is never taken for it. Where an extension is from is decided on
the device from its files: the build's fingerprint list for the ones it ships, the receipt
VPinFE kept for the ones it installed, and the names VPinFE knows. Nothing here needs a
connection. The inventory is logged before any extension is imported.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from common.config import paths
from common.extensions import contract, fingerprint, host, store
from common.security import accepted
from tests.support.accepted import isolated
from tests.support.identity import IDENTITY

RAN = "ran.txt"
BODY = (
    "from pathlib import Path\n"
    f"Path(__file__).with_name({RAN!r}).write_text('yes')\n"
    "def register(ctx): pass\n"
)


def _write(folder: Path, name: str, **changes: object) -> Path:
    directory = folder / name
    directory.mkdir(parents=True)
    raw = {"name": name, "version": "1.0.0", "requires_platform": contract.PLATFORM_ABI, **IDENTITY}
    raw.update(changes)
    (directory / "extension.json").write_text(json.dumps(raw), encoding="utf-8")
    (directory / "__init__.py").write_text(BODY, encoding="utf-8")
    return directory


def _ran(directory: Path) -> bool:
    return (directory / RAN).exists()


class Case(unittest.TestCase):
    def setUp(self) -> None:
        isolated(self)
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name)
        self.bundled = self.root / "bundled"
        self.official = self.root / "official"
        self.installed = self.root / "installed"
        self.settings = self.root / "settings"
        for folder in (self.bundled, self.official, self.installed, self.settings):
            folder.mkdir()
        self.registry = host.Registry(store.ExtensionStore(self.settings))
        self.addCleanup(self.registry.clear)
        for target, name, value in (
            (host, "BUNDLED_DIR", self.bundled),
            (host, "OFFICIAL_DIR", self.official),
            (host, "SEARCH_PATH", (self.installed, self.official, self.bundled)),
            (paths, "EXTENSION_RECEIPTS_PATH", self.root / "receipts.json"),
            (paths, "EXTENSION_LIST_PATH", self.root / "list.json"),
        ):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def install_official(self, name: str) -> Path:
        directory = _write(self.official, name)
        fingerprint.write_receipt(
            name, directory, repository="https://example.test/o/r", release="1.0.0"
        )
        return directory

    def start_again(self) -> host.Registry:
        self.registry.clear()
        self.registry = host.Registry(self.registry.store)
        self.addCleanup(self.registry.clear)
        return self.registry


class Fingerprints(unittest.TestCase):
    def test_the_same_files_make_the_same_fingerprint_wherever_they_are(self) -> None:
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            for folder in (one, two):
                (Path(folder) / "sub").mkdir()
                (Path(folder) / "sub" / "a.txt").write_text("a", encoding="utf-8")
                (Path(folder) / "b.txt").write_text("b", encoding="utf-8")

            self.assertEqual(
                fingerprint.digest(fingerprint.hash_files(one)),
                fingerprint.digest(fingerprint.hash_files(two)),
            )
            self.assertEqual(sorted(fingerprint.hash_files(one)), ["b.txt", "sub/a.txt"])

    def test_what_python_and_the_system_write_on_their_own_is_not_counted(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "a.py").write_text("a", encoding="utf-8")
            before = fingerprint.hash_files(root)
            (root / "__pycache__").mkdir()
            (root / "__pycache__" / "a.cpython-313.pyc").write_bytes(b"x")
            (root / ".DS_Store").write_bytes(b"x")
            (root / "Thumbs.db").write_bytes(b"x")

            self.assertEqual(fingerprint.hash_files(root), before)

    def test_a_bytecode_file_that_stands_alone_is_counted(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "a.py").write_text("a", encoding="utf-8")
            before = fingerprint.hash_files(root)
            (root / "helper.pyc").write_bytes(b"x")

            self.assertEqual(
                fingerprint.differences(before, fingerprint.hash_files(root)), ["helper.pyc"]
            )

    def test_a_link_added_to_a_folder_is_counted(self) -> None:
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as elsewhere:
            root = Path(folder)
            (root / "a.py").write_text("a", encoding="utf-8")
            before = fingerprint.hash_files(root)
            try:
                (root / "sub").symlink_to(elsewhere, target_is_directory=True)
            except OSError:
                self.skipTest("this system does not let a link be made here")

            self.assertEqual(fingerprint.differences(before, fingerprint.hash_files(root)), ["sub"])

    def test_an_added_removed_and_edited_file_each_show(self) -> None:
        expected = {"a": "1", "b": "2", "c": "3"}

        self.assertEqual(
            fingerprint.differences(expected, {"a": "1", "b": "9", "d": "4"}), ["b", "c", "d"]
        )


class WhatTheBuildShips(Case):
    def test_with_no_list_in_the_build_it_is_unchecked_and_official(self) -> None:
        directory = _write(self.bundled, "tool")

        record = self.registry.load(directory)

        self.assertEqual(record.origin, accepted.SHIPPED)
        self.assertTrue(_ran(directory))

    def test_a_packaged_build_with_no_list_trusts_none_of_what_it_ships(self) -> None:
        directory = _write(self.bundled, "tool")

        with (
            mock.patch.object(sys, "frozen", True, create=True),
            self.assertLogs("vpinfe.common.extensions", "WARNING"),
        ):
            record = self.registry.load(directory)

        self.assertEqual(record.origin, accepted.CHANGED)
        self.assertFalse(_ran(directory))

    def test_matching_the_build_list_it_is_official(self) -> None:
        directory = _write(self.bundled, "tool")
        fingerprint.write_build_list(self.bundled)

        record = self.registry.load(directory)

        self.assertEqual(record.origin, accepted.SHIPPED)
        self.assertTrue(_ran(directory))

    def test_an_edited_file_makes_it_changed_and_its_code_is_not_run(self) -> None:
        directory = _write(self.bundled, "tool")
        fingerprint.write_build_list(self.bundled)
        (directory / "__init__.py").write_text(BODY + "# edited\n", encoding="utf-8")

        with self.assertLogs("vpinfe.common.extensions", "WARNING"):
            record = self.registry.load(directory)

        self.assertEqual(record.origin, accepted.CHANGED)
        self.assertEqual((record.state, record.why), (host.OFF, host.NOT_ACCEPTED))
        self.assertEqual(record.differs, ["__init__.py"])
        self.assertFalse(_ran(directory))

    def test_a_file_added_to_it_makes_it_changed(self) -> None:
        directory = _write(self.bundled, "tool")
        fingerprint.write_build_list(self.bundled)
        (directory / "extra.py").write_text("x = 1\n", encoding="utf-8")

        with self.assertLogs("vpinfe.common.extensions", "WARNING"):
            record = self.registry.load(directory)

        self.assertEqual((record.origin, record.differs), (accepted.CHANGED, ["extra.py"]))

    def test_a_list_that_cannot_be_read_leaves_nothing_official(self) -> None:
        directory = _write(self.bundled, "tool")
        (self.bundled / fingerprint.BUILD_LIST).write_text("{not json", encoding="utf-8")

        with self.assertLogs("vpinfe.common.extensions", "WARNING"):
            record = self.registry.load(directory)

        self.assertEqual(record.origin, accepted.CHANGED)
        self.assertFalse(_ran(directory))

    def test_a_changed_one_runs_once_trusted_as_unofficial(self) -> None:
        directory = _write(self.bundled, "tool")
        fingerprint.write_build_list(self.bundled)
        (directory / "__init__.py").write_text(BODY + "# edited\n", encoding="utf-8")
        with self.assertLogs("vpinfe.common.extensions", "WARNING"):
            self.registry.load(directory)

        record = self.registry.accept("tool")

        assert record is not None
        self.assertEqual(record.state, host.LOADED)
        self.assertTrue(_ran(directory))

    def test_an_unofficial_one_switched_off_still_counts_until_the_restart(self) -> None:
        directory = _write(self.bundled, "tool")
        fingerprint.write_build_list(self.bundled)
        (directory / "__init__.py").write_text(BODY + "# edited\n", encoding="utf-8")
        with self.assertLogs("vpinfe.common.extensions", "WARNING"):
            self.registry.load(directory)
        self.registry.accept("tool")

        self.registry.switch("tool", False)

        self.assertEqual([one.name for one in self.registry.unofficial_running()], ["tool"])


class WhatVPinFEInstalled(Case):
    def test_files_as_installed_are_official_and_run_with_no_asking(self) -> None:
        directory = self.install_official("tool")

        record = self.registry.load(directory)

        self.assertEqual(record.origin, accepted.OFFICIAL)
        self.assertEqual(record.state, host.LOADED)
        self.assertTrue(record.as_dict()["trust"]["accepted"])

    def test_an_edited_file_makes_it_changed_and_stops_it(self) -> None:
        directory = self.install_official("tool")
        (directory / "__init__.py").write_text(BODY + "# edited\n", encoding="utf-8")

        with self.assertLogs("vpinfe.common.extensions", "WARNING"):
            record = self.registry.load(directory)

        self.assertEqual(record.origin, accepted.CHANGED)
        self.assertEqual(record.why, host.NOT_ACCEPTED)
        self.assertFalse(_ran(directory))

    def test_a_folder_with_no_receipt_is_not_official(self) -> None:
        directory = _write(self.official, "tool")

        with self.assertLogs("vpinfe.common.extensions", "WARNING"):
            record = self.registry.load(directory)

        self.assertEqual(record.origin, accepted.CHANGED)
        self.assertFalse(_ran(directory))

    def test_the_check_needs_no_connection(self) -> None:
        directory = self.install_official("tool")

        with (
            mock.patch("socket.socket", side_effect=AssertionError("went to the network")),
            mock.patch("socket.create_connection", side_effect=AssertionError("network")),
        ):
            record = self.registry.load(directory)

        self.assertEqual(record.origin, accepted.OFFICIAL)


class AFolderTakingAnOfficialName(Case):
    def test_a_name_the_build_ships_is_an_unofficial_copy(self) -> None:
        _write(self.bundled, "tool")
        copy = _write(self.installed, "tool")

        with self.assertLogs("vpinfe.common.extensions", "WARNING"):
            record = self.registry.load(copy)

        self.assertEqual(record.origin, accepted.REPLACES)
        self.assertFalse(record.official)

    def test_a_name_in_the_list_last_fetched_is_an_unofficial_copy(self) -> None:
        paths.EXTENSION_LIST_PATH.write_text(
            json.dumps({"extensions": [{"name": "elsewhere"}]}), encoding="utf-8"
        )
        copy = _write(self.installed, "elsewhere")

        with self.assertLogs("vpinfe.common.extensions", "WARNING"):
            record = self.registry.load(copy)

        self.assertEqual(record.origin, accepted.REPLACES)

    def test_a_name_nobody_reserved_is_just_in_a_folder(self) -> None:
        paths.EXTENSION_LIST_PATH.write_text("{not json", encoding="utf-8")
        own = _write(self.installed, "mine")

        with self.assertLogs("vpinfe.common.extensions", "WARNING"):
            record = self.registry.load(own)

        self.assertEqual(record.origin, accepted.IN_FOLDER)

    def test_naming_the_team_does_not_make_it_the_team(self) -> None:
        own = _write(
            self.installed,
            "mine",
            author="The VPinFE Team",
            source="https://github.com/vpinfe/extension-mine",
        )

        with self.assertLogs("vpinfe.common.extensions", "WARNING"):
            record = self.registry.load(own)

        self.assertFalse(record.official)
        self.assertTrue(record.claims_team)
        self.assertTrue(record.as_dict()["trust"]["claims_team"])

    def test_what_is_official_is_not_said_to_claim_the_team(self) -> None:
        directory = self.install_official("tool")

        record = self.registry.load(directory)

        self.assertFalse(record.claims_team)


class TheStartInTwoPasses(Case):
    def test_every_extension_is_on_the_log_before_any_of_them_runs(self) -> None:
        one = _write(self.installed, "first")
        two = _write(self.installed, "second")
        for name in ("first", "second"):
            manifest = host._read_manifest(self.installed / name)
            accepted.accept_extension(
                name,
                accepted.IN_FOLDER,
                folder=str(self.installed),
                version=manifest.version,
                scopes=manifest.scopes,
                capabilities=manifest.capabilities,
                play=manifest.play,
            )
        seen: list[list[str]] = []
        real = host._import

        def importing(name: str, directory: Path) -> object:
            seen.append([line for line in logs.output if "Extension inventory" in line])
            return real(name, directory)

        with self.assertLogs("vpinfe.common.extensions.host", "INFO") as logs:
            with mock.patch.object(host, "_import", importing):
                self.registry.load_installed()

        self.assertEqual(len(seen), 2)
        for lines in seen:
            self.assertEqual(len(lines), 2, lines)
        self.assertTrue(_ran(one) and _ran(two))

    def test_the_inventory_says_where_each_is_from_and_its_fingerprint(self) -> None:
        directory = _write(self.installed, "mine")

        with self.assertLogs("vpinfe.common.extensions", "INFO") as logs:
            self.registry.load_installed()

        line = next(one for one in logs.output if "Extension inventory: mine" in one)
        self.assertIn("1.0.0", line)
        self.assertIn(f"origin={accepted.IN_FOLDER}", line)
        self.assertIn(str(directory.parent), line)
        self.assertIn(fingerprint.digest(fingerprint.hash_files(directory))[:16], line)

    def test_one_that_cannot_be_read_is_in_the_inventory_and_stops_no_other(self) -> None:
        broken = self.installed / "broken"
        broken.mkdir()
        (broken / "extension.json").write_text("{", encoding="utf-8")
        _write(self.installed, "fine")

        with self.assertLogs("vpinfe.common.extensions", "INFO") as logs:
            found = self.registry.load_installed()

        self.assertTrue(any("Extension inventory: broken unreadable" in one for one in logs.output))
        self.assertEqual({one.name: one.state for one in found}["broken"], host.FAILED)

    def test_a_copy_that_is_not_trusted_gives_way_to_the_included_one(self) -> None:
        _write(self.installed, "tool", version="2.0.0")
        shipped = _write(self.bundled, "tool", version="1.0.0")

        with self.assertLogs("vpinfe.common.extensions", "INFO"):
            self.registry.load_installed()

        record = self.registry.get("tool")
        assert record is not None
        self.assertEqual(record.directory, shipped)
        self.assertTrue(record.official)


class TurningACopyOff(Case):
    def setUp(self) -> None:
        super().setUp()
        self.shipped = _write(self.bundled, "tool", version="1.0.0")
        self.copy = _write(self.installed, "tool", version="2.0.0")
        manifest = host._read_manifest(self.copy)
        accepted.accept_extension(
            "tool",
            accepted.REPLACES,
            folder=str(self.installed),
            version=manifest.version,
            scopes=manifest.scopes,
            capabilities=manifest.capabilities,
            play=manifest.play,
        )

    def test_the_accepted_copy_runs_in_place_of_the_included_one(self) -> None:
        self.registry.load_installed()

        record = self.registry.get("tool")
        assert record is not None
        self.assertEqual(record.directory, self.copy)
        self.assertTrue(_ran(self.copy) and not _ran(self.shipped))

    def test_switched_off_the_included_one_takes_the_name_back_at_once(self) -> None:
        self.registry.load_installed()

        record = self.registry.switch("tool", False)

        assert record is not None
        self.assertEqual(record.directory, self.shipped)
        self.assertEqual(record.state, host.LOADED)
        assert record.waiting is not None
        self.assertEqual(
            (record.waiting.directory, record.waiting.why), (self.copy, host.SWITCHED_OFF)
        )

    def test_and_stays_back_at_the_next_start(self) -> None:
        self.registry.load_installed()
        self.registry.switch("tool", False)
        (self.shipped / RAN).unlink(missing_ok=True)

        again = self.start_again()
        again.load_installed()

        record = again.get("tool")
        assert record is not None
        self.assertEqual((record.directory, record.state), (self.shipped, host.LOADED))
        assert record.waiting is not None
        self.assertEqual(record.waiting.why, host.SWITCHED_OFF)

    def test_switching_the_copy_back_on_takes_the_name_at_the_next_start(self) -> None:
        self.registry.load_installed()
        self.registry.switch("tool", False)

        record = self.registry.switch_copy("tool", True)

        assert record is not None and record.waiting is not None
        self.assertEqual(record.waiting.why, host.STARTS_AT_RESTART)
        again = self.start_again()
        again.load_installed()
        now = again.get("tool")
        assert now is not None
        self.assertEqual((now.directory, now.state), (self.copy, host.LOADED))

    def test_the_included_ones_own_switch_is_not_the_copys(self) -> None:
        self.registry.load_installed()
        self.registry.switch("tool", False)

        self.assertTrue(self.registry.store.enabled("tool"))
        self.assertFalse(self.registry.store.enabled("tool.copy"))


class TurningOffACopyThatAddedRoutes(Case):
    def test_the_included_one_waits_for_the_next_start_so_the_copys_routes_stay_shut(self) -> None:
        body = (
            "from fastapi import APIRouter\n"
            "def register(ctx):\n"
            "    ctx.add_router(APIRouter(), tier='play')\n"
        )
        _write(self.bundled, "tool", version="1.0.0")
        copy = _write(self.installed, "tool", version="2.0.0", play=["router"])
        (copy / "__init__.py").write_text(body, encoding="utf-8")
        manifest = host._read_manifest(copy)
        accepted.accept_extension(
            "tool",
            accepted.REPLACES,
            folder=str(self.installed),
            version=manifest.version,
            scopes=manifest.scopes,
            capabilities=manifest.capabilities,
            play=manifest.play,
        )
        self.registry.load_installed()
        self.registry.mounted()

        record = self.registry.switch("tool", False)

        assert record is not None
        self.assertFalse(record.running)
        self.assertEqual((record.state, record.why), (host.OFF, host.STARTS_AT_RESTART))
        self.assertNotIn("tool", [one.name for one in self.registry.records() if one.running])

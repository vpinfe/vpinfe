"""An extension VPinFE did not ship stays stopped until the Owner accepts it."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from common.config import paths
from common.extensions import check, contract, host, store
from common.security import accepted
from tests.support.accepted import isolated
from tests.support.identity import IDENTITY

# Written beside the extension when it is imported, so a test can tell code that ran from
# code that was only found.
RAN = "ran.txt"
BODY = (f"from pathlib import Path\n"
        f"Path(__file__).with_name({RAN!r}).write_text('yes')\n"
        "def register(ctx): pass\n")


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
        self.registry = host.Registry(store.ExtensionStore(self.root))
        self.addCleanup(self.registry.clear)
        self.folder = self.root / "found"
        self.folder.mkdir()

    def accept(self, name: str) -> None:
        manifest = host._read_manifest(self.folder / name)
        accepted.accept_extension(name, accepted.IN_FOLDER, folder=str(self.folder),
                                  version=manifest.version, scopes=manifest.scopes,
                                  capabilities=manifest.capabilities,
                                  play=manifest.play)


class AnExtensionInAFolder(Case):
    def test_is_not_started_and_its_code_is_not_run(self) -> None:
        directory = _write(self.folder, "tool")

        with self.assertLogs("vpinfe.common.extensions", "WARNING"):
            record = self.registry.load(directory)

        self.assertEqual((record.state, record.why), (host.OFF, host.NOT_ACCEPTED))
        self.assertFalse(_ran(directory), "its code was imported before it was accepted")

    def test_says_where_it_is_from_and_what_it_declares(self) -> None:
        directory = _write(self.folder, "tool", scopes=["games:read"])

        with self.assertLogs("vpinfe.common.extensions", "WARNING"):
            shown = self.registry.load(directory).as_dict()

        self.assertEqual(shown["origin"], accepted.IN_FOLDER)
        self.assertFalse(shown["trust"]["accepted"])
        self.assertEqual(shown["trust"]["folder"], str(self.folder))
        self.assertEqual(shown["trust"]["uses"], ["games:read"])
        self.assertFalse(shown["trust"]["accepted_before"])

    def test_starts_once_it_is_accepted(self) -> None:
        directory = _write(self.folder, "tool")
        with self.assertLogs("vpinfe.common.extensions", "WARNING"):
            self.registry.load(directory)

        record = self.registry.accept("tool")

        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.state, host.LOADED)
        self.assertTrue(_ran(directory))

    def test_runs_at_the_next_start_with_no_asking_after_it_was_accepted(self) -> None:
        directory = _write(self.folder, "tool")
        self.accept("tool")

        record = self.registry.load(directory)

        self.assertEqual(record.state, host.LOADED)

    def test_that_asks_for_more_in_a_later_version_stops_again(self) -> None:
        directory = _write(self.folder, "tool", scopes=["games:read"])
        self.accept("tool")
        (directory / "extension.json").write_text(json.dumps({
            "name": "tool", "version": "2.0.0", "requires_platform": contract.PLATFORM_ABI,
            **IDENTITY,
            "scopes": ["games:read", "games:write"]}), encoding="utf-8")

        with self.assertLogs("vpinfe.common.extensions", "WARNING"):
            record = self.registry.load(directory)

        self.assertEqual((record.state, record.why), (host.OFF, host.NOT_ACCEPTED))
        trust = record.as_dict()["trust"]
        self.assertEqual(trust["new_access"], ["games:write"])
        self.assertTrue(trust["accepted_before"])

    def test_that_adds_a_surface_a_remote_can_open_in_a_later_version_stops_again(self) -> None:
        directory = _write(self.folder, "tool")
        self.accept("tool")
        (directory / "extension.json").write_text(json.dumps({
            "name": "tool", "version": "2.0.0", "requires_platform": contract.PLATFORM_ABI,
            **IDENTITY,
            "play": ["list:scores"]}), encoding="utf-8")

        with self.assertLogs("vpinfe.common.extensions", "WARNING"):
            record = self.registry.load(directory)

        self.assertEqual((record.state, record.why), (host.OFF, host.NOT_ACCEPTED))
        self.assertEqual(record.as_dict()["trust"]["new_access"], ["play:list:scores"])

    def test_that_adds_a_second_surface_a_remote_can_open_asks_about_that_one_only(self) -> None:
        directory = _write(self.folder, "tool")
        (directory / "extension.json").write_text(json.dumps({
            "name": "tool", "version": "1.0.0", "requires_platform": contract.PLATFORM_ABI,
            **IDENTITY,
            "play": ["list:scores"]}), encoding="utf-8")
        self.accept("tool")
        (directory / "extension.json").write_text(json.dumps({
            "name": "tool", "version": "2.0.0", "requires_platform": contract.PLATFORM_ABI,
            **IDENTITY,
            "play": ["list:scores", "section:rank"]}), encoding="utf-8")

        with self.assertLogs("vpinfe.common.extensions", "WARNING"):
            record = self.registry.load(directory)

        self.assertEqual(record.as_dict()["trust"]["new_access"], ["play:section:rank"])

    def test_that_names_a_surface_whose_key_has_a_space_or_colon_is_accepted(self) -> None:
        directory = _write(self.folder, "tool")
        (directory / "extension.json").write_text(json.dumps({
            "name": "tool", "version": "1.0.0", "requires_platform": contract.PLATFORM_ABI,
            **IDENTITY,
            "play": ["section:my scores", "verb:game:rate:all"]}), encoding="utf-8")

        record = self.registry.load(directory)

        assert record.manifest is not None
        self.assertEqual(record.manifest.play, ("section:my scores", "verb:game:rate:all"))

    def test_that_names_a_surface_with_no_key_is_refused(self) -> None:
        directory = _write(self.folder, "tool")
        (directory / "extension.json").write_text(json.dumps({
            "name": "tool", "version": "1.0.0", "requires_platform": contract.PLATFORM_ABI,
            **IDENTITY,
            "play": ["list:"]}), encoding="utf-8")

        with self.assertLogs("vpinfe.common.extensions", "ERROR"):
            record = self.registry.load(directory)

        self.assertEqual(record.state, host.FAILED)

    def test_that_names_play_among_its_tiers_is_refused(self) -> None:
        directory = _write(self.folder, "tool")
        (directory / "extension.json").write_text(json.dumps({
            "name": "tool", "version": "2.0.0", "requires_platform": contract.PLATFORM_ABI,
            **IDENTITY,
            "tiers": ["play"]}), encoding="utf-8")

        with self.assertLogs("vpinfe.common.extensions", "ERROR"):
            record = self.registry.load(directory)

        self.assertEqual(record.state, host.FAILED)

    def test_that_adds_the_protected_tier_in_a_later_version_runs(self) -> None:
        directory = _write(self.folder, "tool")
        self.accept("tool")
        (directory / "extension.json").write_text(json.dumps({
            "name": "tool", "version": "2.0.0", "requires_platform": contract.PLATFORM_ABI,
            **IDENTITY,
            "tiers": ["protected"]}), encoding="utf-8")

        self.assertEqual(self.registry.load(directory).state, host.LOADED)

    def test_that_asks_for_no_more_in_a_later_version_does_not_ask_again(self) -> None:
        directory = _write(self.folder, "tool", scopes=["games:read"])
        self.accept("tool")
        (directory / "extension.json").write_text(json.dumps({
            "name": "tool", "version": "2.0.0", "requires_platform": contract.PLATFORM_ABI,
            **IDENTITY,
            "scopes": ["games:read"]}), encoding="utf-8")

        self.assertEqual(self.registry.load(directory).state, host.LOADED)

    def test_cannot_be_accepted_by_a_name_nothing_answers_to(self) -> None:
        self.assertIsNone(self.registry.accept("nothing"))

    def test_does_not_start_by_being_switched_on(self) -> None:
        directory = _write(self.folder, "tool")
        with self.assertLogs("vpinfe.common.extensions", "WARNING"):
            self.registry.load(directory)

        with self.assertLogs("vpinfe.common.extensions", "WARNING"):
            self.registry.switch("tool", True)

        self.assertFalse(_ran(directory))
        record = self.registry.get("tool")
        assert record is not None
        self.assertEqual(record.why, host.NOT_ACCEPTED)

    def test_is_not_started_when_the_list_cannot_be_read(self) -> None:
        directory = _write(self.folder, "tool")
        self.accept("tool")
        paths.ACCEPTED_PATH.write_text("{not json", encoding="utf-8")

        with self.assertLogs("vpinfe.common.extensions", "WARNING"):
            record = self.registry.load(directory)

        self.assertEqual(record.why, host.NOT_ACCEPTED)


class WhatVPinFEShips(Case):
    def test_runs_with_no_acceptance(self) -> None:
        shipped = host.BUNDLED_DIR / "vpinplay"

        record = self.registry.load(shipped)

        self.assertEqual(record.origin, accepted.SHIPPED)
        self.assertNotEqual(record.why, host.NOT_ACCEPTED)
        self.assertTrue(record.as_dict()["trust"]["accepted"])


class AReplacement(Case):
    """A folder copy named like a built-in does not take its place until accepted."""

    def setUp(self) -> None:
        super().setUp()
        self.bundled = self.root / "bundled"
        self.installed = self.root / "installed"
        self.bundled.mkdir()
        self.installed.mkdir()
        shipped = _write(self.bundled, "tool", version="1.0.0")
        self.replacement = _write(self.installed, "tool", version="2.0.0")
        for target, value in ((host, ("BUNDLED_DIR", self.bundled)),
                              (host, ("SEARCH_PATH", (self.installed, self.bundled)))):
            patcher = mock.patch.object(target, *value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.shipped = shipped

    def test_the_built_in_keeps_running_and_the_copy_waits(self) -> None:
        with self.assertLogs("vpinfe.common.extensions", "WARNING"):
            self.registry.load_installed()

        running = self.registry.get("tool")
        assert running is not None
        self.assertEqual(running.state, host.LOADED)
        self.assertEqual(running.directory, self.shipped)
        self.assertFalse(_ran(self.replacement), "the copy's code ran before it was accepted")
        waiting = running.as_dict()["waiting"]
        self.assertEqual((waiting["version"], waiting["reason_key"]),
                         ("2.0.0", host.NOT_ACCEPTED))
        self.assertEqual(waiting["trust"]["origin"], accepted.REPLACES)

    def test_accepting_it_leaves_the_built_in_running_until_the_next_start(self) -> None:
        with self.assertLogs("vpinfe.common.extensions", "WARNING"):
            self.registry.load_installed()

        self.registry.accept("tool")

        running = self.registry.get("tool")
        assert running is not None
        self.assertEqual((running.state, running.directory), (host.LOADED, self.shipped))
        self.assertFalse(_ran(self.replacement))
        self.assertEqual(running.as_dict()["waiting"]["reason_key"], host.STARTS_AT_RESTART)

    def test_the_next_start_runs_the_accepted_copy(self) -> None:
        with self.assertLogs("vpinfe.common.extensions", "WARNING"):
            self.registry.load_installed()
        self.registry.accept("tool")
        self.registry.clear()

        self.registry.load_installed()

        record = self.registry.get("tool")
        assert record is not None
        self.assertEqual(record.directory, self.replacement)
        self.assertTrue(_ran(self.replacement))

    def test_an_acceptance_of_the_folder_copy_does_not_cover_a_replacement(self) -> None:
        accepted.accept_extension("tool", accepted.IN_FOLDER, folder="/x", version="2.0.0",
                                  scopes=[], capabilities=[])

        with self.assertLogs("vpinfe.common.extensions", "WARNING"):
            self.registry.load_installed()

        record = self.registry.get("tool")
        assert record is not None
        self.assertEqual(record.directory, self.shipped)


class TheCheck(Case):
    def test_looks_at_an_extension_that_is_not_accepted(self) -> None:
        directory = _write(self.folder, "tool")

        lines, _passed = check.check(directory)

        self.assertEqual(lines[0], "loads      tool 1.0.0")

    def test_it_accepts_nothing(self) -> None:
        directory = _write(self.folder, "tool")

        check.check(directory)

        self.assertFalse(accepted.accepted_before("tool", accepted.IN_FOLDER))


if __name__ == "__main__":
    unittest.main()

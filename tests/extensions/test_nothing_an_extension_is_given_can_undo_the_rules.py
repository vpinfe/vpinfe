"""Protects: (b) nothing an extension is handed can make it official, trusted or unblocked.

An extension reads its manifest, writes its own settings and is given a context. None of
the three may set where it is from, mark it accepted, switch itself on or reach the
records core keeps about it.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from common.config import paths
from common.extensions import contract, fingerprint, host, store
from common.security import accepted
from tests.support.accepted import isolated
from tests.support.identity import IDENTITY

# Words that would name a way to change what the rules decide. Matched as whole words
# inside an attribute name, so `accepts_files` is caught and `account` is not.
FORBIDDEN = frozenset({
    "accept", "accepted", "accepts", "trust", "trusted", "origin", "official", "unofficial",
    "receipt", "receipts", "fingerprint", "fingerprints", "shield", "notice", "unblock",
    "switch", "switched", "enable", "enabled", "enabling",
})

CLAIMS = {
    "origin": "official",
    "official": True,
    "accepted": True,
    "trusted": True,
    "fingerprint": "0" * 64,
    "receipt": {"repository": "https://example.test/o/r", "release": "1.0.0", "files": {}},
    "switched_off": [],
}

# Reads which library kinds the Owner switched off; it changes nothing about an extension.
READS_ONLY = frozenset({"ctx.games.switched_off_kinds"})

BODY = "captured = []\ndef register(ctx):\n    captured.append(ctx)\n"


def _write(folder: Path, name: str, **changes: object) -> Path:
    directory = folder / name
    directory.mkdir(parents=True)
    raw = {"name": name, "version": "1.0.0", "requires_platform": contract.PLATFORM_ABI, **IDENTITY}
    raw.update(changes)
    (directory / "extension.json").write_text(json.dumps(raw), encoding="utf-8")
    (directory / "__init__.py").write_text(BODY, encoding="utf-8")
    return directory


def _words(attribute: str) -> set[str]:
    return {one for one in attribute.lower().split("_") if one}


def _surface(owner: object, path: str = "ctx", depth: int = 0) -> list[str]:
    """Every public attribute name reachable from `owner`, as a dotted path."""
    found = []
    for attribute in dir(owner):
        if attribute.startswith("_"):
            continue
        here = f"{path}.{attribute}"
        found.append(here)
        value = getattr(owner, attribute, None)
        if depth < 2 and value is not None and type(value).__module__.startswith("common."):
            found.extend(_surface(value, here, depth + 1))
    return found


class Case(unittest.TestCase):
    def setUp(self) -> None:
        isolated(self)
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name)
        self.folder = self.root / "found"
        self.folder.mkdir()
        self.settings = self.root / "settings"
        self.settings.mkdir()
        self.store = store.ExtensionStore(self.settings)
        self.registry = host.Registry(self.store)
        self.addCleanup(self.registry.clear)
        for target, name, value in (
            (host, "BUNDLED_DIR", self.root / "bundled"),
            (host, "OFFICIAL_DIR", self.root / "official"),
            (host, "SEARCH_PATH", (self.folder,)),
            (paths, "EXTENSION_RECEIPTS_PATH", self.root / "receipts.json"),
            (paths, "EXTENSION_LIST_PATH", self.root / "list.json"),
        ):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def accept(self, name: str) -> None:
        manifest = host._read_manifest(self.folder / name)
        accepted.accept_extension(name, accepted.IN_FOLDER, folder=str(self.folder),
                                  version=manifest.version, scopes=manifest.scopes,
                                  capabilities=manifest.capabilities, play=manifest.play)

    def running(self, name: str) -> host.Record:
        self.accept(name)
        with self.assertLogs("vpinfe.common.extensions", "INFO"):
            self.registry.load_installed()
        record = self.registry.get(name)
        assert record is not None and record.context is not None
        return record


class TheManifest(Case):
    def test_claims_in_it_do_not_make_the_extension_official(self) -> None:
        _write(self.folder, "tool", **CLAIMS)

        with self.assertLogs("vpinfe.common.extensions", "INFO"):
            self.registry.load_installed()

        record = self.registry.get("tool")
        assert record is not None
        self.assertFalse(record.official)
        self.assertEqual(record.origin, accepted.IN_FOLDER)
        self.assertEqual((record.state, record.why), (host.OFF, host.NOT_ACCEPTED))

    def test_claims_in_it_do_not_unblock_one_that_is_stopped(self) -> None:
        directory = _write(self.folder, "tool", **CLAIMS)

        with self.assertLogs("vpinfe.common.extensions", "INFO"):
            self.registry.load_installed()

        self.assertFalse((directory / "__pycache__").exists())
        record = self.registry.get("tool")
        assert record is not None
        self.assertNotEqual(record.state, host.LOADED)

    def test_claiming_the_team_is_noticed_and_not_believed(self) -> None:
        _write(self.folder, "tool", author="VPinFE Team", source="https://github.com/vpinfe/tool")

        with self.assertLogs("vpinfe.common.extensions", "INFO"):
            self.registry.load_installed()

        record = self.registry.get("tool")
        assert record is not None
        self.assertTrue(record.claims_team)
        self.assertFalse(record.official)


class TheSettings(Case):
    def test_writing_any_key_leaves_the_rules_as_they_were(self) -> None:
        _write(self.folder, "tool")
        running = self.running("tool")
        ctx = running.context
        assert ctx is not None
        files = fingerprint.hash_files(running.directory)
        before = (self.store.enabled("tool"), running.examine(), files)

        for key, value in CLAIMS.items():
            ctx.config.set(key, str(value))
        ctx.config.set("accounts", "{}")
        ctx.config.set("declared", "{}")

        now = fingerprint.hash_files(running.directory)
        after = (self.store.enabled("tool"), running.examine(), now)
        self.assertEqual(before, after)
        manifest = running.manifest
        assert manifest is not None
        self.assertTrue(
            accepted.extension_accepted("tool", accepted.IN_FOLDER, manifest.scopes,
                                        manifest.capabilities, str(self.folder),
                                        manifest.play)
        )
        self.assertEqual(fingerprint.receipt("tool"), None)

    def test_its_settings_are_not_the_core_switch_list(self) -> None:
        _write(self.folder, "tool")
        ctx = self.running("tool").context
        assert ctx is not None
        self.store.set_enabled("tool", False)

        ctx.config.set("switched_off", "")

        self.assertFalse(self.store.enabled("tool"))


class TheContext(Case):
    def test_offers_no_name_for_changing_what_the_rules_decide(self) -> None:
        _write(self.folder, "tool")
        ctx = self.running("tool").context

        named = [one for one in _surface(ctx) if one not in READS_ONLY
                 and _words(one.rsplit(".", 1)[-1]) & FORBIDDEN]

        self.assertEqual(named, [])

    def test_the_manifest_it_hands_back_names_no_origin_or_trust(self) -> None:
        _write(self.folder, "tool")
        ctx = self.running("tool").context
        assert ctx is not None

        named = [one for one in dir(ctx.manifest) if _words(one) & FORBIDDEN]

        self.assertEqual(named, [])


if __name__ == "__main__":
    unittest.main()

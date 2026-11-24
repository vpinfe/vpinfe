"""Protects: (d) an extension whose dependencies are missing, or clash with another's, stays off
with a reason while VPinFE and every other extension start; (e) a fetched dependency is found on
the import path and can never shadow what VPinFE or the standard library provides.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from common.config import paths
from common.extensions import dependencies, host
from common.extensions.dependencies import Dependency
from common.online import extension_dependencies
from common.security import accepted
from tests.extensions.test_host import HostCase

BODY = "import {module}\n\n\ndef register(ctx):\n    pass\n"


class DependencyCase(HostCase):
    def setUp(self) -> None:
        super().setUp()
        self.dependencies = Path(tempfile.mkdtemp(dir=self.root))
        patcher = mock.patch.object(paths, "DEPENDENCIES_DIR", self.dependencies)
        patcher.start()
        self.addCleanup(patcher.stop)
        before = list(sys.path)
        modules = set(sys.modules)
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), before))
        self.addCleanup(lambda: [sys.modules.pop(one) for one in set(sys.modules) - modules
                                 if one.startswith("probe_")])

    def dependency(self, name: str, version: str, module: str) -> Path:
        folder = self.dependencies / dependencies.PURE / f"{name}-{version}"
        (folder / module).mkdir(parents=True)
        (folder / module / "__init__.py").write_text(f"VERSION = {version!r}\n",
                                                     encoding="utf-8")
        (folder / dependencies.RECEIPT).write_text(json.dumps({"dependency": f"{name}=={version}"}),
                                                encoding="utf-8")
        return folder

    def extension(self, name: str, *lines: str, module: str = "probe_one") -> host.Record:
        """Loaded after the Owner has accepted it: the dependency check comes once it is trusted."""
        directory = self.make(name, {"dependencies": list(lines)}, BODY.format(module=module))
        prepared = self.registry._prepare(directory)
        manifest = prepared.manifest
        assert manifest is not None
        accepted.accept_extension(
            prepared.name, prepared.origin, folder=prepared.folder, version=manifest.version,
            scopes=manifest.scopes, capabilities=manifest.capabilities, play=manifest.play,
            dependencies=manifest.dependencies)
        return self.registry._start(prepared)


class WhenDependenciesAreMissing(DependencyCase):
    def test_it_stays_off_and_names_what_it_lacks(self) -> None:
        record = self.extension("miss", "probe-one==1.0")
        self.assertEqual((record.state, record.why), (host.OFF, host.LACKS_DEPENDENCIES))
        self.assertIn("probe-one==1.0", record.reason)

    def test_two_missing_are_both_named(self) -> None:
        record = self.extension("miss", "probe-one==1.0", "probe-two==2.0")
        self.assertIn("probe-one==1.0", record.reason)
        self.assertIn("probe-two==2.0", record.reason)

    def test_the_other_extensions_still_start(self) -> None:
        self.extension("miss", "probe-one==1.0")
        fine = self.registry.load(self.make("fine", None, "def register(ctx): pass\n"))
        self.assertTrue(fine.running)

    def test_it_starts_when_the_dependencies_arrive(self) -> None:
        record = self.extension("miss", "probe-one==1.0")
        self.dependency("probe-one", "1.0", "probe_one")
        self.registry.dependencies_arrived("miss")
        self.assertTrue(record.running, record.reason)


class WhenDependenciesArePresent(DependencyCase):
    def test_the_extension_imports_them(self) -> None:
        self.dependency("probe-one", "1.0", "probe_one")
        record = self.extension("here", "probe-one==1.0")
        self.assertTrue(record.running, record.reason)

    def test_their_folder_goes_at_the_end_of_the_path_once(self) -> None:
        folder = self.dependency("probe-one", "1.0", "probe_one")
        self.extension("here", "probe-one==1.0")
        self.extension("again", "probe-one==1.0")
        self.assertEqual(sys.path[-1], str(folder))
        self.assertEqual(sys.path.count(str(folder)), 1)

    def test_a_dependency_cannot_shadow_the_standard_library(self) -> None:
        folder = self.dependency("probe-one", "1.0", "json")
        self.extension("here", "probe-one==1.0", module="json")
        import json as standard
        self.assertFalse(str(standard.__file__).startswith(str(folder)))


class WhenTwoNeedDifferentVersions(DependencyCase):
    def test_the_second_waits_for_the_first_and_names_it(self) -> None:
        self.dependency("probe-one", "1.0", "probe_one")
        self.dependency("probe-one", "2.0", "probe_one")
        first = self.extension("first", "probe-one==1.0")
        second = self.extension("second", "probe-one==2.0")
        self.assertTrue(first.running)
        self.assertEqual((second.state, second.why), (host.OFF, host.DEPENDENCY_CLASH))
        self.assertIn("probe-one", second.reason)
        self.assertIn("1.0", second.reason)

    def test_a_stopped_first_still_holds_its_version_until_a_restart(self) -> None:
        self.dependency("probe-one", "1.0", "probe_one")
        self.dependency("probe-one", "2.0", "probe_one")
        first = self.extension("first", "probe-one==1.0")
        self.registry.disable("first", host.SWITCHED_OFF)
        second = self.extension("second", "probe-one==2.0")
        self.assertFalse(first.running)
        self.assertEqual((second.state, second.why), (host.OFF, host.DEPENDENCY_HELD))
        self.assertIn("1.0", second.reason)

    def test_the_same_version_is_shared(self) -> None:
        self.dependency("probe-one", "1.0", "probe_one")
        self.assertTrue(self.extension("first", "probe-one==1.0").running)
        self.assertTrue(self.extension("second", "probe-one==1.0").running)


class WhenAnUpdateAddsADependency(DependencyCase):
    def test_an_accepted_extension_is_not_trusted_with_a_dependency_it_did_not_list(self) -> None:
        self.dependency("probe-one", "1.0", "probe_one")
        record = self.extension("here", "probe-one==1.0")
        self.assertTrue(record.is_accepted())
        self.assertEqual([], accepted.new_access(
            record.name, record.origin, [], [], record.folder, (), ["probe-one==1.0"]))
        self.assertEqual(["dependency:probe-two==2.0"], accepted.new_access(
            record.name, record.origin, [], [], record.folder, (),
            ["probe-one==1.0", "probe-two==2.0"]))
        self.assertFalse(accepted.extension_accepted(
            record.name, record.origin, [], [], record.folder, (),
            ["probe-one==1.0", "probe-two==2.0"]))


class WhatTheStartSweeps(DependencyCase):
    def test_a_dependency_nothing_names_goes_and_a_named_one_stays(self) -> None:
        keep = self.dependency("probe-one", "1.0", "probe_one")
        drop = self.dependency("probe-two", "1.0", "probe_two")
        directory = self.make("holder", {"dependencies": ["probe-one==1.0"]})
        with mock.patch.object(host, "SEARCH_PATH", (directory.parent,)):
            extension_dependencies.sweep_unused()
        self.assertTrue(keep.is_dir())
        self.assertFalse(drop.exists())
        self.assertIsNotNone(dependencies.folder_of(Dependency("probe-one", "1.0")))


if __name__ == "__main__":
    unittest.main()

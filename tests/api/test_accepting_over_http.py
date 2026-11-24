"""Accepting a theme or an extension over HTTP: one route each, and the scope it asks for."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import httpapi
from common import extensions
from common.config import paths
from common.extensions import contract, host, store
from common.online import theme_ops
from common.security import accepted
from httpapi import auth, scopes
from tests.support.accepted import isolated
from tests.support.identity import IDENTITY

ELSEWHERE = "https://git.example.net/someone/themes/raw/branch/main/themes.json"


class _Registry:
    themes_dir = "themes"
    themes = {"Mine": {"manifest": {"name": "Mine"}, "registry_info": {"url": "https://x.net/o/r"},
                       "source": ELSEWHERE}}

    def get_themes(self) -> dict:
        return self.themes

    def local_themes(self) -> dict:
        return {}

    def is_installed(self, key: str) -> bool:
        return False


class TheRoutes(unittest.TestCase):
    def setUp(self) -> None:
        isolated(self)
        self.api = httpapi.create_api_app()

    def scope_of(self, method: str, path: str) -> str | None:
        for found, route in auth.iter_api_routes(self.api):
            if found == path and method in (route.methods or ()):
                return auth.route_scope(route)
        self.fail(f"no {method} {path}")

    def test_accepting_an_extension_is_protected(self) -> None:
        self.assertEqual(self.scope_of("POST", "/extensions/{name}/accept"), scopes.PROTECTED)

    def test_accepting_a_theme_is_protected(self) -> None:
        self.assertEqual(self.scope_of("POST", "/themes/{key}/accept"), scopes.PROTECTED)

    def test_reading_what_is_stopped_is_gated_on_reading_config(self) -> None:
        self.assertEqual(self.scope_of("GET", "/themes/unaccepted"), scopes.CONFIG_READ)

    def test_there_is_one_route_to_accept_each_kind(self) -> None:
        accepting = [(sorted(route.methods or ()), path)
                     for path, route in auth.iter_api_routes(self.api) if "accept" in path]

        self.assertEqual(sorted(accepting), [(["GET"], "/themes/unaccepted"),
                                             (["POST"], "/extensions/{name}/accept"),
                                             (["POST"], "/themes/{key}/accept")])


class AcceptingATheme(unittest.TestCase):
    def setUp(self) -> None:
        isolated(self)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        patched = mock.patch.object(theme_ops, "_readable", return_value=_Registry())
        patched.start()
        self.addCleanup(patched.stop)

    def test_records_the_address_the_source_offers_it_from(self) -> None:
        answer = self.client.post("/themes/Mine/accept")

        self.assertEqual(answer.status_code, 200)
        self.assertTrue(answer.json()["accepted"])
        self.assertTrue(accepted.theme_accepted("Mine", ELSEWHERE, stock=False))

    def test_a_theme_nothing_offers_is_404(self) -> None:
        answer = self.client.post("/themes/Nothing/accept")

        self.assertEqual(answer.status_code, 404)
        self.assertFalse(accepted.theme_runs("Nothing"))


class TheStoppedThemes(unittest.TestCase):
    def test_are_read_from_the_folder_alone(self) -> None:
        isolated(self)
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        folder = Path(holder.name)
        patched = mock.patch.object(paths, "THEMES_DIR", folder)
        patched.start()
        self.addCleanup(patched.stop)
        accepted.theme_runs("anything")
        (folder / "Later").mkdir()
        client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

        with mock.patch.object(theme_ops, "_loaded") as read:
            answer = client.get("/themes/unaccepted")

        read.assert_not_called()
        self.assertEqual(answer.json(), {"themes": [{"key": "Later", "name": "Later"}]})


class AcceptingAnExtension(unittest.TestCase):
    def setUp(self) -> None:
        isolated(self)
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        root = Path(holder.name)
        self.folder = root / "found"
        self.folder.mkdir()
        directory = self.folder / "tool"
        directory.mkdir()
        (directory / "extension.json").write_text(json.dumps(
            {"name": "tool", "version": "1.0.0", "requires_platform": contract.PLATFORM_ABI,
             **IDENTITY}),
            encoding="utf-8")
        (directory / "__init__.py").write_text("def register(ctx): pass\n", encoding="utf-8")
        self.registry = host.Registry(store.ExtensionStore(root))
        extensions.set_registry(self.registry)
        self.addCleanup(extensions.set_registry, host.Registry())
        self.addCleanup(self.registry.clear)
        with self.assertLogs("vpinfe.common.extensions", "WARNING"):
            self.registry.load(directory)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def test_starts_it_and_says_so(self) -> None:
        answer = self.client.post("/extensions/tool/accept")

        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.json()["state"], host.LOADED)
        self.assertTrue(answer.json()["trust"]["accepted"])

    def test_an_extension_nothing_answers_to_is_404(self) -> None:
        self.assertEqual(self.client.post("/extensions/nothing/accept").status_code, 404)


if __name__ == "__main__":
    unittest.main()

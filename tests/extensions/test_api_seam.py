"""An extension's routes: gated by core, listed by core, and gone when it breaks.

The app mounts what the registry already holds, so each test builds the registry it
wants and then the app - which is also how `main.py` does it.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from starlette.testclient import TestClient

import httpapi
from common import extensions
from common.extensions import contract, host, store
from httpapi import auth, scopes
from tests.support.accepted import owner_accepts
from tests.support.identity import IDENTITY

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "extensions"


class SeamCase(unittest.TestCase):
    def setUp(self) -> None:
        owner_accepts(self)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.store = store.ExtensionStore(self.root)
        self.registry = host.Registry(self.store)
        extensions.set_registry(self.registry)
        self.addCleanup(extensions.set_registry, host.Registry())
        self.addCleanup(self.registry.clear)

    def client(self, load: bool = True) -> TestClient:
        if load:
            self.registry.load_from(FIXTURES)
        return TestClient(httpapi.create_api_app(), raise_server_exceptions=False)


class MountTests(SeamCase):
    def test_an_extensions_routes_answer_under_its_own_name(self) -> None:
        found = self.client().get("/ext/sample/hello")

        self.assertEqual(found.status_code, 200)
        self.assertEqual(found.json(), {"name": "sample", "greeting": "hello"})

    def test_a_route_reads_the_extensions_own_settings(self) -> None:
        self.store.set_setting("sample", "greeting", "good evening")

        found = self.client().get("/ext/sample/hello")

        self.assertEqual(found.json()["greeting"], "good evening")

    def test_every_extension_route_declares_the_scope_it_was_gated_on(self) -> None:
        """Core attaches the gate, so an extension cannot ship a route without one."""
        self.registry.load_from(FIXTURES)
        api = httpapi.create_api_app()

        gated = {path: auth.route_scope(route)
                 for path, route in auth.iter_api_routes(api)
                 if path.startswith("/ext/")}

        self.assertTrue(gated)
        self.assertEqual(set(gated.values()),
                         {"ext:sample:play", "ext:sample:library",
                          "ext:bystander:play", "ext:bystander:library"})

    def test_core_mounts_its_routes_for_every_extension_with_a_manifest(self) -> None:
        """Core writes these itself, so an extension with nothing to offer still has the
        paths and they answer for it."""
        client = self.client()

        self.assertEqual(client.get("/ext/bystander/settings").status_code, 200)
        self.assertEqual(client.get("/ext/bystander/accounts/anyone").status_code, 404)

    def test_the_settings_core_serves_never_carry_a_secret(self) -> None:
        self.store.set_setting("sample", "token", "hunter2")

        found = self.client().get("/ext/sample/settings").json()["fields"]

        token = next(one for one in found if one["key"] == "token")
        self.assertEqual((token["type"], token["set"]), ("secret", True))
        self.assertNotIn("value", token)
        self.assertNotIn("hunter2", json.dumps(found))

    def test_a_running_extension_grants_the_scope_its_routes_need(self) -> None:
        self.registry.load_from(FIXTURES)

        held = auth.SignInPolicy().identify(None).scopes  # type: ignore[arg-type]

        self.assertTrue(scopes.CORE <= held)
        self.assertLessEqual({"ext:sample:play", "ext:sample:library"}, held)

    def test_every_scope_the_context_checks_is_one_the_manifest_may_declare(self) -> None:
        from common.extensions import games, provided_apps

        for scope in (provided_apps.APPS_PROVIDE, games.GAMES_READ, games.GAMES_WRITE,
                      games.CONFIG_WRITE):
            with self.subTest(scope=scope):
                self.assertTrue(scopes.is_known(scope))

    def test_an_install_with_no_extensions_holds_the_core_scopes_and_no_more(self) -> None:
        self.assertEqual(auth.SignInPolicy().identify(None).scopes, scopes.CORE)  # type: ignore[arg-type]

    def test_the_gate_actually_runs(self) -> None:
        """Declaring the scope is not the same as enforcing it, and the difference is
        invisible under local trust - which grants everything. So the policy is swapped
        for one that grants core and nothing else, and the route has to refuse."""
        class CoreOnly(auth.SignInPolicy):
            name = "test"

            def identify(self, request) -> auth.Identity:
                return auth.Identity(name="test", scopes=scopes.CORE)

        client = self.client()
        auth.set_policy(CoreOnly())
        self.addCleanup(auth.set_policy, auth.SignInPolicy())

        found = client.get("/ext/sample/hello")

        self.assertEqual(found.status_code, 403)
        self.assertIn("ext:sample:library", found.json()["error"]["message"])
        served = client.put("/ext/sample/settings", json={"values": {"greeting": "x"}})
        self.assertEqual(served.status_code, 403)
        self.assertIn("ext:sample:play", served.json()["error"]["message"])


class ListingTests(SeamCase):
    def test_the_listing_names_every_extension_and_its_state(self) -> None:
        found = self.client().get("/extensions").json()["extensions"]

        self.assertEqual([one["name"] for one in found], ["bystander", "sample"])
        self.assertEqual({one["state"] for one in found}, {"loaded"})

    def test_the_listing_carries_what_an_extension_declared(self) -> None:
        found = self.client().get("/extensions").json()["extensions"]
        sample = next(one for one in found if one["name"] == "sample")

        self.assertEqual(sample["display_name"], "Sample")
        self.assertEqual(sample["capabilities"], ["config:own", "ui:mount"])
        self.assertEqual(sample["routes"], ["ext:sample:library", "ext:sample:library"])

    def test_one_that_did_not_load_is_listed_with_the_reason(self) -> None:
        self.store.set_enabled("sample", False)

        found = self.client().get("/extensions").json()["extensions"]
        sample = next(one for one in found if one["name"] == "sample")

        self.assertEqual(sample["state"], "off")
        self.assertEqual(sample["reason"], "Switched off")
        self.assertEqual(sample["reason_key"], "extension.reason.switched_off")


class NeedsTests(SeamCase):
    def test_what_running_extensions_ask_for_is_read_from_one_route(self) -> None:
        found = self.client().get("/extensions/needs")

        self.assertEqual(found.status_code, 200)
        self.assertEqual(found.json(), {"needs": [], "away": [], "failed": []})

    def test_an_extension_switched_off_is_away(self) -> None:
        self.store.set_enabled("sample", False)

        found = self.client().get("/extensions/needs").json()

        self.assertEqual(found["away"], ["sample"])

    def test_the_route_is_gated_on_reading_the_instance(self) -> None:
        api = httpapi.create_api_app()
        gated = {path: auth.route_scope(route) for path, route in auth.iter_api_routes(api)}

        self.assertEqual(gated["/extensions/needs"], scopes.INSTANCE_READ)


class SwitchTests(SeamCase):
    def switch(self, client: TestClient, name: str, on: bool):
        with self.assertLogs("vpinfe.common.extensions", "INFO"):
            return client.put(f"/extensions/{name}/enabled", json={"enabled": on})

    def test_off_answers_with_the_extension_and_its_routes_refuse_at_once(self) -> None:
        client = self.client()

        found = self.switch(client, "sample", False)

        self.assertEqual(found.status_code, 200)
        self.assertEqual((found.json()["state"], found.json()["enabled"]), ("off", False))
        self.assertEqual(found.json()["reason_key"], host.SWITCHED_OFF)
        self.assertFalse(self.store.enabled("sample"))
        self.assertEqual(client.get("/ext/sample/hello").status_code, 501)
        self.assertEqual(client.get("/ext/sample/settings").status_code, 501)
        self.assertEqual(client.get("/ext/bystander/hello").status_code, 200)

    def test_on_is_written_and_waits_for_the_next_start(self) -> None:
        self.store.set_enabled("sample", False)
        client = self.client()

        found = self.switch(client, "sample", True)

        self.assertEqual(found.status_code, 200)
        self.assertEqual((found.json()["state"], found.json()["enabled"]), ("off", True))
        self.assertEqual(found.json()["reason_key"], host.STARTS_AT_RESTART)
        self.assertTrue(self.store.enabled("sample"))

    def test_a_name_nothing_answers_to_is_a_404(self) -> None:
        found = self.client().put("/extensions/nothing/enabled", json={"enabled": False})

        self.assertEqual(found.status_code, 404)
        self.assertIn("nothing", found.json()["error"]["message"])

    def test_the_switch_is_gated_on_writing_config(self) -> None:
        api = httpapi.create_api_app()

        gated = {path: auth.route_scope(route) for path, route in auth.iter_api_routes(api)}

        self.assertEqual(gated["/extensions/{name}/enabled"], scopes.CONFIG_WRITE)


class FailureTests(SeamCase):
    def test_a_route_that_throws_disables_that_extension_and_leaves_core_running(self):
        client = self.client()

        with self.assertLogs("vpinfe.httpapi.errors", "ERROR"), \
                self.assertLogs("vpinfe.common.extensions", "ERROR"):
            self.assertEqual(client.get("/ext/sample/boom").status_code, 500)

        self.assertEqual(self.registry.get("sample").state, host.DISABLED)
        self.assertEqual(client.get("/health").status_code, 200)
        self.assertEqual(client.get("/ext/bystander/hello").status_code, 200)

    def test_a_function_it_supplied_that_raises_is_a_refusal_and_not_a_fault(self) -> None:
        """The extension is answering, with a line a person can read. Disabling it for
        that would take away every other page it has."""
        directory = self.root / "refuser"
        directory.mkdir()
        (directory / "extension.json").write_text(json.dumps({
            "name": "refuser", "version": "1.0.0",
            "requires_platform": contract.PLATFORM_ABI, **IDENTITY, "capabilities": ["ui:mount"],
        }), encoding="utf-8")
        (directory / "__init__.py").write_text(
            "def register(ctx):\n"
            "    def rows():\n"
            "        raise ValueError('The service said no')\n"
            "    ctx.ui.list('things', rows, columns=[{'field': 'name'}], area='community')\n",
            encoding="utf-8")
        self.registry.load(directory)
        client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

        with self.assertLogs("vpinfe.ext.refuser", "WARNING"):
            found = client.get("/ext/refuser/lists/things")

        self.assertEqual(found.status_code, 400)
        refuser = self.registry.get("refuser")
        assert refuser is not None
        self.assertEqual(refuser.state, host.LOADED)

    def test_a_disabled_extensions_routes_say_which_one_and_why(self) -> None:
        client = self.client()
        with self.assertLogs("vpinfe.httpapi.errors", "ERROR"), \
                self.assertLogs("vpinfe.common.extensions", "ERROR"):
            client.get("/ext/sample/boom")

        found = client.get("/ext/sample/hello")

        self.assertEqual(found.status_code, 501)
        self.assertIn("Sample", found.json()["error"]["message"])
        self.assertIn("Unhandled error", found.json()["error"]["message"])

    def test_a_disabled_extension_no_longer_holds_its_scope(self) -> None:
        client = self.client()
        with self.assertLogs("vpinfe.httpapi.errors", "ERROR"), \
                self.assertLogs("vpinfe.common.extensions", "ERROR"):
            client.get("/ext/sample/boom")

        self.assertNotIn("ext:sample:library",
                         auth.SignInPolicy().identify(None).scopes)  # type: ignore[arg-type]

    def test_an_extension_asking_for_a_scope_that_does_not_exist_is_refused(self) -> None:
        """The manifest is the consent surface, so a scope nobody could grant is a
        manifest nobody could have agreed to."""
        directory = self.root / "invented"
        directory.mkdir()
        (directory / "extension.json").write_text(json.dumps({
            "name": "invented", "version": "1.0.0",
            "requires_platform": contract.PLATFORM_ABI, **IDENTITY,
            "scopes": ["tables:teleport"],
        }), encoding="utf-8")
        (directory / "__init__.py").write_text(
            "from fastapi import APIRouter\n"
            "def register(ctx):\n"
            "    router = APIRouter()\n"
            "    @router.get('/hello')\n"
            "    def hello() -> dict:\n"
            "        return {}\n"
            "    ctx.add_router(router)\n", encoding="utf-8")
        self.registry.load(directory)

        with self.assertLogs("vpinfe.common.extensions", "ERROR"):
            client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

        invented = self.registry.get("invented")
        assert invented is not None
        self.assertEqual(invented.state, host.FAILED)
        self.assertEqual(client.get("/ext/invented/hello").status_code, 501)


if __name__ == "__main__":
    unittest.main()

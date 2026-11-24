import unittest

from fastapi import APIRouter
from starlette.testclient import TestClient

import httpapi
from httpapi import auth, scopes


def _client(app=None) -> TestClient:
    return TestClient(app or httpapi.create_api_app(), raise_server_exceptions=False)


def _app_with_guarded_routes() -> object:
    """An app with two synthetic routes carrying different scopes.

    Deliberately not the real /tables: listing tables mints ids, so driving it from
    a unit test would write into whatever library the machine is configured with.
    What is under test here is the boundary, not the catalog.
    """
    app = httpapi.create_api_app()
    router = APIRouter(prefix="/probe")

    @router.get("/tables-ish", dependencies=[auth.requires(scopes.GAMES_READ)])
    def _games_ish() -> dict:
        return {"ok": True}

    @router.get("/vps-ish", dependencies=[auth.requires(scopes.VPS_READ)])
    def _vps_ish() -> dict:
        return {"ok": True}

    app.include_router(router)
    return app


class SignInPolicyTests(unittest.TestCase):
    """This machine is trusted, so the app answers on it exactly as it always has."""

    def setUp(self) -> None:
        auth.set_policy(auth.SignInPolicy())
        self.addCleanup(auth.set_policy, auth.SignInPolicy())

    def test_every_endpoint_answers_on_this_machine(self) -> None:
        client = _client()

        for path in ("/", "/health", "/play/state"):
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 200)

    def test_the_local_identity_holds_every_core_scope(self) -> None:
        identity = auth.SignInPolicy().identify(None)  # type: ignore[arg-type]

        self.assertEqual(identity.scopes, scopes.CORE)
        self.assertTrue(identity.can(scopes.SYSTEM_ADMIN))


class EnforcementTests(unittest.TestCase):
    """Swap the policy and the same routes start refusing. No route changes."""

    def setUp(self) -> None:
        self.addCleanup(auth.set_policy, auth.SignInPolicy())

    def _grant(self, *granted):
        class Policy:
            name = "test"

            def identify(self, request):
                return auth.Identity(name="test", scopes=frozenset(granted))

        auth.set_policy(Policy())

    def test_a_missing_scope_is_refused_and_names_what_was_needed(self) -> None:
        self._grant(scopes.INSTANCE_READ)
        client = _client(_app_with_guarded_routes())

        response = client.get("/probe/tables-ish")

        self.assertEqual(response.status_code, 403)
        error = response.json()["error"]
        self.assertEqual(error["code"], "forbidden")
        self.assertIn(scopes.GAMES_READ, error["message"])

    def test_a_granted_scope_still_passes(self) -> None:
        self._grant(scopes.INSTANCE_READ)

        self.assertEqual(_client().get("/health").status_code, 200)

    def test_scopes_are_not_interchangeable(self) -> None:
        """games:read must not open the door to the outbound VPSdb lookup."""
        self._grant(scopes.GAMES_READ)
        client = _client(_app_with_guarded_routes())

        self.assertEqual(client.get("/probe/tables-ish").status_code, 200)
        self.assertEqual(client.get("/probe/vps-ish").status_code, 403)

    def test_no_identity_fails_closed(self) -> None:
        """If the middleware did not run, the answer is no rather than a guess."""
        app = httpapi.create_api_app()
        app.user_middleware = [m for m in app.user_middleware
                               if m.cls is not auth.ScopeMiddleware]
        app.middleware_stack = app.build_middleware_stack()

        with self.assertLogs("vpinfe.httpapi.auth", level="ERROR"):
            response = _client(app).get("/health")

        self.assertEqual(response.status_code, 403)


class RouteDeclarationTests(unittest.TestCase):
    """Forgetting to declare a scope has to be impossible, not merely discouraged."""

    def test_every_shipped_route_declares_one(self) -> None:
        app = httpapi.create_api_app()

        unscoped = [path for path, route in auth.iter_api_routes(app)
                    if auth.route_scope(route) is None]

        self.assertEqual(unscoped, [])

    def test_an_unscoped_route_stops_the_app_from_starting(self) -> None:
        app = httpapi.create_api_app()
        router = APIRouter(prefix="/forgot")

        @router.get("/something")
        def _forgot() -> dict:
            return {}

        app.include_router(router)

        with self.assertRaises(RuntimeError) as raised:
            auth.assert_every_route_declares_a_scope(app)

        self.assertIn("/forgot/something", str(raised.exception))

    def test_an_unknown_scope_is_rejected_where_it_is_written(self) -> None:
        with self.assertRaises(ValueError):
            auth.requires("tables:destroy")

    def test_extension_scopes_are_namespaced_and_accepted(self) -> None:
        scope = scopes.extension_scope("wovp", "read")

        self.assertEqual(scope, "ext:wovp:read")
        self.assertTrue(scopes.is_known(scope))
        self.assertNotIn(scope, scopes.CORE, "an extension can never claim a core scope")
        auth.requires(scope)


class CapabilityTests(unittest.TestCase):
    def test_discovery_reports_the_feature_behind_each_capability(self) -> None:
        declared = {c["name"]: c for c in _client().get("/").json()["capabilities"]}

        self.assertEqual(declared["library"]["feature"], "library")
        self.assertEqual(declared["play"]["feature"], "frontend")

    def test_an_unavailable_capability_says_why(self) -> None:
        """The reason is shown to users, so 'no' on its own is not good enough."""
        declared = {c["name"]: c for c in _client().get("/").json()["capabilities"]}
        hardware = declared["peripherals"]

        if not hardware["available"]:
            self.assertTrue(hardware["reason"])
        else:
            self.assertIsNone(hardware["reason"])


if __name__ == "__main__":
    unittest.main()

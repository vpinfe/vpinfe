import errno
import socket
import unittest
from unittest import mock
from urllib.request import urlopen

import requests
from fastapi import FastAPI
from starlette.testclient import TestClient

import httpapi
from common.i18n import t
from httpapi import capabilities
from httpapi.errors import (
    ApiError,
    FeatureUnavailableError,
    NotFoundError,
    install_error_handlers,
)
from tests.support.library import fake_game


def _client() -> TestClient:
    return TestClient(httpapi.create_api_app(), raise_server_exceptions=False)


class ManufacturerEndpointTests(unittest.TestCase):
    def test_the_logo_lookup_is_enumerable_over_the_wire(self) -> None:
        """VPS names union library names, each with slug, resolution and count.

        The catalog is patched with table-shaped objects, not row dicts, so the
        count path exercises the real object-to-row conversion - the live app
        hands _catalog() Table objects.
        """
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch

        from common.games.game import Game
        from common.media.manufacturer_logos import configure_manufacturer_logos

        def _game(folder: str, manufacturer: str) -> Game:
            return fake_game(f"/games/{folder}", folder,
                             meta={"Info": {"Manufacturer": manufacturer}})

        catalog = {
            "id-1": _game("Eight Ball (Bally 1977)", "Bally Manufacturing"),
            "id-2": _game("Eight Ball Deluxe (Bally 1981)", "Bally Manufacturing"),
            "id-3": _game("Garage Build (Homebrew 2020)", "Homebrew Works"),
        }

        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "manufacturer_logos"
            root.mkdir()
            (root / "bally.png").write_bytes(b"png")
            configure_manufacturer_logos(root)
            self.addCleanup(configure_manufacturer_logos, None)

            with patch("httpapi.manufacturers._vps_names",
                       return_value=["Bally Manufacturing", "Bally Wulff"]), \
                 patch("common.games.game_repository.catalog", return_value=catalog):
                response = _client().get("/manufacturers")

        self.assertEqual(response.status_code, 200)
        rows = {row["name"]: row for row in response.json()["manufacturers"]}
        self.assertEqual(rows["Bally Manufacturing"]["slug"], "bally")
        self.assertEqual(rows["Bally Manufacturing"]["logo"], "/manufacturers/bally/logo")
        self.assertEqual(rows["Bally Manufacturing"]["games"], 2)
        self.assertIsNone(rows["Bally Wulff"]["logo"])
        self.assertEqual(rows["Bally Wulff"]["games"], 0)
        self.assertEqual(rows["Homebrew Works"]["games"], 1,
                         "a library-only name still gets a row")


class DiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        # create_api_app() declares the core capabilities, so a test that wants a
        # known registry has to build the app first and clear afterwards.
        self.app = httpapi.create_api_app()
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.addCleanup(capabilities.clear)

    def _isolated(self) -> TestClient:
        capabilities.clear()
        return self.client

    def test_discovery_describes_the_instance(self) -> None:
        body = self.client.get("/").json()

        self.assertEqual(body["name"], "VPinFE")
        self.assertEqual(body["api_version"], "v1")
        self.assertTrue(body["vpinfe_version"])
        self.assertEqual(body["extensions"], [])
        declared = {c["name"] for c in body["capabilities"]}
        self.assertIn("library", declared, "the shipped capabilities are declared")

    def test_discovery_links_point_under_the_api_prefix(self) -> None:
        links = self.client.get("/").json()["links"]

        self.assertEqual(links["self"], "/api/v1")
        self.assertEqual(links["health"], "/api/v1/health")
        self.assertEqual(links["openapi"], "/api/v1/openapi.json")
        self.assertEqual(links["docs"], "/api/v1/docs")
        self.assertEqual(links["events"], "/api/v1/events")

    def test_discovery_says_so_while_setup_is_pending(self) -> None:
        with mock.patch("common.devices.install_setup.pending", return_value=True):
            body = self.client.get("/").json()

        self.assertIs(body["setup_pending"], True)

    def test_discovery_leaves_the_key_out_once_setup_has_finished(self) -> None:
        """A build from before Setup never says it either, so absent has to read as set up
        for both."""
        with mock.patch("common.devices.install_setup.pending", return_value=False):
            body = self.client.get("/").json()

        self.assertNotIn("setup_pending", body)

    def test_every_other_null_is_still_said_with_setup_finished_and_pending(self) -> None:
        """Only `setup_pending` is conditional. A capability with no switch and no reason
        says both as null, and a client reads that as a statement."""
        for pending in (True, False):
            with self.subTest(pending=pending), \
                 mock.patch("common.devices.install_setup.pending", return_value=pending):
                events = next(one for one in self.client.get("/").json()["capabilities"]
                              if one["name"] == "events")

                self.assertIn("feature", events)
                self.assertIsNone(events["feature"])
                self.assertIn("reason", events)
                self.assertIsNone(events["reason"])

    def test_health_reports_ok(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_discovery_says_which_install_is_answering(self) -> None:
        """`name` is the product and reads the same everywhere, so it cannot tell two
        installs apart. `install_id` is the field that can."""
        payload = self.client.get("/").json()

        self.assertEqual(payload["name"], "VPinFE")
        self.assertIn("install_id", payload)
        self.assertIn("display_name", payload)
        self.assertEqual(payload["features"],
                         ["core", "library", "frontend", "devices", "overview"],
                         "an unconfigured install has every feature, and `core` is on "
                         "the wire because every install has it")

    def test_asking_who_this_is_does_not_write_to_the_config(self) -> None:
        """Minting happens once at startup. A GET that wrote would make every reader a
        writer, and read-only installs a bug report."""
        from unittest.mock import patch

        with patch("common.devices.install_identity.ensure_id") as mint:
            self.client.get("/")

        mint.assert_not_called()

    def test_declared_capabilities_appear_in_discovery(self) -> None:
        self._isolated()
        capabilities.declare(capabilities.Capability(
            name="library",
            feature="library",
            description="Table inventory",
        ))
        capabilities.declare(capabilities.Capability(
            name="peripherals",
            feature="frontend",
            is_available=lambda: (False, "No DOF hardware detected"),
        ))

        declared = self.client.get("/").json()["capabilities"]

        names = [c["name"] for c in declared]
        self.assertEqual(names, sorted(names), "sorted by name for a stable payload")

        by_name = {c["name"]: c for c in declared}
        self.assertEqual(by_name["library"]["feature"], "library")
        self.assertTrue(by_name["library"]["available"])
        self.assertIsNone(by_name["library"]["reason"])
        self.assertFalse(by_name["peripherals"]["available"])
        self.assertEqual(by_name["peripherals"]["reason"], "No DOF hardware detected")

    def test_infrastructure_belongs_to_no_feature(self) -> None:
        """`events` and `jobs` are present wherever the API is, so they name no feature
        and no feature switch can take them away."""
        self._isolated()
        capabilities.declare(capabilities.Capability(name="events"))

        declared = self.client.get("/").json()["capabilities"]

        self.assertIsNone(declared[0]["feature"])

    def test_an_unknown_feature_is_refused(self) -> None:
        """A typo would declare a capability nothing can ever switch on."""
        with self.assertRaises(ValueError):
            capabilities.Capability(name="library", feature="somewhere-else")

    def test_launch_declares_whether_this_machine_can_do_it(self) -> None:
        """Reading play state works without a launcher; starting a game does not.
        Discovery has to say so, or a client shows a Play button that always 501s."""
        declared = {c["name"]: c for c in self.client.get("/").json()["capabilities"]}

        self.assertIn("launch", declared)
        self.assertEqual(declared["launch"]["feature"], "frontend")
        self.assertIsNotNone(declared["launch"].get("available"))

    def test_a_broken_availability_probe_does_not_break_discovery(self) -> None:
        def _explode():
            raise RuntimeError("probe blew up")

        self._isolated()
        capabilities.declare(capabilities.Capability(
            name="flaky", feature="frontend", is_available=_explode))

        with self.assertLogs(capabilities.logger, "ERROR") as logged:
            response = self.client.get("/")
        declared = response.json()["capabilities"]

        self.assertEqual(response.status_code, 200)
        self.assertFalse(declared[0]["available"])
        self.assertEqual(declared[0]["reason"], t("error.capabilities.check_failed"))
        self.assertIn("probe blew up", "\n".join(logged.output))


class ErrorEnvelopeTests(unittest.TestCase):
    """Every failure under /api/v1 comes back in the one envelope shape."""

    def _envelope(self, response):
        body = response.json()
        self.assertEqual(set(body), {"error"})
        self.assertEqual(set(body["error"]), {"code", "message", "details"})
        return body["error"]

    def test_unknown_route_returns_not_found_envelope(self) -> None:
        response = _client().get("/no-such-thing")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self._envelope(response)["code"], "not_found")

    def test_wrong_method_returns_method_not_allowed_envelope(self) -> None:
        response = _client().post("/health")

        self.assertEqual(response.status_code, 405)
        self.assertEqual(self._envelope(response)["code"], "method_not_allowed")

    def test_api_error_carries_its_code_status_and_details(self) -> None:
        api = httpapi.create_api_app()

        @api.get("/raises")
        def _raises():
            raise ApiError("teapot", "I'm a teapot", status_code=418, details={"tea": "earl grey"})

        response = TestClient(api, raise_server_exceptions=False).get("/raises")
        error = self._envelope(response)

        self.assertEqual(response.status_code, 418)
        self.assertEqual(error["code"], "teapot")
        self.assertEqual(error["message"], "I'm a teapot")
        self.assertEqual(error["details"], {"tea": "earl grey"})

    def test_helper_errors_map_to_their_statuses(self) -> None:
        api = httpapi.create_api_app()

        @api.get("/missing")
        def _missing():
            raise NotFoundError("No such table")

        @api.get("/unavailable")
        def _unavailable():
            raise FeatureUnavailableError("DOF is not configured on this instance")

        client = TestClient(api, raise_server_exceptions=False)

        missing = client.get("/missing")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(self._envelope(missing)["code"], "not_found")

        unavailable = client.get("/unavailable")
        self.assertEqual(unavailable.status_code, 501)
        self.assertEqual(self._envelope(unavailable)["code"], "feature_unavailable")

    def test_bad_query_parameter_returns_invalid_request_with_details(self) -> None:
        api = httpapi.create_api_app()

        @api.get("/needs-int")
        def _needs_int(count: int):
            return {"count": count}

        response = TestClient(api, raise_server_exceptions=False).get("/needs-int?count=nope")
        error = self._envelope(response)

        self.assertEqual(response.status_code, 422)
        self.assertEqual(error["code"], "invalid_request")
        self.assertTrue(error["details"])

    def test_unhandled_exception_returns_internal_error_and_leaks_nothing(self) -> None:
        api = httpapi.create_api_app()

        @api.get("/boom")
        def _boom():
            raise RuntimeError("secret internal detail")

        with self.assertLogs("vpinfe.httpapi.errors", level="ERROR"):
            response = TestClient(api, raise_server_exceptions=False).get("/boom")
        error = self._envelope(response)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(error["code"], "internal_error")
        self.assertNotIn("secret internal detail", response.text)

    def test_something_in_the_way_is_a_conflict_in_its_own_words(self) -> None:
        """A busy job, a device that will not answer and an update that cannot be taken
        are raised far from any route, and reach it without a handler on the way."""
        from common.games import mobile_transfer
        from common.online import app_updater
        from common.runtime import jobs

        def raising(refusal: Exception):
            def route() -> None:
                raise refusal
            return route

        api = httpapi.create_api_app()
        refusals = {"raises-busy": jobs.JobBusyError(t("error.jobs.busy")),
                    "raises-away": mobile_transfer.DeviceUnreachableError(
                        t("device.reason.timed_out")),
                    "raises-latest": app_updater.UpdateError(
                        t("error.instance.already_latest"))}
        for path, refusal in refusals.items():
            api.add_api_route(f"/{path}", raising(refusal))
        client = TestClient(api, raise_server_exceptions=False)

        for path, refusal in refusals.items():
            with self.subTest(path):
                response = client.get(f"/{path}")

                self.assertEqual(response.status_code, 409)
                self.assertEqual(self._envelope(response)["message"], str(refusal))

    def test_a_refused_write_is_not_told_to_the_seam_as_a_break(self) -> None:
        told = mock.Mock()
        api = FastAPI()
        install_error_handlers(api, on_unhandled=told)

        @api.get("/locked")
        def _locked():
            raise PermissionError(errno.EACCES, "Permission denied", "/games/Locked")

        with self.assertLogs("vpinfe.httpapi.errors", "WARNING"):
            response = TestClient(api, raise_server_exceptions=False).get("/locked")

        self.assertEqual((response.status_code, self._envelope(response)["message"]),
                         (409, t("said.why.no_permission_at", path="/games/Locked")))
        told.assert_not_called()

    def test_a_host_that_did_not_answer_is_not_a_conflict(self) -> None:
        with socket.socket() as free:
            free.bind(("127.0.0.1", 0))
            port = free.getsockname()[1]
        told = mock.Mock()
        api = FastAPI()
        install_error_handlers(api, on_unhandled=told)

        @api.get("/over-requests")
        def _over_requests():
            requests.get(f"http://127.0.0.1:{port}/", timeout=5)

        @api.get("/over-urllib")
        def _over_urllib():
            urlopen(f"http://127.0.0.1:{port}/", timeout=5)

        client = TestClient(api, raise_server_exceptions=False)
        for path, said in (("/over-requests", t("said.why.no_answer_at", host="127.0.0.1")),
                           ("/over-urllib", t("said.why.nothing_answers"))):
            with self.subTest(path):
                with self.assertLogs("vpinfe.httpapi.errors", "WARNING"):
                    response = client.get(path)
                error = self._envelope(response)

                self.assertEqual((response.status_code, error["code"], error["message"]),
                                 (502, "upstream_unavailable", said))
        told.assert_not_called()


class OpenApiTests(unittest.TestCase):
    def test_spec_is_served_and_scoped_to_the_mount(self) -> None:
        spec = _client().get("/openapi.json").json()

        self.assertEqual(spec["info"]["title"], "VPinFE API")
        self.assertIn("/health", spec["paths"])

    def test_docs_page_is_served(self) -> None:
        self.assertEqual(_client().get("/docs").status_code, 200)


class RegistrationTests(unittest.TestCase):
    """register() mounts the API without disturbing the app it mounts onto."""

    def setUp(self) -> None:
        self.parent = FastAPI()

        @self.parent.get("/api/host-owned")
        def _legacy():
            return {"launching": False, "table_name": None}

        @self.parent.get("/existing")
        def _existing():
            return {"ok": True}

        httpapi.register(self.parent)
        self.client = TestClient(self.parent, raise_server_exceptions=False)

    def test_discovery_answers_with_and_without_the_trailing_slash(self) -> None:
        for path in ("/api/v1", "/api/v1/"):
            with self.subTest(path=path):
                response = self.client.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["api_version"], "v1")

    def test_setup_pending_is_said_at_both_addresses(self) -> None:
        for path in ("/api/v1", "/api/v1/"):
            for pending in (True, False):
                with self.subTest(path=path, pending=pending), \
                        mock.patch("common.devices.install_setup.pending", return_value=pending):
                    body = self.client.get(path, follow_redirects=False).json()
                    self.assertEqual(body.get("setup_pending"), True if pending else None)

    def test_mounted_routes_are_reachable(self) -> None:
        self.assertEqual(self.client.get("/api/v1/health").json(), {"status": "ok"})

    def test_openapi_servers_url_lets_clients_resolve_real_paths(self) -> None:
        spec = self.client.get("/api/v1/openapi.json").json()

        # Paths in the spec are mount-relative, so the server URL is what makes
        # a generated client hit /api/v1/health rather than /health.
        self.assertEqual(spec.get("servers"), [{"url": "/api/v1"}])

    def test_existing_routes_are_untouched(self) -> None:
        self.assertEqual(self.client.get("/existing").json(), {"ok": True})
        self.assertEqual(
            self.client.get("/api/host-owned").json(),
            {"launching": False, "table_name": None},
        )

    def test_cors_is_scoped_to_the_api(self) -> None:
        preflight = {"Origin": "http://localhost:8000", "Access-Control-Request-Method": "GET"}

        allowed = self.client.options("/api/v1/health", headers=preflight)
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.headers["access-control-allow-origin"], "*")

        # The host app keeps whatever CORS behavior it had, which is none.
        untouched = self.client.options("/api/host-owned", headers=preflight)
        self.assertNotIn("access-control-allow-origin", untouched.headers)

    def test_the_envelope_does_not_apply_outside_the_api(self) -> None:
        outside = self.client.get("/not-a-route")

        self.assertEqual(outside.status_code, 404)
        self.assertNotIn("error", outside.json())


class DeclaredIdentityEndpointTests(unittest.TestCase):
    """The import endpoint refuses a claim it cannot trust, before anything is written.

    Validated at the boundary rather than recorded and regretted: a client asserting a
    confidence it has not earned is the failure the matcher measurements ruled out.
    """

    def _import(self, declared: dict):
        return _client().post("/uploads/nope/import", json={"declared": declared})

    def test_a_basis_outside_the_closed_set_is_rejected(self) -> None:
        response = self._import({"t.vpx": {"game_id": "g", "confirmed_by": "auto"}})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("confirmed_by", response.text)

    def test_naming_a_record_without_saying_how_is_rejected(self) -> None:
        response = self._import({"t.vpx": {"vps_file_id": "f", "host_item_id": "h"}})
        self.assertEqual(response.status_code, 400, response.text)

    def test_a_vps_file_id_without_its_item_is_rejected(self) -> None:
        """One VPS record can front many artifacts, so the record id alone is ambiguous."""
        response = self._import(
            {"t.vpx": {"vps_file_id": "f", "confirmed_by": "declared"}})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("host_item_id", response.text)

    def test_the_error_names_the_file_it_is_about(self) -> None:
        """A bundle declares several files; "invalid" alone would not say which."""
        response = self._import({"backglass.png": {"confirmed_by": "auto", "game_id": "g"}})
        self.assertIn("backglass.png", response.text)

    def test_every_refused_file_is_in_the_details(self) -> None:
        response = self._import({
            "a.vpx": {"confirmed_by": "auto", "game_id": "g"},
            "b.png": {"vps_file_id": "f", "confirmed_by": "declared"}})
        error = response.json()["error"]
        self.assertIn("a.vpx", error["message"])
        self.assertEqual([r["file"] for r in error["details"]["refused"]], ["a.vpx", "b.png"])
        self.assertIn("host_item_id", error["details"]["refused"][1]["why"])

    def test_a_well_formed_claim_gets_past_validation(self) -> None:
        """It fails on the unknown upload session, not on the declaration."""
        response = self._import(
            {"t.vpx": {"vps_file_id": "f", "host_item_id": "h", "confirmed_by": "declared"}})
        self.assertNotIn("confirmed_by", response.text)


if __name__ == "__main__":
    unittest.main()

"""`ctx.os`: what an extension may ask an operating system's helper, and when.

The helper is a fake that listens on a socket the way the real one does, so the client's
wire is exercised as well as the rules in front of it.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import socket
import tempfile
import threading
import time
import unittest
import unittest.mock
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, cast

from starlette.testclient import TestClient

import httpapi
from common import events as core_events
from common import extensions
from common.extensions import contract, host, reach, store
from common.extensions.context import ExtensionContext
from common.host import os_helper
from common.runtime import jobs as core_jobs
from common.security import sign_in
from httpapi import auth
from tests.support.accepted import owner_accepts
from tests.support.identity import IDENTITY


class FakeHelper:
    """Answers each connection with what `reply` returns for the request it read."""

    def __init__(self, directory: Path, reply: Callable[[dict], Any] | None = None) -> None:
        self.path = str(directory / "h.sock")
        self.requests: list[dict] = []
        self.reply = reply or (lambda request: {"ok": True, "result": {"done": request["method"]}})
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(self.path)
        self._server.listen(4)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while True:
            try:
                one, _ = self._server.accept()
            except OSError:
                return
            with one:
                held = b""
                while b"\n" not in held:
                    more = one.recv(4096)
                    if not more:
                        break
                    held += more
                try:
                    request = json.loads(held)
                except ValueError:
                    continue
                self.requests.append(request)
                answer: Any = self.reply(request)
                if inspect.isgenerator(answer):
                    try:
                        for chunk in cast(Iterable[bytes], answer):
                            one.sendall(chunk)
                    except OSError:
                        pass
                elif isinstance(answer, bytes):
                    one.sendall(answer)
                elif answer is not None:
                    one.sendall(json.dumps(answer).encode("utf-8") + b"\n")

    def close(self) -> None:
        self._server.close()


class HelperCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="vp")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def helper(self, reply: Callable[[dict], Any] | None = None) -> FakeHelper:
        if not hasattr(socket, "AF_UNIX"):
            self.skipTest("Python on Windows has no Unix socket to stand a helper on")
        try:
            fake = FakeHelper(self.root, reply)
        except PermissionError:
            self.skipTest("this sandbox does not let a test listen on a socket")
        self.addCleanup(fake.close)
        patch = unittest.mock.patch.object(os_helper, "SOCKET", fake.path)
        patch.start()
        self.addCleanup(patch.stop)
        return fake


class ClientTests(HelperCase):
    def test_no_socket_is_no_helper(self) -> None:
        with unittest.mock.patch.object(os_helper, "SOCKET", str(self.root / "none")):
            self.assertFalse(os_helper.available())
            with self.assertRaises(os_helper.HelperAbsentError):
                os_helper.call(os_helper.METHODS["updates.check"], {}, by="x", origin="")

    def test_a_socket_is_a_helper(self) -> None:
        self.helper()

        self.assertTrue(os_helper.available())

    def test_a_plain_file_is_not_a_helper(self) -> None:
        plain = self.root / "plain"
        plain.write_text("", encoding="utf-8")

        with unittest.mock.patch.object(os_helper, "SOCKET", str(plain)):
            self.assertFalse(os_helper.available())

    def test_the_request_is_one_line_naming_the_method_and_who_asked(self) -> None:
        fake = self.helper()

        found = os_helper.call(os_helper.METHODS["updates.check"], {}, by="os_update",
                               origin="this device")

        self.assertEqual(found, {"done": "updates.check"})
        self.assertEqual(fake.requests, [{"method": "updates.check", "args": {},
                                          "by": "os_update", "origin": "this device"}])

    def test_an_answer_of_no_is_a_refusal_with_the_helpers_reason(self) -> None:
        self.helper(lambda _r: {"ok": False, "error": "busy", "message": "Already running"})

        with self.assertRaises(os_helper.HelperRefusedError) as raised:
            os_helper.call(os_helper.METHODS["updates.apply"], {}, by="x", origin="")

        self.assertIn("Already running", str(raised.exception))

    def test_a_reason_is_cut_to_a_line_a_person_can_read(self) -> None:
        self.helper(lambda _r: {"ok": False, "message": "bad\x1b[31m\nthing" + "x" * 500})

        with self.assertRaises(os_helper.HelperRefusedError) as raised:
            os_helper.call(os_helper.METHODS["updates.apply"], {}, by="x", origin="")

        text = str(raised.exception)
        self.assertNotIn("\x1b", text)
        self.assertNotIn("\n", text)
        self.assertLess(len(text), 300)

    def test_an_answer_that_is_not_json_is_unreadable(self) -> None:
        self.helper(lambda _r: b"not json\n")

        with self.assertRaises(os_helper.HelperBrokenError):
            os_helper.call(os_helper.METHODS["updates.check"], {}, by="x", origin="")

    def test_an_answer_that_does_not_say_ok_is_unreadable(self) -> None:
        self.helper(lambda _r: {"result": 1})

        with self.assertRaises(os_helper.HelperBrokenError):
            os_helper.call(os_helper.METHODS["updates.check"], {}, by="x", origin="")

    def test_an_answer_with_no_end_is_cut_off(self) -> None:
        self.helper(lambda _r: b"x" * (os_helper.MAX_LINE + 4096))

        with self.assertRaises(os_helper.HelperBrokenError):
            os_helper.call(os_helper.METHODS["updates.check"], {}, by="x", origin="")

    def test_a_helper_that_never_answers_times_out(self) -> None:
        silent = os_helper.Method("updates.check", "os:updates", changes=False, timeout=0.2)
        self.helper(lambda _r: time.sleep(0.6))

        with self.assertRaises(os_helper.HelperBrokenError) as raised:
            os_helper.call(silent, {}, by="x", origin="")

        self.assertIn("in time", str(raised.exception))

    def test_a_helper_that_drips_bytes_still_times_out_on_the_whole_answer(self) -> None:
        patient = os_helper.Method("updates.check", "os:updates", changes=False, timeout=0.5)

        def drip(_request):
            for _ in range(40):
                time.sleep(0.1)
                yield b"x"

        self.helper(drip)
        started = time.monotonic()

        with self.assertRaises(os_helper.HelperBrokenError):
            os_helper.call(patient, {}, by="x", origin="")

        self.assertLess(time.monotonic() - started, 2)

    def test_an_answer_nested_past_what_json_can_read_is_unreadable(self) -> None:
        self.helper(lambda _r: b"[" * 60000 + b"\n")

        with self.assertRaises(os_helper.HelperBrokenError):
            os_helper.call(os_helper.METHODS["updates.check"], {}, by="x", origin="")

    def test_an_answer_with_a_number_json_cannot_carry_is_unreadable(self) -> None:
        self.helper(lambda _r: b'{"ok":true,"result":NaN}\n')

        with self.assertRaises(os_helper.HelperBrokenError):
            os_helper.call(os_helper.METHODS["updates.check"], {}, by="x", origin="")

    def test_an_argument_json_cannot_carry_is_the_callers_mistake(self) -> None:
        here = os_helper.Method("updates.check", "os:updates", changes=False, timeout=1,
                                args=("where",))

        with self.assertRaises(ValueError):
            os_helper.call(here, {"where": Path("/tmp")}, by="x", origin="")

    def test_an_argument_the_method_does_not_take_never_leaves(self) -> None:
        fake = self.helper()

        with self.assertRaises(ValueError):
            os_helper.call(os_helper.METHODS["updates.apply"], {"package": "vim"},
                           by="x", origin="")

        self.assertEqual(fake.requests, [])

    def test_no_method_takes_a_command_a_path_a_package_or_an_address(self) -> None:
        taken = {name for one in os_helper.METHODS.values() for name in one.args}

        self.assertFalse(taken & {"command", "cmd", "path", "file", "package", "address",
                                  "host", "url", "script"})

    def test_every_method_has_a_sentence_and_an_area_an_extension_can_declare(self) -> None:
        for one in os_helper.METHODS.values():
            self.assertTrue(one.sentence)
            self.assertNotEqual(one.sentence, f"os.method.{one.name}.says")
            self.assertIn(one.area, contract.CAPABILITIES)


class ExtensionCase(HelperCase):
    """A running extension that declares what a test needs."""

    def setUp(self) -> None:
        owner_accepts(self)
        super().setUp()
        self.store = store.ExtensionStore(self.root)
        self.registry = host.Registry(self.store)
        extensions.set_registry(self.registry)
        self.addCleanup(extensions.set_registry, host.Registry())
        self.addCleanup(self.registry.clear)

    def make(self, name: str, capabilities: list[str], body: str = "",
             tiers: tuple[str, ...] = ()) -> ExtensionContext:
        directory = self.root / "made" / name
        directory.mkdir(parents=True)
        (directory / "extension.json").write_text(json.dumps({
            "name": name, "version": "1.0.0", "requires_platform": contract.PLATFORM_ABI,
            **IDENTITY,
            "capabilities": capabilities, "tiers": list(tiers)}), encoding="utf-8")
        (directory / "__init__.py").write_text(body or "def register(ctx):\n    pass\n",
                                               encoding="utf-8")
        record = self.registry.load(directory)
        self.assertTrue(record.running, record.reason)
        return cast(ExtensionContext, record.context)


class CallTests(ExtensionCase):
    def test_a_method_in_an_area_the_manifest_never_declared_is_refused(self) -> None:
        self.helper()
        ctx = self.make("undeclared", ["ui:mount"])

        with self.assertRaises(contract.ContractError) as raised:
            ctx.os.call("updates.check")

        self.assertIn("os:updates", str(raised.exception))

    def test_a_method_nobody_offers_is_refused(self) -> None:
        self.helper()
        ctx = self.make("wishful", ["os:updates"])

        with self.assertRaises(contract.ContractError):
            ctx.os.call("shell.run")

    def test_a_read_needs_no_request(self) -> None:
        fake = self.helper()
        ctx = self.make("reader", ["os:updates"])

        self.assertEqual(ctx.os.call("updates.check"), {"done": "updates.check"})
        self.assertEqual(fake.requests[0]["by"], "reader")

    def test_a_change_with_no_request_behind_it_is_refused(self) -> None:
        """An event handler, a timer or a thread the extension started has no mark."""
        fake = self.helper()
        ctx = self.make("sleeper", ["os:updates"])

        with self.assertRaises(PermissionError):
            ctx.os.call("updates.apply")

        self.assertEqual(fake.requests, [])

    def test_a_change_from_a_thread_the_extension_started_is_refused(self) -> None:
        fake = self.helper()
        ctx = self.make("threaded", ["os:updates"])
        outcome: list[BaseException | None] = []

        def work() -> None:
            try:
                ctx.os.call("updates.apply")
                outcome.append(None)
            except PermissionError as exc:
                outcome.append(exc)

        with reach.marking(reach.PROTECTED, "this device"):
            thread = threading.Thread(target=work)
            thread.start()
            thread.join()

        self.assertIsInstance(outcome[0], PermissionError)
        self.assertEqual(fake.requests, [])

    def test_a_change_from_an_event_handler_inside_a_request_is_refused(self) -> None:
        fake = self.helper()
        ctx = self.make("listener", ["os:updates"])
        outcome: list[BaseException | None] = []

        def handler(**_payload) -> None:
            try:
                ctx.os.call("updates.apply")
                outcome.append(None)
            except PermissionError as exc:
                outcome.append(exc)

        ctx.events.subscribe("os.test.event", handler)
        self.addCleanup(lambda: [core_events.unsubscribe(name, one)  # type: ignore[func-returns-value]
                                 for name, one in ctx.events.registered])
        with reach.marking(reach.PROTECTED, "this device"):
            core_events.emit("os.test.event")

        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], PermissionError)
        self.assertEqual(fake.requests, [])

    def test_a_change_from_a_task_that_outlives_the_request_is_refused(self) -> None:
        fake = self.helper()
        ctx = self.make("lingerer", ["os:updates"])

        async def later() -> None:
            await asyncio.sleep(0)
            ctx.os.call("updates.apply")

        async def run() -> None:
            with reach.marking(reach.PROTECTED, "this device"):
                task = asyncio.get_running_loop().create_task(later())
            with self.assertRaises(PermissionError):
                await task

        asyncio.run(run())
        self.assertEqual(fake.requests, [])

    def test_a_change_from_a_request_below_protected_is_refused(self) -> None:
        fake = self.helper()
        ctx = self.make("shy", ["os:updates"])

        for tier in (reach.PLAY, reach.LIBRARY):
            with reach.marking(tier, "192.0.2.7"), self.assertRaises(PermissionError):
                ctx.os.call("updates.apply")

        self.assertEqual(fake.requests, [])

    def test_a_change_from_a_protected_request_goes_through_naming_who_asked(self) -> None:
        fake = self.helper()
        ctx = self.make("updater", ["os:updates"])

        with reach.marking(reach.PROTECTED, "this device"):
            found = ctx.os.call("updates.apply")

        self.assertEqual(found, {"done": "updates.apply"})
        self.assertEqual(fake.requests, [{"method": "updates.apply", "args": {},
                                          "by": "updater", "origin": "this device"}])

    def test_the_log_names_who_asked_and_from_where(self) -> None:
        self.helper()
        ctx = self.make("noted", ["os:updates"])

        with self.assertLogs("vpinfe.ext.noted", logging.INFO) as lines, \
                reach.marking(reach.PROTECTED, "this device"):
            ctx.os.call("updates.apply")

        self.assertIn("asked the system for updates.apply from this device", lines.output[0])

    def test_no_helper_is_the_services_failure_and_not_the_requests(self) -> None:
        ctx = self.make("lonely", ["os:updates"])

        with unittest.mock.patch.object(os_helper, "SOCKET", str(self.root / "none")), \
                self.assertRaises(contract.Unavailable):
            ctx.os.call("updates.check")

    def test_an_answer_that_cannot_be_read_is_the_services_failure_too(self) -> None:
        self.helper(lambda _r: b"junk\n")
        ctx = self.make("puzzled", ["os:updates"])

        with self.assertRaises(contract.Unavailable):
            ctx.os.call("updates.check")

    def test_available_says_whether_a_helper_stands(self) -> None:
        ctx = self.make("looker", ["os:updates"])
        with unittest.mock.patch.object(os_helper, "SOCKET", str(self.root / "none")):
            self.assertFalse(ctx.os.available)

        self.helper()

        self.assertTrue(ctx.os.available)

    def test_methods_lists_what_the_manifest_declared_with_core_sentences(self) -> None:
        declared = self.make("declared", ["os:updates"])
        none = self.make("nothing", ["ui:mount"])

        self.assertEqual({one["name"]: one["changes"] for one in declared.os.methods},
                         {"updates.check": False, "updates.apply": True})
        self.assertTrue(all(one["says"] for one in declared.os.methods))
        self.assertEqual(none.os.methods, ())


class JobTests(ExtensionCase):
    def wait(self, job: core_jobs.Job) -> None:
        deadline = time.time() + 5
        while job.state == core_jobs.RUNNING and time.time() < deadline:
            time.sleep(0.01)
        self.assertNotEqual(job.state, core_jobs.RUNNING)

    def test_a_job_started_from_a_protected_request_may_change(self) -> None:
        fake = self.helper()
        ctx = self.make("jobbed", ["os:updates"])

        with reach.marking(reach.PROTECTED, "this device"):
            job = ctx.jobs.submit("apply", lambda _job: ctx.os.call("updates.apply"))
        self.wait(job)

        self.assertEqual((job.state, job.result), (core_jobs.DONE, {"done": "updates.apply"}))
        self.assertEqual(len(fake.requests), 1)

    def test_a_job_started_from_nowhere_may_not(self) -> None:
        fake = self.helper()
        ctx = self.make("jobless", ["os:updates"])

        with self.assertLogs("vpinfe.common.runtime.jobs", logging.ERROR):
            job = ctx.jobs.submit("apply", lambda _job: ctx.os.call("updates.apply"))
            self.wait(job)

        self.assertEqual(job.state, core_jobs.FAILED)
        self.assertEqual(fake.requests, [])


ROUTES = '''
from fastapi import APIRouter


def register(ctx):
    router = APIRouter()

    @router.post("/apply")
    def apply():
        return ctx.os.call("updates.apply")

    @router.post("/start")
    def start():
        ctx.jobs.submit("apply", lambda job: ctx.os.call("updates.apply"))
        return {"started": True}

    ctx.add_router(router, tier="protected")
'''


class RequestTests(ExtensionCase):
    """The mark is set by core for each request and reaches the thread a route runs on."""

    def client(self, peer: str) -> TestClient:
        self.make("os_update", ["os:updates"], ROUTES, tiers=("protected",))
        found = TestClient(httpapi.create_api_app(), client=(peer, 50000),
                           base_url="http://localhost", raise_server_exceptions=False)
        if peer != "127.0.0.1":
            held = sign_in.SignIn(self.root / "sessions.json")
            previous = sign_in.instance()
            sign_in.use(held)
            self.addCleanup(sign_in.use, previous)
            token, _session = held.open(kind=sign_in.REMOTE, address=peer)
            found.cookies.set(auth.COOKIE, token)
        return found

    def test_a_change_from_this_device_is_made(self) -> None:
        fake = self.helper()

        found = self.client("127.0.0.1").post("/ext/os_update/apply")

        self.assertEqual(found.status_code, 200, found.text)
        self.assertEqual(found.json(), {"done": "updates.apply"})
        self.assertEqual(fake.requests[0]["origin"], "this device")

    def test_a_change_from_another_device_is_refused_and_nothing_is_sent(self) -> None:
        fake = self.helper()

        found = self.client("192.0.2.7").post("/ext/os_update/apply")

        self.assertEqual(found.status_code, 403, found.text)
        self.assertEqual(fake.requests, [])

    def test_an_extension_that_failed_a_change_is_not_disabled_for_it(self) -> None:
        self.helper()

        self.client("192.0.2.7").post("/ext/os_update/apply")

        record = self.registry.get("os_update")
        assert record is not None
        self.assertTrue(record.running)

    def test_a_job_started_by_a_route_carries_the_requests_mark(self) -> None:
        fake = self.helper()

        found = self.client("127.0.0.1").post("/ext/os_update/start")
        deadline = time.time() + 5
        while not fake.requests and time.time() < deadline:
            time.sleep(0.01)

        self.assertEqual(found.status_code, 200, found.text)
        self.assertEqual([one["method"] for one in fake.requests], ["updates.apply"])


RECIPE = Path(__file__).resolve().parent.parent.parent / "docs" / "extensions" / "examples" \
    / "recipes" / "os_update"


class RecipeTests(ExtensionCase):
    """The cookbook's recipe, loaded as it is shipped, against the fake helper."""

    def load(self):
        with unittest.mock.patch.object(host, "this_platform", return_value="linux"), \
                unittest.mock.patch.object(host, "this_distribution",
                                           return_value=("vpinos", "VPinOS")):
            record = self.registry.load(RECIPE)
        self.assertTrue(record.running, record.reason)
        self.ui = record.context.ui
        self.system = record.context.system
        return record.context.ui.action_objects["updates"]

    def reboot_file(self, present: bool):
        real = os.path.exists
        patch = unittest.mock.patch(
            "os.path.exists",
            lambda path: present if path == "/run/reboot-required" else real(path))
        patch.start()
        self.addCleanup(patch.stop)

    def install_answering(self, **said):
        fake = self.helper(lambda r: {"ok": True, "result": {
            "installed": 1, "reboot_required": False, "vpinfe_upgraded": False, **said}})
        dialog = self.load()
        with reach.marking(reach.PROTECTED, "this device"):
            started = dialog.run({})
        deadline = time.time() + 5
        while time.time() < deadline:
            job = core_jobs.get(started["job_id"])
            if job and job.state != core_jobs.RUNNING:
                break
            time.sleep(0.01)
        return fake

    def test_the_dialog_counts_what_is_waiting_and_uses_cores_sentence(self) -> None:
        self.helper(lambda r: {"ok": True, "result": {"count": 7}} if r["method"]
                    == "updates.check" else {"ok": True, "result": {}})
        asked = self.load().ask(None)

        self.assertIn(["Updates Waiting", "7"], asked["summary"])
        self.assertIn(["This Will", "Install system updates on this device"],
                      asked["summary"])

    def test_the_dialog_holds_install_where_no_helper_stands(self) -> None:
        dialog = self.load()
        with unittest.mock.patch.object(os_helper, "SOCKET", str(self.root / "none")):
            asked = dialog.ask(None)

        self.assertIs(asked["ready"], False)

    def test_install_starts_a_job_that_applies_the_updates(self) -> None:
        fake = self.helper()
        dialog = self.load()

        with reach.marking(reach.PROTECTED, "this device"):
            started = dialog.run({})
        deadline = time.time() + 5
        while not fake.requests and time.time() < deadline:
            time.sleep(0.01)

        self.assertIn("job_id", started)
        self.assertEqual([one["method"] for one in fake.requests], ["updates.apply"])

    def test_an_install_that_upgraded_vpinfe_is_offered_a_restart(self) -> None:
        self.reboot_file(False)
        self.install_answering(vpinfe_upgraded=True)

        self.assertEqual(self.ui.needs_function(),
                         [{"kind": "restart", "count": 1, "token": "vpinfe"}])

    def test_an_install_that_changed_nothing_of_vpinfes_offers_no_restart(self) -> None:
        self.reboot_file(False)
        self.install_answering()

        self.assertEqual(self.ui.needs_function(), [])

    def test_a_flag_that_is_not_true_offers_no_restart(self) -> None:
        self.reboot_file(False)
        self.install_answering(vpinfe_upgraded="true")

        self.assertEqual(self.ui.needs_function(), [])

    def test_a_device_waiting_on_a_reboot_offers_one(self) -> None:
        self.reboot_file(True)
        self.load()

        self.assertEqual(self.ui.needs_function(),
                         [{"kind": "restart", "count": 1, "token": "reboot"}])

    def test_the_restart_dialog_says_reboot_where_the_system_asks_for_one(self) -> None:
        self.reboot_file(True)
        self.load()
        said = self.ui.action_objects["restart"].ask(None)["summary"][0][1]

        self.reboot_file(False)
        self.assertNotEqual(self.ui.action_objects["restart"].ask(None)["summary"][0][1], said)

    def test_restarting_reboots_when_the_system_asks_for_it_and_clears_the_offer(self) -> None:
        self.reboot_file(True)
        self.load()
        with unittest.mock.patch.object(self.system, "reboot") as reboot, \
                unittest.mock.patch.object(self.system, "restart_vpinfe") as restart:
            self.ui.action_objects["restart"].run({})

        reboot.assert_called_once()
        restart.assert_not_called()

    def test_restarting_restarts_only_vpinfe_when_no_reboot_is_needed(self) -> None:
        self.reboot_file(False)
        self.load()
        self.ui.action_objects["updates"].upgraded = True
        with unittest.mock.patch.object(self.system, "reboot") as reboot, \
                unittest.mock.patch.object(self.system, "restart_vpinfe") as restart:
            self.ui.action_objects["restart"].run({})

        restart.assert_called_once()
        reboot.assert_not_called()
        self.assertEqual(self.ui.needs_function(), [])

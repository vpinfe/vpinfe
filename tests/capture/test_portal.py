"""KDE Plasma and GNOME: the screen-sharing portal's calls and answers, the restore token,
each stream matched to its screen, and GStreamer reading it. The bus is stood in for, so
nothing here reaches a desktop; the messages are built with jeepney as they would be."""

from __future__ import annotations

import configparser
import os
import tempfile
import threading
import unittest
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

from jeepney import DBusAddress, new_method_return, new_signal
from jeepney.fds import FileDescriptor
from jeepney.low_level import HeaderFields, Parser
from starlette.testclient import TestClient

import httpapi
from common import service_errors
from common.capture import adapters, geometry, placing, preflight, run, session, trial
from common.capture.adapters import portal
from common.host import launch_state, tools
from common.i18n import t
from common.runtime import jobs
from httpapi import models
from tests.capture.test_preflight import ELEMENTS, MONITORS, found, report
from tests.capture.test_session import QUICK, Cabinet
from tests.capture.test_trial import Playfield

SENDER = ":1.42"
SESSION = "/org/freedesktop/portal/desktop/session/1_42/vpinfe_s"
# The playfield's screen and the backglass's shared; the DMD's, HDMI-A-1, not.
STREAMS = [(64, {"position": (0, 0), "size": (1080, 1920), "source_type": 1,
                 "id": "0"}),
           (65, {"position": (1080, 0), "size": (1920, 1080), "source_type": 1,
                 "id": "1"})]


class FakeBus:
    """The session bus as the portal answers it, in plain values, each call noted."""

    sender = SENDER

    def __init__(self, *, started: int = portal.ANSWERED, restore: str = "next",
                 created: int = portal.ANSWERED, times_out: str = "",
                 streams: list[Any] | None = None) -> None:
        self.started = started
        self.restore = restore
        self.created = created
        self.times_out = times_out
        self.streams = STREAMS if streams is None else streams
        self.asked: list[tuple[str, list[Any], str, float]] = []
        self.remotes: list[int] = []
        self.closed_sessions: list[str] = []
        self.closed = False

    def ask(self, method: str, args: Any, token: str,
            timeout: float) -> tuple[int, dict[str, Any]]:
        self.asked.append((method, list(args), token, timeout))
        if method == self.times_out:
            raise TimeoutError
        if method == "CreateSession":
            return self.created, {"session_handle": SESSION}
        if method == "SelectSources":
            return portal.ANSWERED, {}
        return self.started, {"streams": self.streams,
                              **({"restore_token": self.restore} if self.restore else {})}

    def call(self, method: str, args: Any) -> int:
        self.asked.append((method, list(args), "", 0.0))
        remote = os.open(os.devnull, os.O_RDONLY)
        self.remotes.append(remote)
        return remote

    def close_session(self, handle: str) -> None:
        self.closed_sessions.append(handle)

    def close(self) -> None:
        self.closed = True

    def options(self, method: str) -> dict[str, Any]:
        return next(args[-1] for name, args, *_ in self.asked if name == method)


def _open(fd: int) -> bool:
    try:
        os.fstat(fd)
    except OSError:
        return False
    return True


class _Kept(unittest.TestCase):
    def setUp(self) -> None:
        held = tempfile.TemporaryDirectory()
        self.addCleanup(held.cleanup)
        self.kept_at = Path(held.name) / "capture" / "portal.json"

    def adapter(self, bus: FakeBus | None = None, token: str = "",
                desktop: str = "KDE Plasma") -> portal.PortalAdapter:
        if token:
            portal.keep(token, desktop, self.kept_at)
        self.bus = bus or FakeBus()
        return portal.PortalAdapter(desktop, lambda: MONITORS, lambda: self.bus,
                                    kept_at=self.kept_at)


class SessionTests(unittest.TestCase):
    """Which desktop, and where the portal answers."""

    def test_kde_and_gnome_on_wayland_are_the_portals(self) -> None:
        for said, desktop in (("KDE", "KDE Plasma"), ("ubuntu:GNOME", "GNOME"),
                              ("GNOME-Classic:GNOME", "GNOME")):
            with self.subTest(said):
                found_here = adapters.resolve({"WAYLAND_DISPLAY": "wayland-0",
                                               "XDG_CURRENT_DESKTOP": said}, tools.LINUX)

                self.assertIsInstance(found_here, portal.PortalAdapter)
                self.assertEqual(found_here.desktop, desktop)  # type: ignore[union-attr]

    def test_a_request_is_answered_where_the_sender_and_its_token_say(self) -> None:
        self.assertEqual(portal.request_path(":1.42", "vpinfe_ab12"),
                         "/org/freedesktop/portal/desktop/request/1_42/vpinfe_ab12")

    def test_the_session_bus_is_its_address_or_the_runtime_directorys(self) -> None:
        self.assertEqual(portal.bus_address({"DBUS_SESSION_BUS_ADDRESS": "unix:path=/b",
                                             "XDG_RUNTIME_DIR": "/run/user/1000"}),
                         "unix:path=/b")
        self.assertEqual(portal.bus_address({"XDG_RUNTIME_DIR": "/run/user/1000"}),
                         "unix:path=/run/user/1000/bus")
        with self.assertRaises(OSError):
            portal.connect({})


class MessageTests(unittest.TestCase):
    """Each call as jeepney puts it on the wire, read back."""

    def _sent(self, method: str, args: list[Any]) -> Any:
        parser = Parser()
        parser.add_data(portal.method_call(method, args).serialise(serial=1))
        return parser.get_next_message()

    def test_each_method_is_the_portals_screencast_with_its_documented_signature(
            self) -> None:
        for method, args in (("CreateSession", [{"handle_token": "a"}]),
                             ("SelectSources", [SESSION, {"handle_token": "b"}]),
                             ("Start", [SESSION, "", {"handle_token": "c"}]),
                             ("OpenPipeWireRemote", [SESSION, {}])):
            with self.subTest(method):
                fields = self._sent(method, args).header.fields

                self.assertEqual((fields[HeaderFields.destination], fields[HeaderFields.path],
                                  fields[HeaderFields.interface], fields[HeaderFields.member],
                                  fields[HeaderFields.signature]),
                                 (portal.PORTAL, portal.DESKTOP, portal.SCREENCAST, method,
                                  portal.SIGNATURES[method]))

    def test_each_option_is_typed_as_the_portal_documents_it(self) -> None:
        sent = self._sent("SelectSources", [SESSION, {
            "handle_token": "t", "types": portal.MONITOR, "multiple": True,
            "persist_mode": portal.UNTIL_REVOKED, "restore_token": "r"}])

        self.assertEqual(sent.body, (SESSION, {
            "handle_token": ("s", "t"), "types": ("u", 1), "multiple": ("b", True),
            "persist_mode": ("u", 2), "restore_token": ("s", "r")}))

    def test_starts_answer_is_read_to_plain_values(self) -> None:
        signal = new_signal(DBusAddress(portal.request_path(SENDER, "x"),
                                        interface=portal.REQUEST), "Response", "ua{sv}",
                            (0, {"streams": ("a(ua{sv})", [(64, {
                                "position": ("(ii)", (0, 0)), "size": ("(ii)", (1080, 1920)),
                                "source_type": ("u", 1)})]),
                                "restore_token": ("s", "abc")}))
        parser = Parser()
        parser.add_data(signal.serialise(serial=2))

        response, results = parser.get_next_message().body

        self.assertEqual((response, portal.plain(results)), (0, {
            "streams": [(64, {"position": (0, 0), "size": (1080, 1920), "source_type": 1})],
            "restore_token": "abc"}))


class _Connection:
    """A jeepney connection that answers as the portal does, without a bus."""

    unique_name = SENDER

    def __init__(self, *, times_out: bool = False, handle: str = "") -> None:
        self.times_out = times_out
        self.handle = handle
        self.sent: list[tuple[str, str]] = []
        self.remote = -1
        self.closed = False

    @contextmanager
    def filter(self, rule: Any) -> Any:
        self.rule = rule
        yield deque()

    def send_and_get_reply(self, message: Any, timeout: float | None = None) -> Any:
        fields = message.header.fields
        member, path = fields[HeaderFields.member], fields[HeaderFields.path]
        self.sent.append((member, path))
        if member == "OpenPipeWireRemote":
            self.remote = os.open(os.devnull, os.O_RDONLY)
            return new_method_return(message, "h", (FileDescriptor(self.remote),))
        if member in ("AddMatch", "RemoveMatch", "Close"):
            return new_method_return(message)
        self.request = portal.request_path(SENDER, message.body[-1]["handle_token"][1])
        return new_method_return(message, "o", (self.handle or self.request,))

    def recv_until_filtered(self, queue: Any, timeout: float | None = None) -> Any:
        """The portal's answer, heard only where the subscription matches it."""
        answer = new_signal(DBusAddress(self.request, interface=portal.REQUEST),
                            "Response", "ua{sv}", (0, {"session_handle": ("s", SESSION)}))
        if self.times_out or not self.rule.matches(answer):
            raise TimeoutError
        return answer

    def close(self) -> None:
        self.closed = True


class JeepneyBusTests(unittest.TestCase):
    """The one piece that speaks D-Bus, against a connection that answers as the portal."""

    def test_the_answer_is_heard_on_the_request_subscribed_before_the_call(self) -> None:
        connection = _Connection()
        bus = portal._Jeepney(connection)

        answer = bus.ask("CreateSession", [{"handle_token": "vpinfe_1"}], "vpinfe_1", 5.0)

        path = portal.request_path(SENDER, "vpinfe_1")
        self.assertEqual(answer, (0, {"session_handle": SESSION}))
        self.assertEqual([member for member, _ in connection.sent],
                         ["AddMatch", "CreateSession", "RemoveMatch"])
        self.assertEqual(connection.rule.header_fields["path"], path)

    def test_no_answer_in_time_closes_the_request_so_no_picker_is_left_up(self) -> None:
        connection = _Connection(times_out=True)
        bus = portal._Jeepney(connection)

        with self.assertRaises(TimeoutError):
            bus.ask("Start", [SESSION, "", {"handle_token": "vpinfe_2"}], "vpinfe_2", 0.1)

        self.assertIn(("Close", portal.request_path(SENDER, "vpinfe_2")), connection.sent)
        self.assertEqual(connection.sent[-1][0], "RemoveMatch")

    def test_a_request_named_elsewhere_is_refused(self) -> None:
        bus = portal._Jeepney(_Connection(handle="/org/freedesktop/portal/desktop/request/1"))

        with self.assertRaises(OSError):
            bus.ask("CreateSession", [{"handle_token": "vpinfe_3"}], "vpinfe_3", 5.0)

    def test_a_remote_is_handed_over_as_the_callers_own_descriptor(self) -> None:
        connection = _Connection()

        remote = portal._Jeepney(connection).call("OpenPipeWireRemote", [SESSION, {}])

        self.assertEqual(remote, connection.remote)
        os.close(remote)

    def test_closing_a_session_closes_the_portals_session_object(self) -> None:
        connection = _Connection()
        bus = portal._Jeepney(connection)

        bus.close_session(SESSION)
        bus.close()

        self.assertEqual(connection.sent, [("Close", SESSION)])
        self.assertTrue(connection.closed)


class StreamTests(unittest.TestCase):
    def test_each_stream_is_its_node_and_where_the_desktop_lays_it_out(self) -> None:
        said = portal.streams_in({"streams": [*STREAMS, (66, {"size": (1920, 1080)}),
                                              "junk", ("x", {})]})

        self.assertEqual(said, [portal.Stream(64, (0, 0), (1080, 1920)),
                                portal.Stream(65, (1080, 0), (1920, 1080)),
                                portal.Stream(66, None, (1920, 1080))])

    def test_a_screen_is_matched_to_a_stream_by_where_it_is_and_its_size(self) -> None:
        streams = portal.streams_in({"streams": STREAMS})
        outputs = {one.name: one for one in adapters.ffmpeg.x11_outputs(MONITORS)}

        self.assertEqual(portal.matched(outputs["DP-1"], streams).node, 64)  # type: ignore[union-attr]
        self.assertEqual(portal.matched(outputs["DP-2"], streams).node, 65)  # type: ignore[union-attr]
        self.assertIsNone(portal.matched(outputs["HDMI-A-1"], streams))
        self.assertIsNone(portal.matched(outputs["DP-2"], [portal.Stream(9, (1080, 0),
                                                                          (1280, 720))]))


class OpenSessionTests(unittest.TestCase):
    def test_a_restore_shares_monitors_many_at_once_kept_until_revoked(self) -> None:
        bus = FakeBus()

        opened = portal.open_session(bus, "kept", portal.RESTORE_SECONDS)

        self.assertEqual([one[0] for one in bus.asked],
                         ["CreateSession", "SelectSources", "Start"])
        self.assertEqual({key: value for key, value in bus.options("SelectSources").items()
                          if key != "handle_token"},
                         {"types": 1, "multiple": True, "persist_mode": 2,
                          "restore_token": "kept"})
        self.assertNotIn("cursor_mode", bus.options("SelectSources"))
        self.assertEqual(bus.asked[2][1][:2], [SESSION, ""])
        self.assertEqual(bus.asked[2][3], portal.RESTORE_SECONDS)
        self.assertEqual((opened.handle, [one.node for one in opened.streams],
                          opened.restore), (SESSION, [64, 65], "next"))

    def test_each_call_is_answered_on_a_token_of_its_own(self) -> None:
        bus = FakeBus()
        portal.open_session(bus, "", 1.0)

        tokens = [token for _, _, token, _ in bus.asked]
        self.assertEqual(len(set(tokens)), 3)
        self.assertEqual([args[-1]["handle_token"] for _, args, _, _ in bus.asked], tokens)
        self.assertTrue(all(token.startswith("vpinfe_") for token in tokens))

    def test_asking_a_person_sends_no_token(self) -> None:
        bus = FakeBus()
        portal.open_session(bus, "", portal.CHOOSE_SECONDS)

        self.assertNotIn("restore_token", bus.options("SelectSources"))

    def test_a_restore_not_answered_in_time_has_spent_its_token(self) -> None:
        bus = FakeBus(times_out="Start")

        with self.assertRaises(portal.PortalError) as failed:
            portal.open_session(bus, "kept", portal.RESTORE_SECONDS)

        self.assertEqual((failed.exception.key, failed.exception.spent),
                         (portal.FORGOT, True))
        self.assertEqual(bus.closed_sessions, [SESSION])

    def test_a_person_who_cancels_is_said_as_such(self) -> None:
        for restore, said in (("", portal.CANCELLED), ("kept", portal.FORGOT)):
            with self.subTest(restore=restore):
                with self.assertRaises(portal.PortalError) as failed:
                    portal.open_session(FakeBus(started=portal.CANCELLED_BY_PERSON),
                                        restore, 1.0)
                self.assertEqual(failed.exception.key, said)

    def test_a_person_who_does_not_answer_in_time_is_said_as_such(self) -> None:
        with self.assertRaises(portal.PortalError) as failed:
            portal.open_session(FakeBus(times_out="Start"), "", 1.0)

        self.assertEqual(failed.exception.key, portal.TIMED_OUT)

    def test_a_session_never_created_spends_nothing(self) -> None:
        with self.assertRaises(portal.PortalError) as failed:
            portal.open_session(FakeBus(created=2), "kept", 1.0)

        self.assertEqual((failed.exception.key, failed.exception.spent),
                         (portal.UNREACHABLE, False))


class TokenTests(_Kept):
    def test_the_token_is_kept_per_desktop(self) -> None:
        portal.keep("abc", "KDE Plasma", self.kept_at)

        self.assertEqual(portal.kept("KDE Plasma", self.kept_at), "abc")
        self.assertEqual(portal.kept("GNOME", self.kept_at), "")

    def test_keeping_none_forgets_it(self) -> None:
        portal.keep("abc", "KDE Plasma", self.kept_at)
        portal.keep("", "KDE Plasma", self.kept_at)

        self.assertFalse(self.kept_at.exists())
        self.assertEqual(portal.kept("KDE Plasma", self.kept_at), "")

    def test_a_file_that_cannot_be_read_keeps_nothing(self) -> None:
        self.kept_at.parent.mkdir(parents=True)
        self.kept_at.write_text("{", encoding="utf-8")

        self.assertEqual(portal.kept("KDE Plasma", self.kept_at), "")


class AdapterTests(_Kept):
    def test_until_someone_chooses_every_screen_says_so_and_nothing_is_asked(self) -> None:
        adapter = self.adapter()

        self.assertEqual(adapter.refused(), (adapters.NOT_CHOSEN, {"desktop": "KDE Plasma"}))
        screens = adapter.begin({"playfield": adapter.outputs()[0]})

        self.assertEqual((screens["playfield"].output, screens["playfield"].reason),
                         (None, adapters.NOT_CHOSEN))
        self.assertEqual(self.bus.asked, [])

    def test_a_kept_token_is_nothing_refused(self) -> None:
        self.assertIsNone(self.adapter(token="abc").refused())

    def test_the_screens_are_the_display_models_so_reading_them_asks_nothing(self) -> None:
        adapter = self.adapter(token="abc")

        self.assertEqual([(one.name, one.x, one.width, one.index) for one in adapter.outputs()],
                         [("DP-1", 0, 1080, 0), ("DP-2", 1080, 1920, 1),
                          ("HDMI-A-1", 3000, 1920, 2)])
        self.assertEqual(adapter.windows(), [])
        self.assertEqual(self.bus.asked, [])

    def test_gstreamer_without_a_part_recording_needs_says_which(self) -> None:
        adapter = self.adapter()

        self.assertIsNone(adapter.lacks(found()))
        self.assertEqual(adapter.lacks(found(elements=ELEMENTS - {"x264enc"})),
                         (tools.GSTREAMER, "x264enc"))
        self.assertIsNone(adapter.lacks(found(missing=("gstreamer",))))

    def test_each_screen_is_reached_by_its_stream_and_one_not_shared_says_so(self) -> None:
        adapter = self.adapter(token="kept")
        outputs = {one.name: one for one in adapter.outputs()}

        screens = adapter.begin({"playfield": outputs["DP-1"], "backglass": outputs["DP-2"],
                                 "scoreview": outputs["HDMI-A-1"]})

        self.assertEqual((screens["playfield"].output.name,  # type: ignore[union-attr]
                          screens["playfield"].output.index),  # type: ignore[union-attr]
                         ("DP-1", 64))
        self.assertEqual(screens["backglass"].output.index, 65)  # type: ignore[union-attr]
        self.assertEqual((screens["scoreview"].reason, screens["scoreview"].params),
                         (portal.NOT_SHARED, {"desktop": "KDE Plasma"}))
        self.assertEqual(self.bus.options("SelectSources")["restore_token"], "kept")
        self.assertEqual(portal.kept("KDE Plasma", self.kept_at), "next")
        adapter.end()

    def test_each_program_gets_a_remote_of_its_own_and_end_gives_them_back(self) -> None:
        adapter = self.adapter(token="kept")
        playfield = adapter.begin({"playfield": adapter.outputs()[0]})["playfield"].output

        first = adapter.lend(playfield)  # type: ignore[arg-type]
        second = adapter.lend(playfield)  # type: ignore[arg-type]

        self.assertNotEqual(first.remote, second.remote)
        self.assertEqual(adapters.passing(first), {"pass_fds": (first.remote,)})
        self.assertEqual([one[1] for one in self.bus.asked if one[0] == "OpenPipeWireRemote"],
                         [[SESSION, {}], [SESSION, {}]])
        adapter.end()

        self.assertFalse(any(_open(one) for one in self.bus.remotes))
        self.assertEqual((self.bus.closed_sessions, self.bus.closed), ([SESSION], True))
        self.assertEqual(adapter.lend(playfield).remote, -1)  # type: ignore[arg-type]

    def test_a_restore_that_does_not_answer_forgets_the_choice(self) -> None:
        adapter = self.adapter(FakeBus(times_out="Start"), token="kept")

        with self.assertLogs("vpinfe.common.capture.adapters.portal", "WARNING"):
            screens = adapter.begin({"playfield": adapter.outputs()[0]})

        self.assertEqual(screens["playfield"].reason, portal.FORGOT)
        self.assertEqual(portal.kept("KDE Plasma", self.kept_at), "")
        self.assertTrue(self.bus.closed)
        self.assertEqual(adapter.refused(), (adapters.NOT_CHOSEN, {"desktop": "KDE Plasma"}))

    def test_a_portal_out_of_reach_keeps_the_choice_for_next_time(self) -> None:
        adapter = portal.PortalAdapter("GNOME", lambda: MONITORS, _unreachable,
                                       kept_at=self.kept_at)
        portal.keep("kept", "GNOME", self.kept_at)

        with self.assertLogs("vpinfe.common.capture.adapters.portal", "WARNING"):
            screens = adapter.begin({"playfield": adapter.outputs()[0]})

        self.assertEqual(screens["playfield"].reason, portal.UNREACHABLE)
        self.assertEqual(portal.kept("GNOME", self.kept_at), "kept")

    def test_a_picture_is_one_buffer_through_pngenc(self) -> None:
        output = adapters.Output("DP-1", 0, 0, 1080, 1920, (1080, 1920), 0.0, geometry.NONE,
                                 64, 23)

        argv = self.adapter().still(found(), output, Path("/tmp/a b/playfield.png"))

        self.assertEqual(argv, [str(Path("/usr/bin/gstreamer")), "-q", "pipewiresrc", "fd=23",
                                "path=64", "do-timestamp=true", "keepalive-time=1000",
                                "num-buffers=1", "!", "videoconvert", "!", "pngenc", "!",
                                "filesink", f"location={Path('/tmp/a b/playfield.png')}"])


def _unreachable() -> portal.Bus:
    raise OSError("No session bus")


class ChooseTests(_Kept):
    def test_a_person_is_asked_with_no_token_and_time_to_reach_the_device(self) -> None:
        adapter = self.adapter(token="old")

        said = adapter.choose()

        self.assertNotIn("restore_token", self.bus.options("SelectSources"))
        self.assertEqual(self.bus.asked[2][3], portal.CHOOSE_SECONDS)
        self.assertEqual(said, {"kept": True, "shared": 2, "screens": 3, "reason": None})
        self.assertEqual(portal.kept("KDE Plasma", self.kept_at), "next")
        self.assertEqual((self.bus.closed_sessions, self.bus.closed), ([SESSION], True))

    def test_a_desktop_that_gives_no_token_would_ask_again(self) -> None:
        said = self.adapter(FakeBus(restore="")).choose()

        self.assertEqual((said["kept"], said["reason"]),
                         (False, {"key": portal.NOT_KEPT, "params": {"desktop": "KDE Plasma"}}))
        self.assertEqual(portal.kept("KDE Plasma", self.kept_at), "")

    def test_cancelled_or_unanswered_is_said(self) -> None:
        for bus, key in ((FakeBus(started=portal.CANCELLED_BY_PERSON), portal.CANCELLED),
                         (FakeBus(times_out="Start"), portal.TIMED_OUT)):
            with self.subTest(key), \
                    self.assertLogs("vpinfe.common.capture.adapters.portal", "WARNING"):
                said = self.adapter(bus).choose()

                self.assertEqual(said["reason"]["key"], key)
                self.assertTrue(bus.closed)

    def test_choosing_is_a_job_and_only_where_the_desktop_asks(self) -> None:
        jobs.reset_for_tests()
        self.addCleanup(jobs.reset_for_tests)
        launch_state.clear()
        self.addCleanup(launch_state.clear)
        adapter = self.adapter()
        done = threading.Event()

        def chose(timeout: float = 0) -> dict[str, Any]:
            done.set()
            return {"kept": True}

        adapter.choose = chose  # type: ignore[method-assign]

        job = portal.start_choosing(adapter)

        self.assertTrue(done.wait(5))
        self.assertEqual(job.kind, jobs.KIND_MEDIA_CAPTURE)
        with self.assertRaises(service_errors.UnavailableError):
            portal.start_choosing(adapters.Unsupported("wayland", adapters.NO_WAY))


class ReportTests(_Kept):
    def test_until_chosen_nothing_records_and_the_fix_is_choose_screens(self) -> None:
        said = report(self.adapter())

        self.assertFalse(said["available"])
        self.assertEqual(said["reason"]["key"], adapters.NOT_CHOSEN)
        self.assertEqual(said["reason"]["fix"], tools.FIX_AUTO)
        self.assertEqual(preflight.words(said["reason"]),
                         t("capture.reason.with_remedy",
                           reason=t(adapters.NOT_CHOSEN, desktop="KDE Plasma"),
                           remedy=t("capture.portal.not_chosen.remedy")))
        self.assertEqual({row["id"] for row in said["tools"]}, {"ffmpeg", "gstreamer"})
        self.assertTrue(said["commands"]["record"].startswith("{recorder} -q -e {input} !"))

    def test_chosen_it_records_one_screen_after_another_with_sound(self) -> None:
        said = report(self.adapter(token="kept"))

        self.assertTrue(said["available"])
        self.assertFalse(said["at_once"])
        self.assertTrue(said["sound"]["available"])
        self.assertEqual(self.bus.asked, [])

    def test_the_api_serves_a_remedy_that_is_no_tools(self) -> None:
        for said in (report(self.adapter()),
                     {**report(), "available": False,
                      "reason": preflight.reason(adapters.SCREEN_PERMISSION)}):
            with self.subTest(said["reason"]["key"]):
                served = models.CaptureReport.model_validate(said)

                self.assertEqual(served.reason.remedy.setting, "")  # type: ignore[union-attr]

    def test_take_picture_takes_nothing_rather_than_raise_the_picker(self) -> None:
        from common.capture import freeze

        self.assertIn(adapters.NOT_CHOSEN, freeze._TOOL_REASONS)


class RouteTests(_Kept):
    def test_choose_screens_is_accepted_as_a_job(self) -> None:
        client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        job = jobs.Job(id="j1", kind=jobs.KIND_MEDIA_CAPTURE)
        with patch("common.capture.adapters.resolve", return_value=self.adapter()), \
                patch.object(portal, "start_choosing", return_value=job) as started:
            response = client.post("/capture/screens/choose")

        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.headers["Location"], "/api/v1/jobs/j1")
        self.assertIsInstance(started.call_args.args[0], portal.PortalAdapter)

    def test_elsewhere_there_is_nothing_to_choose(self) -> None:
        client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        with patch("common.capture.adapters.resolve",
                   return_value=adapters.Unsupported("wayland", adapters.NO_WAY)):
            response = client.post("/capture/screens/choose")

        self.assertEqual(response.status_code, 501, response.text)


class _PortalCabinet(Cabinet):
    """The cabinet's recorders and FFmpeg, reading GStreamer's `location=`."""

    def __init__(self) -> None:
        super().__init__()
        self.passed: list[tuple[int, ...]] = []

    def popen(self, argv: list[str], **kwargs: Any) -> Any:
        self.passed.append(tuple(kwargs.get("pass_fds") or ()))
        return super().popen([*argv[:-1], argv[-1].removeprefix("location=")], **kwargs)


class RecordingTests(_Kept):
    def _session(self, adapter: portal.PortalAdapter, cabinet: Cabinet,
                 work: Path) -> session.Result:
        outputs = {one.name: one for one in adapter.outputs()}
        config = configparser.ConfigParser()
        config["windows.playfield"] = {"rotation": "0"}
        target = session.Target("game1", object(), "", None,
                                ("playfield_video", "backglass_video", "scoreview_video"))
        return session.Session(
            target, QUICK, adapter=adapter,
            screens={"playfield": outputs["DP-1"], "backglass": outputs["DP-2"],
                     "scoreview": outputs["HDMI-A-1"]},
            found=found(), at_once=False, codec="h264", work=work, config=config,
            kit=cabinet.kit()).run()

    def test_a_session_around_the_recording_a_remote_for_each_recorder(self) -> None:
        adapter = self.adapter(token="kept")
        cabinet = _PortalCabinet()

        with tempfile.TemporaryDirectory() as held, \
                self.assertLogs("vpinfe.common.capture.session", "WARNING"):
            result = self._session(adapter, cabinet, Path(held) / "recording")

        self.assertEqual({one["kind"] for one in result.placed},
                         {"playfield_video", "backglass_video"})
        self.assertEqual(result.failed, [{"kind": "scoreview_video", "reason": {
            "key": portal.NOT_SHARED, "params": {"window": "scoreview",
                                                 "desktop": "KDE Plasma"}}}])
        spawned = [argv for kind, argv, *_ in cabinet.log if kind == "spawn"]
        self.assertEqual([argv[argv.index("pipewiresrc") + 2] for argv in spawned],
                         ["path=64", "path=65"])
        self.assertEqual(cabinet.passed, [(self.bus.remotes[0],), (self.bus.remotes[1],)])
        self.assertTrue(self.bus.closed)

    def test_nothing_chosen_launches_nothing(self) -> None:
        cabinet = _PortalCabinet()

        with tempfile.TemporaryDirectory() as held, \
                self.assertLogs("vpinfe.common.capture.session", "WARNING"):
            result = self._session(self.adapter(), cabinet, Path(held) / "recording")

        self.assertEqual(result.state, session.FAILED)
        self.assertEqual({one["reason"]["key"] for one in result.failed},
                         {adapters.NOT_CHOSEN})
        self.assertEqual(cabinet.launched_with, {})


class TestCommandTests(_Kept):
    def test_test_records_the_playfield_through_a_remote_of_its_own(self) -> None:
        adapter = self.adapter(token="kept")
        playfield = adapter.outputs()[0]
        config = configparser.ConfigParser()
        config["windows.playfield"] = {"rotation": "0"}
        device = run.Device(adapter, found(), config, {"playfield": playfield},
                            placing.Placing([playfield], config, []))
        recorder = Playfield()
        passed: list[Any] = []
        spawn = adapters.spawn

        def spawned(popen: Any, argv: list[str], output: Any = None, **streams: Any) -> Any:
            passed.append(adapters.passing(output))
            return spawn(popen, [*argv[:-1], argv[-1].removeprefix("location=")], output,
                         **streams)

        with tempfile.TemporaryDirectory() as held, \
                patch.object(run, "reach", return_value=device), \
                patch("common.capture.preflight.report", return_value=report()), \
                patch.object(trial, "WORK", Path(held) / "test"), \
                patch.object(adapters, "spawn", spawned):
            jobs.reset_for_tests()
            said = trial.test({}, kit=recorder.kit(), seconds=0.05)

        self.assertIn("pipewiresrc fd=", said["record"])
        self.assertIn("path=64", said["record"])
        self.assertEqual(passed, [{"pass_fds": (self.bus.remotes[0],)}])
        self.assertTrue(self.bus.closed)

    def test_a_screen_the_desktop_does_not_hand_over_is_said_as_the_refusal(self) -> None:
        adapter = self.adapter()
        playfield = adapter.outputs()[0]
        config = configparser.ConfigParser()
        device = run.Device(adapter, found(), config, {"playfield": playfield},
                            placing.Placing([playfield], config, []))

        with tempfile.TemporaryDirectory() as held, \
                patch.object(run, "reach", return_value=device), \
                patch("common.capture.preflight.report", return_value=report()), \
                patch.object(trial, "WORK", Path(held) / "test"), \
                self.assertRaises(service_errors.UnavailableError) as refused:
            jobs.reset_for_tests()
            trial.test({}, kit=Playfield().kit(), seconds=0.05)

        self.assertEqual(str(refused.exception), t(adapters.NOT_CHOSEN, desktop="KDE Plasma"))


if __name__ == "__main__":
    unittest.main()

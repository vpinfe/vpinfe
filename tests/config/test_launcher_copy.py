"""Copying launchers to other machines.

The id surviving the trip is what the whole thing rests on: it is what makes the same
launcher exist on every cabinet under one name, and what lets a mapping mean the same
thing on both ends.
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import requests

from common.games import launcher_copy, launchers
from common.i18n import t


class _Client:
    """A device that records what it was asked to write."""

    def __init__(self, fail_on: str = "", fail_mappings: bool = False,
                 fail_files: bool = False):
        self.launchers: list[tuple[str, dict]] = []
        self.files: list[tuple[str, bytes]] = []
        self.mappings: list[tuple[str, str]] = []
        self._fail_files = fail_files
        self._fail_on = fail_on
        self._fail_mappings = fail_mappings

    def put_launcher(self, launcher_id, body):
        if self._fail_on and body.get("display_name") == self._fail_on:
            raise RuntimeError("connection refused")
        self.launchers.append((launcher_id, body))
        return body

    def put_launcher_settings_file(self, launcher_id, data):
        if self._fail_files:
            raise RuntimeError("connection refused")
        self.files.append((launcher_id, data))
        return {}

    def put_launcher_mapping(self, table_id, launcher_id):
        if self._fail_mappings:
            raise RuntimeError("connection refused")
        self.mappings.append((table_id, launcher_id))
        return {}


def _launcher(launcher_id: str, name: str, **settings) -> dict:
    return {"launcher_id": launcher_id, "app": "vpx", "display_name": name,
            "enabled": True, "owns_ini": True,
            "settings": {"bin_path": "/opt/vpx", **settings}}


def _device(device_id: str, name: str) -> dict:
    return {"device_id": device_id, "display_name": name}


class CopyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clients: dict[str, _Client] = {}

    def _client_for(self, device):
        return self.clients.setdefault(device["device_id"], _Client())

    def test_the_id_survives_the_trip(self) -> None:
        """The whole trick. Renumbering on the way in would break every mapping that
        travelled with it."""
        launcher_copy.copy_to([_device("cab", "Cab")], [_launcher("keep-me", "VPX")],
                              client_for=self._client_for)

        sent = self.clients["cab"].launchers
        self.assertEqual([one[0] for one in sent], ["keep-me"])

    def test_the_settings_travel(self) -> None:
        launcher_copy.copy_to(
            [_device("cab", "Cab")],
            [_launcher("a", "VPX", launch_env="X=1")],
            client_for=self._client_for)

        body = self.clients["cab"].launchers[0][1]
        self.assertEqual(body["settings"]["launch_env"], "X=1")
        self.assertEqual(body["display_name"], "VPX")

    def test_the_settings_file_goes_as_bytes_and_its_path_stays_behind(self) -> None:
        """A path from this machine names nothing on the other, so a copy that carried it
        would play with a file that is not there."""
        with TemporaryDirectory() as folder:
            quiet = Path(folder) / "quiet.ini"
            quiet.write_bytes(b"[Player]\nBallTrail=0\n")

            launcher_copy.copy_to(
                [_device("cab", "Cab")], [_launcher("a", "VPX", ini_path=str(quiet))],
                client_for=self._client_for)

        client = self.clients["cab"]
        self.assertNotIn("ini_path", client.launchers[0][1]["settings"])
        self.assertEqual(client.files, [("a", b"[Player]\nBallTrail=0\n")])

    def test_a_launcher_on_its_programs_own_file_sends_none(self) -> None:
        launcher_copy.copy_to([_device("cab", "Cab")], [_launcher("a", "VPX")],
                              client_for=self._client_for)

        self.assertEqual(self.clients["cab"].files, [])

    def test_a_file_that_is_there_but_cannot_be_read_is_said_not_dropped(self) -> None:
        """Dropping it would have the launcher arrive on the other device's own settings
        with nothing saying it was meant to carry these."""
        with TemporaryDirectory() as folder:
            quiet = Path(folder) / "quiet.ini"
            quiet.mkdir()
            client = _Client()

            found = launcher_copy.copy_to(
                [_device("cab", "Cab")], [_launcher("a", "VPX", ini_path=str(quiet))],
                client_for=lambda _d: client)

        self.assertEqual(found[0].error, t("said.file_not_arrived", name="VPX"))

    def test_a_file_that_is_not_there_sends_nothing_and_no_path(self) -> None:
        launcher_copy.copy_to(
            [_device("cab", "Cab")], [_launcher("a", "VPX", ini_path="/nowhere/quiet.ini")],
            client_for=self._client_for)

        client = self.clients["cab"]
        self.assertEqual(client.files, [])
        self.assertNotIn("ini_path", client.launchers[0][1]["settings"])

    def test_a_file_that_does_not_arrive_says_the_launcher_did(self) -> None:
        """The two fixes differ: a launcher that arrived plays on the other device's own
        settings, while one that did not is not there at all."""
        with TemporaryDirectory() as folder:
            quiet = Path(folder) / "quiet.ini"
            quiet.write_bytes(b"x")
            client = _Client(fail_files=True)

            found = launcher_copy.copy_to(
                [_device("cab", "Cab")], [_launcher("a", "VPX", ini_path=str(quiet))],
                client_for=lambda _d: client)

        self.assertEqual(found[0].error, t("said.file_not_arrived", name="VPX"))
        self.assertEqual(found[0].launchers, 1)

    def test_the_copy_does_not_claim_to_own_an_ini(self) -> None:
        """`owns_ini` says whether *this* install created the file. The copy created
        nothing on the machine it lands on, and a wrong answer there is what decides
        whether removing it offers to delete somebody's real configuration."""
        launcher_copy.copy_to([_device("cab", "Cab")], [_launcher("a", "VPX")],
                              client_for=self._client_for)

        self.assertFalse(self.clients["cab"].launchers[0][1]["owns_ini"])

    def test_mappings_go_only_where_their_launcher_went(self) -> None:
        """A mapping naming a launcher the target does not have is dropped on read at the
        far end, so sending one is a write that quietly does nothing."""
        launcher_copy.copy_to(
            [_device("cab", "Cab")], [_launcher("sent", "VPX")],
            {"t1": "sent", "t2": "not-sent"}, client_for=self._client_for)

        self.assertEqual(self.clients["cab"].mappings, [("t1", "sent")])

    def test_nothing_is_mapped_when_none_are_asked_for(self) -> None:
        launcher_copy.copy_to([_device("cab", "Cab")], [_launcher("a", "VPX")],
                              client_for=self._client_for)

        self.assertEqual(self.clients["cab"].mappings, [])

    def test_every_device_gets_its_own_answer(self) -> None:
        """Three cabinets with the second asleep is a partial success, and a caller told
        only "failed" would send somebody to check all three."""
        def client_for(device):
            if device["device_id"] == "asleep":
                raise OSError("no route to host")
            return self.clients.setdefault(device["device_id"], _Client())

        found = launcher_copy.copy_to(
            [_device("a", "Cab A"), _device("asleep", "Cab B"), _device("c", "Cab C")],
            [_launcher("x", "VPX")], client_for=client_for)

        self.assertEqual([one.ok for one in found], [True, False, True])
        self.assertEqual((found[1].error, found[1].reason),
                         (t("said.not_reached"), "no route to host"))

    def test_a_launcher_that_does_not_arrive_stops_that_device(self) -> None:
        """And says which one. Carrying on would leave a device holding half a set with
        nothing saying which half."""
        client = _Client(fail_on="Second")

        found = launcher_copy.copy_to(
            [_device("cab", "Cab")],
            [_launcher("a", "First"), _launcher("b", "Second"),
             _launcher("c", "Third")],
            client_for=lambda _d: client)

        self.assertFalse(found[0].ok)
        self.assertIn("Second", found[0].error)
        self.assertEqual(found[0].launchers, 1)

    def test_launchers_arriving_without_their_mappings_says_so(self) -> None:
        """Two different states to be in, and the fix differs: one is re-copy, the other
        is that the tables are still on the default."""
        client = _Client(fail_mappings=True)

        found = launcher_copy.copy_to([_device("cab", "Cab")], [_launcher("a", "VPX")],
                                      {"t1": "a"}, client_for=lambda _d: client)

        self.assertIn("the launchers arrived", found[0].error)
        self.assertEqual(found[0].launchers, 1)


class RefusedTests(unittest.TestCase):
    """A device that already has a launcher by that name keeps it."""

    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = launchers.LauncherStore(tmp.name)
        patcher = patch.object(launchers, "get_launcher_store", return_value=self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store.put(launchers.Launcher(launcher_id="here", app="vpx",
                                          display_name="VPX"))

    def _copy(self, launcher: dict) -> launcher_copy.Outcome:
        return launcher_copy.copy_to([_device("me", "This One")], [launcher],
                                     client_for=lambda _d: launcher_copy.LocalWrites())[0]

    def test_a_different_launcher_under_a_name_in_use_is_refused_and_named(self) -> None:
        found = self._copy(_launcher("there", "vpx"))

        self.assertEqual((found.error, found.reason),
                         (t("said.not_arrived", name="vpx"),
                          t("error.launchers.name_taken", name="vpx")))
        self.assertIsNone(self.store.get("there"))

    def test_the_same_launcher_arriving_again_updates_it(self) -> None:
        found = self._copy(_launcher("here", "VPX", launch_env="X=1"))

        self.assertTrue(found.ok, found.error)
        kept = self.store.get("here")
        assert kept is not None
        self.assertEqual(kept.value("launch_env"), "X=1")

    def test_a_device_across_the_network_is_quoted_not_its_status(self) -> None:
        answer = requests.Response()
        answer.status_code = 400
        answer._content = json.dumps(
            {"error": {"message": "Another launcher is already called VPX."}}).encode()

        class _Refusing(_Client):
            def put_launcher(self, launcher_id, body):
                raise requests.HTTPError("400 Client Error", response=answer)

        found = launcher_copy.copy_to([_device("cab", "Cab")], [_launcher("a", "VPX")],
                                      client_for=lambda _d: _Refusing())[0]

        self.assertEqual((found.error, found.reason),
                         ("VPX did not arrive", "Another launcher is already called VPX."))


class SaidTests(unittest.TestCase):
    """Counts where it worked, names where it did not."""

    def test_all_good_is_a_count(self) -> None:
        found = [launcher_copy.Outcome("a", "Cab A"), launcher_copy.Outcome("b", "Cab B")]

        self.assertEqual((launcher_copy.said(found), launcher_copy.trouble(found)),
                         ("Copied to 2 devices", ""))

    def test_one_device_is_not_pluralised(self) -> None:
        self.assertEqual(launcher_copy.said([launcher_copy.Outcome("a", "Cab A")]),
                         "Copied to 1 device")

    def test_a_failure_is_named_under_the_count(self) -> None:
        found = [launcher_copy.Outcome("a", "Cab A"),
                 launcher_copy.Outcome("b", "Cab B", error="VPX did not arrive",
                                       reason="Nothing answers at cab-b.local"),
                 launcher_copy.Outcome("c", "Cab C", error="asleep")]

        self.assertEqual((launcher_copy.said(found), launcher_copy.trouble(found)),
                         ("Copied to 1 of 3",
                          "Cab B - VPX did not arrive. Nothing answers at cab-b.local; "
                          "Cab C - asleep"))

    def test_all_bad_does_not_claim_a_partial_success(self) -> None:
        found = [launcher_copy.Outcome("b", "Cab B", error="asleep")]

        self.assertEqual(launcher_copy.said(found), "Nothing was copied")


if __name__ == "__main__":
    unittest.main()

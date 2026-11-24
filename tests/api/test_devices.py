"""The registry: which devices an install has been told about.

Data only. There is no routing, no aggregation and no picking one to launch on - all
three need decisions across devices that nothing has made. What a registry buys today is
attribution: an event carries the `install_id` it happened on, and this turns that id
into a name someone recognizes.
"""

from __future__ import annotations

import unittest
from configparser import ConfigParser
from typing import Any
from unittest.mock import Mock, patch

import requests

import httpapi
from common.devices import device_client
from common.devices import device_registry as registry_module
from common.games import mobile_ops
from common.i18n import t
from common.runtime import jobs
from common.settings import config_service
from tests.support.library import TempTree

try:
    from starlette.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[assignment,misc]

CAB: dict[str, Any] = {"device_id": "Aaaa111111", "display_name": "basement cab",
       "features": ["library", "frontend"]}
DESK = {"device_id": "Bbbb222222", "display_name": "desktop", "features": ["device"]}


@unittest.skipIf(TestClient is None, "starlette test client unavailable")
class DeviceRegistryApiTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        registry = self.registry = registry_module.DeviceRegistry(self.root / "devices.json")
        patcher = patch.object(registry_module, "get_device_registry", lambda: registry)
        patcher.start()
        self.addCleanup(patcher.stop)
        also = patch("common.devices.device_ops.get_device_registry", lambda: registry)
        also.start()
        self.addCleanup(also.stop)
        # These tests are about remote devices; this install's own row is pinned elsewhere.
        listing = patch.object(config_service, "get_ini_config", lambda: registry.config())
        listing.start()
        self.addCleanup(listing.stop)
        quiet = patch.object(httpapi.instance, "mint_identity", lambda: None)
        quiet.start()
        self.addCleanup(quiet.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def test_a_hub_knows_nobody_until_someone_says_hello(self) -> None:
        body = self.client.get("/devices").json()

        self.assertEqual(body, {"count": 0, "devices": []})

    def test_announcing_records_what_the_device_said(self) -> None:
        response = self.client.put("/devices", json=CAB)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["device_id"], CAB["device_id"])
        self.assertEqual(body["display_name"], CAB["display_name"])
        self.assertEqual(body["features"], CAB["features"])
        self.assertTrue(body["first_seen"])
        self.assertEqual(body["links"]["self"], "/api/v1/devices/Aaaa111111")

    def test_announcing_twice_is_one_device_heard_from_twice(self) -> None:
        """A device restarting must not become a second entry."""
        first = self.client.put("/devices", json=CAB).json()
        self.client.put("/devices", json=CAB | {"display_name": "renamed"})

        listed = self.client.get("/devices").json()
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["devices"][0]["display_name"], "renamed",
                         "the install owns its name; the registry is a copy")
        self.assertEqual(listed["devices"][0]["first_seen"], first["first_seen"],
                         "it is the same device, however many times it reconnects")

    def test_two_devices_are_two_entries(self) -> None:
        self.client.put("/devices", json=CAB)
        self.client.put("/devices", json=DESK)

        listed = self.client.get("/devices").json()

        self.assertEqual(listed["count"], 2)
        self.assertEqual({p["device_id"] for p in listed["devices"]},
                         {CAB["device_id"], DESK["device_id"]})

    def test_the_address_is_observed_rather_than_claimed(self) -> None:
        """A device behind a router does not know how it is reached, so a body that
        said would be a claim. The socket is the only party that knows."""
        body = self.client.put("/devices", json=CAB | {"address": "10.0.0.99"}).json()

        self.assertNotEqual(body["address"], "10.0.0.99")

    def test_a_device_with_no_id_is_refused(self) -> None:
        """An install with no id is not an identity, and a registry keyed on "" is a
        registry of one."""
        response = self.client.put("/devices", json={"device_id": "  "})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get("/devices").json()["count"], 0)

    def test_one_device_answers_for_itself(self) -> None:
        self.client.put("/devices", json=CAB)

        body = self.client.get(f"/devices/{CAB['device_id']}").json()

        self.assertEqual(body["display_name"], CAB["display_name"])

    def test_an_unknown_device_is_a_404(self) -> None:
        self.assertEqual(self.client.get("/devices/Nope111111").status_code, 404)

    def test_forgetting_a_device_removes_it(self) -> None:
        self.client.put("/devices", json=CAB)

        self.assertEqual(self.client.delete(f"/devices/{CAB['device_id']}").status_code,
                         204)
        self.assertEqual(self.client.get("/devices").json()["count"], 0)

    def test_forgetting_one_that_was_never_there_is_a_404(self) -> None:
        self.assertEqual(self.client.delete("/devices/Nope111111").status_code, 404)

    def test_the_install_you_are_using_cannot_be_forgotten(self) -> None:
        own = self.registry.ensure_self()

        response = self.client.delete(f"/devices/{own}")

        self.assertEqual(response.status_code, 400, response.text)
        self.assertTrue(self.registry.knows(own))
        self.assertEqual(self.registry.self_id(), own)

    def test_an_unknown_kind_is_refused(self) -> None:
        """A closed set, checked at the boundary. manager switches on this to decide what
        it can do with an entry, so a kind it has never heard of is worse stored than
        rejected - it would reach a screen as a device nothing knows how to talk to."""
        response = self.client.put("/devices", json=CAB | {"kind": "toaster"})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get("/devices").json()["count"], 0,
                         "refused means not stored, not stored-then-complained-about")

    def _list_mobile(self, name: str = "iPad", address: str = "192.168.1.60",
                     port: int = 2112) -> dict[str, Any]:
        """List a VPX Mobile device where a person does, and read it back as a device."""
        item = self.client.post("/config/mobile_devices", json={
            "name": name, "address": address, "port": port}).json()
        return self.client.get(f"/devices/{item['id']}").json()

    def test_a_kind_round_trips(self) -> None:
        phone = self._list_mobile("iPhone", "192.168.1.50")

        self.assertEqual(phone["kind"], "vpx_mobile")
        self.assertEqual(self.client.get(f"/devices/{phone['device_id']}").json()["kind"],
                         "vpx_mobile")

    def test_an_install_cannot_announce_under_a_listed_mobile_id(self) -> None:
        listed = self._list_mobile()

        response = self.client.put("/devices", json={"device_id": listed["device_id"]})

        self.assertEqual(response.status_code, 400, response.text)

    def test_a_mobile_device_cannot_announce_itself(self) -> None:
        response = self.client.put("/devices", json={
            "device_id": "Pppp444444", "kind": "vpx_mobile", "address": "192.168.1.50"})

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.client.get("/devices").json()["count"], 0)

    def test_an_announcement_with_no_kind_is_a_vpinfe_install(self) -> None:
        """Every device that can announce itself today is one, so the default is the
        one a caller written before kind existed would have meant."""
        body = self.client.put("/devices", json={"device_id": "Cccc333333"}).json()

        self.assertEqual(body["kind"], "vpinfe")

    def test_a_mobile_device_is_added_without_an_id_and_gets_one(self) -> None:
        """The phone is not the caller - a person is registering it - so it cannot offer
        an id and one is minted here."""
        body = self._list_mobile("iPad", "192.168.1.60")

        self.assertTrue(body["device_id"], "an id was minted")
        self.assertEqual(body["address"], "192.168.1.60", "declared, not observed")
        self.assertEqual(self.client.get("/devices").json()["count"], 1)

    def test_two_mobile_devices_coexist(self) -> None:
        """An iPhone and an iPad at once. An id derived from an address would have
        collapsed them the moment DHCP handed one the other's."""
        first = self._list_mobile("iPhone", "192.168.1.50")
        second = self._list_mobile("iPad", "192.168.1.60")

        self.assertNotEqual(first["device_id"], second["device_id"])
        self.assertEqual(self.client.get("/devices").json()["count"], 2)

    def test_a_mobile_device_keeps_its_id_when_its_address_changes(self) -> None:
        added = self._list_mobile("iPhone", "192.168.1.50")

        self.client.put(f"/config/mobile_devices/{added['device_id']}",
                        json={"name": "iPhone", "address": "192.168.1.77", "port": 2112})
        moved = self.client.get(f"/devices/{added['device_id']}").json()

        self.assertEqual(moved["address"], "192.168.1.77")
        self.assertEqual(self.client.get("/devices").json()["count"], 1, "one phone")

    def test_a_mobile_device_a_person_wrote_has_not_been_heard_from(self) -> None:
        """Nothing has answered yet, so it is neither seen nor reachable, and rewriting
        its name or address does not make it so."""
        added = self._list_mobile("iPhone", "192.168.1.50")
        self.client.put(f"/config/mobile_devices/{added['device_id']}",
                        json={"name": "Tablet", "address": "192.168.1.77", "port": 2113})
        edited = self.client.get(f"/devices/{added['device_id']}").json()

        self.assertEqual((added["last_seen"], added["last_reachable"]), ("", ""))
        self.assertEqual((edited["last_seen"], edited["last_reachable"]), ("", ""))
        self.assertEqual((edited["display_name"], edited["address"], edited["port"]),
                         ("Tablet", "192.168.1.77", 2113))

    def test_a_mobile_device_needs_an_address(self) -> None:
        """Its address is the only way to reach it, and nothing else will supply one."""
        response = self.client.post("/config/mobile_devices", json={"name": "iPad"})

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.client.get("/devices").json()["count"], 0)

    def test_an_address_with_a_scheme_or_a_port_is_refused(self) -> None:
        response = self.client.post("/config/mobile_devices", json={
            "name": "iPad", "address": "http://192.168.1.60:2112"})

        self.assertEqual(response.status_code, 400, response.text)

    def test_a_mobile_device_is_forgotten_where_it_is_listed(self) -> None:
        added = self._list_mobile()

        refused = self.client.delete(f"/devices/{added['device_id']}")
        gone = self.client.delete(f"/config/mobile_devices/{added['device_id']}")

        self.assertEqual(refused.status_code, 400, refused.text)
        self.assertEqual(gone.status_code, 204, gone.text)
        self.assertEqual(self.client.get("/devices").json()["count"], 0)

    def _send(self, answer: dict[str, str]) -> tuple[Any, Mock]:
        added = self._list_mobile("iPad", "192.0.2.7")
        submit = Mock(return_value=jobs.Job(id="j1", kind="device_send"))
        with patch.object(device_client, "probe", return_value=answer), \
                patch.object(mobile_ops.game_repository, "game_folder",
                             return_value=self.root / "Alpha"), \
                patch.object(mobile_ops.job_registry, "submit", submit):
            return self.client.post(f"/devices/{added['device_id']}/games",
                                    json={"games": ["g1"]}), submit

    def test_a_send_to_a_device_that_does_not_answer_is_refused_before_a_job_exists(
            self) -> None:
        response, submit = self._send({"state": device_client.UNREACHABLE, "reason": "Asleep"})

        self.assertEqual(response.status_code, 409)
        self.assertIn("Asleep", response.text)
        submit.assert_not_called()

    def test_a_send_to_a_device_that_answers_starts_the_job(self) -> None:
        response, submit = self._send({"state": device_client.ANSWERING, "reason": ""})

        self.assertEqual(response.status_code, 202)
        submit.assert_called_once()

    def _sending_config(self, **vpxmobile: str) -> Any:
        parser = ConfigParser()
        parser.read_dict({"vpxmobile": vpxmobile})
        return patch.object(mobile_ops, "get_ini_config", return_value=parser)

    def test_the_mobile_word_is_nothing_while_the_switch_is_off(self) -> None:
        with self._sending_config(config_mask="mobile"):
            self.assertEqual(mobile_ops._sending()[1], "")

    def test_the_mobile_word_is_the_mask_without_its_dots_once_the_switch_is_on(self) -> None:
        with self._sending_config(send_masked_config="true", config_mask=".mobile.",
                                  chunk_size="4096"):
            self.assertEqual(mobile_ops._sending(), (4096, "mobile"))

    def test_a_word_that_carries_the_extension_is_the_word_alone(self) -> None:
        for mask in ("cab.ini", ".cab.INI", " cab .ini "):
            with self._sending_config(send_masked_config="true", config_mask=mask):
                self.assertEqual(mobile_ops._sending()[1], "cab", mask)

    def test_the_switch_reads_as_the_manager_draws_it(self) -> None:
        for on in ("1", "yes", "on", "True"):
            with self._sending_config(send_masked_config=on, config_mask="mobile"):
                self.assertEqual(mobile_ops._sending()[1], "mobile", on)

    def test_a_chunk_size_that_is_no_number_is_the_default(self) -> None:
        with self._sending_config(chunk_size="lots"):
            self.assertEqual(mobile_ops._sending()[0], mobile_ops.mobile_transfer.CHUNK_BYTES)

    def test_the_plan_names_the_games_that_would_send_a_mobile_settings_file(self) -> None:
        added = self._list_mobile("iPad", "192.0.2.7")
        has = self.root / "Alpha"
        has.mkdir()
        (has / "Alpha.vpx").write_bytes(b"vpx")
        (has / "Alpha.mobile.ini").write_text("[Player]", encoding="utf-8")
        lacks = self.root / "Beta"
        lacks.mkdir()
        (lacks / "Beta.vpx").write_bytes(b"vpx")
        folders = {"g1": has, "g2": lacks}
        with self._sending_config(send_masked_config="true", config_mask="mobile"), \
                patch.object(mobile_ops.game_repository, "game_folder",
                             side_effect=lambda game_id: folders[game_id]):
            body = self.client.post(f"/devices/{added['device_id']}/games/plan",
                                    json={"games": ["g1", "g2"]}).json()

        self.assertEqual(body["mobile_settings"], ["g1"])

    def test_the_plan_names_nothing_while_the_switch_is_off(self) -> None:
        added = self._list_mobile("iPad", "192.0.2.7")
        has = self.root / "Alpha"
        has.mkdir()
        (has / "Alpha.vpx").write_bytes(b"vpx")
        (has / "Alpha.mobile.ini").write_text("[Player]", encoding="utf-8")
        with self._sending_config(config_mask="mobile"), \
                patch.object(mobile_ops.game_repository, "game_folder", return_value=has):
            body = self.client.post(f"/devices/{added['device_id']}/games/plan",
                                    json={"games": ["g1"]}).json()

        self.assertEqual(body["mobile_settings"], [])

    def test_the_settings_flag_and_the_word_reach_the_transfer(self) -> None:
        added = self._list_mobile("iPad", "192.0.2.7")
        submit = Mock(return_value=jobs.Job(id="j1", kind="device_send"))
        sent = Mock()
        with self._sending_config(send_masked_config="true", config_mask="mobile"), \
                patch.object(device_client, "probe",
                             return_value={"state": device_client.ANSWERING, "reason": ""}), \
                patch.object(mobile_ops.game_repository, "game_folder",
                             return_value=self.root / "Alpha"), \
                patch.object(mobile_ops.job_registry, "submit", submit), \
                patch.object(mobile_ops.mobile_transfer, "send", sent):
            self.client.post(f"/devices/{added['device_id']}/games",
                             json={"games": ["g1"], "settings": True})
            submit.call_args.args[1](Mock())

        self.assertTrue(sent.call_args.kwargs["settings"])
        self.assertEqual(sent.call_args.kwargs["mobile_word"], "mobile")

    def test_what_a_device_carries_says_which_library_game_each_folder_is(self) -> None:
        added = self._list_mobile("iPad", "192.0.2.7")
        game = Mock(game_dir_name="Alpha (Bally 1991)")
        with patch.object(mobile_ops.mobile_transfer, "carried",
                          return_value=["Alpha (Bally 1991)", "Stray"]), \
                patch.object(mobile_ops.game_repository, "catalog",
                             return_value={"g1": game}):
            body = self.client.get(f"/devices/{added['device_id']}/games").json()

        self.assertEqual(body["count"], 2)
        self.assertEqual(body["games"], [{"folder": "Alpha (Bally 1991)", "game_id": "g1"},
                                         {"folder": "Stray", "game_id": None}])

    def test_a_folder_in_two_locations_is_the_game_of_the_higher_one(self) -> None:
        added = self._list_mobile("iPad", "192.0.2.7")
        lower = Mock(game_dir_name="Alpha (Bally 1991)")
        higher = Mock(game_dir_name="Alpha (Bally 1991)")
        with patch.object(mobile_ops.mobile_transfer, "carried",
                          return_value=["Alpha (Bally 1991)"]), \
                patch.object(mobile_ops.game_repository, "catalog",
                             return_value={"lower": lower, "higher": higher}), \
                patch.object(mobile_ops.game_identity, "in_priority",
                             return_value=[higher, lower]):
            body = self.client.get(f"/devices/{added['device_id']}/games").json()

        self.assertEqual(body["games"], [{"folder": "Alpha (Bally 1991)",
                                          "game_id": "higher"}])

    def test_an_install_still_cannot_omit_its_id(self) -> None:
        """Minting is for devices that cannot identify themselves. An install can."""
        response = self.client.put("/devices", json={"display_name": "cab"})

        self.assertEqual(response.status_code, 400)

    def test_the_registry_is_linked_from_discovery(self) -> None:
        """So an integrator finds it by asking rather than by reading this file."""
        links = self.client.get("/").json()["links"]

        self.assertEqual(links["devices"], "/api/v1/devices")

    def test_an_install_says_which_port_it_answers_on(self) -> None:
        """The socket says where a request came from, never what that machine listens
        on, so this is the only way the other half of an address arrives."""
        self.client.put("/devices", json={**CAB, "port": 8001})

        device = self.client.get(f"/devices/{CAB['device_id']}").json()
        self.assertEqual(device["port"], 8001)

    def test_the_listing_carries_the_scheme_an_install_announced(self) -> None:
        """A listing without it is dialed over plain HTTP, and an install serving HTTPS
        resets that connection."""
        self.registry.record(CAB["device_id"], address="10.0.0.5", port=8001, https=True)

        listed = self.client.get("/devices").json()["devices"][0]
        one = self.client.get(f"/devices/{CAB['device_id']}").json()

        self.assertEqual((listed["https"], one["https"]), (True, True))

    def test_a_device_that_says_no_port_is_recorded_without_one(self) -> None:
        """Every entry written before installs sent one. Half an address is not an
        address, and 0 is what says so."""
        self.client.put("/devices", json=CAB)

        device = self.client.get(f"/devices/{CAB['device_id']}").json()
        self.assertEqual(device["port"], 0)

    def test_probing_reports_a_state_for_every_device(self) -> None:
        """Including the ones it cannot dial: a listing that silently drops those is a
        listing that says a device is fine because nothing asked it."""
        self.client.put("/devices", json=CAB)
        self.client.put("/devices", json=DESK)

        probes = self.client.post("/devices/probe").json()["probes"]

        self.assertEqual({p["device_id"] for p in probes},
                         {CAB["device_id"], DESK["device_id"]})

    def test_a_device_with_no_port_cannot_be_asked_rather_than_being_down(self) -> None:
        """Two different facts. Switching the machine on does not fix this one."""
        self.client.put("/devices", json=CAB)

        probe = self.client.post("/devices/probe").json()["probes"][0]

        self.assertEqual(probe["state"], "unaskable")
        self.assertIn("port", probe["reason"])

    def test_a_device_that_does_not_answer_is_unreachable(self) -> None:
        # Port 9 discards whatever it is sent, so nothing answers on it.
        self.client.put("/devices", json={**CAB, "port": 9})

        probe = self.client.post("/devices/probe").json()["probes"][0]

        self.assertEqual(probe["state"], "unreachable")
        self.assertEqual(probe["reason"], t(probe["reason_key"]))
        self.assertNotIn("HTTPConnectionPool", probe["reason"])

    def test_one_device_can_be_asked_on_its_own(self) -> None:
        """So a page can show each answer as it comes, rather than all of them when the
        slowest has timed out."""
        self.client.put("/devices", json={**CAB, "port": 9})
        self.client.put("/devices", json=DESK)

        response = self.client.post(f"/devices/{CAB['device_id']}/probe")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["device_id"], CAB["device_id"])
        self.assertEqual(response.json()["state"], "unreachable")

    def test_whether_it_is_paired_and_whether_its_certificate_changed_reach_the_page(
            self) -> None:
        """The Devices page opens its pairing door on these two, so a model that drops
        them leaves a device that needs pairing with no way to ask."""
        self.client.put("/devices", json={**CAB, "port": 9})
        answer = {"state": device_client.ANSWERING, "paired": False, "version": "3.0.0"}

        with patch.object(device_client, "probe", return_value=answer):
            unpaired = self.client.post(f"/devices/{CAB['device_id']}/probe").json()
        changed = {"state": device_client.UNREACHABLE, "paired": False,
                   "certificate_changed": True}
        with patch.object(device_client, "probe", return_value=changed):
            moved = self.client.post(f"/devices/{CAB['device_id']}/probe").json()

        self.assertIs(unpaired["paired"], False)
        self.assertFalse(unpaired["certificate_changed"])
        self.assertTrue(moved["certificate_changed"])

    def test_asking_one_it_does_not_know_is_a_404(self) -> None:
        response = self.client.post("/devices/Zzzz999999/probe")

        self.assertEqual(response.status_code, 404)

    def test_a_device_that_never_answered_has_no_reachable_time_from_a_probe(self) -> None:
        """It has one from announcing - that is the push half - but a failed probe must
        not advance it, or the value stops meaning anything."""
        self.client.put("/devices", json={**CAB, "port": 9})
        announced = self.client.get(f"/devices/{CAB['device_id']}").json()["last_reachable"]

        self.client.post("/devices/probe")

        after = self.client.get(f"/devices/{CAB['device_id']}").json()["last_reachable"]
        self.assertEqual(after, announced)

    def test_announcing_again_without_a_port_keeps_the_one_it_gave(self) -> None:
        """A device is the same device however many times it reconnects, and an
        announcement that omits a field is not the device withdrawing it."""
        self.client.put("/devices", json={**CAB, "port": 8001})
        self.client.put("/devices", json=CAB)

        device = self.client.get(f"/devices/{CAB['device_id']}").json()
        self.assertEqual(device["port"], 8001)


class _Answering:
    """A device that answers its probe, running `features`, with `report` for what its
    browser plays - or raising it, where it is an exception."""

    def __init__(self, features: list[str], report: dict | Exception) -> None:
        self.features = features
        self.report = report
        self.asked = 0

    def probe(self) -> dict:
        return {"state": device_client.ANSWERING, "version": "3.0.0", "reason": "",
                "features": self.features}

    def frontend_browser(self) -> dict:
        self.asked += 1
        if isinstance(self.report, Exception):
            raise self.report
        return self.report


NO_H264 = {"state": "no_h264", "browser": "Chromium 145.0.7632.0",
           "formats": {"h264": False, "vp9": True, "hevc": None}}


@unittest.skipIf(TestClient is None, "starlette test client unavailable")
class ProbeAsksTheBrowserTests(TempTree):
    """The probe asks an answering frontend device what its browser plays, and the
    registry keeps the answer for when it is switched off."""

    def setUp(self) -> None:
        super().setUp()
        registry = registry_module.DeviceRegistry(self.root / "devices.json")
        self.enterContext(patch.object(registry_module, "get_device_registry",
                                       lambda: registry))
        self.enterContext(patch("common.devices.device_ops.get_device_registry", lambda: registry))
        self.device = _Answering(["library", "frontend"], NO_H264)
        self.enterContext(patch.object(device_client, "for_device",
                                       side_effect=lambda *_: self.device))
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        self.client.put("/devices", json={**CAB, "port": 8001})

    def _probed(self) -> dict:
        return self.client.post(f"/devices/{CAB['device_id']}/probe").json()

    def _held(self) -> dict | None:
        return self.client.get(f"/devices/{CAB['device_id']}").json()["browser"]

    def test_what_it_plays_comes_back_with_the_probe_and_is_kept(self) -> None:
        probed = self._probed()["browser"]

        self.assertEqual((probed["state"], probed["name"], probed["formats"]),
                         ("no_h264", "Chromium 145.0.7632.0",
                          {"h264": False, "vp9": True, "hevc": None}))
        self.assertTrue(probed["checked_at"])
        self.assertEqual(self._held(), probed)

    def test_a_device_switched_off_keeps_the_last_answer(self) -> None:
        kept = self._probed()["browser"]
        self.device = _Failing(requests.ConnectionError("off"))  # type: ignore[assignment]

        self._probed()

        self.assertEqual(self._held(), kept)

    def test_one_too_old_to_say_keeps_what_was_held(self) -> None:
        kept = self._probed()["browser"]
        self.device = _Answering(["frontend"],
                                 device_client.TooOldError(t(device_client.TOO_OLD)))

        self.assertEqual(self._probed()["browser"], kept)
        self.assertEqual(self._held(), kept)

    def test_one_that_no_longer_runs_the_frontend_has_it_cleared(self) -> None:
        self._probed()
        self.device = _Answering(["library"], NO_H264)

        probed = self._probed()

        self.assertIsNone(probed["browser"])
        self.assertIsNone(self._held())
        self.assertEqual(self.device.asked, 0)

    def test_this_install_is_not_asked_by_its_own_probe(self) -> None:
        kept = self._probed()["browser"]
        self.device = device_client.LocalDevice()  # type: ignore[assignment]

        self.assertEqual(self._probed()["browser"], kept)


class _Failing:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def probe(self) -> dict:
        raise self.exc


class ProbeReasonTests(unittest.TestCase):
    """What failed, said as what a person can do something about."""

    def _reason(self, exc: Exception) -> str:
        found = device_client.probe(_Failing(exc))  # type: ignore[arg-type]
        self.assertEqual(found["state"], device_client.UNREACHABLE)
        self.assertEqual(found["reason"], t(found["reason_key"]))
        return str(found["reason_key"])

    def test_a_machine_with_nothing_on_its_port_is_told_apart(self) -> None:
        try:
            # Windows retries a closed loopback port for about two seconds before refusing.
            requests.get("http://127.0.0.1:1/api/v1", timeout=10)
        except requests.ConnectionError as exc:
            self.assertEqual(self._reason(exc), "device.reason.refused")
        else:  # pragma: no cover
            self.skipTest("something answers on port 1 here")

    def test_no_answer_in_time(self) -> None:
        self.assertEqual(self._reason(requests.ConnectTimeout("slow")),
                         "device.reason.timed_out")

    def test_an_answer_that_cannot_be_read(self) -> None:
        self.assertEqual(self._reason(requests.HTTPError("500")),
                         "device.reason.unreadable")
        self.assertEqual(self._reason(ValueError("Invalid JSON")),
                         "device.reason.unreadable")

    def test_anything_else_could_not_be_reached(self) -> None:
        self.assertEqual(self._reason(requests.ConnectionError("no route")),
                         "device.reason.unreachable")


if __name__ == "__main__":
    unittest.main()

"""Which client answers for a device, and what a remote one refuses to guess at.

The resolver is the whole seam: every caller asks `for_device` and then talks to what
it gets back, so a wrong answer here is one install asking the wrong machine.
"""

from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from common import service_errors
from common.config import paths
from common.devices import device_client
from common.runtime import lifecycle
from common.security import access_keys, http_client, pinned_certificates

LOCAL = "Aaaa111111"
CAB = {"device_id": "Bbbb222222", "address": "192.168.1.50", "port": 8001}


class ResolverTests(unittest.TestCase):
    def test_this_install_is_the_local_device(self) -> None:
        client = device_client.for_device({"device_id": LOCAL}, LOCAL)

        self.assertIsInstance(client, device_client.LocalDevice)

    def test_another_install_with_an_address_is_reached_over_it(self) -> None:
        client = device_client.for_device(CAB, LOCAL)

        self.assertIsInstance(client, device_client.RemoteDevice)
        assert isinstance(client, device_client.RemoteDevice)
        self.assertEqual(client.base_url, "http://192.168.1.50:8001")

    def test_a_device_that_never_said_its_port_cannot_be_dialled(self) -> None:
        """Every entry written before installs announced one. Half an address is not an
        address, and a client built on it would time out rather than say so."""
        client = device_client.for_device({**CAB, "port": 0}, LOCAL)

        self.assertIsNone(client)

    def test_a_device_with_no_address_cannot_be_dialled(self) -> None:
        client = device_client.for_device({**CAB, "address": ""}, LOCAL)

        self.assertIsNone(client)

    def test_without_knowing_which_install_is_ours_nothing_is_assumed_local(self) -> None:
        """An install that has not identified itself must not answer for a device on the
        grounds that it cannot tell them apart."""
        client = device_client.for_device(CAB, None)

        self.assertIsInstance(client, device_client.RemoteDevice)

    def test_a_url_keeps_one_slash(self) -> None:
        self.assertEqual(device_client.RemoteDevice("http://x:1/")._url("/update"),
                         "http://x:1/api/v1/update")

    def test_check_now_asks_the_device_to_ask_now(self) -> None:
        asked: list[str] = []

        def get_json(url: str, **kwargs) -> dict:
            asked.append(url)
            return {}

        with patch.object(http_client, "get_json", get_json):
            device_client.RemoteDevice("http://x:1").update_check()
            device_client.RemoteDevice("http://x:1").update_check(refresh=True)

        self.assertEqual(asked, ["http://x:1/api/v1/update",
                                 "http://x:1/api/v1/update?refresh=true"])


class RemoteRefusalTests(unittest.TestCase):
    """What cannot be learned by asking. Raising beats an empty list: "no screens" and
    "not a thing this device can be asked" are different facts."""

    def setUp(self) -> None:
        self.device = device_client.RemoteDevice("http://192.168.1.50:8001")

    def test_screens_are_not_something_a_hub_can_enumerate(self) -> None:
        with self.assertRaises(device_client.NotThisDeviceError):
            self.device.displays()

    def test_the_browser_is_not_something_a_hub_can_read(self) -> None:
        with self.assertRaises(device_client.NotThisDeviceError):
            self.device.browser_path()

    def test_input_bindings_are_not_something_a_hub_can_read(self) -> None:
        with self.assertRaises(device_client.NotThisDeviceError):
            self.device.bindings(None)  # type: ignore[arg-type]

    def test_something_that_is_not_a_lifecycle_action_is_refused(self) -> None:
        """Refused here rather than dialled: an unknown pair would fail as a timeout on
        a machine that was never going to answer for it, instead of as a sentence."""
        with self.assertRaises(device_client.NotThisDeviceError):
            self.device.request("toaster", "toast")

    def test_every_pair_the_vocabulary_has_is_routed(self) -> None:
        """Quitting another machine's app had no route at all until the actions route
        existed, so the fleet surface could offer exactly one verb."""
        for scope, action in lifecycle.offered():
            with self.subTest(pair=(scope, action)):
                with patch.object(http_client, "post_json",
                                  return_value={"performed": True, "stopped": True}):
                    self.assertTrue(self.device.request(scope, action))

    def test_a_route_an_older_install_lacks_is_said_in_words(self) -> None:
        """It answers everything it knows about and 404s the rest, which is not a
        failure - and a person should not be shown an HTTP status and a URL for it."""
        import requests

        gone = requests.HTTPError("nope")
        gone.response = types.SimpleNamespace(status_code=404)  # type: ignore[assignment]
        with patch.object(http_client, "get_json", side_effect=gone):
            with self.assertRaises(device_client.TooOldError):
                self.device.actions()
            with self.assertRaises(device_client.TooOldError):
                self.device.logs()
            with self.assertRaises(device_client.TooOldError):
                self.device.frontend_browser()

    def test_a_refusal_for_want_of_a_token_is_a_state_not_a_failure(self) -> None:
        import requests

        for status in (401, 403):
            with self.subTest(status=status):
                refused = requests.HTTPError("no")
                refused.response = types.SimpleNamespace(status_code=status)  # type: ignore[assignment]
                with patch.object(http_client, "get_json", side_effect=refused):
                    with self.assertRaises(device_client.NotPairedError):
                        self.device.actions()
                    with self.assertRaises(device_client.NotPairedError):
                        self.device.frontend_browser()

    def test_a_real_failure_is_still_a_real_failure(self) -> None:
        """Only a missing route is translated. A machine that broke while answering has
        something worth reading in its own error."""
        import requests

        broke = requests.HTTPError("server fell over")
        broke.response = types.SimpleNamespace(status_code=500)  # type: ignore[assignment]
        with patch.object(http_client, "get_json", side_effect=broke):
            with self.assertRaises(requests.HTTPError):
                self.device.actions()

    def test_a_remote_device_never_asks_the_person_to_confirm(self) -> None:
        """The confirm belongs on the surface that asked, which is not that machine."""
        self.assertFalse(self.device.wants_confirmation(lifecycle.VPINFE))


SECURE = "https://192.168.1.50:8001"
SHOWN = "ab" * 32


class _HttpsInstall(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        patched = patch.object(paths, "SECRETS_PATH", Path(holder.name) / "secrets.json")
        patched.start()
        self.addCleanup(patched.stop)
        self.device = device_client.RemoteDevice(SECURE, "Bbbb222222")
        self.sent: list[tuple[str, object]] = []
        self.payloads: list[object] = []

    def post_json(self, url: str, payload=None, **kwargs) -> dict:
        self.sent.append((url, kwargs.get("trust")))
        self.payloads.append(payload)
        return {"request_id": "r1"} if url.endswith("/code") else {"secret": "tok"}


class PairingAnHttpsInstallTests(_HttpsInstall):
    """The certificate the address shows when a code is asked for is the one trusted once
    the code is traded, and the next one it shows is refused."""

    def test_the_code_is_asked_for_over_the_certificate_the_address_shows(self) -> None:
        with (patch.object(pinned_certificates, "trust_now", lambda _url: SHOWN),
              patch.object(http_client, "post_json", self.post_json)):
            self.assertEqual(self.device.pair_ask(), "r1")

        self.assertEqual(self.sent, [(f"{SECURE}/api/v1/auth/pair/code", SHOWN)])
        self.assertEqual(pinned_certificates.pinned("192.168.1.50", 8001), "")

    def test_the_code_is_asked_for_under_this_installs_name(self) -> None:
        with (patch.object(pinned_certificates, "trust_now", lambda _url: SHOWN),
              patch.object(http_client, "post_json", self.post_json),
              patch.object(device_client, "_own_name", lambda: "Game Room")):
            self.device.pair_ask()

        self.assertEqual(self.payloads, [{"name": "Game Room"}])

    def test_the_code_is_traded_over_the_same_certificate_and_the_pin_is_kept(self) -> None:
        with (patch.object(pinned_certificates, "trust_now", lambda _url: SHOWN),
              patch.object(http_client, "post_json", self.post_json),
              patch.object(access_keys, "keep_pairing_token") as keep):
            self.device.pair_ask()
            self.device.pair("r1", "123456", "cab")

        self.assertEqual(self.sent[1], (f"{SECURE}/api/v1/auth/pair", SHOWN))
        self.assertEqual(pinned_certificates.pinned("192.168.1.50", 8001), SHOWN)
        keep.assert_called_once_with("Bbbb222222", "tok")

    def test_a_token_already_held_is_not_sent_to_a_certificate_not_yet_trusted(self) -> None:
        carried: list[object] = []

        def post_json(url: str, payload=None, **kwargs) -> dict:
            carried.append(kwargs.get("headers"))
            return self.post_json(url, payload, **kwargs)

        with (patch.object(pinned_certificates, "trust_now", lambda _url: SHOWN),
              patch.object(http_client, "post_json", post_json),
              patch.object(access_keys, "pairing_token", lambda _id: "old"),
              patch.object(access_keys, "keep_pairing_token")):
            self.device.pair_ask()
            self.device.pair("r1", "123456", "cab")

        self.assertEqual(carried, [None, None])

    def test_no_pin_is_kept_when_the_trade_fails(self) -> None:
        def refuse(url: str, payload=None, **kwargs) -> dict:
            if url.endswith("/code"):
                return {"request_id": "r1"}
            raise requests.HTTPError("wrong code")

        with (patch.object(pinned_certificates, "trust_now", lambda _url: SHOWN),
              patch.object(http_client, "post_json", refuse)):
            self.device.pair_ask()
            with self.assertRaises(requests.HTTPError):
                self.device.pair("r1", "000000", "cab")

        self.assertEqual(pinned_certificates.pinned("192.168.1.50", 8001), "")

    def test_a_plain_http_install_pairs_as_it_always_did(self) -> None:
        plain = device_client.RemoteDevice("http://192.168.1.50:8001", "Bbbb222222")
        with (patch.object(http_client, "post_json", self.post_json),
              patch.object(access_keys, "keep_pairing_token")):
            plain.pair_ask()
            plain.pair("r1", "123456", "cab")

        self.assertEqual([trust for _url, trust in self.sent], [None, None])
        self.assertEqual(machine_secrets_pins(), None)

    def test_a_pasted_token_pins_what_the_address_shows_now(self) -> None:
        with (patch.object(pinned_certificates, "trust_now", lambda _url: SHOWN),
              patch.object(access_keys, "keep_pairing_token") as keep):
            self.device.pair_with_token("pasted")

        self.assertEqual(pinned_certificates.pinned("192.168.1.50", 8001), SHOWN)
        keep.assert_called_once_with("Bbbb222222", "pasted")

    def test_unpairing_forgets_the_pin_with_the_token(self) -> None:
        pinned_certificates.keep("192.168.1.50", 8001, SHOWN)

        with patch.object(access_keys, "forget_pairing_token") as forget:
            self.device.unpair()

        forget.assert_called_once_with("Bbbb222222")
        self.assertEqual(pinned_certificates.pinned("192.168.1.50", 8001), "")


class ProbingAnHttpsInstallTests(_HttpsInstall):
    def test_an_address_nothing_was_paired_with_is_asked_without_a_secret_unverified(
            self) -> None:
        asked: list[object] = []

        def get_json(url: str, **kwargs) -> dict:
            asked.append(kwargs.get("trust"))
            return {"vpinfe_version": "3.0"}

        with (patch.object(http_client, "get_json", get_json),
              patch.object(access_keys, "pairing_token", lambda _id: "")):
            self.device.probe()

        self.assertEqual(asked[0], pinned_certificates.UNVERIFIED)

    def test_a_paired_address_is_asked_over_its_pin(self) -> None:
        asked: list[object] = []

        def get_json(url: str, **kwargs) -> dict:
            asked.append(kwargs.get("trust"))
            return {}

        with (patch.object(http_client, "get_json", get_json),
              patch.object(access_keys, "pairing_token", lambda _id: "tok")):
            self.device.probe()

        self.assertEqual(asked[0], None)

    def test_a_changed_certificate_is_said_and_the_pair_door_stays_open(self) -> None:
        with patch.object(http_client, "get_json",
                          side_effect=service_errors.CertificateChangedError("x")):
            reach = self.device.probe()

        self.assertTrue(reach["certificate_changed"])
        self.assertFalse(reach["paired"])
        self.assertEqual(reach["state"], device_client.UNREACHABLE)
        self.assertEqual(reach["reason"], device_client.t("device.reason.certificate_changed"))

    def test_a_changed_certificate_is_not_taken_for_a_dead_machine(self) -> None:
        error = service_errors.CertificateChangedError("192.168.1.50")

        self.assertEqual(device_client.why_not(error), "device.reason.certificate_changed")


def machine_secrets_pins():
    from common.security import machine_secrets

    return machine_secrets.read().get(pinned_certificates.PINNED)


if __name__ == "__main__":
    unittest.main()

"""What one install asks of another: its displays, its browser, its input, its lifecycle.

One interface whether that device is this process or another machine. Narrow on purpose -
this is the surface that reaches across, which is what has to be authenticated once a device is
reachable over a network.

A remote device answers only the questions that have a route on its own API. The rest are
in-process by nature: nothing enumerates another machine's screens by asking politely,
and pretending otherwise would put a plausible wrong answer where a caller expected one.
Those raise rather than returning an empty list, because "no screens" and "not something
this device can be asked" are different facts and one of them is a bug.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NoReturn

from common.failures import why
from common.i18n import t
from common.security import pinned_certificates
from common.service_errors import CertificateChangedError

if TYPE_CHECKING:
    from common.config.config_store import ConfigStore

logger = logging.getLogger("vpinfe.common.devices.device_client")

# A probe runs once per device on a page load, so a machine that is off should cost a
# moment rather than the whole listing. Much shorter than the ordinary call timeout.
PROBE_SECONDS = 3


@dataclass(frozen=True)
class Display:
    """One screen, flattened out of whatever enumerated it: nothing can receive an
    NSScreen."""

    id: str
    x: int
    y: int
    width: int
    height: int



class LocalDevice:
    """The device in this process. Imports are deferred so that importing this module
    does not pull the frontend in - an install without that feature has none."""

    def displays(self) -> list[Display]:
        """This machine's screens as macOS reports them. Empty elsewhere, where the
        caller's own enumeration is already right."""
        import sys

        if not sys.platform.startswith("darwin"):
            return []
        try:
            from frontend.chromium_manager import get_mac_screens

            return [Display(id=f"Screen {index}", x=screen.x, y=screen.y,
                            width=screen.width, height=screen.height)
                    for index, screen in enumerate(get_mac_screens())]
        except Exception:
            logger.debug("Could not enumerate this machine's screens", exc_info=True)
            return []

    def show_numbers(self, screens: Sequence[Any]) -> None:
        """Put each screen's number and name on it for a few seconds. Blocking."""
        from frontend import screen_numbers

        screen_numbers.show(screens)

    def show_test_card(self, screen: Any, rotation: Any) -> None:
        """Put the test card on `screen`, turned by `rotation`, and leave it there until
        `close_test_card`. Blocking while it is placed."""
        from frontend import screen_card

        screen_card.show(screen, rotation)

    def turn_test_card(self, rotation: Any) -> None:
        from frontend import screen_card

        screen_card.turn(rotation)

    def keep_test_card(self) -> bool:
        """Say the card is still wanted, and whether it is still up."""
        from frontend import screen_card

        return screen_card.keep()

    def test_card_open(self) -> bool:
        from frontend import screen_card

        return screen_card.is_open()

    def close_test_card(self) -> None:
        from frontend import screen_card

        screen_card.close()

    def browser_path(self) -> str | None:
        """Which browser this device would open its windows with, or None."""
        try:
            from frontend.chromium_manager import get_chromium_path

            return get_chromium_path().path
        except Exception:
            return None

    def browser_in_use(self, configured: str | None = None) -> tuple[str, bool]:
        """The browser this device opens its windows with, and whether it is the bundled
        copy. ("", False) on an install with no frontend. `configured` stands in for the
        Browser Program setting; "" asks what it would be with none set."""
        try:
            from frontend.chromium_manager import get_chromium_path

            found = get_chromium_path(configured)
            return str(found.path or ""), not bool(found.using_local_install)
        except Exception:
            return "", False

    def has_bundled_browser(self) -> bool:
        """Whether this build ships a browser of its own. False on an install with no
        frontend."""
        try:
            from frontend.chromium_manager import has_bundled_browser

            return has_bundled_browser()
        except Exception:
            return False

    def google_chrome_path(self) -> str | None:
        """Where Google Chrome is installed on this machine, or None."""
        try:
            from frontend.chromium_manager import google_chrome_path

            return google_chrome_path()
        except Exception:
            return None

    def browser_options(self, **kwargs: Any) -> list[str]:
        """The flags this device would launch its browser with."""
        from frontend.chromium_manager import get_builtin_chromium_options

        return get_builtin_chromium_options(**kwargs)

    def browser_option_lines(self, added: list[str], **kwargs: Any) -> list[Any]:
        """The command line a window is started with, as `OptionLine`s - what a launch
        gives the browser, with where each option came from."""
        from frontend.chromium_manager import get_builtin_chromium_options, option_lines

        return option_lines(get_builtin_chromium_options(**kwargs), added)

    def parse_browser_options(self, raw: str) -> list[str]:
        from frontend.chromium_manager import parse_additional_chromium_options

        return parse_additional_chromium_options(raw)

    def browser_default_option_names(self) -> list[str]:
        """The names of the options VPinFE starts every window with, which are what a
        setting leaving one out is matched against. Empty on an install with no frontend."""
        try:
            from frontend.chromium_manager import CHROMIUM_BASE_FLAGS
        except Exception:
            logger.warning("The frontend's browser options could not be read", exc_info=True)
            return []
        return list(dict.fromkeys(flag.split("=")[0] for flag in CHROMIUM_BASE_FLAGS))


    def bindings(self, config: ConfigStore) -> dict[str, list[str]]:
        from frontend import input_api

        return input_api.get_bindings(config)

    def wants_confirmation(self, scope: str) -> bool:
        from frontend import lifecycle_host

        return lifecycle_host.wants_confirmation(scope)

    def request(self, scope: str, action: str, **kwargs: Any) -> bool:
        from frontend import lifecycle_host

        return lifecycle_host.request(scope, action, **kwargs)

    def probe(self) -> dict[str, Any]:
        """Always answering: this is the process being asked."""
        from common.vpinfe_version import get_version

        return {"state": ANSWERING, "version": get_version(), "reason": ""}


class TooOldError(RuntimeError):
    """That install does not serve this route.

    Its own error because it is not a failure: an install a release or two behind
    answers everything it knows about and 404s the rest, and a surface should say which
    of the two happened rather than showing somebody an HTTP status and a URL.
    """


def _own_name() -> str:
    """What this install is called, for the pairing code the other device shows."""
    from common.config.paths import get_ini_config
    from common.devices.install_identity import display_name

    return display_name(get_ini_config())


def _absent(exc: Exception) -> bool:
    """Whether a request failed because the route is not there."""
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", 0) == 404


class NotPairedError(RuntimeError):
    """That install turned the request away for want of a token it accepts.

    A state of the pairing rather than a failure: a device this install has not paired
    with refuses every question it is not open to, and asking again says nothing new.
    """


def _refused(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", 0) in (401, 403)


class NotThisDeviceError(RuntimeError):
    """Asked of a remote device something only the machine itself can answer."""


class NotVPinFEError(NotThisDeviceError):
    """Asked of a VPX Mobile device something only VPinFE answers."""


# What a probe found. `unreachable` and `unknown` are different answers: one means the
# asking got nothing, the other that there was nothing to ask - an entry with no
# port, which a device being switched off never causes and switching it on never fixes.
ANSWERING = "answering"
UNREACHABLE = "unreachable"
UNASKABLE = "unaskable"

# What a 404 from an install actually means. Not an error on either side: it answers
# everything it knows about and does not know about this.
TOO_OLD = "error.devices.older_vpinfe"


def _chain(exc: BaseException) -> list[BaseException]:
    """The exception and everything it wraps, followed through `reason` and `args` as
    well as `__cause__`, which is where requests keeps the socket's own error."""
    found: list[BaseException] = []
    waiting: list[object] = [exc]
    while waiting:
        one = waiting.pop()
        if not isinstance(one, BaseException) or any(one is seen for seen in found):
            continue
        found.append(one)
        waiting.extend([one.__cause__, one.__context__, getattr(one, "reason", None),
                        *one.args])
    return found


def why_not(exc: Exception) -> str:
    """The catalog key for why a device did not answer."""
    import requests

    if isinstance(exc, NotVPinFEError):
        return "device.reason.wrong_app"
    if any(isinstance(one, CertificateChangedError) for one in _chain(exc)):
        return "device.reason.certificate_changed"
    if isinstance(exc, (requests.Timeout, TimeoutError)):
        return "device.reason.timed_out"
    if any(isinstance(one, ConnectionRefusedError) for one in _chain(exc)):
        return "device.reason.refused"
    if isinstance(exc, (requests.HTTPError, ValueError)):
        return "device.reason.unreadable"
    return "device.reason.unreachable"


def _not_answering(state: str, key: str) -> dict[str, Any]:
    return {"state": state, "version": "", "reason": t(key), "reason_key": key}


def served(listing: list[dict[str, Any]]) -> list[str]:
    """The names in a capability listing that the install serves."""
    return [str(entry.get("name") or "") for entry in listing if entry.get("available")]


def probe(client: LocalDevice | RemoteDevice | MobileDevice | None) -> dict[str, Any]:
    """Whether a device answers, and what is on the other end.

    One shape for both kinds, because a row shows one of them at a time: `state`, and a
    `what` written for a person - the product and version for an install, and the bare
    fact of answering for a phone, which is all VPX Mobile's file listing proves.
    """
    if client is None:
        return _not_answering(UNASKABLE, "device.reason.no_port")
    try:
        return client.probe()
    except Exception as exc:  # noqa: BLE001 - not answering is an answer
        logger.debug("No answer from %s: %s", getattr(client, "base_url", ""), exc)
        return _not_answering(UNREACHABLE, why_not(exc))



class _Authed:
    """`http_client`'s JSON calls, each sent with the token paired with that device."""

    def __init__(self, device_id: str) -> None:
        self.device_id = device_id

    def _headers(self) -> dict[str, str] | None:
        from common.security import access_keys

        return access_keys.pairing_header(self.device_id)

    def get_json(self, url: str, **kwargs: Any) -> Any:
        from common.security import http_client

        return http_client.get_json(url, headers=self._headers(), **kwargs)

    def post_json(self, url: str, payload: Any = None, **kwargs: Any) -> Any:
        from common.security import http_client

        return http_client.post_json(url, payload, headers=self._headers(), **kwargs)

    def put_json(self, url: str, payload: Any, **kwargs: Any) -> Any:
        from common.security import http_client

        return http_client.put_json(url, payload, headers=self._headers(), **kwargs)


class RemoteDevice:
    """A device on another machine, reached over the API it announced itself from.

    Every call is a request that can time out, so a caller runs these off whatever loop
    it is on. Failures are the caller's to report: an install that cannot reach a device has
    something to say about it, and swallowing that here would leave a screen claiming a
    device is fine because nothing asked it.
    """

    def __init__(self, base_url: str, device_id: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.device_id = device_id
        self._http = _Authed(device_id)
        # What the address showed when it was asked for a code, so the pairing that follows
        # trusts that certificate and no other.
        self._candidate = ""

    def _url(self, path: str) -> str:
        return f"{self.base_url}/api/v1{path}"

    def displays(self) -> list[Display]:
        raise NotThisDeviceError("Another machine's screens cannot be enumerated from here")

    def browser_path(self) -> str | None:
        raise NotThisDeviceError("Another machine's browser cannot be read from here")

    def browser_options(self, **kwargs: Any) -> list[str]:
        raise NotThisDeviceError("Another machine's browser options cannot be read from here")

    def browser_option_lines(self, added: list[str], **kwargs: Any) -> list[Any]:
        raise NotThisDeviceError("Another machine's browser options cannot be read from here")

    def parse_browser_options(self, raw: str) -> list[str]:
        raise NotThisDeviceError("Another machine's browser options cannot be read from here")

    def browser_default_option_names(self) -> list[str]:
        raise NotThisDeviceError("Another machine's browser options cannot be read from here")

    def bindings(self, config: ConfigStore) -> dict[str, list[str]]:
        raise NotThisDeviceError("Another machine's input bindings cannot be read from here")

    def wants_confirmation(self, scope: str) -> bool:
        """False, because the question has already been put. A confirm belongs on the
        surface that asked, and that surface is not on this machine."""
        return False

    def request(self, scope: str, action: str, **kwargs: Any) -> bool:
        """Ask that machine to do it, through the same call a local one takes.

        Closing a table keeps its own route because that is where play lives; everything
        else goes to the actions route. One method either way, so a surface that offers
        these is one code path rather than a local and a remote copy.
        """
        from common.runtime import lifecycle

        if (scope, action) == (lifecycle.TABLE, lifecycle.STOP):
            answer = self._http.post_json(self._url("/play/stop")) or {}
            return bool(answer.get("stopped"))
        if (scope, action) not in lifecycle.offered():
            raise NotThisDeviceError(f"No route on a device for {action} the {scope}")
        return bool(self.perform_action(
            scope, action, str(kwargs.get("reason") or "")).get("performed"))

    def perform_action(self, scope: str, action: str,
                       reason: str = "") -> dict[str, Any]:
        """Do one of them over there, and say what was asked for. The install answering
        already put the question to nobody - an HTTP caller confirms with its own user
        before calling, which is what the API origin means."""
        return dict(self._http.post_json(
            self._url("/actions"),
            {"scope": scope, "action": action,
             "reason": reason or "asked from another install"}) or {})

    def actions(self) -> list[dict[str, Any]]:
        """What that install says it can be asked to do and performs."""
        try:
            said = self._http.get_json(self._url("/actions")) or {}
        except Exception as exc:
            if _absent(exc):
                raise TooOldError(t(TOO_OLD)) from exc
            if _refused(exc):
                raise NotPairedError(why(exc)) from exc
            raise
        return list(said.get("actions") or [])

    def frontend_browser(self) -> dict[str, Any]:
        """What that device's frontend browser plays."""
        try:
            return dict(self._http.get_json(self._url("/frontend/browser")) or {})
        except Exception as exc:
            if _absent(exc):
                raise TooOldError(t(TOO_OLD)) from exc
            if _refused(exc):
                raise NotPairedError(why(exc)) from exc
            raise

    def logs(self, limit: int = 200, level: str = "",
             contains: str = "") -> dict[str, Any]:
        """Recent records from that machine's own log. Answered by the install holding
        the file, which is the only party that has it."""
        from urllib.parse import urlencode

        query = urlencode({"limit": limit, "level": level, "contains": contains})
        try:
            return dict(self._http.get_json(f"{self._url('/logs')}?{query}") or {})
        except Exception as exc:
            if _absent(exc):
                raise TooOldError(t(TOO_OLD)) from exc
            raise

    def update_check(self, refresh: bool = False) -> dict[str, Any]:
        asked = self._url("/update") + ("?refresh=true" if refresh else "")
        return dict(self._http.get_json(asked) or {})

    def play_state(self) -> dict[str, Any]:
        return dict(self._http.get_json(self._url("/play/state")) or {})

    def perform_update(self, *, stop_table: bool = False) -> dict[str, Any]:
        return dict(self._http.post_json(
            self._url("/update"), {"stop_table": stop_table}) or {})

    def config_schema(self) -> list[dict[str, Any]]:
        """What that install says its settings are. Read from the device rather than
        assumed, so one running a newer build offers what it actually has."""
        return list((self._http.get_json(self._url("/config/schema"))
                     or {}).get("sections") or [])

    def config_values(self) -> dict[str, Any]:
        return dict(self._http.get_json(self._url("/config/settings")) or {})

    def put_launcher(self, launcher_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Write one whole launcher to another machine, under the id it already has.

        The id is kept on purpose: that is what makes the same launcher exist on every
        cabinet under one name, and what lets a mapping mean the same thing everywhere.
        Renumbering on the way in would break every mapping that travelled with it.
        """
        from common.devices import launcher_wire

        held = [one for one in self._http.get_json(self._url("/config/launchers")) or []
                if isinstance(one, dict)]
        return launcher_wire.from_item(dict(self._http.put_json(
            self._url(f"/config/launchers/{launcher_id}"),
            launcher_wire.to_item(launcher_id, body, held)) or {}))

    def put_launcher_settings_file(self, launcher_id: str, data: bytes) -> dict[str, Any]:
        """Send the settings file a launcher plays with, after the launcher itself. That
        install keeps it in its own settings folder; a path from this one would name
        nothing there."""
        try:
            return dict(self._http.post_json(
                self._url(f"/launchers/{launcher_id}/settings-file/received"),
                {"data": base64.b64encode(data).decode("ascii")}) or {})
        except Exception as exc:
            if _absent(exc):
                raise TooOldError(t(TOO_OLD)) from exc
            raise

    def put_launcher_mapping(self, table_id: str, launcher_id: str) -> dict[str, Any]:
        """Point a table on another machine at a launcher. Table ids travel because they
        live in the `.info`, so the same id names the same table on both."""
        return dict(self._http.put_json(self._url(f"/launchers/mappings/{table_id}"),
                                         {"launcher_id": launcher_id}) or {})

    def put_config(self, changes: dict[str, Any]) -> dict[str, Any]:
        """Write to another machine's config. The capability model allows it and this
        is the first thing to exercise it."""
        return dict((self._http.put_json(self._url("/config"), {"settings": changes})
                     or {}).get("settings") or {})

    def paired(self) -> bool:
        """Whether the token this install holds for that device is one it still accepts.

        Discovery answers anyone, so it proves nothing about this. False without a token,
        and False where the device turns the token away (it was revoked there)."""
        from common.security import access_keys

        if not access_keys.pairing_token(self.device_id):
            return False
        try:
            self._http.get_json(self._url("/play/state"), timeout=PROBE_SECONDS)
        except Exception as exc:  # noqa: BLE001 - only a refusal means unpaired
            return getattr(getattr(exc, "response", None), "status_code", 0) not in (401, 403)
        return True

    def pair_ask(self) -> str:
        """Ask that device to show a pairing code on its own screen. Its request id.

        Over HTTPS this takes the certificate the address shows now, ignoring any pinned
        before: pairing again is how a changed certificate is trusted."""
        from common.security import http_client

        self._candidate = pinned_certificates.trust_now(self.base_url)
        # Sent bare: whatever token is held belongs to the certificate that was paired, and
        # this one has not been trusted yet.
        said = http_client.post_json(self._url("/auth/pair/code"), {"name": _own_name()},
                                     trust=self._candidate or None) or {}
        return str(said.get("request_id") or "")

    def pair(self, request_id: str, code: str, name: str) -> None:
        """Trade the code read off that screen for a Full Control token, and keep it along
        with the certificate that asked for the code."""
        from common.security import access_keys, http_client

        said = http_client.post_json(
            self._url("/auth/pair"),
            {"request_id": request_id, "code": code, "name": name},
            trust=self._candidate or None) or {}
        pinned_certificates.adopt(self.base_url, self._candidate)
        access_keys.keep_pairing_token(self.device_id, str(said.get("secret") or ""))

    def pair_with_token(self, token: str) -> None:
        """Keep a token made on that install, with the certificate its address shows now."""
        from common.security import access_keys

        pinned_certificates.adopt(self.base_url, pinned_certificates.trust_now(self.base_url))
        access_keys.keep_pairing_token(self.device_id, token)

    def unpair(self) -> None:
        from common.security import access_keys

        access_keys.forget_pairing_token(self.device_id)
        found = pinned_certificates.split(self.base_url)
        if found is not None:
            pinned_certificates.forget(*found)

    def _probe_trust(self) -> str | None:
        """How to meet an https address that nothing was paired with: unverified, because
        the call carries no token and only asks who is there."""
        from common.security import access_keys

        found = pinned_certificates.split(self.base_url)
        if (found is None or access_keys.pairing_token(self.device_id)
                or pinned_certificates.pinned(*found)):
            return None
        return pinned_certificates.UNVERIFIED

    def probe(self) -> dict[str, Any]:
        """Discovery, which is the cheapest call that proves who answered.

        Short timeout on purpose: this runs once per device on a page load, and a
        machine that is off should cost a moment rather than the whole listing.
        """
        try:
            said = dict(self._http.get_json(self._url(""), timeout=PROBE_SECONDS,
                                            trust=self._probe_trust()) or {})
        except CertificateChangedError:
            return {**_not_answering(UNREACHABLE, "device.reason.certificate_changed"),
                    "paired": False, "certificate_changed": True}
        return {"state": ANSWERING,
                "paired": self.paired(),
                "version": str(said.get("vpinfe_version") or ""),
                "features": list(said.get("features") or []),
                # Already in this response, so asking again would be a second call for
                # something we are holding. Names only: the descriptions are the biggest
                # part of the payload and the caller has its own copy of them.
                "capabilities": served(said.get("capabilities") or []),
                "install_id": str(said.get("install_id") or ""),
                "display_name": str(said.get("display_name") or ""),
                "reason": ""}


class MobileDevice:
    """A phone running VPX Mobile, which is not VPinFE and never answers as one.

    It speaks the file transfer protocol the 2.x Mobile page sends over and nothing
    else, so the questions that can be put to it are a much shorter list - and the ones it
    cannot answer raise rather than returning a shape that looks like an answer.
    """

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def __getattr__(self, name: str) -> Callable[..., NoReturn]:
        def refuse(*_args: Any, **_kwargs: Any) -> NoReturn:
            raise NotVPinFEError(f"A VPX Mobile device does not answer {name}")
        return refuse

    def probe(self) -> dict[str, Any]:
        """Its file listing, which is the only thing it offers that proves it is there.

        There is no version to report: the protocol carries no identity.
        """
        from common.security import http_client

        http_client.get_json(f"{self.base_url}/files", timeout=PROBE_SECONDS)
        return {"state": ANSWERING, "version": "", "reason": ""}


_local = LocalDevice()


def local() -> LocalDevice:
    """The device in this process. A function, not the instance, so reaching a remote
    device later changes which device is asked and not how."""
    return _local


def for_device(
        device: dict[str, Any],
        local_device_id: str | None = None
) -> LocalDevice | RemoteDevice | MobileDevice | None:
    """The client for one registry entry, local or remote.

    A device is this process when its id is the one this install answers to. Everything
    else needs both halves of an address - a device that announced itself from before
    ports were recorded has no port, and there is nothing to dial.
    """
    from common.devices import device_registry

    device_id = str(device.get("device_id") or "")
    if local_device_id and device_id == local_device_id:
        return local()

    base_url = device_registry.install_url(device)
    if not base_url:
        return None
    # By kind, because these speak different protocols entirely: a phone runs VPX Mobile
    # and has never heard of /api/v1, so asking it as though it had would report every
    # phone as unreachable.
    if str(device.get("kind") or "") == device_registry.KIND_VPX_MOBILE:
        return MobileDevice(base_url)
    return RemoteDevice(base_url, device_id)

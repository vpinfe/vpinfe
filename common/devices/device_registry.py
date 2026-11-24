"""The devices an install knows about.

Keyed by `device_id`, which is the only thing about a device that never changes: a
display name is meant to be renamed and an address moves with DHCP, so neither can be
the key. Follows `common/games/collection_store.py` - a small JSON file, written whole
and atomically, carrying its own schema version.

For a VPinFE install `device_id` *is* its `install_id`, which is what makes attribution
work: an event carries the `install_id` it happened on, and that value finds the entry.
They are two names because they answer different questions - `install_id` is what an
installation calls itself, `device_id` is what this registry files it under - and because a
device that is not an install has the second and never the first.

This file is state, not config: it sits in the state folder, and `self` names the row that
is this install. A fresh state folder has no `self`, so it mints one - which is what keeps an
image that copies the config folder from cloning an identity. The row carries what VPinFE
keeps about this install, flat, through `self_get` and `set_self`.

A VPX Mobile device cannot announce itself, so a person types it in and it travels with the
setup: its name, address and port are an item of `mobile_devices` in `vpinfe.json`. Here it has
a row of state only - when it last answered and what its browser plays - filed under the same
id. `devices()` joins the two, and a row whose item is gone is not a device.

Data only. Routing a launch to a chosen device, aggregating state across devices and
resolving conflicts between them are separate decisions, and none of them are needed to
tell one device from another.
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from common.atomic_write import write_atomic
from common.config import config_schema
from common.config.config_store import ConfigStore
from common.config.paths import STATE_DIR, get_ini_config
from common.devices.install_identity import mint_id
from common.i18n import t
from common.security import access_keys, pinned_certificates
from common.timestamps import utc_now_iso

logger = logging.getLogger("vpinfe.common.devices.device_registry")

DEVICE_REGISTRY_PATH = STATE_DIR / "devices.json"
SCHEMA = 1
SCHEMA_KEY = "schema"
SELF_KEY = "self"
DEVICES_KEY = "devices"
MOBILE_DEVICES_KEY = "mobile_devices"
DEFAULT_MOBILE_PORT = 2112

# What a device is, as a closed set. A VPinFE install runs our code and answers for
# itself; a phone running VPX Mobile never does, and the registry holds everything known
# about it. Closed because a consumer switches on this - an unrecognized value would
# reach a UI as a device it has no idea how to talk to.
KIND_VPINFE = "vpinfe"
KIND_VPX_MOBILE = "vpx_mobile"
KINDS = (KIND_VPINFE, KIND_VPX_MOBILE)


def mint_device_id() -> str:
    """An id for a device that cannot offer one. Same generator install ids use, so the
    two are indistinguishable and a device that later gains an install id is not a new
    device. Minted once, when the device is added, and never again."""
    return mint_id()


def browser_said(report: dict[str, Any]) -> dict[str, Any]:
    """What an entry keeps of a `GET /frontend/browser` answer: `state`, `name`,
    `formats`, `checked_at` and, where the device said, `library`: an install id, or empty
    where the library it plays did not answer. An answer from a version that predates it has
    none. Empty where the answer has no state."""
    if not report.get("state"):
        return {}
    formats = report.get("formats")
    library = report.get("library")
    return {"state": str(report["state"]), "name": str(report.get("browser") or ""),
            "formats": {str(key)[:16]: value
                        for key, value in list(formats.items())[:32]
                        if value is None or isinstance(value, bool)}
            if isinstance(formats, dict) else {},
            "checked_at": utc_now_iso(),
            **({"library": library[:64]} if isinstance(library, str) else {})}


def address_problem(address: str) -> str:
    """Why this is not an address to reach a device at, or "" where it is one."""
    address = (address or "").strip()
    if not address:
        return t("error.devices.address_needed")
    if "/" in address or ":" in address:
        return t("error.devices.address_invalid")
    return ""


def _refuse_bad_mobile_devices(items: list[dict[str, Any]], _held: list[dict[str, Any]]) -> None:
    for item in items:
        if problem := address_problem(str(item.get("address") or "")):
            raise config_schema.ConfigListError(problem)


config_schema.register_validator(MOBILE_DEVICES_KEY, _refuse_bad_mobile_devices)


def _as_port(raw: Any) -> int:
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def _known_kind(raw: Any) -> str:
    value = str(raw or "").strip()
    return value if value in KINDS else KIND_VPINFE


@dataclass(frozen=True)
class Device:
    """One device this install has seen.

    `display_name` and `features` are what that install last reported, cached so a registry
    can be read without asking every device. They go stale by design - the install owns
    them, this is a copy.
    """

    device_id: str
    kind: str = KIND_VPINFE
    display_name: str = ""
    features: tuple[str, ...] = ()
    address: str = ""
    # Declared by the device, because the socket the address is read off says where a
    # request came from and never what that machine listens on. 0 means it did not say -
    # an entry written before installs sent one, or a device that cannot be dialed back.
    port: int = 0
    # Whether it announced that it answers HTTPS. Moving between the two drops the pairing,
    # since a token paired over HTTPS must not follow the install onto plain HTTP.
    https: bool = False
    first_seen: str = ""
    # When it last announced itself, which is once per startup. An install that has been
    # up for a week said so a week ago, so this is not how recently it was there.
    last_seen: str = ""
    # When it was last known to be there, by either route - it announced, or this install
    # asked and got an answer. The one that means "available", and the only one of the
    # three that a device being switched off ever stops advancing.
    last_reachable: str = ""
    # What its frontend's browser plays, and the install whose library that frontend plays
    # (`library`, absent until the device is on a version that says), as `browser_said` keeps
    # it. Empty where it runs no frontend or has never said.
    browser: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"device_id": self.device_id, "kind": self.kind,
                "display_name": self.display_name,
                "features": list(self.features), "address": self.address, "port": self.port,
                "first_seen": self.first_seen, "last_seen": self.last_seen,
                "last_reachable": self.last_reachable,
                **({"https": True} if self.https else {}),
                **({"browser": dict(self.browser)} if self.browser else {}),
                **self.extra}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Device | None:
        device_id = str(raw.get("device_id", "") or "").strip()
        if not device_id:
            return None
        known = {"device_id", "kind", "display_name", "features", "roles", "address", "port",
                 "first_seen", "last_seen", "last_reachable", "browser", "https"}
        browser = raw.get("browser")
        # `roles` is what entries written before the feature model called this. Read
        # rather than migrated: the words differ too - one that said `hub` meant the
        # library and the device list - and this is a cache the install refreshes itself,
        # so a stale entry corrects on its next announcement rather than on a rewrite.
        declared = raw.get("features") or raw.get("roles") or []
        return cls(
            device_id=device_id,
            # An entry stored before kind existed, or by a build that knows a kind this
            # one does not: read as vpinfe rather than dropped, because the entry is
            # still a real device and losing it is worse than mislabelling it.
            kind=_known_kind(raw.get("kind")),
            display_name=str(raw.get("display_name", "") or ""),
            features=tuple(str(f) for f in declared if str(f).strip()),
            address=str(raw.get("address", "") or ""),
            port=_as_port(raw.get("port")),
            https=raw.get("https") is True,
            first_seen=str(raw.get("first_seen", "") or ""),
            last_seen=str(raw.get("last_seen", "") or ""),
            last_reachable=str(raw.get("last_reachable", "") or ""),
            browser=dict(browser) if isinstance(browser, dict) else {},
            # Anything a newer build wrote is carried through rather than dropped, so a
            # downgrade does not silently strip fields it does not understand.
            extra={k: v for k, v in raw.items() if k not in known},
        )


class DeviceRegistry:
    """Every device this install knows, read and written whole.

    The hand-entered devices are read from `vpinfe.json`: this install's own where there is
    no `path`, and the one beside `path` where there is, so a registry of its own comes with
    a config of its own.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else DEVICE_REGISTRY_PATH
        self._own_config = path is None
        self._config: ConfigStore | None = None
        self._lock = threading.RLock()

    def config(self) -> ConfigStore:
        """Where the hand-entered devices are listed."""
        if self._own_config:
            return get_ini_config()
        if self._config is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._config = ConfigStore(str(self.path.parent / "vpinfe.json"))
        else:
            self._config.reread()
        return self._config

    def _mobile_items(self) -> list[dict[str, Any]]:
        if (not self._own_config and self._config is None
                and not (self.path.parent / "vpinfe.json").exists()):
            return []
        return [item for item in self.config().get_list(MOBILE_DEVICES_KEY)
                if isinstance(item, dict) and str(item.get("id") or "").strip()]

    # -- reading -------------------------------------------------------------

    def devices(self) -> list[Device]:
        """Every device: the installs it has heard from, oldest first, then the mobile
        devices in the order they are listed. An unreadable file is an empty registry, never
        an error: an install with no devices is the normal case, and so is a first run."""
        with self._lock:
            return self._load()

    def get(self, device_id: str) -> Device | None:
        wanted = (device_id or "").strip()
        if not wanted:
            return None
        return next((p for p in self.devices() if p.device_id == wanted), None)

    def knows(self, device_id: str) -> bool:
        return self.get(device_id) is not None

    # -- writing -------------------------------------------------------------

    def record(self, device_id: str, *, kind: str = "", display_name: str = "",
               features: Iterable[str] = (), address: str = "",
               port: int = 0, https: bool | None = None,
               heard: bool = True) -> Device | None:
        """Note that a device exists, or that a known one has been heard from.

        `first_seen` is kept from the existing entry: a device is the same device
        however many times it reconnects. Everything else is refreshed, because the
        install owns those and this is only a copy of what it last said.

        A paired device that moves to another address or port, or between HTTP and HTTPS,
        loses its pairing token and the certificate pinned for it: the token is sent wherever
        the entry points, and a caller must not be able to point it at itself or talk it down
        to plain HTTP. `https` is None where the caller does not know, which leaves it as it was.

        `heard` is false where a person wrote the entry and nothing has answered: it
        leaves `last_seen` and `last_reachable` as they were, blank for a new one.

        `kind` defaults to empty rather than to vpinfe so that a caller updating only an
        address does not restate it. A phone is written once by a person and updated
        afterwards by whatever learns where it is; defaulting would turn it back into an
        install on that second write, silently.
        """
        wanted = (device_id or "").strip()
        if not wanted:
            logger.debug("Ignoring a registry entry with no device id")
            return None
        if kind == KIND_VPX_MOBILE:
            raise ValueError(t("error.devices.mobile_elsewhere"))

        now = utc_now_iso()
        with self._lock:
            devices = {p.device_id: p for p in self._stored_rows()}
            existing = devices.get(wanted)
            if wanted in {str(item["id"]).strip() for item in self._mobile_items()}:
                raise ValueError(t("error.devices.mobile_elsewhere"))
            if (existing is not None and (address and address != existing.address
                                          or port and port != existing.port
                                          or https is not None and https != existing.https)):
                _drop_pairing(existing)
            devices[wanted] = Device(
                device_id=wanted,
                kind=kind or (existing.kind if existing else KIND_VPINFE),
                display_name=display_name or (existing.display_name if existing else ""),
                features=(tuple(str(f) for f in features)
                          or (existing.features if existing else ())),
                address=address or (existing.address if existing else ""),
                port=port or (existing.port if existing else 0),
                https=(existing.https if existing else False) if https is None else https,
                first_seen=existing.first_seen if existing and existing.first_seen else now,
                last_seen=now if heard else (existing.last_seen if existing else ""),
                # The two timestamps differ in what moves them, not in what they prove:
                # this one advances from either direction, and `last_seen` only when it
                # pushed. Neither moves for an entry nobody has heard from (`heard`).
                last_reachable=now if heard else (existing.last_reachable if existing else ""),
                browser=existing.browser if existing else {},
                extra=existing.extra if existing else {},
            )
            self._save(list(devices.values()))
            if existing is None:
                logger.info("DeviceRegistry: new device %s (%s)", wanted, display_name or "unnamed")
            return devices[wanted]

    def record_reachable(self, device_id: str, *, when: str = "",
                         browser: dict[str, Any] | None = None) -> Device | None:
        """Note that this install got an answer out of that device just now.

        The pull half. A device announcing itself is the push half and `record` writes
        the same field, because both prove the same thing - that it was there. What
        differs is who asked: an install announces itself, and this one can ask
        any time it wants to know, which is what stops the answer aging for a week while
        the machine sits there running.

        `browser` replaces what is held, `{}` clearing it; None keeps it.
        """
        wanted = (device_id or "").strip()
        if not wanted:
            return None
        with self._lock:
            known = {p.device_id: p for p in self._load()}
            existing = known.get(wanted)
            if existing is None:
                # Probing something not in the registry is a caller bug, not a discovery
                # path: an entry is created by announcing or by a person adding one.
                return None
            seen = when or utc_now_iso()
            # A mobile device never announces, so the answer to a probe is the only sighting.
            mobile = existing.kind == KIND_VPX_MOBILE
            changed = replace(
                existing, first_seen=existing.first_seen or seen,
                last_seen=seen if mobile else existing.last_seen,
                last_reachable=seen,
                browser=existing.browser if browser is None else dict(browser))
            # Every mobile device a person has removed leaves its row here until a write
            # like this one, which is the only writer that has read the list.
            gone = {p.device_id for p in self._stored_rows()
                    if p.kind == KIND_VPX_MOBILE and p.device_id not in known}
            rows = {p.device_id: p for p in self._stored_rows() if p.device_id not in gone}
            rows[wanted] = changed
            self._save(list(rows.values()))
            return changed

    def forget(self, device_id: str) -> bool:
        """Drop a device. Returns whether there was one to drop - never for this install,
        whose row holds what it keeps about itself, and never for a mobile device, which is
        removed from `mobile_devices`."""
        wanted = (device_id or "").strip()
        with self._lock:
            if wanted and wanted == self._stored_self():
                return False
            devices = self._stored_rows()
            remaining = [p for p in devices
                         if p.device_id != wanted or p.kind == KIND_VPX_MOBILE]
            if len(remaining) == len(devices):
                return False
            for gone in devices:
                if gone.device_id == wanted:
                    _drop_pairing(gone)
            self._save(remaining)
            logger.info("DeviceRegistry: forgot device %s", wanted)
            return True

    # -- this install ----------------------------------------------------------

    def self_id(self) -> str:
        """This install's id, or "" until `ensure_self` has minted one. Never writes."""
        with self._lock:
            return self._stored_self()

    def ensure_self(self) -> str:
        """This install's id, minting and saving one if the file has none. An id that is
        not on disk is not an identity: the next start would mint another."""
        with self._lock:
            existing = self._stored_self()
            if existing:
                return existing
            minted = mint_device_id()
            self._save(self._stored_rows(), self_id=minted)
            self.record(minted, kind=KIND_VPINFE, heard=False)
            logger.info("Minted install id %s", minted)
            return minted

    def self_get(self, name: str, default: Any = None) -> Any:
        """One field of this install's row, `default` where it is not there. Never writes."""
        with self._lock:
            row = next((p for p in self._stored_rows()
                        if p.device_id == self._stored_self()), None)
            return default if row is None else row.as_dict().get(name, default)

    def set_self(self, **fields: Any) -> None:
        """Write fields on this install's row, minting the id first if there is none.

        The one writer for what an install keeps about itself. A value of None removes
        the field, so a key never holds a stored "nothing".
        """
        with self._lock:
            wanted = self.ensure_self()
            devices = {p.device_id: p for p in self._stored_rows()}
            row = devices[wanted].as_dict()
            for name, value in fields.items():
                if value is None:
                    row.pop(name, None)
                else:
                    row[name] = value
            changed = Device.from_dict(row)
            if changed is not None:
                devices[wanted] = changed
                self._save(list(devices.values()))

    def _stored_payload(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _stored_self(self) -> str:
        return str(self._stored_payload().get(SELF_KEY) or "").strip()

    # -- storage -------------------------------------------------------------

    def _load(self) -> list[Device]:
        installs = [device for device in self._stored_rows() if device.kind != KIND_VPX_MOBILE]
        state = {device.device_id: device for device in self._stored_rows()
                 if device.kind == KIND_VPX_MOBILE}
        mobile = [replace(state.get(item["id"]) or Device(device_id=item["id"]),
                          kind=KIND_VPX_MOBILE,
                          display_name=str(item.get("name") or ""),
                          address=str(item.get("address") or ""),
                          port=_as_port(item.get("port")) or DEFAULT_MOBILE_PORT)
                  for item in self._mobile_items()]
        return installs + mobile

    def _stored_rows(self) -> list[Device]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("DeviceRegistry at %s is unreadable; treating it as empty", self.path)
            return []
        raw = payload.get(DEVICES_KEY) if isinstance(payload, dict) else payload
        if not isinstance(raw, list):
            return []
        return [device for device in (Device.from_dict(entry) for entry in raw
                                      if isinstance(entry, dict)) if device is not None]

    def _save(self, devices: list[Device], self_id: str | None = None) -> None:
        # Never stamp a newer file down to what this build writes - that number belongs
        # to whichever VPinFE wrote it, the same rule the config store follows.
        #
        # `self` is read back rather than held: every other method here reads the file
        # fresh, so a save that never minted would drop the id.
        payload = {SCHEMA_KEY: SCHEMA,
                   SELF_KEY: self._stored_self() if self_id is None else self_id,
                   DEVICES_KEY: [_stored_row(device) for device in devices]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._keep_what_cannot_be_read()
        write_atomic(self.path,
                     lambda handle: json.dump(payload, handle, indent=2, ensure_ascii=False))

    def _keep_what_cannot_be_read(self) -> None:
        """Before a save replaces the file: a file that is not JSON is copied aside first,
        and one that cannot be opened raises, so a save never writes over what it could not
        read. Either would otherwise take this install's id with it."""
        if not self.path.exists():
            return
        try:
            json.loads(self.path.read_text(encoding="utf-8"))
        except ValueError:
            stamp = time.strftime("%Y%m%dT%H%M%S")
            kept = self.path.with_name(f"{self.path.name}.unreadable-{stamp}")
            shutil.copy2(self.path, kept)
            logger.warning("DeviceRegistry at %s is not JSON; kept a copy at %s", self.path, kept)


def _stored_row(device: Device) -> dict[str, Any]:
    """What the state file keeps of a device. A mobile device's name, address and port are
    in `mobile_devices`; copying them here would leave two answers to one question."""
    row = device.as_dict()
    if device.kind == KIND_VPX_MOBILE:
        for name in ("display_name", "features", "address", "port"):
            row.pop(name, None)
    return row


_registry: DeviceRegistry | None = None


def get_device_registry() -> DeviceRegistry:
    """This install's registry. One per process."""
    global _registry
    if _registry is None:
        _registry = DeviceRegistry()
    return _registry


def reset_for_tests(path: Path | None = None) -> None:
    global _registry
    _registry = DeviceRegistry(path) if path is not None else None


def _drop_pairing(device: Device) -> None:
    """The token and the pinned certificate kept for a device, which a row no longer
    describes once it moves or is forgotten."""
    access_keys.forget_pairing_token(device.device_id)
    if device.address and device.port:
        pinned_certificates.forget(device.address, device.port)


def keep_own_row(incoming: Path, current: Path, out: Path) -> None:
    """Write to `out` the registry in `incoming` with `self` and this install's own row
    taken from `current`, leaving `incoming` as it is.

    A backup taken on another cabinet must not make this one that cabinet. Where `current`
    names no install, the incoming `self` is dropped rather than adopted, so the next start
    mints one.
    """
    kept = DeviceRegistry(current)
    mine = kept.self_id()
    own = next((row for row in kept._stored_rows() if row.device_id == mine), None)
    payload = DeviceRegistry(incoming)._stored_payload()
    rows = [row for row in payload.get(DEVICES_KEY) or []
            if isinstance(row, dict) and row.get("device_id") != mine]
    if own is not None:
        rows.append(own.as_dict())
    payload[DEVICES_KEY] = rows
    if mine:
        payload[SELF_KEY] = mine
    else:
        payload.pop(SELF_KEY, None)
    write_atomic(out, lambda handle: json.dump(payload, handle, indent=2, ensure_ascii=False))


def paired_header(url: str) -> dict[str, str] | None:
    """The token paired with the install this address reaches, if this install has paired
    with it. Matched on host, port and scheme: a token paired over HTTPS is never sent in the
    clear, whatever the address."""
    parts = urlsplit(url)
    host, port = parts.hostname or "", parts.port or (443 if parts.scheme == "https" else 80)
    for device in get_device_registry().devices():
        if (device.address == host and device.port == port
                and parts.scheme == ("https" if device.https else "http")):
            return access_keys.pairing_header(device.device_id)
    return None


def install_url(device: dict[str, Any]) -> str:
    """Where that install answers, over the scheme it announced, or "" where the entry is
    missing half an address."""
    address = str(device.get("address") or "").strip()
    port = _as_port(device.get("port"))
    if not address or not port:
        return ""
    return f"{'https' if device.get('https') is True else 'http'}://{address}:{port}"

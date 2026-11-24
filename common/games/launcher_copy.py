"""Copying a launcher to another machine.

A copy, not shared storage. Launchers stay per install because what one names is a
program on one machine, and sharing is what you do when several cabinets are built the
same way.

**The copy keeps the id.** That is the whole trick: ids come from the generator every id
in this project uses, so they do not collide across machines, and preserving one means
the same launcher exists on every cabinet under one name and a mapping means the same
thing everywhere. Renumbering on the way in would break every mapping that travelled
with it.

**One way, with no ongoing link.** Edit a cabinet's launcher afterwards and it diverges,
and nothing here reconciles that. Real sync means conflict resolution, change tracking
and a rule about which side wins; growing that accidentally is worse than designing it.

A path that does not apply to the target announces itself: that machine's own path check
marks the program missing and its trouble badge lights, which is the machinery that
already exists for a launcher pointing at nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from common.failures import why
from common.games import launcher_settings
from common.i18n import t

logger = logging.getLogger("vpinfe.common.games.launcher_copy")


class LocalWrites:
    """The two launcher writes against this install's own store.

    Here rather than on `device_client.LocalDevice` because that module is
    infrastructure and may not reach into a domain package - and a launcher is one. It
    gives `copy_to` the same two methods a remote device answers, so copying to the
    machine you are standing at is not a special case.
    """

    def put_launcher(self, launcher_id: str, body: dict[str, Any]) -> dict[str, Any]:
        from common.games import launcher_ops

        return launcher_ops.put(launcher_id, body)

    def put_launcher_settings_file(self, launcher_id: str, data: bytes) -> dict[str, Any]:
        from common.games import launcher_settings

        return launcher_settings.receive_file(launcher_id, data)

    def put_launcher_mapping(self, table_id: str, launcher_id: str) -> dict[str, Any]:
        from common.games import launchers

        launchers.get_launcher_store().assign(table_id, launcher_id)
        return {"table_id": table_id, "launcher_id": launcher_id}


class LauncherWriter(Protocol):
    """What copying needs of a device client: the two writes it makes.

    Named rather than taken as whatever the caller hands over, because `client_for`
    decides how a machine is reached and this decides nothing about that.
    """

    def put_launcher(self, launcher_id: str, body: dict[str, Any]) -> dict[str, Any]: ...

    def put_launcher_settings_file(self, launcher_id: str,
                                   data: bytes) -> dict[str, Any]: ...

    def put_launcher_mapping(self, table_id: str,
                             launcher_id: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class Outcome:
    """What happened for one device. `error` is empty where it worked; `reason` is the
    device's own answer, or `why(exc)`.

    Reported per device rather than as one verdict: copying to three cabinets and having
    the second one asleep is a partial success, and a caller that says only "failed"
    would send somebody to check all three.
    """

    device_id: str
    name: str
    launchers: int = 0
    mappings: int = 0
    error: str = ""
    reason: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def copy_to(devices: Iterable[dict[str, Any]],
            launchers_to_send: Iterable[dict[str, Any]],
            mappings: dict[str, str] | None = None, *,
            client_for: Callable[[dict[str, Any]], LauncherWriter]) -> list[Outcome]:
    """Send launchers, and optionally the tables that name them, to each device.

    `client_for` builds the client for a device, so this does not decide how a machine is
    reached - the same call works for the install you are standing at and for one across
    the network.

    Mappings are filtered to the launchers actually being sent. A mapping naming a
    launcher the target does not have is dropped on read at the far end anyway, so
    sending one is a write that quietly does nothing.
    """
    sending = list(launchers_to_send)
    ids = {one["launcher_id"] for one in sending}
    wanted = {table: to for table, to in (mappings or {}).items() if to in ids}

    found = []
    for device in devices:
        found.append(_send(device, sending, wanted, client_for))
    return found


def _send(device: dict[str, Any], sending: list[dict[str, Any]],
          mappings: dict[str, str],
          client_for: Callable[[dict[str, Any]], LauncherWriter]) -> Outcome:
    name = str(device.get("display_name") or device.get("device_id") or "?")
    device_id = str(device.get("device_id") or "")
    try:
        client = client_for(device)
    except Exception as exc:  # noqa: BLE001 - a device that cannot be addressed is news
        logger.warning("Could not reach %s: %s", name, exc)
        return Outcome(device_id, name, error=t("said.not_reached"), reason=why(exc))

    sent = 0
    for one in sending:
        try:
            client.put_launcher(one["launcher_id"], _body(one))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not copy launcher %s to %s: %s",
                           one.get("display_name"), name, exc)
            return Outcome(device_id, name, launchers=sent,
                           error=t("said.not_arrived", name=one.get("display_name")),
                           reason=_reason(exc))
        sent += 1
        try:
            data = _settings_file(one)
            if data is not None:
                client.put_launcher_settings_file(one["launcher_id"], data)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not copy the settings file of %s to %s: %s",
                           one.get("display_name"), name, exc)
            return Outcome(device_id, name, launchers=sent,
                           error=t("said.file_not_arrived", name=one.get("display_name")),
                           reason=_reason(exc))

    mapped = 0
    for table_id, launcher_id in mappings.items():
        try:
            client.put_launcher_mapping(table_id, launcher_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not copy a mapping to %s: %s", name, exc)
            return Outcome(device_id, name, launchers=sent, mappings=mapped,
                           error=t("said.assignments_lost"), reason=why(exc))
        mapped += 1

    return Outcome(device_id, name, launchers=sent, mappings=mapped)


def _reason(exc: Exception) -> str:
    """What the device said, where it answered with a reason."""
    response = getattr(exc, "response", None)
    try:
        said = response.json()["error"]["message"] if response is not None else ""
    except Exception:  # noqa: BLE001 - a response without a message
        said = ""
    return str(said) if said else why(exc)


def _body(launcher: dict[str, Any]) -> dict[str, Any]:
    """What travels. Not `owns_ini`: it says whether *this* install created the file, and
    the copy did not create anything on the machine it lands on. Not the settings file's
    path either: it names a place on this machine, and the file itself goes after the
    launcher (`_settings_file`)."""
    settings = dict(launcher.get("settings") or {})
    settings.pop(launcher_settings.KEY, None)
    return {"app": launcher.get("app", ""),
            "display_name": launcher.get("display_name", ""),
            "enabled": bool(launcher.get("enabled", True)),
            "owns_ini": False,
            "settings": settings}


def _settings_file(launcher: dict[str, Any]) -> bytes | None:
    """The bytes of the file the launcher plays with where it was pointed at one, else
    None: a launcher on its program's own file has none to send, and the copy plays with
    the program's own file over there."""
    named = str((launcher.get("settings") or {}).get(launcher_settings.KEY) or "").strip()
    if not named:
        return None
    try:
        return Path(named).expanduser().read_bytes()
    except FileNotFoundError:
        return None


def said(outcomes: Iterable[Outcome]) -> str:
    """A notification's message: how many devices it reached."""
    found = list(outcomes)
    good = sum(one.ok for one in found)
    if good == len(found):
        return t("said.copy_result", count=good)
    if not good:
        return t("said.copy_none")
    return t("said.copied_to_some", count=good, total=len(found))


def trouble(outcomes: Iterable[Outcome]) -> str:
    """Its caption: each device that did not take the copy, and why.

    Names the device, because a person whose cabinet was asleep needs to know which one.
    """
    return "; ".join(t("said.device_failed_because", name=one.name, error=one.error,
                       reason=one.reason) if one.reason
                     else t("said.device_failed", name=one.name, error=one.error)
                     for one in outcomes if not one.ok)

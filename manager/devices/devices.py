"""Devices: what each one is, and what it can be asked to do."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

from nicegui import background_tasks, run, ui

from common.config.labels import humanize
from common.devices import device_client, device_registry, install_identity
from common.host import action_ops, addresses
from common.i18n import t
from common.runtime import lifecycle
from manager.devices import browser_state, mobile_add, pairing, send_to_device
from manager.kit import (
    confirm,
    game_tables,
    grid,
    offload,
    panel,
    renderers,
    states,
    thumbnails,
    verbs,
    views,
    when,
)
from manager.kit.api import ApiClient, ApiError
from manager.kit.failed_together import Failures
from manager.kit.on_page import on_page
from manager.settings import settings as settings_page

logger = logging.getLogger("vpinfe.manager.devices.devices")

# What a kind of device can do when nobody can ask it. A vpx_mobile device runs VPX and
# not VPinFE, so it declares nothing, ever - its abilities are known from the kind.
IMPLIED_BY_KIND: dict[str, set[str]] = {
    "vpx_mobile": {"launch"},
}

PRESENT, ABSENT, UNKNOWN = "present", "absent", "unknown"

# What the absence costs, which is what the chip's color means everywhere else in the
# Manager: an unoffered capability is ordinary, one nothing has asked about is not.
_CHIP = {
    PRESENT: ("manager.devices.available_2", "on"),
    ABSENT: ("manager.devices.not_offered", "off"),
    UNKNOWN: ("manager.devices.cannot_determined", "unknown"),
}

# A device there is no way to call back. Not the same as one that is down, and it says
# which: an install announces the port it answers on, and this one never did.
UNREACHABLE_NOTE = "manager.devices.no_port"

# Why an install cannot replace itself, in the words a person reads. The API answers with
# the reason's name; the sentence for it belongs to whatever is showing it.
WHY_NOT = {
    "source_build": "manager.devices.why_not.source",
    "non_release_build": "manager.devices.why_not.non_release",
    "unsupported_architecture": "manager.devices.why_not.architecture",
    "no_matching_asset": "manager.devices.why_not.architecture",
    "asset_missing_file_name": "manager.devices.why_not.release_incomplete",
    "asset_not_attached_to_release": "manager.devices.why_not.release_incomplete",
    "macos_not_supported_yet": "manager.devices.why_not.platform",
    "unsupported_platform": "manager.devices.why_not.platform"
}

# How many records to ask for. Enough to see what led to something without handing over
# a 2MB file to a panel; the path is on the page for anyone who wants the rest.
LOG_LIMIT = 200

# What each level is worth noticing. Only the two that mean something went wrong get a
# hue - a column where every row is lit says nothing about any of them.
# Read by the Logs page too, so one install's log and another's are colored the same.
LOG_LEVELS = {
    "ERROR": "manager-log-bad",
    "CRITICAL": "manager-log-bad",
    "WARNING": "manager-log-warn",
}


def level_class(level: str) -> str:
    # The digest colors a whole line and takes LOG_LEVELS directly, where INFO is not
    # set apart from DEBUG.
    return LOG_LEVELS.get(level) or ("manager-log-info" if level == "INFO"
                                     else "manager-log-plain")


# What a device's menu can offer, in the order it is drawn. The windows' actions lead,
# being the ones met most often. The two that take the whole machine down are last, which
# is where `panel.verb_entries` puts a danger verb anyway.
MENU_ACTIONS = (
    (lifecycle.FRONTEND, lifecycle.START), (lifecycle.FRONTEND, lifecycle.STOP),
    (lifecycle.FRONTEND, lifecycle.RESTART),
    (lifecycle.VPINFE, lifecycle.RESTART), (lifecycle.VPINFE, lifecycle.STOP),
    (lifecycle.SYSTEM, lifecycle.RESTART), (lifecycle.SYSTEM, lifecycle.STOP),
)
QUIT = (lifecycle.VPINFE, lifecycle.STOP)
OPEN_FRONTEND = (lifecycle.FRONTEND, lifecycle.START)
CLOSE_FRONTEND = (lifecycle.FRONTEND, lifecycle.STOP)
FRONTEND_ACTIONS = (OPEN_FRONTEND, CLOSE_FRONTEND, (lifecycle.FRONTEND, lifecycle.RESTART))
# What a lifecycle action costs, which is what decides whether it is asked about first.
_ASKED = {lifecycle.VPINFE, lifecycle.SYSTEM}

# A device that did not answer, as against one that answered wrongly.
SILENT = "silent"
PROBLEM_REASONS = ("device.reason.certificate_changed", "device.reason.wrong_app",
                   "device.reason.unreadable", "device.reason.rejected")

# What a probe found, as the mark on a rail row and the chip on the page. Green for
# answering, because that is the one a person scans the rail for.
_REACH = {
    device_client.ANSWERING: ("manager.devices.answering", "on", "positive"),
    SILENT: ("manager.devices.not_answering", "off", "grey"),
    device_client.UNREACHABLE: ("manager.devices.problem", "bad", "negative"),
    device_client.UNASKABLE: ("manager.devices.cannot_asked", "unknown", "grey"),
}


def shown_state(probe: dict[str, Any] | None) -> str:
    """A probe's state as the page shows it. The wire has one `unreachable`; here it splits.

    Silence is off, asleep or VPinFE closed, and the app cannot tell which, so it is
    quiet. A device that answered wrongly is a problem, and the only red. A reason the
    list does not name counts as silence."""
    state = str((probe or {}).get("state") or "")
    if state == device_client.UNREACHABLE \
            and str((probe or {}).get("reason_key") or "") not in PROBLEM_REASONS:
        return SILENT
    return state


def capability_state(device: dict[str, Any], capability: str,
                     local_device_id: str | None,
                     local_capabilities: set[str],
                     probed: dict[str, Any] | None = None) -> str:
    """One of three answers, never two.

    "Cannot be determined" is its own state on purpose. Collapsing it into "not offered"
    tells someone their hardware lacks a feature when the truth is that nothing has
    asked it - which is a worse error than saying nothing.
    """
    kind = device.get("kind", "vpinfe")
    if kind in IMPLIED_BY_KIND:
        return PRESENT if capability in IMPLIED_BY_KIND[kind] else ABSENT
    if device.get("device_id") == local_device_id:
        return PRESENT if capability in local_capabilities else ABSENT
    # What the probe already heard. A remote install declares its capabilities in the
    # same response the probe reads for its name and version, so this costs nothing -
    # and it used to be thrown away, which left every remote install answering
    # "cannot be determined" for all of them.
    if probed and probed.get("state") == device_client.ANSWERING:
        said = probed.get("capabilities")
        # None is a build that did not say, which is not the same as a build that
        # answered and offers nothing. Reading it as the latter put "Not offered" on
        # every row of a machine that offers all of them.
        if said is None:
            return UNKNOWN
        return PRESENT if capability in set(said) else ABSENT
    # Nothing has asked it, or it did not answer. Not the same as "does not offer it".
    return UNKNOWN


def device_label(device: dict[str, Any]) -> str:
    """What to call a device on screen.

    The name it reported, then the address it answered from - an unnamed install is
    still the one at a particular address, and "device" tells nobody which. The install
    itself falls back to its hostname, so a blank name here means it never reported one.
    """
    return (str(device.get("display_name") or "").strip()
            or str(device.get("address") or "").strip()
            or t("manager.devices.device"))


def own_address() -> str:
    """What another machine would type to reach this one, or "" where nothing but
    loopback is on offer."""
    return next((host for host in addresses.hosts() if host != "localhost"), "")


def _state_rows(context: dict[str, Any]) -> list[tuple[Any, Any]]:
    """Whether it answers, and when it did not, why."""
    reach = context.get("reach")
    found = _REACH.get(shown_state(reach))
    if found is None:
        return [(t("word.state"), panel.state(t("manager.devices.checking"), "unknown"))]
    label, level, _color = found
    rows: list[tuple[Any, Any]] = [(t("word.state"), panel.state(t(label), level))]
    reason = str((reach or {}).get("reason") or "")
    if level != "on" and reason:
        rows.append(panel.note(reason))
    return rows + _pairing_rows(context)


def _pairing_rows(context: dict[str, Any]) -> list[tuple[Any, Any]]:
    """An install that answers but has not been paired: it turns away everything past
    discovery until a code from its own screen is typed here. Pairing again is also how a
    changed certificate is trusted."""
    reach = context.get("reach") or {}
    changed = bool(reach.get("certificate_changed"))
    if (is_local(context) or is_phone(context) or reach.get("paired") is not False
            or (reach.get("state") != device_client.ANSWERING and not changed)):
        return []
    client = client_for(context)
    ask = (context.get("state") or {}).get("ask_device")
    if client is None:
        return []
    device = _of(context)

    @on_page
    async def start() -> None:
        if await pairing.pair(client, device_label(device)) and ask is not None:
            await ask(str(device.get("device_id") or ""))

    def door() -> None:
        with ui.element("div").classes("manager-fact-edit"):
            panel.state(t("manager.devices.certificate_changed" if changed
                          else "manager.devices.needs_pairing"), "warn",
                        hint=t("manager.devices.certificate_changed_hint" if changed
                               else "manager.devices.needs_pairing_hint"))()
            panel.action(t("manager.pairing.pair"), start, icon=verbs.MATCH, inline=True,
                         hint=t("manager.pairing.pair_hint"))()

    return [(t("manager.devices.access"), door)]


def _last_seen_row(device: dict[str, Any]) -> tuple[Any, Any]:
    return (t("word.last_seen"), _when(str(device.get("last_reachable") or ""),
                                       t("word.never")))


def _connection_rows(context: dict[str, Any], address: str) -> list[tuple[Any, Any]]:
    """Whether it is there, when it last was, where, and since when this install has
    known it."""
    device = _of(context)
    rows = [*_state_rows(context), _last_seen_row(device)]
    if is_phone(context) and context.get("reach") and away(context):
        rows.append(panel.note(t("manager.devices.mobile_check")))
    rows += _where_rows(context, address)
    rows.append((t("manager.devices.first_seen"),
                 _when(str(device.get("first_seen") or ""), t("manager.devices.not_known"))))
    if is_local(context) or context.get("library") is None:
        rows.append(panel.note(t("manager.devices.self_note")))
    return rows


def _where_rows(context: dict[str, Any], address: str) -> list[tuple[Any, Any]]:
    """Where it is reached: a plain fact for an install, a field for a mobile device."""
    library = context.get("library")
    if not is_phone(context) or library is None:
        return [(t("manager.devices.address"), address or t("manager.devices.not_known"))]
    device = _of(context)

    async def save(which: str, text: str) -> str:
        text = text.strip()
        if which == "address":
            problem = (mobile_add.address_problem(text)
                       or await _write_mobile(context, address=text))
        else:
            port = mobile_add.port_of(text)
            problem = (t("manager.devices.port_invalid") if port is None
                       else await _write_mobile(context, port=port))
        if not problem:
            await context["rebuild"]()
        return problem

    return [(t("manager.devices.address"), panel.field(
                address, lambda text: save("address", text), refuses=True)),
            (t("manager.devices.port"), panel.field(
                str(device.get("port") or ""), lambda text: save("port", text),
                refuses=True))]


async def _write_mobile(context: dict[str, Any], *, address: str | None = None,
                        port: int | None = None, name: str | None = None) -> str:
    """Rewrite a mobile device's entry with one part changed. Answers why it could not,
    or "" once it is written.

    `device` is the page's own entry, so it is updated in place: a later edit starts from
    what was just written, and what was probed at the old address is forgotten. A new
    address or port is another endpoint, and the caller draws the panel again; a new
    name is put on the header and the grid's row here.
    """
    device = _of(context)
    device_id = str(device.get("device_id") or "")
    where = str(device.get("address") or "") if address is None else address
    at = int(device.get("port") or 0) if port is None else port
    called = str(device.get("display_name") or "") if name is None else name
    try:
        await offload.io(ApiClient().edit_device, device_id, where, at, called)
    except Exception as exc:  # noqa: BLE001 - the reason belongs under the field
        return _why(exc)
    device.update(address=where, port=at, display_name=called)
    state = context.get("state") or {}
    if address is not None or port is not None:
        for cached in ("device_reach", "device_actions"):
            state.get(cached, {}).pop(device_id, None)
        context["reach"] = context["actions"] = None
    landed, retitle = state.get("device_landed"), context.get("retitle")
    ask = state.get("ask_device")
    if ask is not None and (address is not None or port is not None):
        background_tasks.create(ask(device_id), name="manager-device-asked")
    if landed is not None:
        landed(device_id)
    if retitle is not None:
        retitle()
    return ""


def away(context: dict[str, Any]) -> bool:
    """Whether a rail has nothing to ask: the probe found no answer or has not landed.
    An entry with no port is not this case, and each rail already says so."""
    return (not is_local(context) and client_for(context) is not None
            and (context.get("reach") or {}).get("state")
            not in (device_client.ANSWERING, device_client.UNASKABLE))


def away_rows(context: dict[str, Any]) -> list[tuple[Any, Any]]:
    """What a rail says in place of what it cannot read from a device that is not
    answering: the state and its reason, when it was last there, and Try again."""
    return [*_state_rows(context), _last_seen_row(_of(context)), *_try_again_rows(context)]


def _try_again_rows(context: dict[str, Any]) -> list[tuple[Any, Any]]:
    """The way to ask the device again, where the page can ask."""
    ask = (context.get("state") or {}).get("ask_device")
    if ask is None:
        return []
    device_id = str(_of(context).get("device_id") or "")

    @on_page
    async def again() -> None:
        asking = ui.notification(t("manager.devices.checking"), spinner=True, timeout=None)
        try:
            await ask(device_id)
        finally:
            asking.dismiss()

    def door() -> None:
        with ui.element("div").classes("manager-fact-edit"):
            panel.action(t("manager.page.try_again"), again, icon=verbs.REFRESH,
                         inline=True, hint=t("manager.devices.probe_one"))()

    return [("", door)]


@dataclass(frozen=True)
class Target:
    """One device a menu acts on: its entry, who answers for it, and what it offers.

    `offered` is the device's own `/actions` answer, read where the page already reaches
    the device, because a menu has to open at once. `forgettable` is false for this
    install's own entry, which announces itself again at once.
    """

    device: dict[str, Any]
    client: Any = None
    offered: list[dict[str, Any]] | None = None
    forgettable: bool = True


def acts(library: Any, targets: list[Target], rerender: Callable[[], Any] | None,
         *, routed: dict[tuple[str, str], Callable[[], Any]] | None = None,
         led: tuple[tuple[str, str], str] | None = None) -> list[panel.Verb]:
    """What can be done to these devices: what they say they perform, then Forget.

    A verb is offered when any of them performs it and acts on those that do.

    `routed` swaps a pair's handler for one that asks and reports for itself, and `led`
    puts a pair first with its note after the label; both are for the one device a bar
    speaks for.
    """
    out: list[panel.Verb] = []
    for pair in MENU_ACTIONS:
        hits = [(one, entry) for one in targets
                for entry in _menu_entries(one.offered or []) if _pair(entry) == pair]
        if not hits:
            continue
        verb = replace(_device_verb(hits, (routed or {}).get(pair) if len(hits) == 1 else None,
                                    library), group=pair[0])
        if led is not None and pair == led[0]:
            verb = replace(verb, label=t("manager.waiting.menu_lead",
                                         label=verb.label, note=led[1]), group="")
            out.insert(0, verb)
        else:
            out.append(verb)
    forget = [one.device for one in targets if one.forgettable]
    if forget:
        out.append(panel.Verb(t("word.forget"),
                              lambda: _confirm_forget_all(library, forget, rerender),
                              danger=True))
    return out


def _pair(entry: dict[str, Any]) -> tuple[str, str]:
    return str(entry.get("scope") or ""), str(entry.get("action") or "")


def usable(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What a device listed, less what it only listed to say nothing performs it - which
    is how an install from before the list was filtered says so."""
    return [entry for entry in entries
            if entry.get("available", True) or entry.get("reason_key") != action_ops.NOT_WIRED]


def _menu_entries(offered: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_pair = {(str(entry.get("scope") or ""), str(entry.get("action") or "")): entry
               for entry in usable(offered)}
    # The windows' actions are drawn for the state they are in: one that cannot run now is
    # left out, and opening stands in for reopening when there is nothing to reopen.
    for pair in FRONTEND_ACTIONS:
        if pair in by_pair and not by_pair[pair].get("available", True):
            del by_pair[pair]
    if OPEN_FRONTEND in by_pair and CLOSE_FRONTEND not in by_pair:
        by_pair.pop((lifecycle.FRONTEND, lifecycle.RESTART), None)
    return [by_pair[pair] for pair in MENU_ACTIONS if pair in by_pair]


def offers(offered: list[dict[str, Any]] | None, pair: tuple[str, str]) -> bool:
    """Whether the device's menu carries this pair."""
    return any(_pair(entry) == pair for entry in _menu_entries(offered or []))


def windows_closed(offered: list[dict[str, Any]] | None) -> bool:
    """Whether the menu offers to open the windows and not to close them, which is
    an install that owns windows with none up."""
    return offers(offered, OPEN_FRONTEND) and not offers(offered, CLOSE_FRONTEND)


def _device_verb(hits: list[tuple[Target, dict[str, Any]]],
                 routed: Callable[[], Any] | None = None,
                 library: Any = None) -> panel.Verb:
    """One of the lifecycle verbs, asked about first where it costs the machine something.
    One that can run but not now is offered and refused, with its reason."""
    scope, action = _pair(hits[0][1])
    label = lifecycle.label(scope, action)
    ready = [(one, entry) for one, entry in hits if entry.get("available", True)]
    if not ready:
        return panel.Verb(label, danger=scope == lifecycle.SYSTEM,
                          hint=str(hits[0][1].get("reason") or ""))
    if routed is not None:
        return panel.Verb(label, routed, danger=scope == lifecycle.SYSTEM)
    several = len(ready) > 1

    @on_page
    async def perform(one: Target) -> bool | None:
        try:
            done = await offload.io(one.client.perform_action, scope, action)
        except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
            caption = _why(exc)
            if several:
                caption = t("manager.devices.failed_on",
                            device_label=device_label(one.device), reason=caption)
            ui.notify(t("said.action_failed"), caption=caption, type="negative")
            return None
        return bool(done.get("performed"))

    @on_page
    async def go() -> None:
        if scope in _ASKED and not await confirm.ask(
                t("manager.devices.do_to_many", label=label, count=len(ready)) if several
                else f"{label}?",
                detail=_asked_detail((scope, action), [one.device for one, _ in ready]),
                confirm=label, icon=verbs.STOP):
            return
        # A machine on its way down answers before it goes, so "performed" here means
        # the work was handed over rather than finished.
        # This machine last: the others are asked while the page is still here.
        order = sorted(ready, key=lambda hit: hit[0].client is library)
        results = [await perform(one) for one, _ in order]
        done = results.count(True)
        said = _result_said(scope, action, done, several)
        remote = any(ok and one.client is not library
                     for (one, _), ok in zip(order, results, strict=True))
        if done and said:
            ui.notify(said, type="positive")
        elif done and remote:
            ui.notify(t("manager.devices.did_to_many", label=label, count=done)
                      if several else label, type="positive")
        if results.count(False):
            ui.notify(t("manager.devices.not_happen", label=label), type="warning")

    return panel.Verb(label, go, danger=scope == lifecycle.SYSTEM)


def _result_said(scope: str, action: str, done: int, several: bool) -> str:
    """What happened, worded as a result. The actions that take the page away are said to
    every tab as they start, so this adds nothing for them."""
    if scope != lifecycle.FRONTEND:
        return ""
    if several:
        if action == lifecycle.START:
            return t("manager.devices.windows_opened_many", count=done)
        if action == lifecycle.STOP:
            return t("manager.devices.windows_closed_many", count=done)
        return t("manager.devices.windows_reopened_many", count=done)
    if action == lifecycle.START:
        return t("manager.devices.windows_opened")
    if action == lifecycle.STOP:
        return t("manager.devices.windows_closed")
    return t("manager.devices.windows_reopened")


def _asked_detail(pair: tuple[str, str], devices: list[dict[str, Any]]) -> str:
    if len(devices) > 1:
        return t("manager.devices.quit_detail_many" if pair == QUIT
                 else "manager.devices.happens_now_many", count=len(devices))
    return t("manager.devices.quit_detail" if pair == QUIT else "manager.devices.happens_now",
             device_label=device_label(devices[0]))


async def load_actions(library: Any, device: dict[str, Any], local_device_id: str | None,
                       reach: dict[str, Any] | None) -> list[dict[str, Any]]:
    """What a device says it performs, or nothing from one that cannot say.

    Never raises: a device that is asleep, a phone, or an install from before the route
    existed gets its entry's own verbs and no more.
    """
    local = device.get("device_id") == local_device_id
    if device.get("kind") == device_registry.KIND_VPX_MOBILE:
        return []
    if not local and (reach or {}).get("state") != device_client.ANSWERING:
        return []
    client: Any = library if local else device_client.for_device(device, local_device_id)
    if client is None:
        return []
    try:
        return usable(await offload.io(client.actions))
    except device_client.TooOldError:
        return []
    except device_client.NotPairedError:
        logger.debug("%s is not paired with this install", device_label(device))
        return []
    except Exception:  # noqa: BLE001 - unreachable is a state, not a 500
        logger.info("Could not ask %s what it does", device_label(device), exc_info=True)
        return []


def _forget_detail_key(devices: list[dict[str, Any]], seen_on_network: str) -> str:
    """A mobile device is only ever listed by hand, so it does not come back on its own."""
    listed_by_hand = any(d.get("kind") == device_registry.KIND_VPX_MOBILE for d in devices)
    return "manager.devices.forget_by_hand" if listed_by_hand else seen_on_network


@on_page
async def _confirm_forget(library: Any, device: dict[str, Any],
                          rerender: Callable[[], None] | None) -> None:
    """Drop the entry, having said what that does and does not do."""
    name = device_label(device)
    if not await confirm.ask(
            t("manager.devices.forget", name=(name)),
            detail=t(_forget_detail_key([device], "manager.devices.forget_detail")),
            confirm=t("word.forget"), icon=verbs.FORGET):
        return
    try:
        await run.io_bound(library.forget_device, str(device.get("device_id") or ""),
                           str(device.get("kind") or ""))
    except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
        ui.notify(t("manager.devices.forget_failed", name=name), caption=_why(exc),
                  type="negative")
        return
    ui.notify(t("manager.devices.forgot", name=(name)), type="positive")
    if rerender is not None:
        rerender()


@on_page
async def _add_mobile(rerender: Callable[[], None] | None,
                      open_pane: Callable[[], None] | None = None) -> None:
    """The add dialog, and the new device's panel once it is added."""
    added = await mobile_add.add_mobile()
    if not added:
        return
    grid.land_on(SCOPE, {"id": str(added.get("device_id") or "")})
    if rerender is not None:
        rerender()
    if open_pane is not None:
        open_pane()


@on_page
async def _confirm_forget_all(library: Any, devices: list[dict[str, Any]],
                              rerender: Callable[[], None] | None) -> None:
    """The selection's Forget: one question for the lot, then each in turn."""
    if len(devices) == 1:
        await _confirm_forget(library, devices[0], rerender)
        return
    if not await confirm.ask(
            t("manager.devices.forget_count", count=len(devices)),
            detail=t(_forget_detail_key(devices, "manager.devices.removes_entries_here")),
            confirm=t("word.forget"), icon=verbs.FORGET):
        return
    forgot = 0
    failed = Failures()
    for device in devices:
        try:
            await run.io_bound(library.forget_device, str(device.get("device_id") or ""),
                               str(device.get("kind") or ""))
            forgot += 1
        except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
            failed.add(device_label(device), _why(exc))
    failed.say("manager.devices.forget_failed")
    if forgot:
        ui.notify(t("manager.devices.forgot_count", count=forgot), type="positive")
    if rerender is not None:
        rerender()


def _software_rows(device: dict[str, Any], is_local: bool, client: Any,
                   update: dict[str, Any] | None,
                   check: Callable[[], Any] | None = None,
                   why: str = "") -> list[tuple[Any, Any]]:
    """What this device is running, and whether it can take what is published.

    A device with no answer gets an unknown - one that announced itself before ports were
    recorded cannot be reached, and one that is not answering has not said. Either way
    "up to date" would be a guess wearing a fact. `check` is Check now; `why` is what
    went wrong asking it.
    """
    rows: list[tuple[Any, Any]] = []
    if not update:
        rows.append((t("word.version"), panel.state(t("manager.devices.not_known"),
                "unknown")))
        if why:
            rows.append(panel.note(why))
        elif not is_local and client is None:
            rows.append(panel.note(t(UNREACHABLE_NOTE)))
        return rows

    current = str(update.get("current_version") or "unknown")
    checked = str(update.get("checked_at") or "")
    if update.get("error"):
        rows.append((t("word.version"), panel.state(
            t("manager.devices.check_failed"), "unknown", beside=current,
            hint=(t("manager.devices.last_checked", when=when.ago(checked, inline=True))
                  if checked else t("manager.devices.never_checked")))))
    elif update.get("update_available", False) is None:
        rows += _unweighed_rows(update, current)
    elif not update.get("update_available"):
        rows.append((t("word.version"), panel.state(current, "on")))
    else:
        rows += _available_rows(device, client, update, current)
    if check is not None:
        rows.append(settings_page.last_checked(checked, check))
    return rows


def _why_not(update: dict[str, Any]) -> tuple[Any, Any]:
    return panel.note(t(WHY_NOT.get(str(update.get("support_reason") or ""),
                                    "manager.devices.update_self")))


def _unweighed_rows(update: dict[str, Any], current: str) -> list[tuple[Any, Any]]:
    """A build whose own version cannot be compared with the release, such as a source
    checkout's. The release is named, never offered."""
    latest = str(update.get("latest_version") or "")
    chip = (panel.state(t("manager.devices.latest", latest=latest), "unknown", beside=current)
            if latest else panel.state(current, "unknown"))
    return [(t("word.version"), chip), _why_not(update)]


def _available_rows(device: dict[str, Any], client: Any, update: dict[str, Any],
                    current: str) -> list[tuple[Any, Any]]:
    latest = str(update.get("latest_version") or t("manager.devices.newer_build"))
    rows: list[tuple[Any, Any]] = [(t("word.version"), panel.state(
        t("manager.devices.available", latest=(latest)), "warn", beside=current))]
    if not update.get("update_supported"):
        rows.append(_why_not(update))
        return rows

    def update_action() -> None:
        with ui.element("div").classes("manager-fact-edit"):
            panel.action(t("manager.devices.update_3", latest=(latest)),
                         lambda: confirm_update(client, device_label(device), update),
                         icon=verbs.UPDATE, inline=True,
                         hint=t("manager.devices.update_3.help"))()

    rows.append(("", update_action))
    return rows


@on_page
async def confirm_update(client: Any, name: str, update: dict[str, Any]) -> None:
    """Ask before replacing an install, naming which one it is.

    The name is the point of the question. An update replaces the machine it runs on,
    which may not be the one this page is open on - "Update to v3.1" does not say which
    one goes down, and by the time it has, saying so is too late.
    """
    latest = str(update.get("latest_version") or t("manager.devices.published_build"))
    try:
        playing = await offload.io(client.play_state)
    except Exception as exc:  # noqa: BLE001 - a dialog that cannot say what it will do
        ui.notify(t("manager.devices.activity_failed", name=name), caption=_why(exc),
                  type="negative")
        return

    running = str((playing or {}).get("game_name") or "") if (
        playing or {}).get("launching") else ""

    # Named, because "a table is running" is a fact the person asking may not have: the
    # Manager is not necessarily open on the machine the table is on.
    lines = [t("manager.devices.close_note", running=(running))] if running else []
    if not await confirm.ask(
            t("manager.devices.update_2", name=(name), latest=(latest)),
            detail=t("manager.devices.update_detail"),
            lines=lines,
            confirm=t("manager.devices.stop_confirm") if running
            else t("manager.devices.update"),
            icon=verbs.STOP if running else verbs.UPDATE,
            danger=bool(running)):
        return
    await _start_update(client, name, bool(running))


@on_page
async def _start_update(client: Any, name: str, stop_table: bool) -> None:
    try:
        await run.io_bound(lambda: client.perform_update(stop_table=stop_table))
    except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
        ui.notify(t("manager.devices.update_failed"), caption=_why(exc),
                  type="negative")
        return
    # Nothing to redraw towards. Updating this install takes the page's own server down;
    # updating another leaves it up but knowing nothing new until that device is back.
    ui.notify(t("manager.devices.update_staged", name=(name)), type="positive")


def _hostname_placeholder(device: dict[str, Any]) -> str:
    """What it will be called if the field is left empty.

    The name it reports with nothing set is its hostname, so showing that is more use
    than the word "hostname" - it is the actual answer rather than a description of one.
    """
    return str(device.get("display_name") or "") \
        .strip() or t("manager.devices.hostname")




# --- The grid ---------------------------------------------------------------

SCOPE = "manager.devices.columns"

KIND_LABELS = {"vpinfe": "VPinFE", "vpx_mobile": "VPX Mobile"}

_KIND_CHOICES = [{"value": label, "label": label} for label in KIND_LABELS.values()]

# A device the probes have not reached yet holds `UNASKED`, which is a ? until they land.
UNASKED = "unasked"
REACH = states.Table(
    covers=t("manager.devices.state.help"),
    states=(
        states.State(device_client.ANSWERING, t(_REACH[device_client.ANSWERING][0]),
                     t("manager.devices.state.answering"), glyph=states.TICK),
        states.State(SILENT, t(_REACH[SILENT][0]),
                     t("manager.devices.state.silent"), tone=states.QUIET),
        states.State(device_client.UNREACHABLE, t(_REACH[device_client.UNREACHABLE][0]),
                     t("manager.devices.state.problem"), tone=states.BAD),
        states.State(device_client.UNASKABLE, t(_REACH[device_client.UNASKABLE][0]),
                     t("manager.devices.state.unaskable"), tone=states.QUIET),
        states.State(UNASKED, t("manager.devices.not_asked"),
                     t("manager.devices.state.unasked"), glyph=states.UNKNOWN)))

COLUMNS: list[dict[str, Any]] = [
    # Never shown - it exists so every built-in view can sort this device to the top.
    # A column has to be declared to be sorted on, and this one is a fact about the row
    # rather than anything to read.
    grid.column("self", t("manager.devices.device_2"), hide=True,
                help=grid.NO_HELP),
    grid.identifier("name", t("word.name"), 200, pinned="left",
                help=grid.NO_HELP),
    grid.column("kind", t("word.kind"), 120, **grid.choice_filter(_KIND_CHOICES),
                help=grid.NO_HELP),
    REACH.column("state", t("word.state"), 150),
    grid.column("last_seen", t("word.last_seen"), 170,
                help=grid.NO_HELP,
                **when.cell("last_seen")),
    grid.column("address", t("manager.devices.address"), 150,
                help=grid.NO_HELP),
    grid.column("version", t("word.version"), 160,
                help=grid.NO_HELP),
    browser_state.TABLE.column("browser", t("manager.devices.browser"), 150),
    grid.list_column("features", t("manager.devices.features"), 150,
                     help=t("manager.devices.features.help"),
                     looks=renderers.FEATURE_LOOKS),
]

# `self` is out: it is a sort key, not a column somebody picks.
_ALL = [definition["field"] for definition in COLUMNS if definition["field"] != "self"]

# Every view leads with it, so the device you are on is the first row whatever else the
# view orders by - which is what "pinned" means here, and it survives re-sorting because
# a user's sort is added to this rather than replacing it.
_SELF_FIRST = {"colId": "self", "sort": "desc", "sortIndex": 0}

VIEWS: dict[str, list[str] | views.Preset] = {
    "manager.view.everything": views.Preset(
        columns=("name", "kind", "state", "last_seen", "address", "version", "browser"),
        sort=(_SELF_FIRST,
              {"colId": "state", "sort": "asc", "sortIndex": 1},
              {"colId": "name", "sort": "asc", "sortIndex": 2}),
        help=t("manager.devices.all_view.help")),
    "manager.view.answering": views.Preset(
        columns=("name", "kind", "address", "version", "browser", "features"),
        sort=(_SELF_FIRST, {"colId": "name", "sort": "asc", "sortIndex": 1}),
        filters={"state": {"values": [device_client.ANSWERING]}},
        help=t("manager.devices.answering_view.help")),
    "manager.devices.not_answering": views.Preset(
        columns=("name", "kind", "state", "last_seen", "address"),
        sort=(_SELF_FIRST, {"colId": "last_seen", "sort": "asc", "sortIndex": 1}),
        filters={"state": {"values": [SILENT, device_client.UNREACHABLE,
                                      device_client.UNASKABLE]}},
        help=t("manager.devices.unreached_view.help")),
}


def rows(devices: list[dict[str, Any]],
         reach: dict[str, dict[str, Any]] | None = None,
         local_device_id: str | None = None) -> list[dict[str, Any]]:
    """One row per device, flattened for a grid.

    The probe's answer is folded in rather than fetched per row: it arrives once for
    the whole registry, and a column that asked per row would dial every machine again
    each time the grid redrew.
    """
    found = reach or {}
    out = []
    for device in devices:
        device_id = str(device.get("device_id") or "")
        probe = found.get(device_id) or {}
        state = shown_state(probe)
        out.append({
            "id": device_id,
            "self": device_id == (local_device_id or ""),
            "name": device_label(device),
            "kind": KIND_LABELS.get(str(device.get("kind") or "vpinfe"), "VPinFE"),
            "state": state if state in _REACH else UNASKED,
            "version": str(probe.get("version") or ""),
            "browser": str(_browser_of(device, probe).get("state") or ""),
            "address": str(device.get("address") or ""),
            "last_seen": str(device.get("last_reachable") or ""),
            "features": settings_page.features_listed(device.get("features")),
        })
    return [when.said(row, "last_seen") for row in out]


def _browser_of(device: dict[str, Any], probe: dict[str, Any] | None) -> dict[str, Any]:
    return dict((probe or {}).get("browser") or device.get("browser") or {})


def _browser_rows(device: dict[str, Any],
                  probe: dict[str, Any] | None) -> list[tuple[Any, Any]]:
    found = _browser_of(device, probe)
    if not found:
        return []
    name = str(found.get("name") or "")
    rows: list[tuple[Any, Any]] = [(
        t("manager.devices.browser"),
        browser_state.value(name, str(found.get("state") or ""), t("manager.devices.not_known")))]
    checked = str(found.get("checked_at") or "")
    if checked and (probe or {}).get("state") != device_client.ANSWERING:
        rows.append(panel.note(t("manager.devices.last_checked",
                                 when=when.ago(checked, inline=True))))
    return rows


def _when(stamp: str, missing: str) -> Any:
    """How long ago, in the words the grid uses, with the exact time on hover."""
    if not str(stamp or "").strip():
        return missing

    def draw() -> None:
        ui.label(when.ago(stamp)).classes("manager-fact-value").tooltip(when.local(stamp))

    return draw


def build(found: list[dict[str, Any]], library: Any, state: dict[str, Any],
          on_select: Callable[[dict | None], Any],
          probe: Callable[[], Any] | None = None,
          local_device_id: str | None = None,
          rerender: Callable[[], None] | None = None) -> None:
    """Devices as a grid, so the selected row is what the workbench answers for.

    The same shape every other subject uses. Kind and reachability are columns rather
    than groups, which is what lets one question - "which of these is not answering" -
    be a sort, a filter and a saved view instead of a fixed arrangement.
    """
    # Deferred: `games` imports `workbench.all_settings`, and `workbench.device` imports
    # this module for the device sections - so taking the view control at import time
    # closes the loop.
    # Only `build` needs it, and by then every module is loaded.
    from manager.library.games import view_control

    built = rows(found, state.get("device_reach"), local_device_id)
    on_screen = {"rows": len(built)}
    selected: list[dict[str, Any]] = []

    def said() -> str:
        if selected:
            return grid.selection_said(table, len(selected), t(
                "manager.devices.selected", picked=len(selected), shown=on_screen["rows"]))
        away = sum(1 for row in built
                   if row["state"] in (SILENT, device_client.UNREACHABLE,
                                       device_client.UNASKABLE))
        if on_screen["rows"] != len(built):
            return t("manager.devices.devices_2", shown=(on_screen["rows"]),
                     count=(len(built)))
        return t("manager.devices.count_away", count=(len(built)), away=(away)) if away \
            else t("manager.devices.devices", count=(len(built)))

    with ui.row().classes("w-full items-center gap-2 px-3 py-2 mb-2 shrink-0 "
                                  "manager-panel manager-grid-bar"):
        bar = panel.grid_bar()
        wire_views, _picker, showing, describe = view_control(library, SCOPE, VIEWS,
                                                    _ALL, COLUMNS, bar=bar)
        describe()
        with bar.top, panel.bar_end():
            search = panel.search(t("manager.devices.search_devices"))
        with bar.bottom, panel.bar_end():
            count = ui.label(said()).classes("text-xs manager-label")
            panel.add_action(
                [(t("manager.devices.add_mobile"),
                  lambda: _add_mobile(rerender, state.get("open_pane")))],
                empty=all(row.get("self") for row in built))
            show_selection = panel.selection_menu(
                t("manager.devices.bulk_menu"), lambda: grid.selection(table),
                lambda chosen: acts_on(chosen),
                lambda: table.run_grid_method("deselectAll"))
            if probe is not None:
                panel.refresh(probe, t("manager.devices.probe_all"))

    by_id = {row["id"]: row for row in built}
    grid.on_row_focus(SCOPE,
                      lambda event: on_select(by_id.get(grid.focused_row(event))))

    known = {str(one.get("device_id") or ""): one for one in found}

    def _target(row: dict[str, Any]) -> Target | None:
        device_id = str(row.get("id") or "")
        device = known.get(device_id)
        if device is None:
            return None
        return Target(
            device,
            library if row.get("self") else device_client.for_device(device, local_device_id),
            (state.get("device_actions") or {}).get(device_id),
            not row.get("self"))

    def acts_on(rows_on: list[dict[str, Any]]) -> list[panel.Verb]:
        chosen = [one for one in map(_target, rows_on) if one is not None]
        return acts(library, chosen, rerender) if chosen else []

    def _fill_row_menu(row: dict | None) -> None:
        menu.clear()
        if not row or str(row.get("id") or "") not in known:
            return
        on = grid.menu_rows(table, row)
        offered = panel.for_selection(acts_on(on), len(on))
        panel.verb_menu(menu, str(row.get("name") or "") if len(on) == 1
                        else t("manager.devices.devices", count=len(on)), offered)
        if not offered:
            # The browser opened it on the click; there is nothing to show yet.
            menu.close()

    async def on_header_context(col_id: str | None) -> None:
        await grid.header_menu(menu, table, COLUMNS, col_id)

    def on_select_rows(rows_selected: list[dict[str, Any]]) -> None:
        selected[:] = rows_selected
        show_selection(bool(rows_selected))
        count.text = said()

    mine = [local_device_id] if local_device_id else None

    with ui.element("div").classes("w-full grow min-h-0 flex flex-col"):
        table = grid.build(COLUMNS, built, SCOPE, on_select_rows,
                           on_context=_fill_row_menu,
                           on_header_context=on_header_context, view_of=showing,
                           rows_not_selectable=mine)
        menu = ui.context_menu()

        def keep_current() -> None:
            grid.transact(table, built,
                          {"update": [when.said(row, "last_seen") for row in built]}, by_id)

        # Inside the wrapper, so it goes when the grid does.
        ui.timer(60, keep_current)

    def landed(device_id: str) -> None:
        """One device's answer, into its row and the count."""
        device = known.get(device_id)
        if device is None:
            return
        grid.transact(table, built,
                      {"update": rows([device], state.get("device_reach"), local_device_id)},
                      by_id)
        count.text = said()

    state["device_landed"] = landed
    wire_views(table)
    search.on_value_change(
        lambda: table.run_grid_method("setGridOption", "quickFilterText",
                                      search.value or ""))

    async def counted() -> None:
        on_screen["rows"] = await table.run_grid_method("getDisplayedRowCount") or 0
        count.text = said()

    table.on("rowDataUpdated", counted)
    table.on("filterChanged", counted)


# --- Panel sections ---------------------------------------------------------
#
# One function per section of the workbench rail. They take the panel's context and
# hand back fact rows, because what a device is and what may be asked of it belongs
# with the device rather than with the panel that draws it.

def client_for(context: dict[str, Any]) -> Any:
    """Who answers for this device. This install's own client for itself, the client
    that reaches it for anything else - both expose the same calls, which is what lets
    one page draw either."""
    if is_local(context):
        return context.get("library")
    return device_client.for_device(_of(context), context.get("local_device_id"))


def _why(exc: Exception) -> str:
    """This install's API and an install too old for the route already speak in the
    catalog's words; anything else is a device that did not answer."""
    if isinstance(exc, (ApiError, device_client.TooOldError)):
        return str(exc)
    return t(device_client.why_not(exc))


def _of(context: dict[str, Any]) -> dict[str, Any]:
    return context.get("device") or {}


def is_local(context: dict[str, Any]) -> bool:
    return _of(context).get("device_id") == context.get("local_device_id")


def is_phone(context: dict[str, Any]) -> bool:
    return _of(context).get("kind") == device_registry.KIND_VPX_MOBILE


def serves(context: dict[str, Any], capability: str) -> bool:
    """False only where the device is known not to offer `capability`."""
    return capability_state(_of(context), capability, context.get("local_device_id"),
                            context.get("local_capabilities") or set(),
                            context.get("reach")) != ABSENT


async def detail_groups(context: dict[str, Any]) -> list[tuple[Any, Any]]:
    """What a device is, whether it is there, and where its settings are.

    Descriptive, then operational: what a thing is comes before what can be done to it.
    They are headings inside one section rather than rail entries of their own, because
    each holds three rows or fewer and a rail entry that opens one row is a click charged
    for nothing.
    """
    rows_out: list[tuple[Any, Any]] = [(panel.HEADING, t("word.identity"))]
    rows_out += await _identity_rows(context)
    rows_out.append((panel.HEADING, t("manager.devices.connection")))
    # Read off the machine, so off the loop: this machine's own address is a name lookup.
    address = (await offload.io(own_address) if is_local(context)
               else str(_of(context).get("address") or ""))
    rows_out += _connection_rows(context, address)
    rows_out += await _playing_rows(context)
    if is_phone(context):
        # A phone is not an install: no software, no lifecycle, no settings of ours to
        # open. What it has instead is the one thing this end can act on - the games it
        # is carrying - so that heading takes the place of Settings rather than being
        # added beside it.
        rows_out.append((panel.HEADING, t("manager.devices.carrying")))
        rows_out += await _carrying_rows(context)
        rows_out.append((panel.HEADING, t("manager.devices.sending")))
        rows_out.append(panel.note(t("manager.devices.sending_shared")))
        rows_out += await _sending_rows()
    else:
        rows_out.append((panel.HEADING, t("manager.devices.settings")))
        rows_out += settings_door(context)
    return rows_out


async def _playing_rows(context: dict[str, Any]) -> list[tuple[Any, Any]]:
    """The table being played there, with the way to close it. Nothing while none is,
    and nothing from a device that does not say it can close one or does not answer."""
    pair = {"scope": lifecycle.TABLE, "action": lifecycle.STOP}
    if not any(pair.items() <= entry.items() for entry in context.get("actions") or []):
        return []
    client = client_for(context)
    try:
        playing = await offload.io(client.play_state)
    except Exception:  # noqa: BLE001 - a row that cannot be read is not shown
        logger.info("Could not ask %s what it is playing", device_label(_of(context)),
                    exc_info=True)
        return []
    if not playing.get("launching"):
        return []
    name = str(playing.get("game_name") or "")

    @on_page
    async def close() -> None:
        try:
            await offload.io(client.perform_action, lifecycle.TABLE, lifecycle.STOP)
        except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
            ui.notify(t("said.action_failed"), caption=_why(exc), type="negative")
            return
        await context["rebuild"]()

    def draw() -> None:
        with ui.element("div").classes("manager-fact-edit"):
            if name:
                ui.label(name).classes("manager-fact-value truncate min-w-0").tooltip(name)
            panel.action(t("manager.devices.close_action"), close, icon=verbs.STOP,
                         inline=True, hint=t("action.table.stop.help"))()

    return [(t("manager.devices.playing"), draw)]


async def _carrying_rows(context: dict[str, Any]) -> list[tuple[Any, Any]]:
    """What the device holds, asked of the device.

    Never remembered between visits: a mobile device is filled up and emptied by hand and
    taken out of the house, so anything recorded here would be a claim about a machine
    that has not been asked. A device that is not answering is not asked at all, so the
    panel does not wait out the request's retries.
    """
    if away(context):
        # State and Last seen are already drawn under Connection, above this heading.
        return [panel.note(t("manager.devices.carrying_away")), *_try_again_rows(context)]
    device = _of(context)
    library = context.get("library")
    try:
        held = await offload.io(ApiClient().device_games,
                                  str(device.get("device_id") or ""))
    except Exception as exc:
        return [panel.note(t("manager.devices.carrying_failed"), hint=_why(exc))]

    @on_page
    async def forget(folder: str) -> None:
        if not await confirm.ask(
                t("manager.devices.remove", name=folder,
                        device_label=(device_label(device))),
                detail=t("manager.devices.remove_detail"),
                confirm=t("word.remove"), icon=verbs.REMOVE, danger=True):
            return
        try:
            await run.io_bound(ApiClient().remove_from_device,
                               str(device.get("device_id") or ""), folder)
        except Exception as exc:
            ui.notify(t("said.remove_failed"), caption=_why(exc), type="negative")
            return
        await context["rebuild"]()

    games = library.games if library is not None else []
    names = {str(one["id"]): str(one.get("name") or one["id"]) for one in games}
    on_it = {str(one["game_id"]) for one in held if one.get("game_id")}
    rows_out: list[tuple[Any, Any]] = []
    if games:
        rows_out.append((panel.FULL, _send_picker(context, device, games, on_it)))
    if not held:
        return [*rows_out, panel.note(t("manager.devices.carrying_empty"))]

    def row(one: dict[str, Any]) -> Callable[[], None]:
        def draw() -> None:
            with ui.element("div").classes("manager-slot-actions"):
                if not one.get("game_id"):
                    word, why = game_tables.GONE_WORDS
                    ui.label(word).tooltip(why) \
                        .classes("manager-member-chip manager-chip-warn")
                ui.button(t("word.remove"),
                    icon=verbs.REMOVE, on_click=lambda _e=None: forget(one["folder"])) \
                    .props("flat dense no-caps size=sm") \
                    .classes("manager-action manager-action--inline")

        return draw

    rows_out += [(names.get(str(one.get("game_id")), one["folder"]), row(one))
                 for one in held]
    return rows_out


SECTION = "vpxmobile"


def _mobile_word(text: str) -> str:
    """What a person typed, as the word in `<table>.<word>.ini`."""
    word = text.strip().strip(".")
    if word.lower().endswith(".ini"):
        word = word[:-4].strip().strip(".")
    return word


def _mobile_file_shape(text: str | None) -> str:
    word = _mobile_word(text or "")
    return (t("manager.devices.mobile_file_shape", word=word) if word
            else t("manager.devices.mobile_file_none"))


async def _sending_rows() -> list[tuple[Any, Any]]:
    """How a send goes, for every mobile device alike. These are this install's settings,
    written through the config route Settings uses: the device has nowhere to keep them."""
    client = ApiClient()
    try:
        schema = await offload.io(client.config_schema)
        values = await offload.io(client.config_values)
    except Exception as exc:
        return [panel.note(t("manager.devices.sending_failed"), hint=_why(exc))]
    declared = {str(one["key"]): one for block in schema if block.get("name") == SECTION
                for one in block.get("options") or []}
    current = dict(values.get(SECTION) or {})

    def value(key: str) -> Any:
        return settings_page.value_for(declared[key], current.get(key))

    def save(key: str) -> Callable[[Any], Any]:
        return settings_page.saver(client, SECTION, key)

    async def word(text: str) -> None:
        await save("config_mask")(_mobile_word(text))

    typed: dict[str, Any] = {}

    def example() -> None:
        panel.line(_mobile_file_shape(str(value("config_mask")))).bind_text_from(
            typed["control"], "value", backward=_mobile_file_shape)

    async def size(event: Any) -> None:
        await save("chunk_size")(int(declared["chunk_size"]["default"]) if event.value is None
                                 else int(event.value))

    async def turn(event: Any) -> None:
        await save("send_masked_config")(bool(event.value))

    return [
        (t(f"config.{SECTION}.send_masked_config.label"),
         panel.switch(bool(value("send_masked_config")), turn)),
        panel.note(t(f"config.{SECTION}.send_masked_config.description")),
        (t(f"config.{SECTION}.config_mask.label"),
         panel.field(str(value("config_mask")), word,
                     on_control=lambda control: typed.update(control=control))),
        panel.note(t(f"config.{SECTION}.config_mask.description")),
        (panel.ASIDE, example),
        (t(f"config.{SECTION}.chunk_size.label"),
         panel.number(value("chunk_size"), size, low=1, unit=t("manager.devices.bytes"),
                      on_leave=True, on_control=lambda c: c.classes("manager-edit-long"))),
        panel.note(t(f"config.{SECTION}.chunk_size.description")),
    ]


def _send_picker(context: dict[str, Any], device: dict[str, Any],
                 games: list[dict[str, Any]], on_it: set[str]) -> Callable[[], None]:
    """A game typed for and sent at once, with no confirm: the confirm's question is
    which device and whether to go, and a pick here has answered both."""
    library = context["library"]

    def draw() -> None:
        picker = panel.GamePicker(games, on_it, label=t("manager.devices.send_game"),
                                  art=library.game_art()) \
            .props("dense outlined options-dense input-debounce=0 hide-selected "
                   "fill-input clearable") \
            .classes("w-full")
        (library.thumbnail_look() or thumbnails.Look()).in_panel() \
            .apply_to_options(picker, "manager-picker-popup")

        @on_page
        async def pick() -> None:
            picked = str(picker.value or "")
            if not picked:
                return
            picker.value = None
            picker.disable()
            await send_to_device.send([{"id": picked}], device, context.get("state"))
            await context["rebuild"]()

        picker.on_value_change(pick)

    return draw


# Why a device's settings are somewhere else. Said where somebody is looking for them,
# because "not here" without a reason reads as something missing.
SETTINGS_NOTE = "manager.devices.settings_note"

# What is wrong when the door will not open. Both halves matter: one is a machine to go
# and switch on, the other is an entry with nothing to dial.
NO_DOOR = {
    SILENT: "manager.devices.open_refused",
    device_client.UNREACHABLE: "manager.devices.open_problem",
    device_client.UNASKABLE: UNREACHABLE_NOTE,
}


# Where this install's own settings are, which is a place in the Manager already open.
SYSTEM_PATH = "/manager?view=system"


def settings_url(device: dict[str, Any]) -> str:
    """That install's own Manager, landing on System. Empty when there is nothing to
    dial - an entry written before ports were recorded has no port."""
    base = device_registry.install_url(device)
    return f"{base}{SYSTEM_PATH}" if base else ""


def door_reason(device: dict[str, Any], reach: dict[str, Any] | None,
                is_local: bool) -> str:
    """Why the door will not open, or "" when it will.

    A machine that is not answering must not be offered as a live link: the tab opens on
    a connection error, which is a worse answer than being told here.
    """
    if is_local:
        return ""
    found = NO_DOOR.get(shown_state(reach))
    if found:
        return found
    return "" if settings_url(device) else UNREACHABLE_NOTE


def settings_door(context: dict[str, Any]) -> list[tuple[Any, Any]]:
    """The way into that install's own Manager, or the reason there is not one.

    A door rather than a section. Drawing that install's settings here, from a schema
    fetched over HTTP, would mean this build deciding how another build's settings look -
    which is exactly where version skew bites.
    """
    device = _of(context)
    here = is_local(context)
    stopped = door_reason(device, context.get("reach"), here)

    def open_it() -> None:
        # In place for this install, a new tab for anything else: one is a place in the
        # Manager you are already reading, and the other is a different machine's.
        ui.navigate.to(SYSTEM_PATH if here else settings_url(device),
                       new_tab=not here)

    def door() -> None:
        with ui.element("div").classes("manager-fact-edit"):
            panel.action(t("manager.devices.open_settings_here") if here
                    else t("manager.devices.open_settings"),
                         open_it, icon="open_in_new", inline=True,
                         enabled=not stopped,
                         hint=panel.NO_HINT if here
                         else t("manager.devices.open_settings.help"))()

    return [("", door), panel.note(t(stopped) if stopped else t(SETTINGS_NOTE))]


async def _identity_rows(context: dict[str, Any]) -> list[tuple[Any, Any]]:
    """What it is called, and what it is for."""
    device = _of(context)
    # The library this name can be written through, which only the install serving the
    # page has. Without one the name is plain text.
    library = context.get("library") if is_local(context) else None

    # What the install has been told to call itself, which is not what it reports: the
    # reported name already fell back to the hostname, so showing that as the value
    # leaves nothing to tell a chosen name from a defaulted one.
    stored = ""
    if library is not None:
        try:
            values = await offload.io(library.config_values)
            stored = str(((values or {}).get("device") or {}).get("name") or "")
        except Exception:  # noqa: BLE001 - an unreadable name is an empty field, not a 500
            library = None

    @on_page
    async def rename(value: str) -> None:
        if library is None:
            return
        try:
            await run.io_bound(library.put_config,
                               {"device": {"name": value.strip()}})
        except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
            ui.notify(t("manager.devices.save_failed"), caption=_why(exc), type="negative")

    mobile = is_phone(context) and context.get("library") is not None

    async def rename_mobile(value: str) -> str:
        return await _write_mobile(context, name=value.strip() or t("manager.devices.mobile_name"))

    if mobile:
        named: Any = panel.field(str(device.get("display_name") or ""), rename_mobile,
                                 placeholder=t("manager.devices.mobile_name"), refuses=True)
    else:
        named = (panel.field(stored, rename, placeholder=_hostname_placeholder(device))
                 if library is not None else device_label(device))
    rows_out: list[tuple[Any, Any]] = [(t("word.name"), named)]
    if not is_local(context) and not mobile:
        rows_out.append(panel.note(t("manager.devices.name_note")))
    rows_out.append((t("manager.devices.features"), _feature_chips(device.get("features"))))
    if device.get("features"):
        rows_out.append(panel.note(t("manager.devices.features_note")))
    return rows_out


def _feature_chips(features: Any) -> Any:
    """Every feature a device may have, green where it has one and grey where not.

    A device that reported nothing at all reads "Not reported": grey says it was asked and
    does not have the feature, which is a different claim.
    """
    if not features:
        return t("manager.devices.not_reported")
    has = settings_page.features_listed(features)
    known = install_identity.FEATURES
    shown = [*known, *(name for name in has if name not in known)]

    def draw() -> None:
        with ui.element("div").classes("manager-chips"):
            for name in shown:
                ui.label(settings_page.feature_said(name)) \
                    .classes(f"manager-tier manager-tier--{'on' if name in has else 'off'}")

    return draw


# A device that did not answer is not dialled again by a page load for this long: each dial
# of one that is off costs its whole timeout, and every page load would pay it. Asking by
# hand always dials.
SILENCE_HELD_S = 60.0
_silent: dict[str, tuple[float, dict[str, Any]]] = {}


def remember_answer(device_id: str, found: dict[str, Any]) -> dict[str, Any]:
    if found.get("state") == device_client.ANSWERING:
        _silent.pop(device_id, None)
    else:
        _silent[device_id] = (time.monotonic(), found)
    return found


def recent_silence(device_id: str) -> dict[str, Any] | None:
    """What a device said when it last failed to answer, if that was just now."""
    held = _silent.get(device_id)
    if held is None or time.monotonic() - held[0] >= SILENCE_HELD_S:
        return None
    return held[1]


async def ask_each(entries: list[dict[str, Any]],
                   probe: Callable[[str], dict[str, Any]],
                   landed: Callable[[str, dict[str, Any]], Awaitable[None]]) -> list[Exception]:
    """Ask every device at once, and hand each answer to `landed` as it arrives.

    Returns what went wrong with the asking itself. A device that does not answer is an
    answer, and lands like any other.
    """
    failed: list[Exception] = []

    async def one(entry: dict[str, Any]) -> None:
        device_id = str(entry.get("device_id") or "")
        try:
            found = await offload.io(probe, device_id)
        except Exception as exc:  # noqa: BLE001 - the caller decides whether to say so
            failed.append(exc)
            return
        await landed(device_id, found)

    await asyncio.gather(*(one(entry) for entry in entries))
    return failed


def update_checker(is_local: bool, client: Any) -> Any:
    """Whichever client can answer *this* device's update check.

    The local client is this process, and asking it to reach the network for a version
    it already knows would be this install phoning itself - so it does not offer the
    call at all. The Manager does have a way to ask: its own API, which serves `/update`
    for exactly this. Without this the local device raised, the caller logged that a
    device could not be asked, and the one install that never learned it had an update
    was the one in front of you.
    """
    if is_local:
        from manager.kit.api import ApiClient

        return ApiClient().update_check
    return getattr(client, "update_check", None)


async def software_rows(context: dict[str, Any]) -> list[tuple[Any, Any]]:
    """What it is running. Asked of the device rather than read from the registry,
    which holds no version at all."""
    device = _of(context)
    if away(context):
        return [*away_rows(context), *_browser_rows(device, context.get("reach"))]
    return [*await version_rows(context), *_browser_rows(device, context.get("reach"))]


async def version_rows(context: dict[str, Any]) -> list[tuple[Any, Any]]:
    """The version it runs, whether a newer one is out, and when that was last asked.
    For a device that answers; `software_rows` handles one that does not."""
    device = _of(context)
    client = client_for(context)
    update = context.get("update")
    ask = update_checker(is_local(context), client)
    why = ""
    if update is None and ask is not None:
        try:
            update = await offload.io(ask)
        except Exception as exc:  # noqa: BLE001 - unreachable is a state, not a 500
            logger.info("Could not ask %s what it is running",
                        device_label(device), exc_info=True)
            update, why = None, _why(exc)
        context["update"] = update
    return _software_rows(device, is_local(context), client, update,
                          _check_now(context, ask) if ask is not None else None, why)


def _check_now(context: dict[str, Any], ask: Callable[..., Any]) -> Callable[[], Any]:
    @on_page
    async def now() -> None:
        checking = ui.notification(t("manager.devices.checking_updates"), spinner=True,
                                   timeout=None)
        try:
            await offload.io(ask, True)
        except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
            ui.notify(t("manager.devices.ask_failed",
                        device_label=device_label(_of(context))),
                      caption=_why(exc), type="negative")
            return
        finally:
            checking.dismiss()
        rebuild = context.get("rebuild")
        if rebuild is not None:
            await rebuild()

    return now


def capability_rows(context: dict[str, Any]) -> list[tuple[Any, Any]]:
    device = _of(context)
    out: list[tuple[Any, Any]] = []
    for capability in context.get("device_capabilities") or []:
        state = capability_state(device, capability,
                                 context.get("local_device_id"),
                                 context.get("local_capabilities") or set(),
                                 context.get("reach"))
        text, level = _CHIP[state]
        text = t(text)
        out.append((humanize(capability), panel.state(text, level)))
    return out or [panel.intro(t("manager.devices.no_features"))]


async def log_rows(context: dict[str, Any]) -> list[tuple[Any, Any]]:
    """The tail of that install's own log.

    Read from the machine that holds it, which is the only party that has it. Records
    rather than lines, so a traceback arrives under the message that caused it instead
    of as a dozen rows carrying no level of their own.
    """
    client = client_for(context)
    if client is None:
        return [panel.intro(t(UNREACHABLE_NOTE))]
    if away(context):
        return away_rows(context)
    try:
        found = await offload.io(client.logs, LOG_LIMIT)
    except device_client.TooOldError:
        return [panel.intro(t(device_client.TOO_OLD))]
    except Exception as exc:  # noqa: BLE001 - unreachable is a state, not a 500
        logger.info("Could not read the log on %s", device_label(_of(context)),
                    exc_info=True)
        return [panel.intro(t("manager.devices.log_failed"), hint=_why(exc))]

    records = list(found.get("records") or [])
    if not records:
        return [panel.intro(t("manager.devices.log_empty"))]
    return [(panel.FULL, lambda: _log_lines(records, str(found.get("path") or "")))]


def _log_lines(records: list[dict[str, Any]], path: str) -> None:
    """Newest last, the way a log reads and the way `tail` shows one."""
    with ui.column().classes("w-full gap-0 manager-log"):
        for record in records:
            with ui.row().classes("items-baseline gap-2 w-full no-wrap manager-log-row"):
                ui.label(str(record.get("when") or "")).classes("manager-log-when")
                level = str(record.get("level") or "")
                ui.label(level).classes("manager-log-level " + level_class(level))
                ui.label(str(record.get("logger") or "")).classes("manager-log-source")
            ui.label(str(record.get("message") or "")).classes("manager-log-message")
    if path:
        panel.path_text(path, classes="manager-log-path")

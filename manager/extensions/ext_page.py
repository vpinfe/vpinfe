"""The pieces of one extension's panel on the Extensions page that are about the
extension itself: whether it is on, what it is set to, what it can be asked to do and what
it says it can reach.

Core draws all of it. The extension says what its settings are, what its actions are and
what it is holding; none of the treatment is its own, so an extension looks like the
Manager rather than like whoever wrote it - and it keeps working if that extension later
runs somewhere else. Where it came from and whether it can be updated belong to the page
itself (`manager.extensions.extensions`).
"""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

from nicegui import run, ui

from common.extensions import host
from common.failures import why
from common.i18n import t
from common.security import accepted
from manager.extensions import ext_action, ext_mark
from manager.kit import confirm, deeplink, offload, panel, verbs
from manager.kit.api import ApiClient
from manager.kit.on_page import on_page
from manager.security import accept

STATE_WORDS = {host.FAILED: "manager.ext_page.failed",
               host.DISABLED: "manager.ext_page.stopped"}


def address(name: str) -> str:
    """Where one extension is, for a link from anywhere in the Manager."""
    return "/manager?" + deeplink.query({"view": "extensions", "extension": name})


async def read(extension: dict) -> dict[str, Any]:
    """What its settings say, for the draw to use: nothing for one that is not running or
    has none, and `error` for one that would not answer."""
    surfaces = dict(extension.get("surfaces") or {})
    if extension.get("state") != host.LOADED or not surfaces.get("settings"):
        return {}
    base = f"/ext/{extension.get('name')}{surfaces['settings']}"
    try:
        return await offload.io(ApiClient().ext_get, base)
    except Exception as exc:  # noqa: BLE001 - the page says why, never 500s
        return {"error": why(exc)}


def enabled_row(extension: dict, refill: Callable[[], None]) -> tuple[Any, ...]:
    """The switch, which shows what was chosen and not what is running. Turning on one that
    VPinFE does not stand behind asks first."""
    name = str(extension.get("name") or "")

    @on_page
    async def flip(event: Any) -> None:
        if event.value and asks_before_on(extension):
            await trust_and_turn_on(extension, extension)
            refill()
            return
        try:
            await offload.io(ApiClient().set_extension_enabled, name, bool(event.value))
        except Exception as exc:  # noqa: BLE001
            ui.notify(t("said.on_failed" if event.value else "said.off_failed"),
                      caption=why(exc), type="negative")
        refill()

    return (t("manager.ext_page.enabled"),
            panel.switch(bool(extension.get("enabled", True)), flip))


def settings_entries(extension: dict, settings: dict[str, Any],
                     refill: Callable[[], None]) -> list[tuple[Any, ...]]:
    """What the extension is set to and what it can be asked to do, from what `read`
    found. Nothing for an extension that has neither."""
    name = str(extension.get("name") or "")
    running = extension.get("state") == host.LOADED
    surfaces = dict(extension.get("surfaces") or {})
    entries = setting_rows(ApiClient(), f"/ext/{name}{surfaces.get('settings', '')}",
                           settings, refill)
    if extension.get("has_settings") and not running and not cannot_run(extension):
        entries.append(panel.note(t("manager.ext_page.settings_when_running")))
    offered = list(extension.get("actions") or []) if running else []
    if offered:
        entries += [(panel.HEADING, t("word.actions")),
                    (panel.FULL, lambda: _actions(offered, name, refill))]
    return entries


def origin_chip(extension: dict) -> Callable[[], None] | None:
    """What not coming from VPinFE makes it, by its name, for anything VPinFE did not
    ship or list. The picture is the author's to choose, so this is what stops it passing
    as official."""
    trust = dict(extension.get("trust") or {})
    if not trust or (accepted.is_official(str(trust.get("origin")))
                     and not extension.get("left_list")):
        return None
    stopped = not trust.get("accepted")
    tip = t("manager.ext_page.unofficial_tip")
    if trust.get("origin") == accepted.REPLACES:
        tip = "\n".join((tip, t("manager.trust.replaces",
                                name=extension.get("display_name") or "")))
    return panel.state(t("manager.ext_page.not_trusted_chip" if stopped
                         else "manager.ext_page.unofficial_chip"),
                       "bad" if stopped else "warn", hint=tip, glyph=ext_mark.GLYPH)


def picture(extension: dict) -> Callable[[], None] | None:
    """The frame before the name: its own picture once it is shipped or accepted, and the
    mark every extension has until then. None for one whose manifest was refused, which
    has no identity to show."""
    if not extension.get("author"):
        return None
    name = quote(str(extension.get("name") or ""), safe="")
    address = f"/api/v1/extensions/{name}/picture?v={quote(str(extension.get('version') or ''))}"
    shown = bool(extension.get("picture_shown"))

    def draw() -> None:
        with ui.element("div").classes("manager-ext-picture"):
            ui.icon(ext_mark.PICTURE_GLYPH).classes("manager-ext-picture-glyph")
            if shown:
                image = ui.image(address).props("fit=cover no-spinner").classes(
                    "manager-ext-picture-image")
                image.on("error", lambda: image.set_visibility(False))

    return draw


def reported_rows(extension: dict, help_line: str) -> list[tuple[Any, ...]]:
    """What the author says it uses, each word saying what it touches, with `help_line`
    under it when there is something to say about how far that can be trusted."""
    words = [_plainly(str(one)) for one in
             [*(extension.get("scopes") or []), *(extension.get("capabilities") or [])]]
    _, remote = _remote_names([f"{PLAY_PREFIX}{one}" for one in extension.get("play") or []])
    entries: list[tuple[Any, ...]] = []
    if words:
        entries.append((t("manager.trust.uses"), _chips(words)))
    if remote:
        entries.append((t("manager.trust.remote"), _chips(remote)))
    if entries and help_line:
        entries.append(panel.note(help_line))
    return entries


def cannot_run(extension: dict) -> bool:
    """Not offered on this machine, so it never starts and its settings never show."""
    return extension.get("state") == host.OFF and extension.get("reason_key") in (
        host.WRONG_PLATFORM, host.LACKS_FEATURES)


def findings(extension: dict, open_this_device: Callable[[], None],
              refill: Callable[[], None]) -> list[Callable[[], None]]:
    """What stops it, under the header. Nothing for one that is running or only switched
    off, which the switch already says."""
    state, said = extension.get("state"), str(extension.get("reason") or "")
    if extension.get("reason_key") == host.NOT_ACCEPTED:
        return [_unaccepted(extension, extension, refill)]
    waiting = extension.get("waiting") or {}
    if waiting.get("reason_key") == host.NOT_ACCEPTED:
        return [_unaccepted(extension, waiting, refill)]
    if waiting.get("reason_key") == host.STARTS_AT_RESTART:
        return [finding(t("manager.ext_page.replacement_trusted"),
                         t(host.STARTS_AT_RESTART))]
    if cannot_run(extension):
        fix = panel.action(t("manager.ext_page.open_device"), open_this_device,
                           icon=verbs.GO, hint=t("manager.ext_page.open_hint")) \
            if extension.get("reason_key") == host.LACKS_FEATURES else None
        return [finding(t("manager.ext_page.cannot_run"), said, fix)]
    if state in STATE_WORDS:
        return [finding(t(STATE_WORDS[str(state)]), said)]
    return []


def _unaccepted(extension: dict, copy: dict, refill: Callable[[], None]
                ) -> Callable[[], None]:
    """It is stopped until the Owner says yes. `copy` is what would run: the extension
    itself, or the installed copy that would replace this built-in."""
    own = copy is extension

    @on_page
    async def decide() -> None:
        await trust_and_turn_on(extension, copy)
        refill()

    fix = panel.action(t("manager.trust.ext_button"), decide, icon=verbs.ACCEPT,
                       hint=t("manager.ext_page.accept_hint"))
    if own:
        return finding(t("manager.ext_page.not_trusted"),
                        t("manager.ext_page.not_trusted_detail"), fix)
    return finding(t("manager.ext_page.replacement_waiting"),
                    t("manager.ext_page.replacement_detail",
                      version=str(copy.get("version") or ""),
                      author=str(copy.get("author") or "")),
                    fix)


def asks_before_on(extension: dict) -> bool:
    """Whether turning it on asks first: anything VPinFE did not ship. One whose manifest was
    refused has nothing to ask about."""
    trust = dict(extension.get("trust") or {})
    return bool(trust and extension.get("author")
                and not accepted.is_official(str(trust.get("origin"))))


async def trust_and_turn_on(extension: dict, copy: dict) -> bool:
    """Ask the notice about `copy`, the extension itself or the installed copy waiting behind
    an included one, and on a yes record the answer and turn it on. False when it was
    declined or could not be done; a failure is said on screen."""
    name = str(extension.get("name") or "")
    trust = dict(copy.get("trust") or {})
    shown = str(extension.get("display_name") or name)
    used, remote = _remote_names(
        [str(one) for one in trust.get("uses") or copy.get("uses") or []])
    new, new_remote = _remote_names([str(one) for one in trust.get("new_access") or []])
    before = bool(trust.get("accepted_before"))
    yes = await accept.extension(
        shown, str(copy.get("version") or ""), author=str(copy.get("author") or ""),
        source=str(copy.get("source") or ""), path=str(copy.get("path") or ""),
        uses=[_plainly(one) for one in used], remote=remote,
        replaces=shown if trust.get("origin") == accepted.REPLACES else "",
        changed=len(trust.get("changed_files") or []),
        claims_team=bool(trust.get("claims_team")),
        added=[*(_plainly(one) for one in new), *new_remote] if before else [],
        was=str(trust.get("accepted_version") or "") if before else "")
    if not yes:
        return False
    try:
        client = ApiClient()
        await offload.io(client.accept_extension, name)
        if copy is extension:
            await offload.io(client.set_extension_enabled, name, True)
    except Exception as exc:  # noqa: BLE001
        ui.notify(t("manager.ext_page.trust_failed"), caption=why(exc), type="negative")
        return False
    return True


def finding(line: str, detail: str,
             fix: Callable[[], None] | None = None) -> Callable[[], None]:
    def draw() -> None:
        with ui.element("div").classes("manager-attention w-full"):
            ui.icon("error_outline").classes("manager-attention-icon")
            with ui.column().classes("gap-0 min-w-0 grow"):
                ui.label(line).classes("manager-attention-line")
                if detail:
                    ui.label(detail).classes("manager-member-table")
            if fix is not None:
                fix()

    return draw


def setting_rows(client: Any, base: str, found: dict[str, Any],
                  refill: Callable[[], None]) -> list[tuple[Any, ...]]:
    """What the extension is configured with, saved as each field is left."""
    if found.get("error"):
        return [panel.intro(t("manager.ext_page.list_failed"), hint=str(found["error"]))]
    entries: list[tuple[Any, ...]] = []
    if found.get("help"):
        entries.append(panel.intro(str(found["help"])))
    for field in found.get("fields") or []:
        name = str(field.get("key") or "")
        if not name:
            continue
        entries.append((str(field.get("label") or name),
                        _control(client, base, name, field, found, refill)))
        if field.get("help"):
            entries.append(panel.note(str(field["help"])))
    return entries


def _control(client: Any, base: str, key: str, field: dict, held: dict[str, Any],
             refill: Callable[[], None]) -> Any:
    """One setting, in the control its type asks for. A text field shows the extension's
    refusal under itself and keeps what was typed. A switch or a select has no place for
    one, so it says so in a notice and goes back to what is stored."""
    @on_page
    async def save(new_value: Any, *, under: bool = False) -> str:
        try:
            await run.io_bound(client.ext_put, base, {"values": {key: new_value}})
        except Exception as exc:  # noqa: BLE001
            if under:
                return why(exc)
            ui.notify(t("said.save_failed"), caption=why(exc), type="negative")
            refill()
            return ""
        if not await _stands(client, base, held, key, new_value):
            refill()
        return ""

    return control(client, field, save)


async def _stands(client: Any, base: str, held: dict[str, Any], key: str,
                  new_value: Any) -> bool:
    """Whether the page still describes the extension after `key` was set to `new_value`:
    it does where the extension reads back what the page held except that one value. The
    page then keeps its controls, and anything else it reads back is drawn again. A secret
    box is emptied by a save, so a secret always draws again."""
    if any(one.get("key") == key and one.get("type") == "secret"
           for one in held.get("fields") or []):
        return False
    try:
        fresh = await run.io_bound(client.ext_get, base)
    except Exception:  # noqa: BLE001 - what cannot be read is drawn again
        return False
    expected = copy.deepcopy(held)
    for one in expected.get("fields") or []:
        if one.get("key") == key and "value" in one:
            one["value"] = new_value
    if fresh != expected:
        return False
    held.clear()
    held.update(fresh)
    return True


def control(client: Any, field: dict, save: Callable[..., Awaitable[str]]) -> Any:
    """A field in the control its type asks for, wherever it is kept: `save(value)` writes
    it, and `save(value, under=True)` answers why it was refused where a text field can show
    that. A secret saves `None` for Clear. The same for a setting and for an account."""
    kind = str(field.get("type") or "string")
    value = field.get("value")
    key = str(field.get("key") or "")
    label = str(field.get("label") or key)
    if kind == "switch":
        return panel.switch(bool(value), lambda event: save(bool(event.value)))
    if kind == "select":
        choices = {str(one[0]): str(one[1]) for one in field.get("choices") or []}
        return panel.select(choices, str(value or ""), lambda event: save(event.value))
    if kind == "multi":
        choices = {str(one[0]): str(one[1]) for one in field.get("choices") or []}
        return panel.multi_select(choices, list(value or []),
                                  lambda event: save(list(event.value or [])))
    if kind == "number":
        return panel.number(_figure(value), lambda event: save(event.value), whole=False,
                            low=field.get("min"), high=field.get("max"),
                            unit=str(field.get("unit") or ""), on_leave=True)
    if kind == "path":
        return _path_control(client, field, str(value or ""), lambda text: save(text, under=True))
    if kind == "secret":
        return secret_control(label, bool(field.get("set")),
                               lambda typed: save(typed, under=True) if typed.strip() else None,
                               lambda: save(None))
    return panel.field(str(value or ""), lambda typed: save(typed, under=True),
                       refuses=True, placeholder=str(field.get("placeholder") or ""))


def _figure(stored: Any) -> float | None:
    try:
        return float(stored)
    except (TypeError, ValueError):
        return None


def secret_control(label: str, is_set: bool, save_typed: Callable[[str], Any],
                   clear: Callable[[], Awaitable[Any]],
                   detail: str = "", disabled: bool = False) -> Callable[[], None]:
    """A secret is never handed back, so the box starts empty and says whether one is
    held. Saving an empty box would clear it, so only a typed value is saved; Clear is the
    way to empty it, and asks first, saying `detail` of what follows. With `disabled`, neither
    the box nor Clear can be used."""
    @on_page
    async def forget() -> None:
        if await confirm.ask(t("manager.ext_page.clear_question", label=label),
                             detail=detail or t("manager.ext_page.clear_detail"),
                             confirm=t("manager.ext_page.clear"), icon=verbs.CLEAR):
            await clear()

    def draw() -> None:
        with ui.row().classes("items-center gap-2 no-wrap"):
            panel.field("", save_typed, refuses=True, secret=True,
                        placeholder=t("manager.ext_page.secret_set" if is_set
                                      else "manager.ext_page.secret_not_set"),
                        disabled=disabled)()
            if is_set:
                panel.action(t("manager.ext_page.clear"), forget, icon=verbs.CLEAR,
                             inline=True, hint=t("manager.ext_page.clear_hint"),
                             enabled=not disabled)()

    return draw


def _path_control(client: Any, field: dict, value: str,
                  save_text: Callable[[str], Awaitable[str]]) -> Callable[[], None]:
    """A path, saved when the field is left, and at once when Browse chose it."""
    def draw() -> None:
        control = panel.path_field(str(field.get("placeholder") or ""),
                                   wants=str(field.get("wants") or "dir"), value=value,
                                   browse=client.folders)
        state = {"focused": False, "saved": value}

        async def leave() -> None:
            state["focused"] = False
            text = str(control.value or "").strip()
            if text == state["saved"]:
                return
            state["saved"] = text
            said = await save_text(text)
            control.props["error"] = bool(said)
            control.props["error-message"] = said
            control.update()

        control.on("focus", lambda: state.update(focused=True))
        control.on("blur", leave)
        control.on("keydown.enter", lambda: control.run_method("blur"))
        # A value that changes while the field is not focused was chosen in Browse.
        control.on_value_change(lambda: None if state["focused"] else leave())

    return draw


def _actions(offered: list[dict], name: str, refill: Callable[[], None]) -> None:
    """Refills once an action has run: it may have changed a setting shown above it."""
    @on_page
    async def run_one(action: dict) -> None:
        if await ext_action.open_action(name, action):
            refill()

    with ui.element("div").classes("manager-slot-actions"):
        for action in offered:
            panel.action(str(action.get("label") or action.get("key") or ""),
                         lambda _e=None, action=action: run_one(action),
                         icon=verbs.RUN, hint=str(action.get("description") or ""))()


# What a scope or capability is called on screen. The wire says `games:write`; a person
# reading a consent list should not have to work out what that lets somebody do.
PLAINLY = {
    "games:read": "manager.ext_page.read_library",
    "games:write": "manager.ext_page.games_write",
    "input:read": "manager.ext_page.input_read",
    "players:read": "manager.ext_page.see_players",
    "filesystem:read": "manager.ext_page.filesystem_read",
    "config:read": "manager.ext_page.config_read",
    "config:write": "manager.ext_page.config_write",
    "ui:mount": "manager.ext_page.ui_mount",
    "config:own": "manager.ext_page.config_own",
    "net:outbound": "manager.ext_page.reach_internet",
    "proc:spawn": "manager.ext_page.proc_spawn",
    "hardware:usb": "manager.ext_page.hardware_usb",
    "fs:read": "manager.ext_page.read_files",
    "fs:write": "manager.ext_page.write_files",
    "os:updates": "manager.ext_page.os_updates",
}

PLAY_PREFIX = "play:"


def _remote_name(surface: str) -> str:
    """A surface the manifest names, as a person would call it: its kind and its key."""
    kind, _, rest = surface.partition(":")
    if kind == "router":
        return t("manager.ext_page.play_router")
    subject, _, key = rest.partition(":") if kind == "verb" else ("", "", rest)
    return t(f"manager.ext_page.play_{kind}", key=_spaced_words(key),
             subject=_spaced_words(subject))


def _spaced_words(identifier: str) -> str:
    """A manifest's snake_case key as capitalized words. A key is an identifier, not a name
    the catalog spells, so it is capitalized here to sit in a Title Case phrase."""
    return " ".join(word.capitalize() for word in identifier.split("_"))


def _remote_names(words: list[str]) -> tuple[list[str], list[str]]:
    """Split what an extension uses from what a Remote can open, the second named by kind."""
    rest = [one for one in words if not one.startswith(PLAY_PREFIX)]
    return rest, [_remote_name(one[len(PLAY_PREFIX):])
                  for one in words if one.startswith(PLAY_PREFIX)]


def _plainly(name: str) -> str:
    """Its own name is the fallback, never a guess: an unknown scope shown as prose
    somebody invented is worse than one shown as it is."""
    key = PLAINLY.get(name)
    return t(key) if key else name


def _chips(words: list[str]) -> Callable[[], None]:
    return accept.chips(words)

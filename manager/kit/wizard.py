"""The shared stepping control: Back, Next, Run and the summary, for any flow that asks
its questions in stages rather than all at once.

A flow answers every call with a step, and how many there are is read off that, never
declared: no fields means press it and it happens, fields mean fill them in first, and a
`summary` or a `confirm` means the last step before Run. A flow reaches this through
`Calls`, its own four calls already bound to wherever it answers from - HTTP for an
extension (`manager/extensions/ext_action.py`), or nothing at all for a flow with no
boundary to cross.
"""

from __future__ import annotations

import asyncio
import json
import re
import weakref
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from nicegui import run, ui

from common.failures import why
from common.i18n import literal_or, t
from common.online import sites
from manager.kit import (
    appearance,
    appearance_picker,
    deeplink,
    more_below,
    panel,
    report_groups,
    verbs,
    wizard_lists,
)
from manager.kit import dialog as frame
from manager.kit.api import ApiClient
from manager.kit.on_page import on_page

# How often to ask a running job how it is doing. A job here is minutes of copying, so a
# tighter loop would be a question asked hundreds of times for the same answer.
POLL_SECONDS = 1.0

# What a field's `type` draws, in the Manager's own grammar. Checked against the field
# table an extension author reads (docs/extensions/reference/actions.md) - the two must agree.
FIELD_TYPES = {
    "string": "panel.field",
    "secret": "panel.field",
    "path": "panel.path_field",
    "select": "panel.select",
    "multi": "panel.multi_select",
    "switch": "panel.switch",
    "choice": "panel.choice",
    "number": "panel.number",
    "checklist": "wizard_lists.checklist",
    "settle": "wizard_settle.settle",
}

# The field and the job report that draw the library's content register here when their
# module is imported. A `settle` field with no drawer raises in `_controls`.
FIELD_DRAWERS: dict[str, Callable[..., Callable[[], None]]] = {}
REPORT_DRAWERS: list[tuple[Callable[[dict], bool], Callable[[dict], None]]] = []

# The forward-verb button's own marker, so a keypress can find whichever step's button is
# current without rebinding a listener every time the step redraws.
_FORWARD = "manager-wizard-forward"

# The pictures core draws itself. Any other name is a Material icon: an extension names
# a picture and never supplies one.
_SOFT = 'fill="currentColor" fill-opacity="0.12"'
_DRAWN = ('<svg viewBox="0 0 64 48" fill="none" stroke="currentColor" stroke-width="1.6" '
          'stroke-linejoin="round" stroke-linecap="round">')
_ART = {
    "desktop": (
        _DRAWN + f'<rect x="12" y="3" width="40" height="27" rx="2" {_SOFT}/>'
        '<path d="M32 30v6M25 36h14"/><rect x="10" y="41" width="32" height="5" rx="1.2"/>'
        '<rect x="47" y="39.5" width="7" height="8" rx="3.5"/></svg>'),
    "cabinet": (
        _DRAWN + f'<rect x="17" y="2" width="30" height="16" rx="1.5" {_SOFT}/>'
        f'<rect x="23" y="20.5" width="18" height="5" rx="1" {_SOFT}/>'
        f'<path d="M21 29h22l9 17H12z" {_SOFT}/>'
        '<circle cx="6" cy="40" r="2.2"/><circle cx="58" cy="40" r="2.2"/></svg>'),
}
# A Material icon's ligature. Quasar also reads `img:` and `svguse:` names as drawings.
_ICON_NAME = re.compile(r"[a-z0-9_]+")


# What an option of a picture choice asks next: the walk's values, which it writes its
# answer into, and what to call after it has.
Detail = Callable[[dict[str, Any], Callable[[], None]], None]


@dataclass
class Calls:
    """A flow's own four calls: the first step (or, given a key, a done step reopened),
    the check that answers the next one, an act that answers the same one, and the run,
    which is told the step it runs from. `job` polls a run that answered with a `job_id`
    instead of finishing."""
    first: Callable[[str | None], Awaitable[dict]]
    check: Callable[[dict[str, Any], str], Awaitable[dict]]
    act: Callable[[str, dict[str, Any], str], Awaitable[dict]]
    run: Callable[[dict[str, Any], str], Awaitable[dict]]
    job: Callable[[str], Awaitable[dict]]
    # How far a call that is still going has got: `message`, and `done` of `total` where
    # it can count. Asked while one of the above is outstanding; a flow with nothing to
    # say leaves it out.
    progress: Callable[[], Awaitable[dict]] | None = None


class Walk:
    """The stepping loop: the current answer, its history, and the values gathered so
    far. Back, Next, an act, reopening a done step, and Run are decided here, never
    drawn, so the rules hold without a browser.
    """

    def __init__(self, calls: Calls, first: dict) -> None:
        self.calls = calls
        self.step = first
        self.history: list[dict] = []
        self.values: dict[str, Any] = {}

    async def next(self) -> dict:
        if not self.step.get("ready", True):
            return self.step
        found = await self.calls.check(self.values, str(self.step.get("step") or ""))
        # A step that answers with itself is the same question asked again, not a new
        # one. Remembering it would make Back go nowhere.
        if str(found.get("step") or "") != str(self.step.get("step") or ""):
            self.history.append(self.step)
        self.step = found
        return found

    async def reach(self, step_key: str, limit: int = 8) -> dict:
        """Step forward on the values already held until `step_key` is the step, or the
        flow stops advancing: a step that refuses what it was given is where it is shown."""
        for _ in range(limit):
            if str(self.step.get("step") or "") == step_key:
                break
            before = str(self.step.get("step") or "")
            await self.next()
            if str(self.step.get("step") or "") == before or not self.step.get("ready", True):
                break
        return self.step

    def back(self) -> dict:
        self.step = self.history.pop()
        return self.step

    async def act(self, key: str) -> dict:
        # No `ready` gate: an act is often what a step needs pressed to become ready
        # (Connect) rather than something it is withheld until.
        self.step = await self.calls.act(key, self.values, str(self.step.get("step") or ""))
        return self.step

    async def goto(self, step_key: str) -> dict:
        # Through `first`, not `check` - reopening a done step asks nothing new of it,
        # the way a page reload does. History is untouched; Back still goes where it
        # pointed before the jump.
        self.step = await self.calls.first(step_key)
        return self.step

    async def run(self) -> dict:
        if not self.step.get("ready", True):
            return self.step
        here = str(self.step.get("step") or "")
        found = await self.calls.run(self.values, here)
        # A run that answers with a step is a path that turned out longer than the last
        # step drawn said: the step it was drawn as the end of is not the end after all.
        if found.get("step"):
            if str(found["step"]) != here:
                self.history.append(self.step)
            self.step = found
        return found


async def _asking(calls: Calls, wait: Any, buttons: Any, work: Awaitable[Any]) -> Any:
    """Await one of the flow's calls, showing how far it has got once it is slow.

    A call that answers at once draws nothing. One that takes longer than a poll gets a
    bar and the flow's own words in `wait`, the strip above the buttons that the step's
    scroll never hides, and the buttons are held so a second press does not start the
    same read twice. The call is awaited where it was asked,
    because a step may need the page it was asked from; it is the watching that runs
    beside it.
    """
    shown: dict[str, Any] = {}
    watching = asyncio.ensure_future(_watch_slow(calls, wait, buttons, shown))
    try:
        return await work
    finally:
        watching.cancel()
        if "block" in shown:
            shown["block"].delete()
        _release(shown.get("held", []))


async def _watch_slow(calls: Calls, wait: Any, buttons: Any, shown: dict[str, Any]) -> None:
    await asyncio.sleep(POLL_SECONDS)
    shown["held"] = _hold(buttons)
    with wait, ui.column().classes("w-full gap-1 px-3 pt-2") as block:
        shown["bar"] = ui.linear_progress(show_value=False).props("indeterminate") \
            .classes("w-full")
        shown["said"] = ui.label(t("manager.ext_action.working")).classes("manager-help")
    shown["block"] = block
    while calls.progress is not None:
        try:
            _show(shown, await calls.progress())
        except Exception:  # noqa: BLE001 - a wait is not made worse by not knowing
            pass
        await asyncio.sleep(POLL_SECONDS)


def _show(shown: dict[str, Any], now: dict) -> None:
    said = str(now.get("message") or "")
    if not said:
        return
    total, done = int(now.get("total") or 0), int(now.get("done") or 0)
    shown["said"].text = (t("manager.ext_action.progress_of", message=said, done=done,
                            total=total) if total else said)
    if total:
        shown["bar"].props(remove="indeterminate")
        shown["bar"].value = min(done / total, 1.0)
    else:
        shown["bar"].props("indeterminate")


def _hold(buttons: Any) -> list[Any]:
    """Disable the forward button of a step that is being waited on, so a second press does
    not start the same read twice. Back and Cancel stay, because a wait that does not end
    must be leavable. Returns what it disabled, for `_release`."""
    held = [one for one in buttons.default_slot.children
            if _FORWARD in one.classes and getattr(one, "enabled", False)]
    for one in held:
        one.set_enabled(False)
    return held


def _release(held: list[Any]) -> None:
    for one in held:
        one.set_enabled(True)


def _controls(fields: list[dict], values: dict[str, Any], *,
             client: ApiClient, errors: dict[str, Any], welcome: bool = False,
             on_change: Callable[[], None] | None = None) -> dict[str, tuple[str, Any]]:
    """Draw what the task asked for, in the Manager's own grammar.

    Returns each field's own kind and control, keyed by its key - what the dialog host
    reads back before Next, Back or Run (a path field only pushes what it holds on its
    own check, and Next can come sooner than that), and where it sends focus and a
    refusal from `errors`. `welcome` is the step's own: a choice that carries pictures
    lays them out as tiles on one and as rows on any other. `on_change` is told after any
    control has written its value, for a step that gates its forward button on them.
    """
    # Each control writes its own key, so the key is bound by a call rather than
    # captured off the loop variable.
    followers: dict[str, list[Callable[[], None]]] = {}

    def wrote(key: str) -> None:
        for follow in followers.get(key, ()):
            follow()
        if on_change:
            on_change()

    def changed(key: str, cast: Callable[[Any], Any]) -> Callable[[Any], None]:
        def write(event: Any) -> None:
            values[key] = cast(event.value)
            wrote(key)
        return write

    def typed(key: str) -> Callable[[str], None]:
        def write(text: str) -> None:
            values[key] = text
            wrote(key)
        return write

    def refused(control: Any, key: str) -> None:
        said = errors.get(key)
        if not said:
            return
        text, detail = _worded(said)
        control.props["error"] = True
        control.props["error-message"] = text
        if detail:
            control.tooltip(detail)

    def refusing(draw: Callable[[], None], key: str) -> Callable[[], None]:
        said = errors.get(key)
        if not said:
            return draw
        text, detail = _worded(said)

        def drawn() -> None:
            with ui.column().classes("gap-1 min-w-0"):
                draw()
                panel.line(text, hint=detail, classes="manager-help text-negative")
        return drawn

    held: dict[str, tuple[str, Any]] = {}
    entries: list[tuple[Any, Any]] = []
    asked = sum(1 for field in fields if field.get("key"))
    for field in fields:
        key = str(field.get("key") or "")
        if not key:
            continue
        values.setdefault(key, field.get("value"))
        kind = str(field.get("type") or "string")
        label = str(field.get("label") or key)
        if kind == "multi":
            choices = {str(one[0]): str(one[1]) for one in field.get("choices") or []}

            def caught(control: Any, key: str = key) -> None:
                held[key] = ("multi", control)
                refused(control, key)

            entries.append((label, panel.multi_select(
                choices, list(values.get(key) or []),
                changed(key, lambda value: list(value or [])), on_control=caught)))
        elif kind == "select":
            choices = {str(one[0]): str(one[1]) for one in field.get("choices") or []}
            says = {str(one[1]): str(one[2]) for one in field.get("choices") or []
                    if len(one) > 2 and one[2]}

            def caught(control: Any, key: str = key) -> None:
                held[key] = ("select", control)
                refused(control, key)

            entries.append((label, panel.select(
                choices, str(values.get(key) or ""),
                changed(key, lambda value: str(value or "")), describes=says,
                on_control=caught)))
        elif kind == "switch":
            def caught(control: Any, key: str = key) -> None:
                held[key] = ("switch", control)

            entries.append((label, refusing(panel.switch(
                bool(values.get(key)), changed(key, bool), on_control=caught), key)))
        elif kind == "choice":
            options = [(str(one[0]), str(one[1]), str(one[2]) if len(one) > 2 else "",
                       _picture_named(one[3]) if len(one) > 3 else "")
                      for one in field.get("choices") or []]

            def caught(control: Any, key: str = key) -> None:
                held[key] = ("choice", control)

            if any(one[3] for one in options):
                entries.append((panel.FULL, refusing(_pictured(
                    label, options, str(values.get(key) or ""), typed(key),
                    tiles=welcome, labelled=asked > 1, on_control=caught,
                    details=field.get("details") or {}, values=values,
                    changed=on_change or (lambda: None),
                    url_for=client.browsed_file_url), key)))
            else:
                entries.append((label, refusing(panel.choice(
                    [one[:3] for one in options], str(values.get(key) or ""),
                    changed(key, lambda value: str(value or "")), on_control=caught), key)))
        elif kind == "number":
            def caught(control: Any, key: str = key) -> None:
                held[key] = ("number", control)
                refused(control, key)

            entries.append((label, panel.number(
                values.get(key), changed(key, lambda value: value),
                unit=str(field.get("unit") or ""),
                low=field.get("min"), high=field.get("max"),
                whole=False, on_control=caught)))
        elif kind == "checklist":
            def caught(control: Any, key: str = key) -> None:
                held[key] = ("ticks", control)

            def ticked(ticks: list[str], key: str = key) -> None:
                values[key] = ticks
                wrote(key)

            entries.append((panel.FULL, refusing(wizard_lists.checklist(
                field, list(values.get(key) or []), ticked,
                question=label if asked > 1 else "", on_control=caught), key)))
        elif kind == "settle":
            def caught(control: Any, key: str = key) -> None:
                held[key] = ("settle", control)

            def settled(picks: dict[str, str], key: str = key) -> None:
                values[key] = picks
                wrote(key)

            if "settle" not in FIELD_DRAWERS:
                raise RuntimeError("manager.library.wizard_settle is not imported")
            entries.append((panel.FULL, refusing(FIELD_DRAWERS["settle"](
                field, dict(values.get(key) or {}), settled, client=client,
                question=label if asked > 1 else "", on_control=caught), key)))
        elif kind == "path":
            def draw_path(field: dict = field, key: str = key) -> None:
                def synced(_state: str, said: str) -> str:
                    # The 0.3s check is a poll, not a push on every keystroke; Next
                    # reads the control directly instead of waiting on it.
                    values[key] = said
                    wrote(key)
                    return ""

                control = panel.path_field(
                    str(field.get("placeholder") or ""),
                    wants=str(field.get("wants") or "dir"),
                    value=str(values.get(key) or ""), width="w-full",
                    on_checked=synced, browse=client.folders,
                    opens_at=str(field.get("opens_at") or ""))
                # Without this the field reserves a row for a message that is rarely there.
                control.props("hide-bottom-space")
                held[key] = ("path", control)
                refused(control, key)

            entries.append((label, draw_path))
        elif kind == "secret":
            def caught(control: Any, key: str = key) -> None:
                held[key] = ("secret", control)
                refused(control, key)

            # Drawn empty unless the person has typed one on this walk: what an extension
            # already holds is never sent here, only whether it holds one.
            entries.append((label, panel.field(
                str(values.get(key) or ""), typed(key), secret=True, on_control=caught,
                placeholder=t("manager.ext_page.secret_set" if field.get("set")
                              else "manager.ext_page.secret_not_set"))))
        else:
            def caught(control: Any, key: str = key) -> None:
                held[key] = ("string", control)
                refused(control, key)

            entries.append((label, panel.field(
                str(values.get(key) or ""), typed(key), on_control=caught)))
        if field.get("help"):
            entries.append((panel.ASIDE, _aside(field["help"], values, followers)))
    panel.facts(ui, entries)
    return held


class _Picks(ui.element):
    """The group a picture choice is drawn in; `value` is the key picked, which `_sync`
    reads back the way it reads a radio."""

    def __init__(self, value: str) -> None:
        super().__init__("div")
        self.value = value


def _picture_named(named: Any) -> str | list[str]:
    """A picture is a name, or the paths of a few files the flow names."""
    if isinstance(named, list):
        return [str(one) for one in named if one]
    return str(named or "")


def _picture(name: str | list[str], url_for: Callable[[str], str]) -> None:
    if isinstance(name, list):
        wizard_lists.shots(name, url_for)
    elif name.startswith(appearance_picker.PICTURE_PREFIX):
        # A miniature of the Manager in one appearance, drawn in that appearance's colors.
        with ui.element("span").classes("manager-wizard-appearance"):
            appearance_picker.swatch(name.removeprefix(appearance_picker.PICTURE_PREFIX))
    elif name in _ART:
        ui.html(_ART[name], sanitize=False, tag="span").classes("manager-wizard-art")
    elif _ICON_NAME.fullmatch(name):
        ui.icon(name).classes("manager-wizard-icon")


def _pictured(question: str, options: list[tuple[str, str, str, str | list[str]]],
              value: str,
              on_change: Callable[[str], None], *, tiles: bool, labelled: bool,
              on_control: Callable[[Any], None],
              details: Mapping[str, Detail] | None = None,
              values: dict[str, Any] | None = None,
              changed: Callable[[], None] = lambda: None,
              url_for: Callable[[str], str] = str) -> Callable[[], None]:
    """A choice whose options carry pictures: `(key, label, help, picture)`. They are
    tiles on a welcome and rows elsewhere. Pictures every row shares (a sample of the
    user's own files, when the options are ways to read them) are drawn once above the
    rows. The question is drawn over them on a welcome
    and on a step with other fields to tell it from; alone on a step, the step's own
    title is the question and the group carries it as its accessible name only.

    `details` draws what an option asks next, under its row and only while it is the one
    picked, and is handed the walk's `values`, which it writes its answer into, and
    `changed` to call after it has. Callables cannot travel as JSON, so only a flow that
    draws in this process (Setup) can give them."""
    details = details or {}
    pictures = [picture for _, _, _, picture in options]
    shared = (not tiles and len(pictures) > 1 and isinstance(pictures[0], list)
              and bool(pictures[0]) and all(one == pictures[0] for one in pictures))

    def draw() -> None:
        with ui.column().classes("w-full gap-0"):
            if tiles or labelled:
                ui.label(question).classes("manager-card-title manager-wizard-question")
            if shared:
                _picture(pictures[0], url_for)
            group = _Picks(value).classes(
                "manager-wizard-picks" + (" manager-wizard-picks--tiles" if tiles else ""))
            group.props("role=radiogroup tabindex=-1")
            group.props["aria-label"] = question
            drawn: dict[str, tuple[ui.element, ui.icon]] = {}
            under: dict[str, ui.element] = {}

            def pick(key: str) -> None:
                group.value = key
                for one, (row, mark) in drawn.items():
                    chosen = one == key
                    if chosen:
                        row.classes(add="manager-source-row--chosen")
                    else:
                        row.classes(remove="manager-source-row--chosen")
                    row.props["aria-checked"] = "true" if chosen else "false"
                    mark.set_visibility(chosen)
                for one, holder in under.items():
                    holder.set_visibility(one == key)
                on_change(key)

            with group:
                for key, name, help_text, picture in options:
                    chosen = key == value
                    row = ui.element("div").classes(
                        "manager-source-row manager-source-row--pick manager-wizard-pick"
                        + (" manager-wizard-pick--tile" if tiles else "")
                        + (" manager-source-row--chosen" if chosen else ""))
                    row.props(f"role=radio tabindex=0 aria-checked={'true' if chosen else 'false'}")
                    row.on("click", lambda key=key: pick(key))
                    row.on("keydown.space.prevent", lambda key=key: pick(key))
                    with row:
                        if not shared:
                            _picture(picture, url_for)
                        with ui.column().classes("gap-0 min-w-0 manager-wizard-pick-text"):
                            ui.label(name).classes("manager-source-name")
                            if help_text:
                                ui.label(help_text).classes("manager-help")
                        mark = ui.icon("check_circle").classes("manager-wizard-pick-mark")
                        mark.set_visibility(chosen)
                    drawn[key] = (row, mark)
                    if key in details:
                        with ui.element("div").classes("manager-wizard-detail") as holder:
                            details[key](values if values is not None else {}, changed)
                        holder.set_visibility(chosen)
                        under[key] = holder
            on_control(group)

    return draw


def _sync(values: dict[str, Any], held: dict[str, tuple[str, Any]]) -> None:
    """What is on screen now, back into the walk's values."""
    for key, (kind, control) in held.items():
        if kind == "multi":
            values[key] = list(control.value or [])
        elif kind == "switch":
            values[key] = bool(control.value)
        elif kind in ("ticks", "settle"):
            values[key] = control.value
        elif kind == "number":
            # Not stringified: a number field holds None or a number, and `str(None)`
            # would write the word "None" into an answer nobody typed.
            values[key] = control.value
        elif kind == "secret":
            # Empty means keep what is held, so it is left out and not sent as "".
            if str(control.value or "").strip():
                values[key] = str(control.value)
            else:
                values.pop(key, None)
        else:
            values[key] = str(control.value or "")


def _focus_now(control: Any) -> None:
    """A step's own first field, focused directly rather than through a `show` event.
    The dialog host calls this from its second step on; the page host, with no `show`
    event of its own, calls it for every step. Steps down to a nested `input`/`textarea`
    the same way `frame.focus` does.
    """
    ui.run_javascript(f"""
        (() => {{
          let tries = 0;
          const go = () => {{
            const field = document.getElementById('c{control.id}');
            if (!field) {{ if (++tries < 40) setTimeout(go, 25); return; }}
            const target = field.matches('input,textarea') ? field
              : (field.querySelector('input,textarea') || field);
            target.focus();
          }};
          go();
        }})()
    """)


def _focus_title(control: Any) -> None:
    """Steps to the label itself, which needs `tabindex=-1` to take focus at all - a
    plain label is not in the tab order by default."""
    ui.run_javascript(f"""
        (() => {{
          const el = document.getElementById('c{control.id}');
          if (el) el.focus();
        }})()
    """)


def _enter_presses_forward(root_id: int) -> str:
    """Enter anywhere in `root_id` but a textarea presses whichever step's forward
    button is current."""
    return f"""
        (() => {{
          const root = document.getElementById('c{root_id}');
          if (!root) return;
          // An Enter that a button took or a row was listening for is that control's.
          // Decided on the way down: the row has stopped listening by the time it is let go.
          let taken = false;
          root.addEventListener('keydown', (event) => {{
            if (event.key !== 'Enter') return;
            taken = !!(window.__bindingListen || {{}}).row
              || !!(event.target.closest && event.target.closest('button, [role=button]'));
          }}, true);
          root.addEventListener('keyup', (event) => {{
            if (event.key !== 'Enter' || event.target.tagName === 'TEXTAREA' || taken) {{
              taken = false;
              return;
            }}
            const btn = root.querySelector('.{_FORWARD}');
            if (btn && !btn.disabled) btn.click();
          }});
        }})()
    """


def _sync_step(step_key: str) -> None:
    """Writes `step` into the address; every other query parameter is left alone."""
    ui.run_javascript(f"""
        (() => {{
          const url = new URL(location.href);
          const step = {json.dumps(step_key)};
          if (step) url.searchParams.set('step', step); else url.searchParams.delete('step');
          history.replaceState(null, '', url.pathname + url.search);
        }})()
    """)


def _step_row(steps: list[dict[str, Any]], current: str, go: Callable[[str], Any]) -> None:
    """The path as it stands: one segment per named step, its name under it. Only a
    `done` step answers a click - a step still ahead has nothing to reopen, and
    clicking it would say otherwise. A segment may `hold` several steps, which are one
    name in the row: it is here for any of them and a click reopens the first."""
    with ui.element("div").classes("manager-wizard-steps"):
        for entry in steps:
            key = str(entry.get("key") or "")
            text = str(entry.get("label") or key)
            held = [str(one) for one in entry.get("holds") or [key]]
            state = "here" if current in held else "done" if entry.get("done") else ""
            segment = ui.element("div").classes(
                "manager-wizard-step" + (f" manager-wizard-step--{state}" if state else ""))
            if state == "here":
                segment.props("aria-current=step")
            with segment:
                name = ui.label(text)
                if state == "done":
                    name.classes("manager-link").on("click", lambda key=held[0]: go(key))


def _greeting(found: dict, root: ui.element, crest: ui.element, mark: bool) -> bool:
    """Centers the step when it says it is a welcome, and puts VPinFE's mark over it where
    the caller asked. Returns whether it is one."""
    welcome = bool(found.get("welcome"))
    if welcome:
        root.classes(add="manager-wizard--welcome")
    else:
        root.classes(remove="manager-wizard--welcome")
    crest.clear()
    if welcome and mark:
        with crest:
            ui.image(appearance.LOGO).classes("manager-wizard-mark")
    return welcome


def _acts(acts: list[dict[str, Any]], go: Callable[[str], Any]) -> None:
    """An act is `{key, label}`; the flow draws no icon of its own, so this carries the
    same one the wizard's own confirm does for the same reason.
    """
    with ui.row().classes("gap-2 px-3 mb-2"):
        for act in acts:
            key = str(act.get("key") or "")
            label = str(act.get("label") or key)
            panel.action(label, lambda key=key: go(key), icon=verbs.RUN,
                        hint=str(act.get("hint") or label))()


def _aside(help_: Any, values: dict[str, Any] | None = None,
           followers: dict[str, list[Callable[[], None]]] | None = None) -> Callable[[], None]:
    """The line under a field. A string is said as it is; `{follows, says, else}` says what
    `says` holds for the value the field named `follows` has now, and `else` for any other,
    and changes as that field does."""
    if not isinstance(help_, dict):
        def plain() -> None:
            ui.label(str(help_)).classes("manager-help")
        return plain

    follows = str(help_.get("follows") or "")
    says = {str(one): str(text) for one, text in (help_.get("says") or {}).items()}
    other = str(help_.get("else") or "")

    def now() -> str:
        return says.get(str((values or {}).get(follows) or ""), other)

    def draw() -> None:
        line = ui.label(now()).classes("manager-help")

        def follow() -> None:
            line.set_text(now())
            line.set_visibility(bool(now()))
        line.set_visibility(bool(now()))
        if followers is not None:
            followers.setdefault(follows, []).append(follow)
    return draw


async def open_dialog(*, label: str, calls: Calls, under: str, mark: bool = False,
                      aside: str = "", close_when_done: bool = False,
                      hand_off: Callable[[str], None] | None = None,
                      values: dict[str, Any] | None = None, until: str = "",
                      place: str = "", walking: bool = False,
                      roomy: bool = False) -> bool | str | None:
    """Run one flow in the dialog host: ask what it asks, in as many steps as it asks
    it, then do it.

    The flow decides what to ask next from what it has been given so far, and says so
    by answering with another step. This keeps everything answered and hands the whole
    lot back each time, along with the step being left, so a step is a question rather
    than a session - nothing is held between requests that could be stale by the time it
    is used, and going Back and forward again asks the same questions in the same order
    rather than jumping over the ones already answered.

    It stops asking when it answers with a summary or a `confirm` instead of fields.
    `under` is where a run's report belongs in the flow's own catalog. `mark` puts VPinFE's
    mark over a welcome step; only core passes it, so an extension's welcome is centered
    without it. `aside` is the label of a way out that is not Cancel, such as leaving the
    flow to finish later, drawn on the far left of every step; with it a first step has no
    Cancel as well. `close_when_done` closes the dialog on a run that answers at once,
    where the default says "Done" over a Close button; a run that starts a job is watched
    either way. `hand_off` is given the job's id instead, and the dialog closes at once: a
    run that takes minutes is followed where jobs are followed, not held open here.
    `values` are answers the flow starts with, and `until` a step it is walked to, on
    them, before the dialog is drawn. The browser's Back presses the dialog's own Back, and
    closes the dialog only on a step that has none. `roomy` gives the card the larger size,
    for a flow whose steps draw a picture beside a list.

    True when the flow was run and the dialog closed after it, False when it was cancelled
    before that. In a walk over rows (`walking`, `place` saying where in it) there is no
    Cancel: Skip answers `walks.SKIPPED` and leaves the row as it was, Stop answers
    `walks.STOPPED` and ends the run.
    """
    walk = Walk(calls, await calls.first(None))
    if values:
        walk.values.update(values)
        if until:
            waiting = ui.notification(t("manager.ext_action.preparing"), spinner=True,
                                      timeout=None)
            try:
                await walk.reach(until)
            except Exception as exc:  # noqa: BLE001 - the first step is still a fair place to start
                ui.notify(t("manager.ext_action.step_failed"), caption=why(exc),
                          type="negative")
            finally:
                waiting.dismiss()
    client = ApiClient()
    held: dict[str, tuple[str, Any]] = {}
    opened = False
    current_key = ""

    card = "manager-wizard-card manager-wizard-card--roomy" if roomy else "manager-wizard-card"
    with frame.opened("", ignore_outside_click=True, classes=card) as dialog:
        track = ui.element("div").classes("w-full")
        with ui.element("div").classes("manager-wizard-scroll") as scroll:
            crest = ui.element("div").classes("manager-wizard-crest")
            heading = ui.label("").classes("manager-dialog-title manager-wizard-title") \
                .props("tabindex=-1")
            body = ui.column().classes("w-full gap-0 manager-wizard-body")
        more = more_below.attach(scroll)
        wait = ui.element("div").classes("w-full manager-wizard-wait")
        buttons = frame.footer()

        def draw(found: dict) -> None:
            nonlocal held, opened, current_key
            fresh = current_key != str(found.get("step") or "")
            current_key = str(found.get("step") or "")
            heading.text = str(found.get("title") or label)
            track.clear()
            body.clear()
            buttons.clear()
            summary = found.get("summary")
            # Setup's own, since a callable cannot travel as JSON: `gate` says whether the
            # answer is complete, asked again after every change; `before()` draws above
            # the fields, `draw(values)` below them, writing into the values itself, and
            # `after(values)` below the notes.
            gate: Callable[[dict[str, Any]], bool] | None = found.get("gate")
            # Whether the answer as it stands is the last, for a step that carries a
            # `confirm` but may still lead on: where it says no, the forward button is Next.
            ends: Callable[[dict[str, Any]], bool] | None = found.get("ends")
            # Told the values after any control has written one, for a step that shows
            # the effect of a choice as it is made.
            changed: Callable[[dict[str, Any]], None] | None = found.get("changed")
            forward: list[Any] = []

            def foot() -> None:
                buttons.clear()
                forward.clear()
                with buttons:
                    if aside:
                        frame.aside(aside, lambda: dialog.submit(False), icon=verbs.CLOSE)
                    if walking:
                        frame.leave(dialog, True)
                    if walk.history:
                        frame.quiet(t("word.back"), _back, icon=verbs.BACK) \
                            .classes(deeplink.BACK_STEP)
                    elif not aside and not walking:
                        frame.cancel(lambda: dialog.submit(False))
                    if summary:
                        go = frame.answer(str(found.get("confirm") or label or t("word.run")),
                                           _start, icon=verbs.RUN)
                    elif found.get("confirm") and (ends is None or ends(walk.values)):
                        go = frame.answer(str(found["confirm"]), _start, icon=verbs.DONE)
                    else:
                        go = frame.answer(t("word.next"), _next, icon=verbs.NEXT)
                    if not found.get("ready", True):
                        go.disable()
                    elif gate:
                        forward.append(go)
                        go.set_enabled(bool(gate(walk.values)))
                go.classes(_FORWARD)

            def regate() -> None:
                if changed is not None:
                    changed(walk.values)
                if ends is not None:
                    foot()
                elif forward and gate:
                    forward[0].set_enabled(bool(gate(walk.values)))
            welcome = _greeting(found, scroll, crest, mark)
            if found.get("steps"):
                with track:
                    _step_row(list(found["steps"]), current_key, _goto)
            with body:
                if place:
                    ui.label(place).classes("manager-help px-3")
                if found.get("help"):
                    ui.label(str(found["help"])).classes("manager-wizard-lead")
                if summary:
                    _summary(summary)
                if found.get("before"):
                    found["before"]()
                held = _controls(list(found.get("fields") or []), walk.values,
                                 client=client, errors=dict(found.get("errors") or {}),
                                 welcome=welcome,
                                 on_change=regate if gate or ends or changed else None)
                if found.get("draw"):
                    found["draw"](walk.values)
                if found.get("acts"):
                    _acts(list(found["acts"]), _act)
                _lines(list(found.get("notes") or []),
                       t("manager.ext_action.worth_knowing") if summary else "")
                if found.get("after"):
                    found["after"](walk.values)
                if not found.get("ready", True):
                    ui.label(str(found.get("reason") or "")).classes("manager-help px-3")
            foot()
            # held keeps insertion order, so its first entry is the step's first field.
            first = next(iter(held.values()), None)
            control = first[1] if first else None
            if not opened:
                opened = True
                if control is not None:
                    frame.focus(dialog, control)
                # Bound once: a listener added on every redraw would pile up on the
                # same persistent dialog element, one more for every step visited.
                dialog.on("show", lambda: ui.run_javascript(
                    _enter_presses_forward(dialog.id)))
                dialog.on("show", lambda: ui.run_javascript(more_below.watch(scroll, more)))
            elif fresh:
                _focus_title(heading)
            elif control is not None:
                _focus_now(control)

        @on_page
        async def _next() -> None:
            _sync(walk.values, held)
            try:
                found = await _asking(calls, wait, buttons, walk.next())
            except Exception as exc:  # noqa: BLE001
                ui.notify(t("manager.ext_action.step_failed"), caption=why(exc),
                          type="negative")
                return
            draw(found)

        def _back() -> None:
            _sync(walk.values, held)
            draw(walk.back())

        @on_page
        async def _act(key: str) -> None:
            _sync(walk.values, held)
            try:
                found = await _asking(calls, wait, buttons, walk.act(key))
            except Exception as exc:  # noqa: BLE001
                ui.notify(t("manager.ext_action.step_failed"), caption=why(exc),
                          type="negative")
                return
            draw(found)

        @on_page
        async def _goto(step_key: str) -> None:
            _sync(walk.values, held)
            try:
                found = await walk.goto(step_key)
            except Exception as exc:  # noqa: BLE001
                ui.notify(t("manager.ext_action.step_failed"), caption=why(exc),
                          type="negative")
                return
            draw(found)

        async def _start() -> None:
            # Escape cannot leave a run: the report it ends on is drawn in this dialog.
            dialog.props("persistent")
            # Nor can the browser's Back press Back: it falls to Escape, which this refuses.
            stepping = [one for one in buttons.default_slot.children
                        if deeplink.BACK_STEP in one.classes]
            for one in stepping:
                one.classes(remove=deeplink.BACK_STEP)
            try:
                await _run()
            finally:
                if not dialog.is_deleted:
                    dialog.props(remove="persistent")
                    for one in stepping:
                        if not one.is_deleted:
                            one.classes(deeplink.BACK_STEP)

        @on_page
        async def _run() -> None:
            _sync(walk.values, held)
            try:
                started = await _asking(calls, wait, buttons, walk.run())
            except Exception as exc:  # noqa: BLE001
                ui.notify(t("said.start_failed"), caption=why(exc), type="negative")
                return
            if started.get("step"):
                draw(started)
                return
            job_id = str(started.get("job_id") or "")
            if not job_id and started.get("ok") is False:
                ui.notify(str(started.get("reason") or t("manager.ext_action.not_run")),
                          type="negative")
                return
            if close_when_done and not job_id:
                if walking and started.get("message"):
                    ui.notify(str(started["message"]))
                dialog.submit(True)
                return
            if job_id and hand_off is not None:
                hand_off(job_id)
                dialog.submit(True)
                return
            track.clear()
            # Escape on the report is Close: the run happened, and callers redraw on True.
            dialog.escape_answer = True
            if job_id:
                await _watch(calls.job, job_id, body=body, buttons=buttons, heading=heading,
                            under=under, close=lambda: dialog.submit(True))
                return
            # Finished already. Some actions are one call and a sentence, and making
            # those wear a progress bar would be theatre.
            _finished(body, buttons, lambda: dialog.submit(True), started)

        draw(walk.step)

    answer = await dialog
    return answer if walking else bool(answer)


async def open_page(*, label: str, calls: Calls, under: str, step: str | None = None,
                    mark: bool = False) -> None:
    """Run one flow as a Manager view: the same steps the dialog host walks, drawn on
    the page instead of in a dialog, and the current step kept in the address so a
    reload comes back to it.

    `step` is the key the caller read off its own address, if any - the flow answers
    `GET` with it, the way a done step reopens. Answering `None` gets its first step,
    same as the dialog host. `mark` is the dialog host's.
    """
    walk = Walk(calls, await calls.first(step))
    client = ApiClient()
    held: dict[str, tuple[str, Any]] = {}
    opened = False
    current_key = ""

    with ui.column().classes("w-full gap-0 manager-wizard-page") as page:
        track = ui.element("div").classes("w-full")
        with ui.element("div").classes("w-full") as content:
            crest = ui.element("div").classes("manager-wizard-crest")
            heading = ui.label("").classes("manager-dialog-title manager-wizard-title") \
                .props("tabindex=-1")
            body = ui.column().classes("w-full min-w-0 gap-0")
        wait = ui.element("div").classes("w-full manager-wizard-wait")
        buttons = frame.footer()

        def draw(found: dict) -> None:
            nonlocal held, opened, current_key
            fresh = current_key != str(found.get("step") or "")
            current_key = str(found.get("step") or "")
            heading.text = str(found.get("title") or label)
            track.clear()
            body.clear()
            buttons.clear()
            summary = found.get("summary")
            welcome = _greeting(found, content, crest, mark)
            if found.get("steps"):
                with track:
                    _step_row(list(found["steps"]), current_key, _goto)
            with body:
                if found.get("help"):
                    ui.label(str(found["help"])).classes("manager-wizard-lead")
                if summary:
                    _summary(summary)
                held = _controls(list(found.get("fields") or []), walk.values,
                                 client=client, errors=dict(found.get("errors") or {}),
                                 welcome=welcome)
                if found.get("acts"):
                    _acts(list(found["acts"]), _act)
                _lines(list(found.get("notes") or []),
                      t("manager.ext_action.worth_knowing") if summary else "")
                if not found.get("ready", True):
                    ui.label(str(found.get("reason") or "")).classes("manager-help px-3")
            with buttons:
                if walk.history:
                    frame.quiet(t("word.back"), _back, icon=verbs.BACK)
                if summary:
                    go = frame.answer(str(found.get("confirm") or label or t("word.run")),
                                       _start, icon=verbs.RUN)
                elif found.get("confirm"):
                    go = frame.answer(str(found["confirm"]), _start, icon=verbs.DONE)
                else:
                    go = frame.answer(t("word.next"), _next, icon=verbs.NEXT)
                if not found.get("ready", True):
                    go.disable()
            go.classes(_FORWARD)
            _sync_step(current_key)
            # held keeps insertion order, so its first entry is the step's first field.
            first = next(iter(held.values()), None)
            control = first[1] if first else None
            if not opened:
                opened = True
                if control is not None:
                    _focus_now(control)
                # Bound once: a listener added on every redraw would pile up on the
                # same page element, one more for every step visited.
                ui.run_javascript(_enter_presses_forward(page.id))
            elif fresh:
                _focus_title(heading)
            elif control is not None:
                _focus_now(control)

        @on_page
        async def _next() -> None:
            _sync(walk.values, held)
            try:
                found = await _asking(calls, wait, buttons, walk.next())
            except Exception as exc:  # noqa: BLE001
                ui.notify(t("manager.ext_action.step_failed"), caption=why(exc),
                          type="negative")
                return
            draw(found)

        def _back() -> None:
            _sync(walk.values, held)
            draw(walk.back())

        @on_page
        async def _act(key: str) -> None:
            _sync(walk.values, held)
            try:
                found = await _asking(calls, wait, buttons, walk.act(key))
            except Exception as exc:  # noqa: BLE001
                ui.notify(t("manager.ext_action.step_failed"), caption=why(exc),
                          type="negative")
                return
            draw(found)

        @on_page
        async def _goto(step_key: str) -> None:
            _sync(walk.values, held)
            try:
                found = await walk.goto(step_key)
            except Exception as exc:  # noqa: BLE001
                ui.notify(t("manager.ext_action.step_failed"), caption=why(exc),
                          type="negative")
                return
            draw(found)

        @on_page
        async def _start() -> None:
            _sync(walk.values, held)
            try:
                started = await _asking(calls, wait, buttons, walk.run())
            except Exception as exc:  # noqa: BLE001
                ui.notify(t("said.start_failed"), caption=why(exc), type="negative")
                return
            if started.get("step"):
                draw(started)
                return
            job_id = str(started.get("job_id") or "")
            if not job_id and started.get("ok") is False:
                ui.notify(str(started.get("reason") or t("manager.ext_action.not_run")),
                          type="negative")
                return
            track.clear()
            if job_id:
                # No `close`: nothing here is a dialog to dismiss.
                await _watch(calls.job, job_id, body=body, buttons=buttons, heading=heading,
                            under=under, close=None)
                return
            _finished(body, buttons, None, started)

        draw(walk.step)


def _summary(rows: list[Sequence[Any]]) -> None:
    """What the previous steps add up to, before anything is done about it.

    A row of one is a heading rather than a fact. Where a summary covers both what was
    chosen and what it comes to, running the two together makes a count read as one more
    setting - and the counts are the part somebody is deciding on.
    """
    panel.facts(ui, [(panel.HEADING, str(row[0])) if len(row) < 2
                     else (str(row[0]), str(row[1]))
                     for row in rows])


def _worded(said: Any) -> tuple[str, str]:
    if isinstance(said, dict):
        detail = str(said.get("detail") or "")
        text = str(said.get("text") or "")
        return (text, detail) if text else (detail, "")
    return str(said), ""


def _line(text: str, detail: str, to: str = "") -> None:
    if not to:
        panel.line(text, hint=detail, classes="manager-help px-3")
        return
    with ui.element("div").classes("manager-help px-3"):
        panel.link_out(text, to=to, hint=detail, rel="noopener noreferrer")()


def _lines(lines: list[Any], title: str) -> None:
    if not lines:
        return
    if title:
        panel.facts(ui, [(panel.HEADING, title)])
    for line in lines:
        to = str(line.get("to") or "") if isinstance(line, dict) else ""
        _line(*_worded(line), to=to if sites.is_web(to) else "")


# The runs each page drew the report of itself, which that page's toast for a finished run
# leaves alone. Per page: a run watched on one is still news on another. Weak, so a page
# that has gone takes its set with it.
_WATCHED: weakref.WeakKeyDictionary[Any, set[str]] = weakref.WeakKeyDictionary()


def watching(job_id: str) -> bool:
    """Whether this page is drawing the report of `job_id` itself."""
    return job_id in _WATCHED.get(ui.context.client, ())


async def _watch(poll: Callable[[str], Awaitable[dict]], job_id: str, *, body: Any,
                 buttons: Any, heading: Any, under: str,
                 close: Callable[[], Any] | None) -> None:
    """Poll a run that answered with a `job_id` instead of finishing, then draw what
    it came to. `close` is the dismiss button's own action; a page has nothing to
    dismiss to and draws no button for it.
    """
    _WATCHED.setdefault(ui.context.client, set()).add(job_id)
    body.clear()
    buttons.clear()
    with body, ui.column().classes("w-full gap-1 px-3"):
        bar = ui.linear_progress(value=0, show_value=False).classes("w-full")
        said = ui.label(t("manager.ext_action.working")).classes("manager-help")
    stop = None
    if close is not None:
        with buttons:
            stop = frame.cancel(close, t("word.close"))
            stop.disable()

    while True:
        job = await poll(job_id)
        bar.value = int(job.get("pct") or 0) / 100
        said.text = str(job.get("message") or t("manager.ext_action.working"))
        if str(job.get("state")) not in ("running", "queued"):
            break
        await run.io_bound(_wait)

    if stop is not None:
        stop.enable()
    heading.text = t("manager.ext_action.what_happened") if job.get("state") == "done" \
        else t("manager.ext_action.not_finish")
    report(body, job, under)


def _finished(body: Any, buttons: Any, close: Callable[[], Any] | None, answer: dict) -> None:
    """A flow that ran and is done, with whatever it wants to say about it. `close` is
    the dismiss action, drawn only where there is one."""
    body.clear()
    buttons.clear()
    with body:
        said = str(answer.get("message") or t("word.done"))
        ui.label(said).classes("manager-help px-3")
        facts = [(one[0], one[1]) for one in (answer.get("summary") or [])]
        if facts:
            panel.facts(ui, facts)
    if close is not None:
        with buttons:
            frame.cancel(close, t("word.close"))


def _compare(rows: list[dict]) -> None:
    """Expected beside actual, and what is short.

    Every row, including the ones that agree: a line that appears only when something
    went wrong makes a clean import read as a report with things missing from it.
    """
    entries = []
    for row in rows:
        want, got = int(row.get("expected", 0)), int(row.get("actual", 0))
        short = want - got
        entries.append((str(row.get("label") or row.get("key") or ""),
                        _counts(want, got, short)))
    panel.facts(ui, entries)


def _counts(want: int, got: int, short: int) -> Any:
    def draw() -> None:
        with ui.row().classes("items-center gap-2"):
            ui.label(str(got)).classes("manager-fact-value")
            if short:
                # The number alone cannot say whether it is right. What was expected is
                # what makes a shortfall visible without anybody counting.
                ui.label(t("manager.ext_action.of_expected", want=want)).classes("manager-help")
                ui.label(t("manager.ext_action.short", short=(short))).classes(
                    "manager-member-chip manager-chip-warn")
    return draw


def _wait() -> None:
    import time

    time.sleep(POLL_SECONDS)


def _show_all(job: dict, group: str) -> None:
    """The link to a group's games in Tables, where it holds more than a few."""
    found = report_groups.groups(job).get(group)
    if found is None or len(found.game_ids) <= report_groups.SHOW_ALL_OVER:
        return
    with ui.row().classes("px-3"):
        panel.link(t("manager.report.show_all", count=len(found.game_ids)),
                   to=report_groups.tables_address(str(job.get("id") or ""), group),
                   window=report_groups.MANAGER_WINDOW)()


def report(body: Any, job: dict, under: str, *, limit: int | None = 20) -> None:
    """What happened, once it has. The counts, and every game that was not imported -
    a run that says only "done" leaves somebody to find the gaps themselves.

    `under` is the flow's own place in its catalog. `limit` is how many of a list to
    name before "and N more"; None names all of it.
    """
    body.clear()
    with body:
        if job.get("state") == "failed":
            ui.label(str(job.get("error") or t("manager.ext_action.not_finish"))) \
                .classes("manager-help px-3")
            return
        result = job.get("result") or {}
        compared = list(result.get("against") or [])
        for has_report, draw_report in REPORT_DRAWERS:
            if has_report(job):
                draw_report(job)
                return
        if compared:
            _compare(compared)
        elif result:
            panel.facts(ui, [(literal_or("", f"{under}.result.{key}", fallback=str(key))[0],
                              str(value))
                             for key, value in result.items()
                             if isinstance(value, (int, str))])
        _show_all(job, report_groups.IMPORTED)
        held = list(result.get("already_here") or [])
        if held:
            panel.facts(ui, [(panel.HEADING, t("manager.ext_action.already", count=len(held)))])
            for row in held[:limit]:
                name = row.get("name") or row.get("key")
                ui.label(t("manager.ext_action.matched", name=name, how=row["how"])
                         if row.get("how") else str(name)).classes("manager-help px-3")
            if limit is not None and len(held) > limit:
                ui.label(t("manager.ext_action.more",
                        count=len(held) - limit)).classes("manager-help px-3")
            _show_all(job, report_groups.ALREADY_HERE)
        missed = [row for row in (result.get("rows") or []) if row.get("error")]
        if missed:
            panel.facts(ui, [(panel.HEADING,
                              t("manager.ext_action.not_found", count=len(missed)))])
            for row in missed[:limit]:
                error, detail = _worded(row["error"])
                _line(t("manager.ext_action.missed", name=(row.get("name") or row.get("key")),
                        error=error), detail)
            if limit is not None and len(missed) > limit:
                ui.label(t("manager.ext_action.more",
                        count=len(missed) - limit)).classes("manager-help px-3")

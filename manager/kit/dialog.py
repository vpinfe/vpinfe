"""The Manager's dialogs: the card to draw in, an Escape that can answer, and the footer buttons."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Generator, Iterator
from contextlib import contextmanager
from typing import Any

from nicegui import ui
from nicegui.events import ValueChangeEventArguments

from common.i18n import t
from manager.kit import panel, verbs, walks
from manager.kit.on_page import on_page

_UNSET: Any = object()


class Dialog(ui.dialog):
    """A dialog that can answer for Escape: once Escape has left it, `await box` gives
    `escape_answer` if one is set, whatever `submit` said.

    NiceGUI clears a dialog's result when it hides, so an answer cannot be handed over by
    the Escape event itself. A dialog is awaited once; the answer is not reset.
    """

    def __init__(self) -> None:
        super().__init__()
        self.escape_answer: Any = _UNSET
        self._escaped = False
        self.on("escape-key", self._heard_escape)

    def _heard_escape(self) -> None:
        self._escaped = True

    def __await__(self) -> Generator[Any, None, Any]:
        answered = yield from super().__await__()
        if self._escaped and self.escape_answer is not _UNSET:
            return self.escape_answer
        return answered


SIZES = ("question", "form", "wide", "large", "full")


@contextmanager
def opened(title: str, *, size: str = "form", tall: bool = False,
           persistent: bool = False, ignore_outside_click: bool = False,
           classes: str = "") -> Iterator[Dialog]:
    """Draw the dialog's contents inside; the caller awaits the dialog it yields.

    `size` is one of `SIZES`: a question, a form, a wide form, a large list, or the whole
    screen. `tall` fixes the height, for a list that scrolls inside it. The frame draws the
    side margin, the title rule and the footer rule, so a module sets none of them.

    A title of "" draws none, for a dialog whose title changes as it goes.

    Quasar's `persistent` ignores Escape as well as a click outside. A dialog with typed
    input takes `ignore_outside_click` instead, so Escape still cancels. `persistent` is
    for a run in progress or an answer that cannot be left unsaid.
    """
    if size not in SIZES:
        raise ValueError(f"a dialog is one of {SIZES}, not {size!r}")
    props = " ".join(p for p in ("persistent" if persistent else "",
                                 "no-backdrop-dismiss" if ignore_outside_click else "",
                                 "maximized" if size == "full" else "") if p)
    dialog = made().props(props)
    with dialog, ui.card().classes(" ".join(("manager-dialog",
                                             "" if size == "form" else f"manager-dialog--{size}",
                                             "manager-dialog--tall" if tall else "",
                                             classes)).strip()):
        if title:
            ui.label(title).classes("manager-dialog-title")
        yield dialog


@contextmanager
def band(name: str, trailing: str = "") -> Iterator[ui.row]:
    """A heading over what follows, with its count or one quiet act at the right.

    `trailing` is a count or a short word; a control goes inside the context instead.
    """
    with ui.row().classes("manager-band"):
        ui.label(name).classes("manager-fact-heading manager-band-name")
        if trailing:
            ui.label(trailing).classes("manager-band-trailing manager-band-count")
        with ui.row().classes("manager-band-trailing items-center gap-1") as holder:
            yield holder
        if not holder.default_slot.children:
            holder.delete()


@contextmanager
def well(*, scrolls: bool = False) -> Iterator[ui.column]:
    """The bordered panel a list sits in. A `scrolls` well takes the height left by the
    dialog's `tall`; its `manager-well-rows` column scrolls and what is above it stays put."""
    with ui.column().classes("manager-well" + (" manager-well--scrolls" if scrolls else "")) as box:
        yield box


@contextmanager
def pane() -> Iterator[ui.column]:
    """What a picked entry is compared with, beside the list. Goes inside `body(split=True)`."""
    with ui.column().classes("manager-pane gap-2") as box:
        yield box


@contextmanager
def body(*, split: bool = False) -> Iterator[ui.element]:
    """The column between the title and the footer. `split` lays a list and a `pane`
    side by side, level at the top and the bottom, and stacks them on a narrow screen."""
    with ui.element("div").classes("manager-dialog-body"
                                   + (" manager-dialog-body--split" if split else "")) as box:
        yield box


@contextmanager
def sections() -> Iterator[tuple[ui.element, ui.element]]:
    """A rail of areas with the open area's page beside it, for a dialog that is one subject
    in several parts. Yields the rail and the page; the page takes the height `tall` leaves
    and scrolls what does not fit, and keeps its own margin from the rail."""
    with ui.element("div").classes("manager-sections manager-dialog-sections"):
        rail = ui.element("div").classes("manager-section-rail")
        work = ui.element("div").classes("manager-section-work")
        yield rail, work


def tabs(on_change: Callable[[Any], Any] | None = None) -> ui.tabs:
    """The strip over a dialog's panels: put `ui.tab`s in it, then `ui.tab_panels`."""
    strip = ui.tabs(on_change=on_change).props("dense align=left no-caps") \
        .classes("manager-dialog-tabs w-full")
    return strip


def on_escape(box: Dialog, act: Callable[[], Any]) -> None:
    """Run `act` on Escape. A `persistent` dialog fires no Escape event, so `box` takes
    `ignore_outside_click`; an awaited one sets `box.escape_answer` instead."""
    box.on("escape-key", act)


def made() -> Dialog:
    """An empty dialog, for one that is not a question and draws its own card.

    It is deleted once hidden, so it opens once; a `hide` listener goes on before that.
    """
    with ui.element() as origin:
        origin.visible = False
        dialog = Dialog()

    def gone() -> None:
        if dialog.value:
            return
        dialog.delete()
        if not origin.is_deleted:
            origin.delete()

    def opening(event: ValueChangeEventArguments) -> None:
        if event.value and not listening:
            listening.append(True)
            dialog.on("hide", gone)

    listening: list[bool] = []
    dialog.on_value_change(opening)
    return dialog


def footer() -> ui.row:
    return ui.row().classes("manager-dialog-footer")


def said(control: Any, message: str) -> None:
    """Put `message` under a field made with `room`, or take it away where it is empty."""
    control.props["error"] = bool(message)
    control.props["error-message"] = message
    control.update()


def field(value: str = "", *, placeholder: str = "", lines: int = 0,
          browse: Callable[[str], Awaitable[str | None]] | None = None,
          default: str = "", whose: str = "", left_empty: str = "",
          name: str = "", room: bool = False, readonly: bool = False,
          browse_tip: str = "") -> Any:
    """The panel's own field, answering nothing until the dialog does.

    `browse` takes the field's current value and returns the path picked, or None; what
    it returns replaces the text.

    A path field given `default` or `left_empty` is the panel's own, which draws what an
    empty field stands for. Its value is that default while empty.

    `name` is what a screen reader calls the field, the label its row shows. `room` keeps
    the line under the field free for `said`, so an error does not resize the dialog, and
    takes the error away once the value changes.

    A `readonly` field takes no typing: the box opens `browse` wherever it is pressed, and
    `browse_tip` names that button.
    """
    if browse is not None and (default or left_empty):
        held: list[Any] = []
        panel.field(value, lambda _text: None, placeholder=placeholder,
                    left_empty=left_empty, default=default, whose=whose, browse=browse,
                    on_control=held.append)()
        return held[0]
    with ui.element("div").classes("manager-fact-edit"):
        if lines:
            control: Any = ui.textarea(value=value, placeholder=placeholder) \
                .props(f"dense borderless rows={lines} debounce=0") \
                .classes("manager-edit-field")
        else:
            control = ui.input(value=value, placeholder=placeholder) \
                .props("dense borderless debounce=0").classes("manager-edit-field")
        if name:
            control.props["aria-label"] = name
        if room:
            control.props["bottom-slots"] = True
            control.on_value_change(lambda: said(control, ""))
        if lines:
            return control
        if browse is not None:
            @on_page
            async def browsed() -> None:
                chosen = await browse(control.value or "")
                if chosen and not control.is_deleted:
                    control.value = chosen
                    panel.path_ends(control)

            control.classes("manager-adorned")
            panel.path_input(control)
            with control.add_slot("append"):
                panel.path_buttons(control, browsed, browse_tip)
            if readonly:
                control.props("readonly")
                control.on("click", browsed, js_handler=panel.OPENS_ON_CLICK_JS)
        return control


def quiet(label: str, on_click: Callable[[], Any], *, icon: str) -> ui.button:
    return ui.button(label, icon=icon, on_click=on_click).props("flat no-caps")


def cancel(on_click: Callable[[], Any], label: str = "") -> ui.button:
    return quiet(label or t("word.cancel"), on_click, icon=verbs.CANCEL)


def leave(box: Dialog, walking: bool) -> None:
    """The way out without answering. In a walk it is Skip, which leaves this row as it was,
    and Stop, which ends the run. Escape in a walk is Stop.
    """
    if walking:
        quiet(t("word.stop"), lambda: box.submit(walks.STOPPED), icon=verbs.STOP)
        quiet(t("word.skip"), lambda: box.submit(walks.SKIPPED), icon=verbs.SKIP)
        box.escape_answer = walks.STOPPED
    else:
        cancel(lambda: box.submit(walks.SKIPPED))


def aside(label: str, on_click: Callable[[], Any], *, icon: str) -> ui.button:
    """A second act, quiet and on the far left, away from the answer."""
    return quiet(label, on_click, icon=icon).classes("manager-dialog-aside")


def answer(label: str, on_click: Callable[[], Any], *, icon: str,
           danger: bool = False) -> ui.button:
    return ui.button(label, icon=icon, on_click=on_click) \
        .props("no-caps" + (" color=negative" if danger else ""))


def focus(box: ui.dialog, control: Any, *, select: bool = False) -> None:
    """The caret in `control` once the dialog is up, or with `select` its text selected
    so typing replaces it. Quasar's autofocus does not land in a dialog, and anything
    earlier than `show` is overridden by its own focus.

    Steps down to the first `input` or `textarea` inside `control`, or focuses `control`
    itself where there is none. A path field draws no element under its own id, only its
    input, so that is where it looks.
    """
    then = ";requestAnimationFrame(() => target.select())" if select else ""
    box.on("show", lambda: ui.run_javascript(
        "((field) => { const target = field.matches('input,textarea') ? field"
        f" : (field.querySelector('input,textarea') || field); target.focus(){then}; }})"
        f"(document.getElementById('c{control.id}')"
        f" || document.getElementById('{panel.native_id(control)}'))"))


def enter_presses(button: ui.button) -> None:
    """Enter anywhere in the dialog but a textarea or a control that takes Enter itself
    presses `button`, once the key was pressed and let go on the same element. Bound in the
    browser: NiceGUI does not forward a keyup from a Quasar input inside a dialog."""
    ui.run_javascript(f"""
        (() => {{
          let tries = 0;
          const wire = () => {{
            const button = document.getElementById('c{button.id}');
            if (!button) {{ if (++tries < 40) setTimeout(wire, 25); return; }}
            const dialog = button.closest('.q-dialog');
            let pressed = null;
            dialog.addEventListener('keydown', (event) => {{
              pressed = event.key === 'Enter' ? event.target : null;
            }});
            dialog.addEventListener('keyup', (event) => {{
              const origin = pressed;
              pressed = null;
              if (event.key !== 'Enter' || origin !== event.target) return;
              if (event.target.tagName === 'TEXTAREA') return;
              if (event.target.closest('button, a, [role=button], [role=menuitem]')) return;
              button.click();
            }});
          }};
          wire();
        }})()
    """)

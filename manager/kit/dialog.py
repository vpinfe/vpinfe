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


@contextmanager
def opened(title: str, *, wide: bool = False, full: bool = False,
           persistent: bool = False, ignore_outside_click: bool = False,
           classes: str = "") -> Iterator[Dialog]:
    """Draw the dialog's contents inside; the caller awaits the dialog it yields.

    A title of "" draws none, for a dialog whose title changes as it goes.

    Quasar's `persistent` ignores Escape as well as a click outside. A dialog with typed
    input takes `ignore_outside_click` instead, so Escape still cancels. `persistent` is
    for a run in progress or an answer that cannot be left unsaid.
    """
    props = " ".join(p for p in ("persistent" if persistent else "",
                                 "no-backdrop-dismiss" if ignore_outside_click else "",
                                 "maximized" if full else "") if p)
    dialog = made().props(props)
    with dialog, ui.card().classes(" ".join(("manager-dialog",
                                             "manager-dialog--wide" if wide else "",
                                             "manager-dialog--full" if full else "",
                                             classes)).strip()):
        if title:
            ui.label(title).classes("manager-dialog-title")
        yield dialog


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
    """Enter anywhere in the dialog but a textarea presses `button`. Bound in the
    browser: NiceGUI does not forward a keyup from a Quasar input inside a dialog."""
    ui.run_javascript(f"""
        (() => {{
          let tries = 0;
          const wire = () => {{
            const button = document.getElementById('c{button.id}');
            if (!button) {{ if (++tries < 40) setTimeout(wire, 25); return; }}
            button.closest('.q-dialog').addEventListener('keyup', (event) => {{
              if (event.key === 'Enter' && event.target.tagName !== 'TEXTAREA') button.click();
            }});
          }};
          wire();
        }})()
    """)

"""Putting text on the clipboard, and the fallback when the browser will not."""

from __future__ import annotations

from nicegui import ui

from common.i18n import t
from manager.kit import dialog as frame

# Both halves run in the browser at the moment of the click, which is the whole point.
# The clipboard API is a secure-context feature and http://cab.local:8001 - the ordinary
# way this install is reached from another machine - is not one, so the old command is
# what is left there. It needs the click's own permission, and a Manager click that goes
# to the server and back has spent it by the time anything runs.
COPY_JS = """() => {
  const text = %s;
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(() => emit(true), () => emit(false));
    return;
  }
  const box = document.createElement('textarea');
  box.value = text;
  box.style.position = 'fixed';
  box.style.top = '-1000px';
  document.body.appendChild(box);
  box.focus();
  box.select();
  let done = false;
  try { done = document.execCommand('copy'); } catch (e) { done = false; }
  document.body.removeChild(box);
  emit(done);
}"""


def copied(done: bool, text: str) -> None:
    """Copied, or shown so it can be copied by hand.

    A button that silently does nothing is worse than no button. Where the browser
    refused - an old command in a page it does not trust - the text goes on screen
    already selected.
    """
    if done:
        ui.notify(t("word.copied"), type="positive")
        return
    _show_to_copy(text)


def _show_to_copy(text: str) -> None:
    with frame.opened(t("manager.about.clipboard_blocked"), size="wide") as box:
        ui.label(t("manager.about.manual_copy")).classes("manager-help")
        with ui.element("div").classes("w-full"):
            ui.textarea(value=text).props("outlined readonly rows=18") \
                .classes("w-full manager-log")
        with frame.footer():
            frame.cancel(box.close, t("word.close"))
    box.open()
    # Found in the document rather than through the element's own id: `getElement`
    # answers with the Vue component, whose root here is a fragment, so there is no
    # node on it to search. One dialog is open, and it holds one text box.
    #
    # Half a second in, because the dialog focuses itself on the way in and undoes an
    # earlier selection - which would leave the line above promising something untrue.
    # On focus as well, so a click into the box cannot leave it unselected.
    ui.timer(0.5, lambda: ui.run_javascript(
        '(() => { const t = document.querySelector(".q-dialog textarea"); '
        'if (!t) return; '
        'if (!t.dataset.selectAll) { t.dataset.selectAll = "1"; '
        't.addEventListener("focus", () => t.select()); } '
        't.focus(); t.select(); })()'), once=True)

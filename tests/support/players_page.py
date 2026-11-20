"""What a drive of the Players page reads: the selectors for its controls and the small
helpers that turn a page's text and a picture into something a test can compare."""

from __future__ import annotations

import io
import json
from typing import cast

from PIL import Image

SHOWN = "(el => el.getClientRects().length > 0)"
DIALOG = ".q-dialog"
FOOTER = f"{DIALOG} .manager-dialog-footer .q-btn"
# What a control says: its words, or its icon's name where it has none.
SAYS = ("(el => { const copy = el.cloneNode(true);"
        " copy.querySelectorAll('.q-icon, .material-icons').forEach(i => i.remove());"
        " const icon = el.querySelector('.q-icon, .material-icons');"
        " return copy.textContent.trim() || (icon ? icon.textContent.trim() : ''); })")
# The index of the first shown element under `%s` that says `%s`, or -1.
AT = (f"(() => [...document.querySelectorAll(%s)].findIndex(el => {SHOWN}(el)"
      f" && {SAYS}(el) === %s))()")
TEXT = "(el => el ? el.innerText.trim().replace(/\\s+/g, ' ') : null)(document.querySelector(%s))"
WORK = ".manager-section-work"
RAIL = ".manager-section-row"
ACTS = f"{WORK} .manager-slot-actions .q-btn"
SHOWN_ACTS = f"[...document.querySelectorAll('{ACTS}')].filter({SHOWN})"
TITLE = ("(el => el ? el.innerText.trim() : null)"
         "(document.querySelector('.manager-panel-header .manager-workbench-title'))")
RAIL_NAMES = (f"[...document.querySelectorAll('{RAIL}')].filter({SHOWN})"
              ".map(el => el.innerText.trim().split('\\n')[0])")
# A dialog that has finished opening: Quasar scales it in, and a click on the way lands
# somewhere else.
DIALOG_UP = ("[...document.querySelectorAll('.q-dialog .q-card')].some(card =>"
             " getComputedStyle(card.closest('.q-dialog__inner')).transform === 'none'"
             " && card.getBoundingClientRect().width > 0)")
DIALOG_TEXT = ("(() => { const box = [...document.querySelectorAll('.q-dialog')].pop();"
               " return box ? box.innerText.trim() : null; })()")


NAME_LINES = ".manager-cell-identifier .manager-cell-lines"
CIRCLE = ".manager-cell-identifier .manager-cell-art-box--circle"
PERSON = f"{WORK} .manager-slot-art--person"
NOTICE = ".q-notification"


def _cell(player_id: str, part: str) -> str:
    return f".ag-row[row-id={json.dumps(player_id)}] {part}"


def _centre(data: bytes) -> tuple[int, int, int]:
    """The color at the middle of a picture."""
    with Image.open(io.BytesIO(data)) as shown:
        rgb = shown.convert("RGB")
        return cast(tuple[int, int, int], rgb.getpixel((rgb.width // 2, rgb.height // 2)))


def _text(selector: str) -> str:
    return TEXT % json.dumps(selector)

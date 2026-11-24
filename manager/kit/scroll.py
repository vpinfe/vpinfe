"""A redraw that keeps the person's place in the list.

Clearing a container and filling it again leaves the page one layout pass with nothing
in it, and the browser clamps the scroller to the top before the new rows exist. `held`
reads the position first and puts it back once the redraw has been sent.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from nicegui import ui

# The workbench's own scroller: the panel body that every list and card in it draws into.
BODY = ".manager-workbench-body"

_STORE = "window.__managerHeldTop = (document.querySelector(%s) || {}).scrollTop || 0;"

# Retried over a few frames because the rows arrive in a message of their own: the first
# frame after the redraw can still be too short to scroll to the old place.
_PUT = """
(() => {
  const s = document.querySelector(%s), want = window.__managerHeldTop || 0;
  if (!s || !want) return;
  let frames = 0;
  const put = () => {
    s.scrollTop = want;
    if (++frames < 15 && Math.abs(s.scrollTop - want) > 1) requestAnimationFrame(put);
  };
  requestAnimationFrame(put);
})()
"""


async def held(redraw: Callable[[], Awaitable[None]], scroller: str = BODY) -> None:
    """Runs `redraw` and leaves `scroller` where it was.

    The position is stored in the page before the redraw and read back after it, so the
    redraw never waits on the browser. The client is taken first: the redraw can delete
    the slot this runs in, and a script sent afterwards has nowhere to be sent from.
    """
    try:
        client = ui.context.client
    except RuntimeError:
        await redraw()
        return
    selector = json.dumps(scroller)
    client.run_javascript(_STORE % selector)
    await redraw()
    if not client.is_deleted:
        client.run_javascript(_PUT % selector)


_KEEP = """
(() => {
  let tries = 0;
  const start = () => {
    const grid = document.getElementById('c%d');
    if (!grid) return ++tries < 60 && requestAnimationFrame(start);
    if (grid.__keepsScroll) return;
    grid.__keepsScroll = true;
    const scroller = () => {
      for (let el = grid.parentElement; el; el = el.parentElement) {
        const y = getComputedStyle(el).overflowY;
        if (y === 'auto' || y === 'scroll') return el;
      }
      return document.scrollingElement;
    };
    let top = 0, redrawn = false;
    document.addEventListener('scroll', (event) => {
      const s = scroller();
      if (!redrawn && (event.target === s || event.target === document)) top = s.scrollTop;
    }, true);
    new MutationObserver(() => {
      const s = scroller();
      redrawn = true;
      if (s.scrollTop + 1 < top) s.scrollTop = top;
      requestAnimationFrame(() => requestAnimationFrame(() => { redrawn = false; }));
    }).observe(grid, {childList: true});
  };
  start();
})()
"""


def keep(element: ui.element) -> None:
    """Keeps the page's place across a redraw that empties `element` and fills it again.

    Something in that redraw makes the browser lay the page out while `element` is empty,
    which puts the scroller at the top, and a script sent from the server arrives after the
    rows do. So the page itself notes where its scroller stands and puts it back as the rows
    arrive. Call it once, where `element` is built.
    """
    ui.run_javascript(_KEEP % element.id)

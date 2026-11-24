"""Keep the focused control, and what was typed into it, across a swap of the part of the
page that holds it.

A region that draws again replaces its controls with new ones, and the browser drops focus
with the old one. The page notes each focused control as focus reaches it: what it is
called and how many like it come before it in the region. After a swap `give_back` focuses
the one that stands in its place and types back what the old one held when that carries on
from what the new one shows, which is what the person typed after the part was built; a
value the save changed is left as the save made it. Focus that was left on purpose, or is
already on a control of the new part, is left alone. A password box is never noted: what it
held is a secret that the save took, and typing it back would put it where it was emptied.

The note is made when focus arrives, not when the swap starts: the server's messages that
replace elements are sent before any script it asks the page to run, so a script cannot
look at the old part first.
"""

from __future__ import annotations

from typing import Any

PLACE_JS = """
(() => {
  const FOCUSABLE = 'input, textarea, select, button, [tabindex]:not([tabindex="-1"])';
  const shown = (el) => el.getClientRects().length > 0;
  const named = (el) => {
    const owner = el.closest('.q-field, .q-toggle, .q-checkbox, .q-btn');
    const label = owner && owner.querySelector('.q-field__label, .q-toggle__label,'
      + ' .q-checkbox__label');
    return ((label || (owner && owner.matches('.q-btn') ? owner : null) || {}).textContent || '')
      .trim().replace(/\\s+/g, ' ').slice(0, 60);
  };
  // No placeholder: it describes the value, which a save can change.
  const key = (el) => [el.tagName, el.type || '', el.getAttribute('role') || '',
    el.getAttribute('aria-label') || '', named(el)].join('|');
  const like = (box, wanted) => [...box.querySelectorAll(FOCUSABLE)]
    .filter((el) => shown(el) && key(el) === wanted);
  let last = null;
  document.addEventListener('focusin', (event) => {
    const el = event.target.closest(FOCUSABLE) || event.target;
    if (el.type === 'password') { last = null; return; }
    const boxes = [];
    for (let box = el.closest('.manager-region'); box;
         box = box.parentElement && box.parentElement.closest('.manager-region')) {
      boxes.push(box);
    }
    last = {el, raw: event.target, key: key(el), boxes: boxes.map((box) => box.id),
            ordinals: boxes.map((box) => like(box, key(el)).indexOf(el))};
  }, true);
  document.addEventListener('focusout', (event) => {
    const raw = event.target;
    setTimeout(() => {
      if (last && last.raw === raw && raw.isConnected && document.hasFocus()) last = null;
    }, 0);
  }, true);
  window.managerPlace = {
    giveBack(id) {
      const at = last ? last.boxes.indexOf(id) : -1;
      if (at < 0) return;
      const place = last;
      last = null;
      let frames = 0;
      const later = () => { if (++frames < 30) requestAnimationFrame(attempt); };
      const attempt = () => {
        const box = document.getElementById(id);
        const now = document.activeElement;
        if (!box || (now && now !== document.body && now !== place.el)) return;
        if (place.el.isConnected) return later();
        const el = like(box, place.key)[place.ordinals[at]];
        if (!el) return later();
        const text = place.el.matches('input, textarea') ? place.el.value : undefined;
        const from = text === undefined ? null : place.el.selectionStart;
        const to = text === undefined ? null : place.el.selectionEnd;
        el.focus({preventScroll: true});
        if (text !== undefined && el.value !== text && text.startsWith(el.value)) {
          const own = Object.getOwnPropertyDescriptor(el.constructor.prototype, 'value');
          own.set.call(el, text);
          el.dispatchEvent(new Event('input', {bubbles: true}));
        }
        if (from !== null && el.setSelectionRange) {
          try { el.setSelectionRange(from, to); } catch (_) { /* a type with none */ }
        }
      };
      requestAnimationFrame(attempt);
    },
  };
})();
"""


def give_back(box: Any) -> None:
    """Focus the control that stands where the focused one did, after `box`'s part is
    swapped. With no browser attached to the page there is no focus to give back."""
    if not box.client.has_socket_connection:
        return
    box.client.run_javascript(f"window.managerPlace && managerPlace.giveBack('{box.html_id}')")

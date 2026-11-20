"""Listening for what a player presses, in the browser.

A key press is a browser event and a gamepad is only readable there, so the whole listen
happens client-side and emits the selector it heard. A binding is a selector -
`pad:standard/button:3` - and the table that asks for one never has the person type it.
"""

from __future__ import annotations

import json

from nicegui import ui

# A controller button as stored, in the browser's terms (`pad` and `i`). A pad mapped to the
# standard layout is named for the layout, the form the shipped defaults use, so a press on
# it meets them; any other pad is named by its number.
PAD_TOKEN_JS = ("(pad.mapping === 'standard' ? 'pad:standard/button:' + i "
                ": 'pad:' + pad.index + '/button:' + i)")

# Held this long, a press is a hold.
HOLD_AT_MS = 1000

# How long a press is down before the ring starts filling, so a tap shows none.
RING_AFTER_MS = 250

# One listener for the document, repointed by every table drawn after the first. A row
# listens from a click on its label or field, or Enter on its label, until a press is let
# go or a click lands elsewhere, and the table's own element is told of both. The first
# device heard owns the capture. A dialog opening over the row, or the row leaving the
# page, ends the listening; a dialog the table is itself drawn in does not.
LISTEN_JS = """(() => {
  const listen = window.__bindingListen = window.__bindingListen || {};
  listen.id = %(id)d;
  listen.noPads = %(no_pads)s;
  if (listen.installed) return;
  listen.installed = true;
  listen.row = '';
  const HOLD_AT = %(hold_at)d;
  const RING_AFTER = %(ring_after)d;
  const down = new Map();          // token -> when it went down
  const heard = [];                // in the order they arrived
  const already = new Set();       // buttons down before this began, ignored until let go
  const remote = new Set();        // buttons and keys the cabinet reports down, not this browser's
  let longest = 0;
  let device = '';
  let ringTimer = 0;

  const tell = (name, detail) => {
    const target = document.getElementById('c' + listen.id);
    if (target) target.dispatchEvent(new CustomEvent(name, {detail}));
  };
  const rowElement = () => listen.row
    ? document.querySelector('[data-row="' + CSS.escape(listen.row) + '"]') : null;
  const clear = () => {
    down.clear();
    remote.clear();
    heard.length = 0;
    longest = 0;
    device = '';
    clearTimeout(ringTimer);
    document.querySelectorAll('.manager-binding-ring').forEach(one => one.remove());
  };
  const pads = () => [...(navigator.getGamepads ? navigator.getGamepads() : [])]
    .filter(Boolean);

  const stop = () => {
    clear();
    listen.row = '';
    tell('listen', '');
  };
  const start = (name, byKey) => {
    clear();
    listen.byKey = !!byKey;
    already.clear();
    pads().forEach(pad => pad.buttons.forEach((b, i) => {
      if (b.pressed) already.add(pad.index + ':' + i);
    }));
    listen.row = name;
    tell('listen', name);
  };
  const active = () => {
    if (!listen.row) return false;
    const row = rowElement();
    if (row && ![...document.querySelectorAll('.q-dialog')].some(one => !one.contains(row))) {
      return true;
    }
    stop();
    return false;
  };

  const startRing = () => {
    const row = rowElement();
    const chips = row && row.dataset.holds === '1' && row.querySelector('.manager-chips');
    if (!chips) return;
    ringTimer = setTimeout(() => {
      const ring = document.createElement('span');
      ring.className = 'manager-binding-ring';
      ring.innerHTML = '<svg viewBox="0 0 20 20"><circle class="manager-ring-track" '
        + 'cx="10" cy="10" r="8"/><circle class="manager-ring-fill" cx="10" cy="10" r="8"/>'
        + '</svg>';
      ring.querySelector('.manager-ring-fill').style.animationDuration =
        (HOLD_AT - RING_AFTER) + 'ms';
      chips.appendChild(ring);
    }, RING_AFTER);
  };

  const arrived = (kind, token) => {
    if (!listen.row || (device && device !== kind)) return;
    if (down.has(token)) return;
    if (!down.size) {
      heard.length = 0;
      longest = 0;
      device = kind;
      startRing();
    }
    down.set(token, Date.now());
    if (!heard.includes(token)) heard.push(token);
  };

  const left = (token) => {
    const at = down.get(token);
    if (at === undefined) return;
    longest = Math.max(longest, Date.now() - at);
    down.delete(token);
    if (down.size) return;
    const row = listen.row;
    const holds = rowElement().dataset.holds === '1';
    const one = heard.length === 1 ? heard[0] : 'chord(' + heard.join('+') + ')';
    const binding = holds && longest >= HOLD_AT
      ? one + '@hold:' + Math.round(longest / 100) * 100 : one;
    clear();
    listen.row = '';
    tell('captured', {row, token: binding, key: listen.byKey});
  };

  window.addEventListener('keydown', (e) => {
    if (!active()) return;
    e.preventDefault();
    e.stopPropagation();
    if (!e.repeat && e.code) arrived('key', 'key:' + e.code);
  }, true);
  window.addEventListener('keyup', (e) => {
    if (!active()) return;
    e.preventDefault();
    e.stopPropagation();
    left('key:' + e.code);
  }, true);
  // Enter or Space on a row's focused label asks it to listen, as a click does. Added
  // after the listener above, so the key that starts it is not also heard by it; it is
  // left to travel on, so a dialog's own Enter handling sees the row listening.
  window.addEventListener('keydown', (e) => {
    if (listen.row || e.repeat || (e.key !== 'Enter' && e.key !== ' ')) return;
    const label = e.target instanceof Element && e.target.closest('.manager-binding-label');
    const row = label && label.closest('[data-row]');
    if (!row) return;
    e.preventDefault();
    start(row.dataset.row, true);
  }, true);
  // A key let go while the window is elsewhere never reports, and would hold the capture
  // to its device.
  window.addEventListener('blur', clear);

  setInterval(() => {
    if (!active() || listen.noPads) return;
    for (const pad of pads()) {
      pad.buttons.forEach((b, i) => {
        const seat = pad.index + ':' + i;
        const token = %(pad_token)s;
        if (!b.pressed) { already.delete(seat); if (!remote.has(token)) left(token); return; }
        if (!already.has(seat)) arrived('pad', token);
      });
    }
  }, 40);

  document.addEventListener('mousedown', (e) => {
    if (e.button !== 0 || !(e.target instanceof Element)) return;
    const part = e.target.closest('.manager-binding-label, .manager-binding-field');
    const row = part && part.closest('[data-row]');
    if (!row) { if (listen.row) stop(); return; }
    if (row.dataset.row === listen.row) return;
    // A chip opens its hold menu and a mark removes, so neither asks to listen.
    const chip = row.dataset.holds === '1' && e.target.closest('.manager-chip-holder');
    if (chip || e.target.closest('.manager-chip-remove')) {
      if (listen.row) stop();
      return;
    }
    start(row.dataset.row);
  }, true);
  // Clicking a chip of the row that is listening keeps it listening rather than opening
  // the menu.
  document.addEventListener('click', (e) => {
    if (!listen.row || !(e.target instanceof Element)) return;
    const row = e.target.closest('[data-row]');
    if (row && row.dataset.row === listen.row && e.target.closest('.manager-chip-holder')
        && !e.target.closest('.manager-chip-remove')) e.stopPropagation();
  }, true);
  // A button or key on the cabinet, reported by the frontend while the page holds them. With
  // no row listening it goes to the live line.
  listen.relay = (token, isDown) => {
    if (!active()) {
      if (isDown && window.__bindingLive && window.__bindingLive.relayed) {
        window.__bindingLive.relayed(token);
      }
      return;
    }
    if (isDown) { remote.add(token); arrived(token.startsWith('key:') ? 'key' : 'pad', token); }
    else { remote.delete(token); left(token); }
  };
  // The cabinet's buttons stopped coming, so none of them is still down.
  listen.relayAllUp = () => { for (const token of [...remote]) listen.relay(token, false); };
  listen.stop = stop;
})()"""


def listen_js(line_id: int, *, pads: bool = True) -> str:
    """The listener, told which element carries the table's events. With `pads` False the
    browser's own gamepad poll is left off, for a table that hears controllers itself."""
    return LISTEN_JS % {"id": line_id, "hold_at": HOLD_AT_MS, "ring_after": RING_AFTER_MS,
                        "pad_token": PAD_TOKEN_JS, "no_pads": "false" if pads else "true"}


def focus_label(name: str) -> None:
    """Puts focus back on a row's label once the redrawn row is in the page."""
    ui.run_javascript(f"""
        (() => {{
          let tries = 0;
          const go = () => {{
            const label = document.querySelector(
              '[data-row=' + {json.dumps(json.dumps(name))} + '] .manager-binding-label');
            if (!label) {{ if (++tries < 40) setTimeout(go, 25); return; }}
            label.focus();
          }};
          go();
        }})()
    """)

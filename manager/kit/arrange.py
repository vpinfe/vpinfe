"""Arranging a list of rows by dragging, or by the keyboard.

A list opts in with `data-arrange="<event>"`; its rows are `.manager-member-row` and carry a
`.manager-drag-handle`. A move is emitted as `{from, to}` under that event name, which the
page listens for. `SCRIPT` is run once after the list is drawn and wires every list not yet
wired.
"""

SCRIPT = """
(() => {
 for (const list of document.querySelectorAll('[data-arrange]')) {
  if (list.dataset.wired) continue;
  list.dataset.wired = '1';
  const moved = list.dataset.arrange;

  // The panel scrolls, not the window, so the edges that mean "keep going" are its.
  const scroller = (() => {
    let el = list.parentElement;
    while (el && el !== document.body) {
      const flow = getComputedStyle(el).overflowY;
      if ((flow === 'auto' || flow === 'scroll') && el.scrollHeight > el.clientHeight) {
        return el;
      }
      el = el.parentElement;
    }
    return document.scrollingElement;
  })();

  const all = () => [...list.querySelectorAll('.manager-member-row')];
  const settled = () => all().filter((row) => !row.classList.contains('manager-dragging'));
  const positionOf = (row) => all().indexOf(row);

  // A dead middle and a ramp into the last sixth: a fixed rate is unusable at five
  // rows and too slow at forty, so speed follows how far into the edge you are.
  const EDGE = 0.16, FASTEST = 20;
  let drag = null, grab = null, press = null;

  function lift(row, y) {
    const box = row.getBoundingClientRect();
    const slot = document.createElement('div');
    slot.className = 'manager-drop-slot';
    slot.style.height = box.height + 'px';
    row.parentNode.insertBefore(slot, row);
    drag = {row: row, slot: slot, from: positionOf(row), anchor: row.nextSibling,
            hold: y - box.top, left: box.left, width: box.width, y: y};
    row.classList.add('manager-dragging');
    row.style.width = box.width + 'px';
    place(y);
    drag.frame = requestAnimationFrame(tick);
  }

  // The lifted row follows the pointer and nothing else moves; the slot is the only
  // thing that shifts, so the list you are aiming at holds still while you aim.
  function place(y) {
    if (!drag) return;
    drag.y = y;
    drag.row.style.top = (y - drag.hold) + 'px';
    drag.row.style.left = drag.left + 'px';
    for (const row of settled()) {
      const box = row.getBoundingClientRect();
      if (y < box.top || y > box.bottom) continue;
      const past = y > box.top + box.height / 2;
      list.insertBefore(drag.slot, past ? row.nextSibling : row);
      break;
    }
  }

  function tick() {
    if (!drag) return;
    const box = scroller.getBoundingClientRect();
    const zone = Math.max(24, box.height * EDGE);
    let speed = 0;
    if (drag.y < box.top + zone) {
      speed = -FASTEST * Math.min(1, (box.top + zone - drag.y) / zone);
    } else if (drag.y > box.bottom - zone) {
      speed = FASTEST * Math.min(1, (drag.y - (box.bottom - zone)) / zone);
    }
    if (speed) { scroller.scrollTop += speed; place(drag.y); }
    drag.frame = requestAnimationFrame(tick);
  }

  function drop(keep) {
    if (!drag) return;
    cancelAnimationFrame(drag.frame);
    const row = drag.row, slot = drag.slot, from = drag.from;
    row.classList.remove('manager-dragging');
    row.style.width = row.style.top = row.style.left = '';
    if (keep) { slot.parentNode.insertBefore(row, slot); }
    else { list.insertBefore(row, drag.anchor); }
    slot.remove();
    const to = positionOf(row);
    drag = null;
    // Only when it moved: a click on the handle would otherwise write the list back
    // unchanged and rebuild the panel under the cursor.
    if (keep && to >= 0 && to !== from) emitEvent(moved, {from: from, to: to});
  }

  list.addEventListener('pointerdown', (e) => {
    const row = e.target.closest('.manager-member-row');
    if (!row) return;
    if (e.target.closest('.manager-drag-handle')) {
      e.preventDefault();
      try { e.target.setPointerCapture(e.pointerId); } catch (err) {}
      lift(row, e.clientY);
      return;
    }
    // Touch has no hover and no second button, so the row itself is the grab target
    // after a hold. Cancelled by any real movement, which is a scroll.
    if (e.pointerType === 'touch') {
      press = {row: row, y: e.clientY,
               timer: setTimeout(() => { press = null; lift(row, e.clientY); }, 420)};
    }
  });
  document.addEventListener('pointermove', (e) => {
    if (press && Math.abs(e.clientY - press.y) > 6) {
      clearTimeout(press.timer); press = null;
    }
    if (drag) { e.preventDefault(); place(e.clientY); }
  }, {passive: false});
  document.addEventListener('pointerup', () => {
    if (press) { clearTimeout(press.timer); press = null; }
    drop(true);
  });
  document.addEventListener('pointercancel', () => drop(false));

  // The same move without a pointer. Grab, step, drop - so arranging is reachable from
  // the keyboard, which a drag on its own never is.
  list.addEventListener('keydown', (e) => {
    const handle = e.target.closest('.manager-drag-handle');
    if (!handle) return;
    const row = handle.closest('.manager-member-row');
    if (e.key === ' ' || e.key === 'Enter') {
      e.preventDefault();
      if (grab && grab.row === row) {
        row.classList.remove('manager-grabbed');
        const to = positionOf(row), from = grab.from;
        grab = null;
        if (to >= 0 && to !== from) emitEvent(moved, {from: from, to: to});
      } else {
        grab = {row: row, from: positionOf(row), anchor: row.nextSibling};
        row.classList.add('manager-grabbed');
      }
    } else if (grab && grab.row === row && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
      e.preventDefault();
      const rows = all(), at = rows.indexOf(row);
      const next = e.key === 'ArrowUp' ? at - 1 : at + 1;
      if (next < 0 || next >= rows.length) return;
      list.insertBefore(row, e.key === 'ArrowUp' ? rows[next] : rows[next].nextSibling);
      row.scrollIntoView({block: 'nearest'});
      handle.focus();
    } else if (e.key === 'Escape' && grab && grab.row === row) {
      e.preventDefault();
      list.insertBefore(row, grab.anchor);
      row.classList.remove('manager-grabbed');
      grab = null;
    }
  });
 }
})()
"""

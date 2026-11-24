"""Change a value control on a Manager page the way a person does, and record what the page
threw away to show it.

`watch` installs a mutation observer; `change` acts on one control; `read` says what the
observer saw since the last `watch`. A change that rebuilds the page shows as a reload (the
observer is gone), a busy dim, or one of the landmarks `watch` marked no longer in the
document.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from tests.support.browser_session import BrowserSession

# A control that does something other than hold a value. A Launchers area search is left out
# of the list by `handsOver` in `_LIST`: typing in it opens All Settings, which is navigation.
# A password box is left out too: a save empties a secret, so no typed text carries across.
NOT_A_VALUE = re.compile(
    r"delete|remove|reset|erase|wipe|restart|shut ?down|reboot|launch|clear|uninstall|forget",
    re.I)

_LANDMARKS = (".ag-root-wrapper, .manager-sections, .manager-section-work, "
              ".manager-panel-header, .q-drawer, .manager-page-purpose")
_FIELD = (".manager-sections input.q-field__native, .manager-section-work input.q-field__native,"
          " .manager-sections textarea.q-field__native")

_WATCH = f"""(() => {{
  if (!window.__values) {{
    const seen = window.__values = {{busy: 0, dimmed: 0}};
    const marked = (node, selector) =>
      node.nodeType === 1 && (node.matches(selector) || node.querySelector(selector) !== null);
    new MutationObserver(records => {{
      for (const record of records) {{
        if (record.type === 'attributes') {{
          if (record.target.getAttribute('aria-busy') === 'true') seen.busy++;
          continue;
        }}
        for (const node of record.addedNodes) {{
          if (marked(node, '[aria-busy="true"]')) seen.busy++;
          if (marked(node, '.manager-busy--over')) seen.dimmed++;
        }}
      }}
    }}).observe(document.body, {{subtree: true, childList: true, attributes: true,
                                attributeFilter: ['aria-busy']}});
  }}
  window.__values.busy = 0;
  window.__values.dimmed = 0;
  document.querySelectorAll('[data-landmark]').forEach(el => delete el.dataset.landmark);
  document.querySelectorAll({json.dumps(_LANDMARKS)})
    .forEach(el => {{ el.dataset.landmark = '1'; }});
  window.__values.landmarks = document.querySelectorAll('[data-landmark]').length;
}})()"""

_READ = """(() => {
  const seen = window.__values;
  if (!seen) return {reloaded: true};
  return {reloaded: false, busy: seen.busy, dimmed: seen.dimmed,
          landmarks: seen.landmarks,
          landmarksKept: document.querySelectorAll('[data-landmark]').length};
})()"""

_LIST = f"""(() => {{
  const shown = el => el.getClientRects().length > 0
    && getComputedStyle(el).visibility !== 'hidden';
  const inGrid = el => el.closest('.ag-root-wrapper');
  const pane = el => el.closest('.manager-sections') !== null;
  const handsOver = el => {{
    const bar = el.closest('.manager-section-bar');
    return bar !== null && bar.querySelector('.q-btn') !== null
      && bar.querySelector('.q-select') === null;
  }};
  const label = el => {{
    let e = el, text = '';
    for (let k = 0; k < 5 && e; k++) {{
      e = e.parentElement;
      text = ((e && e.innerText) || '').trim().replace(/\\s+/g, ' ');
      if (text.length > 3) break;
    }}
    return text.slice(0, 70);
  }};
  const out = [];
  document.querySelectorAll('.q-toggle').forEach((el, i) =>
    shown(el) && out.push({{kind: 'toggle', i, label: label(el), pane: pane(el)}}));
  document.querySelectorAll('.q-checkbox').forEach((el, i) =>
    shown(el) && !inGrid(el)
      && out.push({{kind: 'checkbox', i, label: label(el), pane: pane(el)}}));
  document.querySelectorAll('.q-btn-group').forEach((el, i) => {{
    if (!shown(el) || inGrid(el)) return;
    const buttons = [...el.querySelectorAll('.q-btn')];
    const active = b => b.classList.contains('q-btn-item--active')
      || b.getAttribute('aria-pressed') === 'true';
    if (buttons.length >= 2 && buttons.length <= 6 && buttons.some(active))
      out.push({{kind: 'segmented', i, label: label(el), pane: pane(el)}});
  }});
  // A multi-select adds a value rather than switching to another, so it has no flip-back.
  document.querySelectorAll('.q-select').forEach((el, i) =>
    shown(el) && !inGrid(el) && !el.classList.contains('q-select--multiple')
      && out.push({{kind: 'select', i, label: label(el), pane: pane(el)}}));
  document.querySelectorAll({json.dumps(_FIELD)}).forEach((el, i) =>
    shown(el) && !el.readOnly && !el.disabled && !el.closest('.q-select') && !handsOver(el)
      && el.type !== 'password'
      && out.push({{kind: 'input', i, label: label(el), pane: pane(el)}}));
  return out;
}})()"""

_MENU_ITEMS = ("[...document.querySelectorAll('.q-menu .q-item')]"
               ".filter(e => e.getClientRects().length)")
# The items of the menu a click opened: those a menu left open, or closing, was already
# showing before it carry `data-value-stale`.
_FRESH_ITEMS = f"{_MENU_ITEMS}.filter(e => !e.hasAttribute('data-value-stale'))"


MOVE_S = 3.0


@dataclass(frozen=True)
class Control:
    kind: str
    index: int
    label: str
    in_pane: bool

    @property
    def name(self) -> str:
        return f"{self.kind} {self.label!r}"


async def watch(browser: BrowserSession) -> None:
    await browser.evaluate(_WATCH)


async def read(browser: BrowserSession) -> dict[str, Any]:
    return await browser.evaluate(_READ)


async def controls(browser: BrowserSession, *, in_pane: bool) -> list[Control]:
    """The visible value controls outside any grid that hold a value: those in the
    sections pane, or those around it."""
    found = await browser.evaluate(_LIST)
    return [Control(c["kind"], c["i"], c["label"], c["pane"]) for c in found
            if c["pane"] == in_pane and not NOT_A_VALUE.search(c["label"])]


def _state(control: Control) -> str:
    """JavaScript for what `control` shows as its value."""
    at = f"[{control.index}]"
    if control.kind in ("toggle", "checkbox"):
        return f"document.querySelectorAll('.q-{control.kind}'){at}?.getAttribute('aria-checked')"
    if control.kind == "segmented":
        return (f"[...(document.querySelectorAll('.q-btn-group'){at}"
                "?.querySelectorAll('.q-btn') || [])]"
                ".map(b => b.classList.contains('q-btn-item--active')).join()")
    if control.kind == "select":
        return f"document.querySelectorAll('.q-select'){at}?.innerText"
    return f"document.querySelectorAll({json.dumps(_FIELD)}){at}?.value"


async def change(browser: BrowserSession, control: Control) -> None:
    """Move `control` to another value, as a click or a keystroke does.

    Raises `ValueError` for a control with nothing to change to, or that did not take the
    change: a page checked after a change that never happened has been checked for nothing.
    """
    await unmark(browser)
    before = await browser.evaluate(_state(control))
    await _act(browser, control)
    try:
        await browser.wait_for(f"{_state(control)} !== {json.dumps(before)}", MOVE_S)
    except TimeoutError:
        raise ValueError(f"{control.name} did not change") from None


async def _act(browser: BrowserSession, control: Control) -> None:
    if control.kind in ("toggle", "checkbox"):
        await browser.click(f".q-{control.kind}", nth=control.index)
    elif control.kind == "segmented":
        other = await browser.evaluate(f"""(() => {{
          const group = document.querySelectorAll('.q-btn-group')[{control.index}];
          group.dataset.valueTarget = '1';
          const buttons = [...group.querySelectorAll('.q-btn')];
          const was = buttons.findIndex(b => b.classList.contains('q-btn-item--active')
            || b.getAttribute('aria-pressed') === 'true');
          return buttons.findIndex((b, k) => k !== was);
        }})()""")
        await browser.click("[data-value-target] .q-btn", nth=other)
    elif control.kind == "select":
        await browser.evaluate(f"""(() => {{
          document.querySelectorAll('[data-value-stale]')
            .forEach(e => e.removeAttribute('data-value-stale'));
          {_MENU_ITEMS}.forEach(e => e.dataset.valueStale = '1');
        }})()""")
        await browser.click(".q-select", nth=control.index)
        try:
            await browser.wait_for(f"{_FRESH_ITEMS}.length > 0", MOVE_S)
        except TimeoutError:
            raise ValueError(f"{control.name} did not open a menu") from None
        other = await browser.evaluate(f"""(() => {{
          const items = {_FRESH_ITEMS};
          if (items.length < 2) return -1;
          const was = items.findIndex(e => e.classList.contains('q-item--active')
            || e.getAttribute('aria-selected') === 'true');
          return was === 0 ? 1 : 0;
        }})()""")
        if other < 0:
            await browser.escape()
            raise ValueError(f"{control.name} has one choice")
        await browser.evaluate(f"{_FRESH_ITEMS}[{other}].dataset.valuePick = '1'")
        await browser.click(".q-menu .q-item[data-value-pick]")
    else:
        await browser.evaluate(f"""(() => {{
          const el = document.querySelectorAll({json.dumps(_FIELD)})[{control.index}];
          el.dataset.valueTarget = '1';
          el.focus();
        }})()""")
        await browser.click("[data-value-target]")
        await browser.press("End", "End", 35)
        await browser.send("Input.insertText", {"text": "x"})
        await browser.press("Enter", "Enter", 13)
        await browser.send("Input.insertText", {"text": "y"})


_PLACE = """(() => {
  const focused = document.activeElement;
  if (!focused || focused === document.body) return null;
  const el = focused.closest('input, textarea, select, button,'
    + ' [tabindex]:not([tabindex="-1"])') || focused;
  const same = (a, b) => [a.tagName, a.type || '', a.getAttribute('role') || '',
    a.getAttribute('aria-label') || '', a.getAttribute('placeholder') || ''].join('|')
    === [b.tagName, b.type || '', b.getAttribute('role') || '',
    b.getAttribute('aria-label') || '', b.getAttribute('placeholder') || ''].join('|');
  const alike = [...document.querySelectorAll('input, textarea, select, button,'
    + ' [tabindex]:not([tabindex="-1"])')].filter(e => e.getClientRects().length && same(e, el));
  return {tag: el.tagName, role: el.getAttribute('role'), nth: alike.indexOf(el),
          replaced: !el.hasAttribute('data-value-target'),
          text: el.matches('input, textarea') ? el.value : null};
})()"""


async def place(browser: BrowserSession) -> dict[str, Any] | None:
    """Which control has focus, and the text in it: None when focus is on nothing.

    `replaced` is False for the field `change` typed into while it is still the one in the
    page.
    """
    return await browser.evaluate(_PLACE)


async def release(browser: BrowserSession) -> None:
    """Take focus off whatever has it, so the next control starts from none."""
    await browser.evaluate("document.activeElement && document.activeElement.blur()")


async def unmark(browser: BrowserSession) -> None:
    await browser.evaluate(
        "document.querySelectorAll('[data-value-target], [data-value-pick]')"
        ".forEach(el => { delete el.dataset.valueTarget; delete el.dataset.valuePick; })")

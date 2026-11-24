"""Every Manager page and the remote, walked in a real browser.

Each is drawn once: no listener reaches a layout the browser already has, which NiceGUI
answers by drawing all of it again. And every section a rail opens is headed: when any
group in it is headed, the first one is too, and a section with content has a heading.
Every dropdown on screen is a `manager.kit.dropdown.Select`, and the text in each one
stands at the middle of its box. No row, card or control in an open section is
wider than the content width.

Slow: boots a real instance and a real browser.
"""

from __future__ import annotations

import asyncio
import json
import re
import unittest
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote

from apps.vpx.setting_types import TYPES
from common.config.labels import field_label
from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import game_info, write_game
from tests.support.live_instance import LiveInstance
from tests.support.manager_walk import ManagerWalk, clicked

PICKED = "Hand Picked"
SMART = "Smart Bally"
EMPTY = "Nothing Yet"
VPX = "vpx-drive"
# A unit toggle is written as the unit is, and a count ("2 games", "16 more") is a quantity,
# not a name.
UNITS = frozenset({"cm", "in"})
# What a heading shows when the name it would show is empty: a state, as a value is.
STATES = frozenset({"No name"})
# A hyphenated id in the shape it was saved in is data, not a label. A device's own name
# is data too, whatever its shape, and is read from the instance (`device_names`).
_IDENTIFIER = re.compile(r"[a-z0-9]+(-[a-z0-9]+)+")
EMPTY_FOLDERS = "/manager?view=settings&page=library.folders"

# Where each rail lives, and the address that puts a subject under it. None selects the
# first row of that view's list.
VIEWS = (
    ("games", "game=alpha"),
    ("tables", None),
    ("collections", f"collection={quote(PICKED)}"),
    ("collections", f"collection={quote(SMART)}"),
    ("collections", f"collection={quote(EMPTY)}"),
    ("tags", None),
    ("launchers", None),
    ("launchers", f"launcher={VPX}"),
    ("players", None),
    ("devices", None),
    ("media", None),
    ("assets", None),
    ("settings", None),
)

DRAWN_AGAIN = "Event listeners changed after initial definition"
RAIL = "[...document.querySelectorAll('a.manager-nav-row')].map(a => a.getAttribute('href'))"
ROWS = ("[...document.querySelectorAll('.manager-section-row')]"
        ".filter(el => el.getClientRects().length).length")
PICK = """(() => {
  const rows = [...document.querySelectorAll('.ag-center-cols-container .ag-row')]
    .sort((a, b) => a.getAttribute('row-index') - b.getAttribute('row-index'));
  const cell = rows[0]?.querySelector('.ag-cell');
  if (!cell) return false;
  for (const kind of ['mousedown', 'mouseup', 'click'])
    cell.dispatchEvent(new MouseEvent(kind, {bubbles: true, button: 0}));
  return true;
})()"""
# The open section. A page's own name (`panel.header`, Settings) is the page, not a
# group, and a section bar is the whole section's; both are set aside before either
# question is asked, and so are a page's intro and the input table's mapping intro, live
# line and switch, which each speak for the whole page and so read above its first group.
HEADINGS = """(name => {
  const HEAD = '.manager-fact-heading, .manager-card-title,'
    + ' .manager-group:not(.manager-rail-group)';
  const shown = el => el.getClientRects().length > 0
    && getComputedStyle(el).visibility !== 'hidden';
  const ownText = el => [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
  const content = el => shown(el) && (ownText(el)
    || el.matches('input,textarea,img,video,canvas,.q-field,.q-btn,.q-toggle,.q-checkbox'));
  const area = [...document.querySelectorAll('.manager-section-work')].find(shown);
  const problems = [];
  const aside = [...area.querySelectorAll(
    '.manager-panel-heading, .manager-section-bar, .manager-binding-live,'
    + ' .manager-binding-send, .manager-binding-how, .manager-intro')];
  const outside = el => !aside.some(a => a.contains(el));
  const heads = [...area.querySelectorAll(HEAD)].filter(shown).filter(outside);
  const first = [...area.querySelectorAll('*')].filter(outside).find(content);
  if (heads.length && first && !heads.some(h => h === first || h.contains(first)))
    problems.push([name, 'above its first heading: '
      + (first.innerText || first.value || first.tagName).trim().slice(0, 60)]);
  if (!heads.length && first && !area.querySelector('.manager-panel-heading'))
    problems.push([name, 'has no heading: '
      + (first.innerText || first.value || first.tagName).trim().slice(0, 60)]);
  return {count: heads.length, problems};
})(%s)"""
# Every dropdown on screen. A one-line value is measured from the middle of its text to
# the middle of its box; one that carries a label is a label over a value by design, and
# a multiple one is chips, so neither is.
DROPDOWNS = """(() => {
  const shown = el => el.getClientRects().length > 0
    && getComputedStyle(el).visibility !== 'hidden';
  const problems = [];
  let measured = 0;
  for (const select of document.querySelectorAll('.q-select')) {
    if (!shown(select)) continue;
    const name = (select.innerText || '').trim().split('\\n')[0].slice(0, 30);
    if (!select.classList.contains('manager-select')) {
      problems.push([name, 'is not a manager Select']);
      continue;
    }
    const control = select.querySelector('.q-field__control');
    const value = select.querySelector('.q-field__native span, .q-field__native input');
    if (select.querySelector('.q-field__label') || select.classList.contains('q-select--multiple')
        || !control || !value) continue;
    const text = document.createRange();
    text.selectNodeContents(value);
    const at = (value.tagName === 'INPUT' ? value : text).getBoundingClientRect();
    const box = control.getBoundingClientRect();
    if (!at.height || !box.height) continue;
    measured += 1;
    const off = (at.top + at.height / 2) - (box.top + box.height / 2);
    if (Math.abs(off) >= 0.5) problems.push([name, 'sits ' + off.toFixed(1) + 'px off center']);
  }
  return {measured, problems};
})()"""

# Every row, card and control in the open section against the width the content may have. A row
# is a surface, or a flex line holding an action at its right edge, and a finding is one.
# Headings, the input bar and grids span the pane by design.
STRETCHED = """(() => {
  const shown = el => el.getClientRects().length > 0
    && getComputedStyle(el).visibility !== 'hidden';
  const area = [...document.querySelectorAll('.manager-section-work')].find(shown);
  if (!area) return {measured: 0, problems: []};
  const probe = document.createElement('div');
  probe.style.cssText = 'position:absolute;visibility:hidden;width:var(--content-width)';
  document.body.append(probe);
  const limit = probe.getBoundingClientRect().width + 2;
  probe.remove();
  const BARS = '.manager-panel-heading, .manager-fact-heading, .manager-binding-live';
  const SPANS = '.ag-root-wrapper, .nicegui-aggrid';
  const CONTROLS = '.q-field, .q-btn, .q-slider, .q-toggle, textarea, input:not([type=hidden])';
  const found = [...area.querySelectorAll('*')].filter(el => {
    if (!shown(el) || el.matches(BARS) || el.closest(SPANS)) return false;
    const box = el.getBoundingClientRect();
    if (box.width < 40 || box.height < 14) return false;
    if (el.matches(CONTROLS)) return true;
    const css = getComputedStyle(el);
    const fill = css.backgroundColor;
    if ((fill !== 'rgba(0, 0, 0, 0)' && fill !== 'transparent')
        || css.borderTopWidth !== '0px' || css.borderBottomWidth !== '0px') return true;
    if (css.display !== 'flex' || css.flexDirection !== 'row') return false;
    return [...el.querySelectorAll('.q-btn, button, .q-icon')].some(
      act => act.getBoundingClientRect().right > box.right - 40);
  });
  const label = el => (el.className.toString() + ' "'
    + (el.innerText || el.value || '').trim().split('\\n')[0].slice(0, 30) + '"');
  const width = el => Math.round(el.getBoundingClientRect().width);
  const wide = found.filter(el => width(el) > limit);
  const innermost = wide.filter(el => !wide.some(other => other !== el && el.contains(other)));
  return {measured: found.length,
          problems: innermost.map(el => label(el) + ' ' + width(el) + 'px')};
})()"""


# Every path field in a settings page: the same width whether it holds a typed path or the
# default it stands for.
PATH_WIDTHS = """(() => {
  const body = document.querySelector('.manager-settings-body');
  if (!body) return {widths: []};
  const widths = [...body.querySelectorAll('input.manager-path-input')]
    .filter(input => input.getClientRects().length > 0)
    .map(input => [input.value.slice(-24), Math.round(
      input.closest('.q-field').getBoundingClientRect().width)]);
  return {widths};
})()"""


# A path drawn as text reads from its end and has Copy path after it; a path left in a fact
# row as plain text has neither.
PATH_TEXTS = r"""(() => {
  const shown = el => el.getClientRects().length > 0;
  const drawn = [...document.querySelectorAll('.manager-path-text')].filter(shown);
  const problems = [];
  for (const row of drawn) {
    const end = row.querySelector('.manager-path-end');
    const name = end ? end.textContent.replace(/\u200e/g, '') : '';
    if (!end || getComputedStyle(end).direction !== 'rtl') problems.push([name, 'start first']);
    if (!row.querySelector('button[aria-label]')) problems.push([name, 'no Copy path']);
    if (end && getComputedStyle(end).pointerEvents === 'none') problems.push([name, 'no hover']);
  }
  const loose = [...document.querySelectorAll('.manager-fact-value')].filter(shown)
    .map(el => el.textContent.trim())
    .filter(text => /^(\/|~\/|[A-Za-z]:[\\/])/.test(text));
  return {drawn: drawn.length, problems, loose};
})()"""


# The words on every button and heading on screen, one line each, as written: a heading is
# drawn in capitals by the stylesheet, so its text is read from the node, not from the
# screen. An icon is a word in the font, not on the button, so an icon-only button has none.
WORDS = """(() => {
  const shown = el => el.getClientRects().length > 0
    && getComputedStyle(el).visibility !== 'hidden';
  const first = el => {
    const copy = el.cloneNode(true);
    copy.querySelectorAll('.q-icon, .material-icons').forEach(icon => icon.remove());
    return (copy.textContent || '').trim().split('\\n')[0].trim();
  };
  const heads = '.manager-fact-heading, .manager-card-title, .manager-workbench-title,'
    + ' .manager-group:not(.manager-rail-group)';
  const found = [];
  for (const [kind, query] of [['button', '.q-btn'], ['heading', heads]])
    for (const el of document.querySelectorAll(query))
      if (shown(el) && first(el)) found.push([kind, first(el)]);
  return found;
})()"""


def _vpx_install(root: Path) -> dict[str, str]:
    defaults = {"bool": "0", "int": "0", "number": "1.0", "string": "''",
                "choice": "0, 0='One', 1='Two'"}
    sections: dict[str, list[str]] = {}
    for qualified, kind in TYPES.items():
        section, key = qualified.rsplit(".", 1)
        value = defaults[kind].split(",")[0].strip("'")
        sections.setdefault(section, []).append(
            f"; {key}: {key} [Default: {defaults[kind]}]\n{key} = {value}")
    ini = root / "VPinballX.ini"
    ini.write_text("".join(f"[{name}]\n" + "\n".join(lines) + "\n\n"
                           for name, lines in sections.items()))
    program = root / "VPinballX_BGFX"
    program.write_text("#!/bin/sh\nexit 0\n")
    program.chmod(0o755)
    return {"bin_path": str(program), "ini_path": str(ini)}


class ManagerPagesDrive(unittest.TestCase):
    # `headings`: per address, the sections its rail opened and what each is headed
    # with. `drawn`: per address, what the browser said about redrawing as it loaded.
    seen: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        if not chromium_path():
            raise unittest.SkipTest("no Chromium on this machine")
        with TemporaryDirectory() as tmp, TemporaryDirectory() as second, \
                TemporaryDirectory() as program:
            tables = {"t-a1": {"id": "t-a1", "filename": "Alpha 1.vpx", "version": "1"},
                      "t-a2": {"id": "t-a2", "filename": "Alpha 2.vpx", "version": "2"}}
            info = game_info("Alpha", vps_id="", game_id="alpha", tables=tables,
                             Info={"Manufacturer": "Bally", "Year": "1992"},
                             User={"Tags": ["Late Night"]})
            info["vpinfe"]["default_table"] = "t-a2"
            write_game(Path(tmp), "Alpha", info=info, vpx=False,
                       files={"Alpha 1.vpx": b"x", "Alpha 2.vpx": b"x",
                              "pinmame/roms/alpha.zip": b"x", "Alpha 1.ini": b"[Player]"},
                       medias={"wheel.png": b"x"})
            bravo = game_info("Bravo", vps_id="", game_id="bravo",
                              Info={"Manufacturer": "Williams", "Year": "1995"})
            write_game(Path(tmp), "Bravo", info=bravo)
            write_game(Path(second), "Bravo", info=bravo)
            with LiveInstance(Path(tmp)) as instance:
                cls.seen = asyncio.run(cls._drive(instance, second,
                                                  _vpx_install(Path(program))))

    @classmethod
    async def _drive(cls, instance: LiveInstance, second: str,
                     vpx: dict[str, str]) -> dict:
        instance.wait_for_api()
        instance.post("/api/v1/collections", {"name": PICKED, "games": ["alpha", "bravo"]})
        instance.post("/api/v1/collections",
                      {"name": SMART, "filters": {"manufacturer": ["Bally"]}})
        instance.post("/api/v1/collections", {"name": EMPTY, "games": []})
        for path, body in (("config/locations/second",{"path": second, "kind": "root"}),
                           (f"config/launchers/{VPX}", {"id": VPX, "app": "vpx", "enabled": True,
                                        "name": "Drive", "settings": vpx})):
            urllib.request.urlopen(urllib.request.Request(
                instance.manager_url(f"/api/v1/{path}"),
                data=json.dumps(body).encode(), method="PUT",
                headers={"Content-Type": "application/json"}), timeout=10).close()
        headings: dict = {}
        drawn: dict[str, list[str]] = {}
        dropdowns: dict = {"measured": 0, "problems": []}
        stretched: dict = {"measured": 0, "problems": []}
        paths: dict = {"widths": []}
        texts: dict = {"drawn": 0, "problems": [], "loose": []}
        words: dict[tuple[str, str], set[str]] = {}
        device_names = {one["display_name"] or one["address"] for one in json.load(
            urllib.request.urlopen(instance.manager_url("/api/v1/devices"),
                                   timeout=10))["devices"]}

        async def look(where: str) -> None:
            said = await browser.evaluate(DROPDOWNS)
            dropdowns["measured"] += said["measured"]
            dropdowns["problems"] += [[where, *one] for one in said["problems"]]
            wide = await browser.evaluate(STRETCHED)
            stretched["measured"] += wide["measured"]
            stretched["problems"] += [[where, one] for one in wide["problems"]]
            said = await browser.evaluate(PATH_WIDTHS)
            paths["widths"] += [[where, *one] for one in said["widths"]]
            said = await browser.evaluate(PATH_TEXTS)
            texts["drawn"] += said["drawn"]
            texts["problems"] += [[where, *one] for one in said["problems"]]
            texts["loose"] += [[where, one] for one in said["loose"]]
            for kind, text in await browser.evaluate(WORDS):
                words.setdefault((kind, text), set()).add(where)

        async with BrowserSession(chromium_path() or "") as browser:
            await browser.send("Emulation.setDeviceMetricsOverride",
                               {"width": 1700, "height": 1000, "deviceScaleFactor": 1,
                                "mobile": False})
            walk = ManagerWalk(browser, instance)

            async def visit(path: str) -> None:
                said = await walk.visit(path, listen=True)
                drawn.setdefault(path, []).extend(line for line in said if DRAWN_AGAIN in line)
                await look(path)

            await visit("/manager")
            rail = await browser.evaluate(RAIL)
            for view, subject in VIEWS:
                query = f"view={view}" + (f"&{subject}" if subject else "")
                await visit(f"/manager?{query}")
                if not await browser.evaluate(ROWS):
                    await walk.open_pane(clicked(browser, PICK))
                found: dict = {"opened": [], "problems": [], "headings": {}}
                async for name in walk.sections():
                    said = await browser.evaluate(HEADINGS % json.dumps(name))
                    found["opened"].append(name)
                    found["headings"][name] = said["count"]
                    found["problems"] += said["problems"]
                    await look(f"{query} / {name}")
                headings[query] = found
            for path in rail:
                if path not in drawn:
                    await visit(path)
            await visit("/remote")
            # A library with no folders draws a finding where the list was, and the sweep
            # above only ever sees a library that has some.
            for one in json.load(urllib.request.urlopen(
                    instance.manager_url("/api/v1/locations"), timeout=10))["locations"]:
                urllib.request.urlopen(urllib.request.Request(
                    instance.manager_url(f"/api/v1/config/locations/{one['location_id']}"),
                    method="DELETE"), timeout=10).close()
            await visit(EMPTY_FOLDERS)
            stretched["finding"] = await browser.evaluate(
                "!!document.querySelector('.manager-settings-body .manager-attention')")
        return {"headings": headings, "drawn": drawn, "dropdowns": dropdowns,
                "stretched": stretched, "paths": paths, "texts": texts, "words": words,
                "device_names": device_names}

    def test_every_rail_opened_something(self) -> None:
        for query, found in self.seen["headings"].items():
            with self.subTest(query):
                self.assertTrue(found["opened"])

    def test_a_vpx_install_opens_each_area_of_its_settings(self) -> None:
        self.assertEqual(self.seen["headings"][f"view=launchers&launcher={VPX}"]["opened"],
                         ["Details", "Displays", "Input", "Sound", "Graphics", "Plugins",
                          "All Settings"])

    def test_a_section_that_reads_before_it_draws_is_read_once_drawn(self) -> None:
        opened = self.seen["headings"][f"view=launchers&launcher={VPX}"]["headings"]
        self.assertGreater(opened["All Settings"], 0)

    def test_a_headed_section_opens_with_a_heading(self) -> None:
        problems = {query: found["problems"] for query, found in self.seen["headings"].items()
                    if found["problems"]}
        self.assertEqual(problems, {})

    def test_every_manager_view_is_drawn_once(self) -> None:
        views = {path: said for path, said in self.seen["drawn"].items()
                 if path.startswith("/manager?view=")}
        self.assertGreater(len(views), 5, sorted(self.seen["drawn"]))
        self.assertEqual({}, {path: said for path, said in views.items() if said})

    def test_the_bare_address_is_drawn_once(self) -> None:
        self.assertEqual([], self.seen["drawn"]["/manager"])

    def test_the_remote_is_drawn_once(self) -> None:
        self.assertEqual([], self.seen["drawn"]["/remote"])

    def test_every_dropdown_is_a_manager_select_with_its_text_centered(self) -> None:
        self.assertGreater(self.seen["dropdowns"]["measured"], 20,
                           "an empty sweep passes and measures nothing")
        self.assertEqual([], self.seen["dropdowns"]["problems"])

    def test_no_row_or_control_stretches_past_the_content_width(self) -> None:
        self.assertGreater(self.seen["stretched"]["measured"], 50,
                           "an empty sweep passes and measures nothing")
        self.assertTrue(self.seen["stretched"]["finding"],
                        "the empty Folders page was not drawn, so its finding was not measured")
        self.assertEqual([], self.seen["stretched"]["problems"])

    def test_every_path_on_a_settings_page_is_one_width_default_or_typed(self) -> None:
        paths = self.seen["paths"]
        self.assertGreater(len(paths["widths"]), 3, "an empty sweep passes and measures nothing")
        by_page: dict[str, set[int]] = {}
        for where, _, width in paths["widths"]:
            by_page.setdefault(where, set()).add(width)
        self.assertEqual({}, {where: sorted(widths) for where, widths in by_page.items()
                              if len(widths) > 1})

    def test_every_button_and_heading_is_in_title_case(self) -> None:
        words = self.seen["words"]
        self.assertGreater(len(words), 100, "an empty sweep passes and measures nothing")
        off = {f"{kind}: {text}": sorted(where)[:2] for (kind, text), where in words.items()
               if text != field_label(text)
                  and not text[0].isdigit() and text not in UNITS | STATES
                  | self.seen["device_names"]
                  and not _IDENTIFIER.fullmatch(text)}
        self.assertEqual({}, off)

    def test_a_path_drawn_as_text_reads_from_its_end_and_can_be_copied(self) -> None:
        texts = self.seen["texts"]
        self.assertGreater(texts["drawn"], 3, "an empty sweep passes and measures nothing")
        self.assertEqual([], texts["problems"])
        self.assertEqual([], texts["loose"])


if __name__ == "__main__":
    unittest.main()

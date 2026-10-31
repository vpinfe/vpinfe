"""A page whose NiceGUI script failed to load reloads itself once, then says so.

A component script that never arrives leaves elements with no update method, and the page
draws blank where they were. The browser reports it in two ways: a rejected dynamic
import, and an error on a `/_nicegui/` script or stylesheet element.

The first failure reloads the page and notes the time in `sessionStorage`. A second one
inside `WINDOW_MS` of that note, or in a browser that will not keep the note, reloads
nothing and puts a message over the page with a Reload button: a loop of reloads is worse
than a blank page. The message is plain DOM because the framework that would draw it is
what failed. A failure reported once the page is being left is ignored: a navigation that
aborts a fetch must not reload over it.
"""

from __future__ import annotations

import json

from common.i18n import t

NOTE_KEY = "vpinfe.scriptFailedReload"
WINDOW_MS = 30_000

SCRIPT = """
(() => {
  const NOTE = %(note)s;
  const WINDOW = %(window)d;
  const WORDS = %(words)s;
  const IMPORT_FAILED = new RegExp([
    'Importing a module script failed',
    'Failed to fetch dynamically imported module',
    'error loading dynamically imported module',
  ].join('|'), 'i');
  const OURS = '/_nicegui/';
  let acted = false;
  let leaving = false;

  const noted = () => {
    try { return Number(sessionStorage.getItem(NOTE)) || 0; } catch (e) { return null; }
  };
  const note = () => {
    try { sessionStorage.setItem(NOTE, String(Date.now())); return true; }
    catch (e) { return false; }
  };
  const fileOf = (address) => {
    try { return new URL(address, location.href).pathname.split('/').pop() || address; }
    catch (e) { return address; }
  };
  const say = (what) => {
    const box = document.createElement('div');
    box.className = 'manager-script-failed';
    box.setAttribute('role', 'alert');
    const title = document.createElement('div');
    title.className = 'manager-script-failed__title';
    title.textContent = WORDS.title;
    const detail = document.createElement('div');
    detail.className = 'manager-script-failed__detail';
    detail.textContent = what;
    const reload = document.createElement('button');
    reload.className = 'manager-script-failed__reload';
    reload.type = 'button';
    reload.textContent = WORDS.reload;
    reload.addEventListener('click', () => location.reload());
    box.append(title, detail, reload);
    (document.body || document.documentElement).appendChild(box);
  };
  const failed = (what) => {
    if (acted || leaving) return;
    acted = true;
    const last = noted();
    if (last !== null && Date.now() - last > WINDOW && note()) { location.reload(); return; }
    say(what);
  };

  window.addEventListener('pagehide', () => { leaving = true; });
  window.addEventListener('unhandledrejection', (event) => {
    const reason = event.reason;
    const said = String((reason && reason.message) || reason || '');
    if (IMPORT_FAILED.test(said)) failed(said);
  });
  window.addEventListener('error', (event) => {
    const el = event.target;
    if (!el || el === window || !el.tagName) return;
    const address = el.src || el.href || '';
    if ((el.tagName === 'SCRIPT' || el.tagName === 'LINK') && address.indexOf(OURS) >= 0) {
      failed(WORDS.file.replace('{file}', fileOf(address)));
    }
  }, true);
})();
"""


def script() -> str:
    return SCRIPT % {
        "note": json.dumps(NOTE_KEY),
        "window": WINDOW_MS,
        "words": json.dumps({
            "title": t("manager.page.script_failed"),
            "file": t("manager.page.script_failed_file", file="{file}"),
            "reload": t("manager.page.reload"),
        }),
    }

"""Where a link that leaves the Manager opens: a new tab each time, or one tab reused.

The browser decides tab or window, so this only decides whether a second link finds the
tab the first one opened. `rel=noopener` is what makes every click a new browsing
context, so reusing one means opening the named tab ourselves, and a tab that is opened
and then reused has to keep an opener. That opener is a hidden frame of the Manager, not
the Manager itself: the site in the tab can reach the frame and nothing else, so it cannot
send the Manager somewhere else.

Only a plain left click on a link that goes to another site and asks for a new tab is
taken. A modified click, a middle click, a right click and every link with a name of its
own (the Manager tab, a player's window) stay the browser's.
"""

from __future__ import annotations

import json

from nicegui import ui

from common.config.config_access import cfg_get
from common.config.paths import get_ini_config

SECTION = "manager"
KEY = "open_links"
NEW = "new"
REUSE = "reuse"
CHOICES = (NEW, REUSE)
TAB = "vpinfe-links"

LINK_TAB_JS = """
(() => {
  if (window.vpinfeLinkTab) return;
  window.vpinfeLinkTab = true;
  let relay = null;
  const opener = () => {
    if (!relay || !relay.isConnected) {
      relay = document.createElement('iframe');
      relay.hidden = true;
      relay.tabIndex = -1;
      relay.srcdoc = '<html></html>';
      document.body.appendChild(relay);
    }
    return relay.contentWindow;
  };
  const reopen = (url) => {
    let tab = null;
    try { tab = opener().open(url, %s); } catch (e) { /* the frame was sent elsewhere */ }
    if (!tab) {
      if (relay) relay.remove();
      relay = null;
      opener();
      return false;
    }
    try { tab.focus(); } catch (e) { /* the browser decides */ }
    return true;
  };
  opener();
  window.vpinfeOpenLink = (url) => {
    if (window.vpinfeLinks === 'reuse' && reopen(url)) return;
    window.open(url, '_blank');
  };
  document.addEventListener('click', (event) => {
    if (window.vpinfeLinks !== 'reuse' || event.defaultPrevented) return;
    if (event.button !== 0 || event.metaKey || event.ctrlKey ||
        event.shiftKey || event.altKey) return;
    const link = event.target.closest && event.target.closest('a[target="_blank"][href]');
    if (!link) return;
    let to;
    try { to = new URL(link.href, location.href); } catch (e) { return; }
    if (!/^https?:$/.test(to.protocol) || to.origin === location.origin) return;
    if (reopen(to.href)) event.preventDefault();
  }, true);
})();
"""


def way(said: object) -> str:
    """A stored value as one of the choices: `new` for nothing or for something odd, since
    the file is one a person may have edited."""
    said = str(said or "").strip().lower()
    return said if said in CHOICES else NEW


def configured() -> str:
    """The way this install opens links."""
    return way(cfg_get(get_ini_config(), SECTION, KEY))


def script(way: str) -> str:
    """The page script, with this install's way set before the first click can happen."""
    return (f"window.vpinfeLinks = {json.dumps(way)};"
            + LINK_TAB_JS % json.dumps(TAB))


def open_link(url: str) -> None:
    """Open an address the page was told about, the way this install opens links."""
    ui.run_javascript(f"window.vpinfeOpenLink({json.dumps(url)})")


def change_js(way: str) -> str:
    """What to run in a live page when the setting changes."""
    return f"window.vpinfeLinks = {json.dumps(way)}"

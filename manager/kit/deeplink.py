"""The address bar as the record of where you are.

So a place can be linked to, and so a reload lands where you were - which matters
because a reload is not always something you chose: a restart takes every open
page with it.

A place adds a history entry and a pick replaces the current one: Back goes to the
page before, not through every row somebody arrowed past. Only a call that says `push`
adds one. The vocabularies of what is valid are passed in rather than imported, which
is what keeps this out of a cycle with the page it serves.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlencode

from nicegui import ui

# What the address carries, and where each comes from. Ordered, so the same place
# always produces the same address and two of them can be compared by eye.
_FIELDS = (
    ("view", lambda state: state.get("view") or ""),
    ("game", lambda state: state.get("game") or ""),
    ("table", lambda state: state.get("table") or ""),
    # "none" rather than nothing: every section closed is somewhere you asked to be,
    # and leaving it out of the address would reopen one on the next reload.
    ("section", lambda state: _section(state)),
    ("slot", lambda state: _slot_kind(state)),
    # Which page of a section is open. Named for that rather than for Settings, which
    # is now one of System's three and not the only one with pages inside it.
    ("page", lambda state: state.get("settings_page") or ""),
    # Which of the Logs page's views is up.
    ("log", lambda state: state.get("log_view") or ""),
    # Which of Discovery's grids is up. A subject's page is not one of them.
    ("tab", lambda state: "" if state.get("subject") else state.get("discovery_tab") or ""),
    # Which half shows where the list and the panel do not fit side by side, with a subject
    # picked: "list" is the place before the panel opened, "open" the one after, and a link
    # without it lands on the panel.
    ("pane", lambda state: showing(state)),
    ("collection", lambda state: state.get("collection") or ""),
    ("launcher", lambda state: state.get("launcher") or ""),
    ("player", lambda state: state.get("player") or ""),
    ("backup", lambda state: state.get("backup") or ""),
    ("entry", lambda state: state.get("entry") or ""),
    # Whose work Discovery's page shows: an author, a maker or a theme, one at a time.
    ("author", lambda state: _subject(state, "author")),
    ("maker", lambda state: _subject(state, "maker")),
    ("theme", lambda state: _subject(state, "theme")),
    # The row an extension's list has open in its pane, by the id the list gave it.
    ("row", lambda state: state.get("ext_row") or ""),
    # A setting by its key: the Tables grid arrives on the launcher's tables that set it.
    ("sets", lambda state: state.get("sets") or ""),
    # The Tables grid on one group of one kept report: the job, then which of its groups.
    ("report", lambda state: state.get("report") or ""),
    ("group", lambda state: state.get("group") or ""),
)

# Only where they mean something. A slot on the devices page is noise in an address
# somebody is meant to be able to read.
# A value may belong to more than one place: a game, its section and its slot are as
# meaningful on the Tables grid as on the Games one, because both answer for a game in
# the workbench beside them. Left as a single name each, a table's section is absent from
# the address on any grid but the one it was declared for.
# Media selects a game, a table and a slot the same way the other two do - a row there
# is one game's slot - so leaving it out would drop the whole selection in silence.
_PANEL = ("games", "tables", "media", "assets")
_ONLY_ON = {"game": _PANEL, "table": _PANEL, "section": _PANEL, "slot": _PANEL,
            "page": ("settings",), "log": ("logs",), "tab": ("discovery",),
            "collection": ("collections", "games"),
            "launcher": ("launchers", "tables"), "sets": ("tables",),
            "report": ("tables",), "group": ("tables", "media"),
            "player": ("players",), "backup": ("backups",), "entry": ("discovery",),
            "author": ("discovery",), "maker": ("discovery",), "theme": ("discovery",)}

SUBJECTS = ("author", "maker", "theme")


# The Logs page's views that have an address; the stream is the one with none.
LOG_VIEWS = ("digest",)

# An extension's list is a view of its own, named `community:<extension>:<list>`.
COMMUNITY_PREFIX = "community:"

# What the address calls a panel with nothing open.
NO_SECTION = "none"
# Every section closed. No rail click reaches it, so it comes only from an address that
# says `section=none`. Named, because it travels in the state and the address, and
# "" appearing in either wants to be findable as a decision rather than as a blank.
COLLAPSED = ""


def _section(state: dict[str, Any]) -> str:
    chosen = state.get("section")
    return NO_SECTION if chosen == COLLAPSED else str(chosen or "")


def _subject(state: dict[str, Any], kind: str) -> str:
    """The name Discovery's page is about, where it is about a `kind`."""
    subject = state.get("subject")
    if isinstance(subject, dict) and subject.get("type") == kind:
        return str(subject.get("name") or "")
    return ""


PANES = ("open", "list")

# The state key that names the row picked on a page whose rows are not games.
_PICKED_ON = {"collections": "collection", "launchers": "launcher", "players": "player",
              "backups": "backup", "discovery": "entry"}


def showing(state: dict[str, Any]) -> str:
    """The half showing, or "" where both are or nothing is picked."""
    if not state.get("stacked"):
        return ""
    if state.get("opened"):
        return "open"
    picked = (state.get("game") or state.get("table") or state.get("ext_row")
              or state.get("subject") or state.get(_PICKED_ON.get(state.get("view") or "", "")))
    return "list" if picked else ""


def _slot_kind(state: dict[str, Any]) -> str:
    """The picked media kind, or "". Tolerant, because keeping the address current
    must never be the thing that takes the page down."""
    slot = state.get("slot")
    return str(slot.get("kind") or "") if isinstance(slot, dict) else ""


def _meant_for(name: str, view: str) -> bool:
    if name == "row":
        return view.startswith(COMMUNITY_PREFIX)
    return view in _ONLY_ON.get(name, (view,))


def query(state: dict[str, Any], *, skip: tuple[str, ...] = ()) -> str:
    """The address for this state, without the path. A colon is left as it is, so a
    view reads `community:vpinplay:tables` in the bar rather than escaped."""
    view = state.get("view") or ""
    return urlencode([(name, read(state)) for name, read in _FIELDS
                      if name not in skip and _meant_for(name, view) and read(state)],
                     safe=":")


def same_place(state: dict[str, Any], other: dict[str, Any]) -> bool:
    """Whether two states name one place and differ at most in which half is showing."""
    return query(state, skip=("pane",)) == query(other, skip=("pane",))


def sync(state: dict[str, Any], *, push: bool = False, over: bool = False) -> None:
    """Put the current place in the address bar, without reloading anything. `push` adds
    a history entry for it, marked `over` when the entry before it is the list it opened a
    panel from; without `push`, the current entry is rewritten. A dialog's entry on top is
    rewritten rather than buried. An address equal to the one showing writes nothing, and
    nothing is written while a move the browser made is unanswered (`settled`).

    The title follows the address, written after it: a title set first would rename the
    entry being left.
    """
    tail = query(state)
    address = "/manager?" + tail if tail else "/manager"
    ui.run_javascript(
        "((address, push, over, title) => {"
        " if (window.vpinArrivals) return;"
        " if (address !== location.pathname + location.search) {"
        "  const taken = push && window.vpinTakeSentinel && window.vpinTakeSentinel();"
        "  history[push && !taken ? 'pushState' : 'replaceState']("
        "push ? (over ? {vpinOver: true} : null) : history.state, '', address); }"
        " if (title) document.title = title; })"
        f"({json.dumps(address)}, {json.dumps(push)}, {json.dumps(over)},"
        f" {json.dumps(_title(state))})")


# Marks the Back button of a dialog that walks steps: Back with it open presses that button
# where any other dialog is closed.
BACK_STEP = "manager-back-step"

_DIALOGS_BACK = """
(() => {
  let sentinels = 0, expected = 0;
  const open = () => document.querySelectorAll('.q-dialog').length;
  const reconcile = () => {
    const want = open();
    if (want > sentinels) {
      for (; sentinels < want; sentinels++)
        history.pushState({vpinSentinel: true}, '', location.href);
    } else if (want < sentinels) {
      const extra = sentinels - want;
      sentinels = want;
      if (history.state && history.state.vpinSentinel) { expected++; history.go(-extra); }
    }
  };
  const escape = () => {
    const key = {key: 'Escape', code: 'Escape', keyCode: 27, which: 27,
                 bubbles: true, cancelable: true};
    document.dispatchEvent(new KeyboardEvent('keydown', key));
    document.dispatchEvent(new KeyboardEvent('keyup', key));
  };
  window.vpinTakeSentinel = () => {
    if (!sentinels || !(history.state && history.state.vpinSentinel)) return false;
    sentinels--;
    return true;
  };
  window.addEventListener('popstate', (event) => {
    if (expected) { expected--; event.stopImmediatePropagation(); return; }
    if (sentinels) {
      event.stopImmediatePropagation();
      sentinels--;
      const all = document.querySelectorAll('.q-dialog');
      const step = all.length && all[all.length - 1].querySelector('.__BACK_STEP__');
      if (step) {
        // The dialog walks steps: Back is its own Back, and the dialog stays open, so
        // the entry this move used is put back.
        step.click();
        sentinels++;
        history.pushState({vpinSentinel: true}, '', location.href);
        return;
      }
      escape();
      // After the hide transition: a dialog that refused has not been removed.
      setTimeout(reconcile, 700);
    } else if (event.state && event.state.vpinSentinel) {
      event.stopImmediatePropagation();
      expected++;
      history.back();
    }
  });
  new MutationObserver(reconcile).observe(document.body, {childList: true});
  reconcile();
})()
"""


def dialogs_follow_back() -> None:
    """Back closes the topmost dialog as Escape does, or presses its `BACK_STEP` button
    where it has one showing. Install before the page's own
    `popstate` listener, which must not hear an entry that belongs to a dialog."""
    ui.run_javascript(_DIALOGS_BACK.replace("__BACK_STEP__", BACK_STEP))


def settled() -> None:
    """Answer one move the page counted: the state now matches the entry reached."""
    ui.run_javascript(
        "window.vpinArrivals = Math.max(0, (window.vpinArrivals || 0) - 1)")


def name_place(state: dict[str, Any]) -> None:
    """Title the entry showing, and leave its address as it is: an address the page arrived
    on can carry what the state does not."""
    ui.run_javascript(f"document.title = {json.dumps(_title(state))} || document.title")


def _title(state: dict[str, Any]) -> str:
    place = state.get("place_title")
    return place() if callable(place) else ""


def forget(state: dict[str, Any]) -> None:
    """Clear everything the address carries, so that applying an address leaves what it
    names and nothing the last place left behind."""
    for name in ("game", "table", "ext_row", "settings_page"):
        state[name] = ""
    for name in ("collection", "launcher", "player", "backup", "entry", "sets", "subject"):
        state[name] = None
    for name in ("slot", "section", "log_view", "discovery_tab", "opening", "find", "pane"):
        state.pop(name, None)


def apply(state: dict[str, Any], params: dict[str, str], *,
          views: Iterable[str], sections: Iterable[str]) -> None:
    """Seed the state from an address somebody arrived on.

    Only what is recognized: a link can be old or hand-typed, and neither should be an
    error - what does not fit is dropped and the default stands. A game id is taken as
    given, because whether it exists is the library's question.
    """
    def clean(name: str) -> str:
        return str(params.get(name) or "").strip().lower()

    # An extension's list or page key keeps its case, so the view is matched without it and
    # set to the key as the rail knows it.
    known = {one.lower(): one for one in views}
    if clean("view") in known:
        state["view"] = known[clean("view")]
    if str(params.get("game") or "").strip():
        state["game"] = str(params["game"]).strip()
    if str(params.get("table") or "").strip():
        state["table"] = str(params["table"]).strip()
    # Every rail's keys, not one rail's: an address can name a table section while the
    # place it belongs to is still being read, and dropping it here would land on the
    # default and quietly ignore half the link.
    if clean("section") == NO_SECTION:
        state["section"] = COLLAPSED
    elif clean("section") in set(sections):
        state["section"] = clean("section")
    if clean("slot"):
        state.setdefault("slot", {"kind": None})
        state["slot"]["kind"] = clean("slot")
    if clean("page"):
        state["settings_page"] = clean("page")
    if clean("log") in LOG_VIEWS:
        state["log_view"] = clean("log")
    if clean("pane") in PANES:
        state["pane"] = clean("pane")
    if clean("tab") and state["view"] == "discovery":
        state["discovery_tab"] = clean("tab")
    if str(params.get("collection") or "").strip():
        state["collection"] = str(params["collection"]).strip()
    if str(params.get("launcher") or "").strip():
        state["launcher"] = str(params["launcher"]).strip()
    if str(params.get("sets") or "").strip():
        state["sets"] = str(params["sets"]).strip()
    if str(params.get("group") or "").strip() and (
            str(params.get("report") or "").strip() or state["view"] == "media"):
        if str(params.get("report") or "").strip():
            state["report"] = str(params["report"]).strip()
        state["group"] = str(params["group"]).strip()
    if str(params.get("player") or "").strip():
        state["player"] = str(params["player"]).strip()
    if str(params.get("backup") or "").strip():
        state["backup"] = str(params["backup"]).strip()
    if str(params.get("entry") or "").strip():
        state["entry"] = str(params["entry"]).strip()
    for kind in SUBJECTS:
        if str(params.get(kind) or "").strip():
            state["subject"] = {"type": kind, "name": str(params[kind]).strip()}
    if str(params.get("row") or "").strip() and str(state.get("view") or "").startswith(
            COMMUNITY_PREFIX):
        state["ext_row"] = str(params["row"]).strip()
    if str(params.get("find") or "").strip() and state["view"] == "backups":
        state["find"] = str(params["find"]).strip()

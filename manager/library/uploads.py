
"""Files arriving in the Manager, and what happens between their arrival and the import.

The engine underneath is not this surface's: analysing what arrived, working out where
each file would go and copying it there all live in the install and are reachable over
the API. What is here is the gesture and the conversation - where you can let go, what
you are shown before anything is written, and how a target is named by where you let go
rather than by a control you had to find first.

**The gesture is the declaration.** Letting go on a game's row names that game; on a
media cell it names the slot as well. Nothing is inferred from a filename, because a
person who dropped a file on a row has already said what they meant more clearly than a
name ever does.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from nicegui import context, run, ui

from common.failures import why
from common.i18n import t
from manager.kit import offload
from manager.kit.on_page import on_page
from manager.library import drop_sheet, drop_target, import_dialog

logger = logging.getLogger("vpinfe.manager.library.uploads")

# The attribute an element declares what a drop on it means with: JSON naming ids, which
# `drop_target.resolve` reads. The nearest element up from the pointer that has one wins.
DROP = "data-drop"
# What a place that takes its own drop declares, so the page-wide handler leaves it alone.
OWN_ZONE = '{"own": true}'


def declaration(**ids: Any) -> str:
    """What an element's `data-drop` holds for these ids, an id left empty left out."""
    return json.dumps({key: value for key, value in ids.items() if value})


def declare(element: Any, **ids: Any) -> None:
    """Make `element` mean this target when a file is dropped on it or anything in it.
    No ids at all takes the declaration away."""
    named = declaration(**ids)
    if named != "{}":
        element._props[DROP] = named
    else:
        element._props.pop(DROP, None)


@dataclass(frozen=True)
class Drop:
    """A finished upload and where it landed, as the page script saw it."""

    where: drop_target.Where = field(default_factory=drop_target.Where)
    upload_id: str = ""
    name: str = ""
    count: int = 0


# AG Grid already puts `row-id` on every row and `col-id` on every cell, so a grid here
# is a drop target without any change to how its rows are built.
#
# Inline rather than a served file: the Manager has no static assets of its own, and the
# served version needs a cache-busting query or a browser keeps the old script - whose
# own guard then blocks the new one. Nothing to bust if there is nothing cached.
_DND_SCRIPT = r"""
if (!window.__managerDnd) {
  window.__managerDnd = true;

  const emit = (payload) => {
    if (typeof emitEvent === 'function') emitEvent('manager_dnd', payload);
  };

  const readEntries = (reader) =>
    new Promise((resolve, reject) => reader.readEntries(resolve, reject));
  const fileOf = (entry) =>
    new Promise((resolve, reject) => entry.file(resolve, reject));

  async function walk(entry, collected) {
    if (entry.isFile) {
      const file = await fileOf(entry);
      collected.push({relpath: entry.fullPath.replace(/^\/+/, ''), file: file});
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      // readEntries answers at most ~100 at a time, so loop until it answers none.
      while (true) {
        const batch = await readEntries(reader);
        if (!batch.length) break;
        for (const child of batch) await walk(child, collected);
      }
    }
  }

  async function collect(transfer) {
    const entries = [];
    for (const item of Array.from(transfer.items || [])) {
      const entry = item.webkitGetAsEntry ? item.webkitGetAsEntry() : null;
      if (entry) entries.push(entry);
    }
    const collected = [];
    if (entries.length) {
      for (const entry of entries) await walk(entry, collected);
    } else {
      for (const file of Array.from(transfer.files || [])) {
        collected.push({relpath: file.name, file: file});
      }
    }
    return collected;
  }

  const ask = (url, options) => fetch(url, options).catch(() => {
    throw {unreached: window.location.hostname};
  });

  async function refused(said) {
    let words = '';
    try { words = (await said.json()).error.message || ''; } catch (e) {}
    return {said: words};
  }

  async function upload(files, say) {
    const begin = await ask('/api/v1/uploads', {method: 'POST'});
    if (!begin.ok) throw await refused(begin);
    const uploadId = (await begin.json()).id;
    let done = 0;
    for (const item of files) {
      const form = new FormData();
      form.append('relpath', item.relpath);
      form.append('file', item.file, item.file.name);
      const said = await ask(`/api/v1/uploads/${uploadId}/files`,
                             {method: 'POST', body: form});
      if (!said.ok) {
        const why = await refused(said);
        await ask(`/api/v1/uploads/${uploadId}`, {method: 'DELETE'}).catch(() => {});
        throw why;
      }
      done += 1;
      say({status: 'progress', done: done, total: files.length, name: item.relpath});
    }
    return uploadId;
  }

  // Every way in ends here. `gathering` is already under way when it arrives, because a
  // drop's files can only be read while the drop is still being handled.
  async function send(gathering, say, where) {
    try {
      const files = await gathering;
      if (!files.length) {
        say({status: 'error', empty: true});
        return;
      }
      say({status: 'progress', done: 0, total: files.length, name: ''});
      const uploadId = await upload(files, say);
      say({status: 'done', upload_id: uploadId, name: named(files), count: files.length,
           ...where});
    } catch (err) {
      say({status: 'error', said: (err && err.said) || '',
           unreached: (err && err.unreached) || ''});
    }
  }

  function named(files) {
    if (files.length === 1) return files[0].relpath;
    const first = files[0].relpath;
    const cut = first.indexOf('/');
    return cut > 0 ? first.slice(0, cut) : '';
  }

  // What the pointer is over, reported as it is. Nothing here decides which game or slot:
  // the page does, from these facts and the view it is showing. Null inside a dialog,
  // which covers the page, and over a place that takes its own drop.
  function landed(target) {
    if (!target.closest || target.closest('.q-dialog')) return null;
    const zone = target.closest('[data-drop]');
    let declared = null;
    if (zone) {
      try { declared = JSON.parse(zone.getAttribute('data-drop')); } catch (e) {}
      if (declared && declared.own) return null;
    }
    const cell = target.closest('.ag-cell');
    const row = target.closest('.ag-row');
    return {
      row_id: row ? (row.getAttribute('row-id') || '') : '',
      col_id: cell ? (cell.getAttribute('col-id') || '') : '',
      cell: cell ? Array.from(cell.classList) : [],
      declared: declared,
      refused: !declared && document.body.dataset.dropsOn !== 'library',
      nodrop: !declared && !!(row && row.classList.contains('manager-drop-no')),
      el: declared ? zone
        : (cell && cell.matches('.manager-drop-media, .manager-drop-asset')) ? cell : row,
    };
  }

  const facts = (where) => ({row_id: where.row_id, col_id: where.col_id,
                             cell: where.cell, declared: where.declared});

  const lit = (el, on) => { if (el) el.classList.toggle('manager-drop-lit', on); };
  let hot = null;

  function highlight(where) {
    const el = where.el || null;
    if (el !== hot) { lit(hot, false); hot = el; lit(hot, true); }
  }

  function clear() { lit(hot, false); hot = null; }

  // A dialog's own ways in. `say` is the emit of the control that asked, so what
  // happens next is heard by that dialog and not by the page behind it.
  window.__managerDrop = (transfer, say) => send(collect(transfer), say, {});

  window.__managerChoose = (folder, say, one) => {
    const input = document.createElement('input');
    input.type = 'file';
    if (folder) input.webkitdirectory = true;
    else input.multiple = !one;
    input.onchange = () => send(Promise.resolve(Array.from(input.files || []).map(
      file => ({relpath: file.webkitRelativePath || file.name, file}))), say, {});
    input.click();
  };

  document.addEventListener('dragover', (event) => {
    if (!event.dataTransfer || !Array.from(event.dataTransfer.types || [])
        .includes('Files')) return;
    // Refused rather than ignored: a drop nothing takes opens the file in place of the
    // Manager.
    event.preventDefault();
    const where = landed(event.target);
    if (!where) {
      event.dataTransfer.dropEffect = 'none';
      document.body.classList.remove('manager-dropping');
      clear();
      return;
    }
    if (where.refused || where.nodrop) {
      event.dataTransfer.dropEffect = 'none';
      document.body.classList.remove('manager-dropping');
      clear();
      return;
    }
    document.body.classList.add('manager-dropping');
    highlight(where);
  });
  // A place that takes its own drop stops the page's dragover, so a row lit on the way
  // there is put out as the pointer enters it.
  document.addEventListener('dragenter', (event) => {
    if (landed(event.target)) return;
    document.body.classList.remove('manager-dropping');
    clear();
  });
  document.addEventListener('dragleave', (event) => {
    if (event.relatedTarget) return;
    document.body.classList.remove('manager-dropping');
    clear();
  });
  document.addEventListener('drop', (event) => {
    if (!event.dataTransfer) return;
    event.preventDefault();
    document.body.classList.remove('manager-dropping');
    const where = landed(event.target);
    clear();
    if (!where) return;
    // Nothing is read for a drop that is refused: the page says why.
    if (where.refused || where.nodrop) emit({status: 'refused', ...facts(where)});
    else send(collect(event.dataTransfer), emit, facts(where));
  });
}
"""


def install() -> None:
    """Make the whole document a drop target, every grid in it. A drop arrives on
    `manager_dnd`, which the page hears with a `listener`."""
    ui.run_javascript(_DND_SCRIPT)


def drops_on(view: str, client: Any) -> None:
    """Tell the page script whether the view showing takes a file dropped on it. Takes the
    client, not the slot: a view that reads before it draws renders from a background task."""
    taken = "library" if drop_target.VIEWS.get(view) else ""
    client.run_javascript(f"document.body.dataset.dropsOn = {json.dumps(taken)};")


def listener(on_arrival: Callable[[Drop], Any]) -> Callable[[Any], None]:
    """What hears one place's uploads, from the first file read to the finished session.

    One per place that starts them: the page for its drop, and a dialog for its own drop
    and pickers, whose controls hand the script their `emit` so the answer comes back to
    them.
    """
    state: dict[str, Any] = {"busy": False, "client": context.client, "note": None}

    def said(event: Any) -> None:
        payload = event.args or {}
        status = payload.get("status")
        if status == "progress":
            total = int(payload.get("total") or 0)
            done = int(payload.get("done") or 0)
            if total > 1:
                _progress(state, t("manager.uploads.reading_2", done=(done), total=(total)))
            elif total:
                _progress(state, t("manager.uploads.reading"))
        elif status == "error":
            _clear(state)
            if payload.get("empty"):
                ui.notify(t("manager.uploads.empty"), type="warning")
            else:
                logger.warning("manager: an upload stopped before it arrived: %s (%s)",
                               _stopped(payload) or "no reason given",
                               payload.get("message") or "no browser message")
                ui.notify(t("manager.uploads.read_failed"), caption=_stopped(payload),
                          type="negative")
        elif status == "done" or status == "refused":
            _clear(state)
            if state["busy"]:
                # One at a time. A second import landing while the first is still being
                # decided would have two dialogs answering for two different sessions.
                ui.notify(t("manager.uploads.busy"), type="warning")
                return
            asyncio.create_task(_handle(state, payload, on_arrival))

    return said


def _stopped(payload: dict[str, Any]) -> str:
    """Why an upload stopped, as far as the page could tell, or "" when it could not."""
    if payload.get("said"):
        return str(payload["said"])
    if payload.get("unreached"):
        return t("said.why.unreachable_at", host=str(payload["unreached"]))
    return ""


def _progress(state: dict[str, Any], text: str) -> None:
    if state.get("note") is None:
        state["note"] = ui.notification(text, spinner=True, timeout=None)
    else:
        state["note"].message = text


def _clear(state: dict[str, Any]) -> None:
    if state.get("note") is not None:
        state["note"].dismiss()
        state["note"] = None


async def _handle(state: dict[str, Any], payload: dict[str, Any],
                  on_drop: Callable[[Drop], Any]) -> None:
    state["busy"] = True
    # Bound to the client that asked. `asyncio.create_task` leaves the slot context the
    # event handler was running in, and anything drawn outside one belongs to no page
    # and is silently never shown - which made a drop that did everything right look
    # like a drop that did nothing.
    client = state["client"]
    try:
        found = Drop(where=drop_target.Where(
                         row_id=str(payload.get("row_id") or ""),
                         col_id=str(payload.get("col_id") or ""),
                         cell=tuple(str(one) for one in payload.get("cell") or ()),
                         declared=payload.get("declared") or None),
                     upload_id=str(payload.get("upload_id") or ""),
                     name=str(payload.get("name") or ""),
                     count=int(payload.get("count") or 0))
        with client:
            answer = on_drop(found)
            if asyncio.iscoroutine(answer):
                await answer
    except Exception:
        logger.exception("manager: a drop could not be handled")
        with client:
            ui.notify(t("manager.uploads.not_work"), type="negative")
    finally:
        state["busy"] = False


@on_page
async def analyzed(library: Any, upload_id: str, *,
                   game_named: bool = False) -> dict[str, Any] | None:
    """What the install makes of what arrived, or None once it has said why not and let
    the files go. Pictures, sounds and videos no name explains are not refused where a game
    is named: the sheet asks where each goes."""
    try:
        analysis = await offload.io(library.upload_analysis, upload_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("manager: upload %s could not be analyzed", upload_id, exc_info=True)
        analysis = {"error": why(exc)}
    if game_named and analysis.get("placeable") and not analysis.get("assets"):
        return {key: value for key, value in analysis.items() if key != "error"}
    if analysis.get("error"):
        logger.warning("manager: upload %s was not read: %s", upload_id, analysis["error"])
        ui.notify(t("manager.uploads.read_failed"), caption=str(analysis["error"]),
                  type="negative")
        await run.io_bound(library.abort_upload, upload_id)
        return None
    return analysis


@on_page
async def arrived(library: Any, upload_id: str, *, source: str, on_done: Callable[[], Any],
                  analysis: dict[str, Any] | None = None, reads_names: bool = True,
                  **target: Any) -> None:
    """The one conversation every file that arrives has, whichever zone or picker took it: what
    it is read as, then the confirmation. `target` is what the zone declared - `confirmed_import`
    names the fields.

    A caller that has read the files already hands the `analysis` over. `reads_names` is False
    for a target whose files are taken as the target says they are, never off their names.
    """
    if analysis is None:
        analysis = {}
        if reads_names:
            read = await analyzed(library, upload_id,
                                  game_named=bool(target.get("game_dir"))
                                  and not target.get("asset_kind")
                                  and not target.get("media_kind"))
            if read is None:
                return
            analysis = read
    await confirmed_import(library, upload_id, analysis, source=source, on_done=on_done,
                           **target)


@on_page
async def confirmed_import(library: Any, upload_id: str, analysis: dict[str, Any], *,
                           source: str, on_done: Callable[[], Any], file_count: int = 0,
                           game_id: str = "",
                           game_dir: str = "", allow_new_game: bool = False,
                           media_kind: str = "", location_id: str = "",
                           asset_kind: str = "", add_table: bool = False,
                           replace_table: str = "", instead: bool = False,
                           table_id: str = "", scope: str = "", page: int = 1,
                           on_imported: Callable[[dict, dict], Any] | None = None,
                           release: import_dialog.ReleaseMatch | None = None) -> None:
    """From staged files to the import confirmation, and what follows a yes.

    A `release` adds the VPS Match line to the confirmation.

    `on_done` runs only after an import, once the library has been read again.
    `on_imported` runs just before it, with the import's report and the plan it ran, for a
    caller that has to act on what landed where. Declined, blocked or empty, the staged
    files are let go and nothing else happens.
    """
    rom_name = _rom_of(library, game_id)
    try:
        plan = await offload.io(library.upload_plan, upload_id, game_dir=game_dir,
                                rom_name=rom_name, allow_new_game=allow_new_game,
                                media_kind=media_kind, location_id=location_id,
                                asset_kind=asset_kind, add_table=add_table,
                                replace_table=replace_table, instead=instead,
                                table_id=table_id, scope=scope, page=page)
    except Exception as exc:  # noqa: BLE001
        logger.warning("manager: upload %s has no destination", upload_id, exc_info=True)
        ui.notify(t("manager.uploads.no_destination"), caption=why(exc),
                  type="negative")
        await run.io_bound(library.abort_upload, upload_id)
        return
    if not any(plan.get(name) for name in ("items", "groups", "unclaimed")):
        reasons = dict.fromkeys(drop_sheet.Sheet(plan).refusals())
        for said in reasons or [t("manager.uploads.nothing_import")]:
            logger.warning("manager: upload %s had nothing to import: %s", upload_id, said)
            ui.notify(said, type="warning")
        await run.io_bound(library.abort_upload, upload_id)
        return

    async def done(report: Any) -> None:
        if game_id:
            library.forget_media(game_id)
        await run.io_bound(library.refresh_after_import)
        if on_imported is not None and not report.get("failed"):
            followed = on_imported(report, plan)
            if asyncio.iscoroutine(followed):
                await followed
        answer = on_done()
        if asyncio.iscoroutine(answer):
            await answer

    await import_dialog.open_for(
        library, upload_id, plan, source=source, file_count=file_count, game_dir=game_dir,
        rom_name=rom_name,
        allow_new_game=allow_new_game, media_kind=media_kind, location_id=location_id,
        asset_kind=asset_kind, add_table=add_table, replace_table=replace_table,
        instead=instead, table_id=table_id, scope=scope, page=page,
        declared=_declared(analysis, game_id),
        release=release, on_done=done)


def _rom_of(library: Any, game_id: str) -> str:
    if not game_id:
        return ""
    found = next((one for one in library.games if str(one.get("id")) == game_id), None)
    return str((found or {}).get("rom") or "").strip()


def _declared(analysis: dict[str, Any], game_id: str) -> dict[str, Any]:
    """What choosing a game said the files are: that game's, on the user's word."""
    if not game_id:
        return {}
    names = {str(entry.get("path") or "").rsplit("/", 1)[-1]
             for asset in analysis.get("assets") or ()
             for entry in asset.get("entries") or ()}
    return {name: {"game_id": game_id, "host": "user", "confirmed_by": "user"}
            for name in names if name}


def token() -> str:
    """A per-page id, for anything that has to tell two drops apart."""
    return uuid4().hex[:12]

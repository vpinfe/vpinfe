"""Inspect Everything: the files as the zip will hold them, with each replaced value marked
and any line of a log removable.

The page is cut into pieces and every control sits above or below them: a log is thousands
of lines, so a page of lines is one escaped block of text whose click says which line, never
a control per line. What a mark hides shows on hover, on this screen only - the originals are
the dialog's own and are not part of anything sent.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from typing import Any

from nicegui import run, ui

from common.i18n import t
from common.support import package, pieces, redact
from common.support.draft import Draft
from manager.kit import panel, verbs
from manager.kit.on_page import on_page

PAGE_LINES = 100
MATCH_LIMIT = 200
KEPT_WHOLE = (package.README, package.SUMMARY)

_PICK_JS = ("(e) => { const l = e.target.closest('[data-line]'); "
            "if (l) emit({file: l.dataset.file, line: Number(l.dataset.line)}); }")


def kind_label(kind: str) -> str:
    return t(f"manager.support.kind.{kind}")


class Inspector:
    def __init__(self, draft: Draft, leave: Callable[[], None]) -> None:
        self.draft = draft
        self.leave = leave
        self.file = ""
        self.page = 0
        self.needle = ""
        self.picked: tuple[str, int] | None = None
        self.body: Any = None

    def draw(self) -> None:
        with ui.column().classes("w-full gap-2 support-inspect") as root:
            self.body = root
        self._fill()

    def _package(self) -> package.Package:
        made = self.draft.made
        if made is None:
            raise RuntimeError("nothing has been gathered")
        return made

    def _rows(self) -> tuple[list[tuple[str, int, str]], bool]:
        made = self._package()
        if self.needle.strip():
            lines = {one.name: _lines(one.text) for one in made.parts}
            found = pieces.matching(made, self.needle, MATCH_LIMIT)
            return [(name, index, lines[name][index]) for name, index in found], \
                len(found) >= MATCH_LIMIT
        part = made.part(self.file) or made.parts[0]
        self.file = part.name
        return [(part.name, index, line) for index, line in enumerate(_lines(part.text))], False

    def _fill(self) -> None:
        self.body.clear()
        rows, capped = self._rows()
        searching = bool(self.needle.strip())
        last = max(0, (len(rows) - 1) // PAGE_LINES)
        self.page = min(self.page, last)
        shown = rows[self.page * PAGE_LINES:(self.page + 1) * PAGE_LINES]
        with self.body:
            self._toolbar()
            if not rows:
                ui.label(t("manager.support.inspect.no_match") if searching
                         else t("manager.support.inspect.empty")).classes("manager-help")
            else:
                block = ui.html(self._html(shown, searching), sanitize=False, tag="div") \
                    .classes("support-lines manager-log")
                block.on("click", self._pick, js_handler=_PICK_JS)
                self._pager(len(rows), shown, last, capped)
            self._picked_bar()
            self._footer()

    def _toolbar(self) -> None:
        made = self._package()
        with ui.row().classes("w-full items-center gap-2 no-wrap"):
            panel.select({one.name: one.name for one in made.parts}, self.file,
                         self._choose_file)()
            panel.field(self.needle, self._search,
                        placeholder=t("manager.support.inspect.search"))()

    def _pager(self, total: int, shown: list[tuple[str, int, str]], last: int,
               capped: bool) -> None:
        with ui.row().classes("w-full items-center gap-2 no-wrap"):
            earlier = t("manager.support.inspect.earlier")
            later = t("manager.support.inspect.later")
            panel.icon_action(earlier, lambda: self._go(self.page - 1), icon=verbs.EARLIER,
                              hint=earlier, enabled=self.page > 0)()
            panel.icon_action(later, lambda: self._go(self.page + 1), icon=verbs.LATER,
                              hint=later, enabled=self.page < last)()
            ui.label(t("manager.support.inspect.showing", first=self.page * PAGE_LINES + 1,
                       last=self.page * PAGE_LINES + len(shown), total=total)) \
                .classes("manager-help")
            if capped:
                ui.label(t("manager.support.inspect.capped", limit=MATCH_LIMIT)) \
                    .classes("manager-help")

    def _picked_bar(self) -> None:
        if self.picked is None:
            return
        name, index = self.picked
        with ui.row().classes("w-full items-center gap-2 no-wrap support-picked"):
            ui.label(t("manager.support.inspect.picked", file=name, line=index + 1)) \
                .classes("manager-help grow min-w-0")
            panel.action(t("manager.support.inspect.remove_line"), self._remove,
                         icon=verbs.REMOVE, hint=t("manager.support.inspect.remove_line_hint"),
                         danger=True)()
            panel.action(t("manager.support.inspect.keep_line"), self._unpick,
                         icon=verbs.KEEP, hint=t("manager.support.inspect.keep_line_hint"))()

    def _footer(self) -> None:
        draft = self.draft
        with ui.row().classes("w-full items-center gap-2"):
            if self.file not in KEPT_WHOLE and not self.needle.strip():
                panel.action(t("manager.support.inspect.leave_out"), self._leave_out,
                             icon=verbs.HIDE, hint=t("manager.support.inspect.leave_out_hint"))()
            for name in draft.left_out:
                panel.action(t("manager.support.inspect.put_back", file=name),
                             lambda name=name: self._put_back(name),
                             icon=verbs.UNHIDE,
                             hint=t("manager.support.inspect.put_back_hint", file=name))()
            if draft.removed:
                panel.action(t("manager.support.inspect.restore_lines",
                               count=len(draft.removed)), self._restore_lines,
                             icon=verbs.REVERT,
                             hint=t("manager.support.inspect.restore_lines_hint"))()
            panel.action(t("manager.support.inspect.back"), self.leave, icon=verbs.BACK,
                         hint=t("manager.support.inspect.back_hint"))()

    def _html(self, rows: list[tuple[str, int, str]], searching: bool) -> str:
        originals = self._package().originals
        out = []
        for name, index, line in rows:
            classes = "support-line"
            if line == package.REMOVED_LINE:
                classes += " support-line--removed"
            if self.picked == (name, index):
                classes += " support-line--picked"
            mark = f"{html.escape(name)}:" if searching else ""
            body = "".join(_piece(one) for one in pieces.line_pieces(line, originals))
            pickable = name not in KEPT_WHOLE and line and line != package.REMOVED_LINE
            address = (f' data-file="{html.escape(name, quote=True)}" data-line="{index}"'
                       if pickable else "")
            out.append(f'<div class="{classes}"{address}>'
                       f'<span class="support-no">{mark}{index + 1}</span>'
                       f'<span class="support-text">{body}</span></div>')
        return "".join(out)

    def _go(self, page: int) -> None:
        self.page = max(0, page)
        self._fill()

    def _choose_file(self, event: Any) -> None:
        self.file = str(event.value or "")
        self.needle = ""
        self.page = 0
        self.picked = None
        self._fill()

    def _search(self, text: str) -> None:
        self.needle = text
        self.page = 0
        self.picked = None
        self._fill()

    def _pick(self, event: Any) -> None:
        args = event.args or {}
        key = (str(args.get("file") or ""), int(args.get("line") or 0))
        self.picked = None if self.picked == key else key
        self._fill()

    def _unpick(self) -> None:
        self.picked = None
        self._fill()

    async def _apply(self, change: Callable[[], None]) -> None:
        change()
        await run.io_bound(self.draft.refresh)
        self._fill()

    @on_page
    async def _remove(self) -> None:
        if self.picked is None:
            return
        name, index = self.picked
        self.picked = None
        await self._apply(lambda: self.draft.remove_line(name, index))

    @on_page
    async def _leave_out(self) -> None:
        name = self.file
        if not name:
            return
        self.file = ""
        self.page = 0
        await self._apply(lambda: self.draft.leave_out(name, True))

    @on_page
    async def _put_back(self, name: str) -> None:
        await self._apply(lambda: self.draft.leave_out(name, False))

    @on_page
    async def _restore_lines(self) -> None:
        await self._apply(self.draft.restore_lines)


def _lines(text: str) -> list[str]:
    found = text.split("\n")
    if found and found[-1] == "":
        found.pop()
    return found


def _piece(one: pieces.Piece) -> str:
    text = html.escape(one.text)
    if one.kind is None:
        return text
    said = kind_label(one.kind) + (f" - {one.original}" if one.original else "")
    return f'<mark class="support-mark" title="{html.escape(said, quote=True)}">{text}</mark>'


def replaced_counts(made: package.Package) -> list[tuple[str, int]]:
    """Each kind with how many times it was replaced, in the engine's own order."""
    return [(kind, made.counts[kind]) for kind in redact.KINDS if made.counts.get(kind)]

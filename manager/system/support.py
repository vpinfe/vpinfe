"""Create Support Package: what goes in, what was kept private, and where it goes.

Runs in this process, never over the HTTP API: only here may a replaced value be shown, on
the person's own screen. Nothing is sent from here; both destinations are links the browser
opens. The draft is dropped when the dialog closes, however it closes.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from nicegui import run, ui

from common.i18n import t
from common.support import destinations, package
from common.support.draft import Draft
from manager.kit import clipboard, panel, verbs, wizard
from manager.kit.on_page import on_page
from manager.system import support_inspect

WHAT = "what"
PRIVACY = "privacy"
SHARE = "share"
STEPS = (WHAT, PRIVACY, SHARE)

PARTS = ("logs", "previous_log", "settings")
HELPED = ("logs", "previous_log")
SHOWN_PRECAUTIONS = 5


def _row(here: str) -> list[dict[str, Any]]:
    before = True
    row = []
    for key in STEPS:
        row.append({"key": key, "label": t(f"manager.support.step.{key}"),
                    "done": before and key != here, "holds": [key]})
        before = before and key != here
    return row


def _what(draft: Draft) -> dict[str, Any]:
    return {
        "step": WHAT, "title": t("manager.support.what.title"),
        "help": t("manager.support.what.lead"), "steps": _row(WHAT),
        "fields": [{"key": part, "type": "switch", "value": getattr(draft, part),
                    "label": t(f"manager.support.what.{part}"),
                    **({"help": t(f"manager.support.what.{part}_help")}
                       if part in HELPED else {})} for part in PARTS],
    }


def _privacy(draft: Draft) -> dict[str, Any]:
    return {
        "step": PRIVACY, "title": t("manager.support.privacy.title"),
        "help": t("manager.support.privacy.lead"), "steps": _row(PRIVACY),
        "draw": lambda values: _draw_privacy(draft),
    }


def _share(draft: Draft) -> dict[str, Any]:
    return {
        "step": SHARE, "title": t("manager.support.share.title"),
        "help": t("manager.support.share.lead"), "steps": _row(SHARE),
        "draw": lambda values: _draw_share(draft),
        "confirm": t("word.done"),
    }


def _made(draft: Draft) -> package.Package:
    if draft.made is None:
        raise RuntimeError("nothing has been gathered")
    return draft.made


def _notes(draft: Draft) -> list[tuple[Any, ...]]:
    """What the person should know about what was read, only where there is something."""
    made = _made(draft)
    said = []
    if draft.logs and made.part(package.LOG) is None and package.LOG not in draft.left_out:
        said.append(t("manager.support.note.no_log"))
    said += [t("manager.support.note.large", file=one.name, count=one.omitted)
             for one in made.parts if one.omitted]
    unread = sum(1 for line in made.summary.splitlines() if line.startswith("Not reported"))
    if unread:
        said.append(t("manager.support.note.unread", count=unread))
    return [panel.note(text) for text in said]


def _replaced(made: package.Package) -> list[tuple[Any, ...]]:
    counts = support_inspect.replaced_counts(made)
    if not counts:
        return [(panel.HEADING, t("manager.support.privacy.replaced")),
                (panel.FULL, lambda: ui.label(t("manager.support.privacy.nothing"))
                 .classes("manager-help"))]
    return [(panel.HEADING, t("manager.support.privacy.replaced")),
            *[(panel.READ, (support_inspect.kind_label(kind), str(count)))
              for kind, count in counts]]


def _caution(made: package.Package) -> list[tuple[Any, ...]]:
    if not made.precautions:
        return []

    def line(text: str) -> Callable[[], None]:
        def draw() -> None:
            ui.label(text).classes("manager-help manager-log")
        return draw

    return [(panel.HEADING, t("manager.support.privacy.caution")),
            *[(panel.FULL, line(one.line))
              for one in made.precautions[:SHOWN_PRECAUTIONS]]]


def _draw_privacy(draft: Draft) -> None:
    holder = ui.column().classes("w-full gap-0")

    def summary() -> None:
        holder.clear()
        with holder:
            _summary(draft, summary, inspect)

    def inspect() -> None:
        holder.clear()
        with holder:
            support_inspect.Inspector(draft, summary).draw()

    summary()


def _summary(draft: Draft, refresh: Callable[[], None], inspect: Callable[[], None]) -> None:
    made = _made(draft)

    @on_page
    async def rebuilt(change: Callable[[], object]) -> None:
        change()
        await run.io_bound(draft.refresh)
        refresh()

    def hide_tables(event: Any) -> Any:
        return rebuilt(lambda: draft.set_hide_tables(bool(event.value)))

    entries: list[tuple[Any, ...]] = [
        *_replaced(made),
        *_caution(made),
        *_notes(draft),
        (panel.HEADING, t("manager.support.privacy.choices")),
        (t("manager.support.privacy.hide_tables"),
         panel.switch(draft.hide_tables, hide_tables)),
        (t("manager.support.privacy.also_hide"), _hiding(draft, rebuilt)),
        panel.note(t("manager.support.privacy.also_hide_help")),
        (panel.FULL, panel.action(t("manager.support.privacy.inspect"), inspect,
                                  icon=verbs.SEARCH,
                                  hint=t("manager.support.privacy.inspect_hint"))),
    ]
    panel.facts(ui, entries)


def _hiding(draft: Draft, rebuilt: Callable[[Callable[[], object]], Any]) -> Callable[[], None]:
    """A word to hide wherever it appears, and the words already hidden with a way to let
    each go."""
    held: dict[str, str] = {}

    def add(text: str) -> Any:
        return rebuilt(lambda: draft.hide_word(text))

    def let_go(word: str) -> Callable[[], Any]:
        return lambda: rebuilt(lambda: draft.unhide(word))

    def draw() -> None:
        with ui.column().classes("gap-1 w-full min-w-0"):
            panel.field("", add, held=held,
                        placeholder=t("manager.support.privacy.also_hide_placeholder"))()
            for word in draft.hide:
                with ui.row().classes("items-center gap-2 no-wrap"):
                    ui.label(word).classes("manager-fact-value truncate min-w-0")
                    panel.icon_action(t("word.remove"), let_go(word), icon=verbs.REMOVE,
                                      hint=t("manager.support.privacy.show_again", word=word))()

    return draw


def _copy(label: str, text: str, hint: str) -> Callable[[], None]:
    """A button that copies in the browser at the click, the only moment it will."""
    return panel.action(label, lambda event: clipboard.copied(bool(event.args), text),
                        icon=verbs.COPY, hint=hint, js=clipboard.COPY_JS % json.dumps(text))


def _draw_share(draft: Draft) -> None:
    made = _made(draft)
    summary = made.summary

    def download() -> None:
        ui.download.content(made.data, made.filename, "application/zip")

    def together(*draws: Callable[[], None]) -> Callable[[], None]:
        def draw() -> None:
            with ui.row().classes("items-center gap-3 manager-fact-edit"):
                for one in draws:
                    one()
        return draw

    panel.facts(ui, [
        (t("manager.support.share.package"), together(
            panel.action(t("word.fetch"), download, icon=verbs.FETCH,
                         hint=panel.NO_HINT))),
        panel.note(t("manager.support.share.package_help", name=made.filename,
                     kb=max(1, round(len(made.data) / 1024)))),
        (t("manager.support.share.discord"), together(
            _copy(t("manager.support.share.copy_summary"),
                  destinations.discord_summary(summary),
                  t("manager.support.share.copy_summary_hint")),
            panel.link_out(t("manager.support.share.open_discord"),
                           to=destinations.DISCORD_URL, rel="noopener noreferrer",
                           hint=t("manager.support.share.open_discord_hint")))),
        panel.note(t("manager.support.share.discord_help")),
        (t("manager.support.share.github"), together(
            panel.link_out(t("manager.support.share.new_issue"),
                           to=destinations.github_link(summary), rel="noopener noreferrer",
                           hint=t("manager.support.share.new_issue_hint")))),
        panel.note(t("manager.support.share.github_help")),
        (t("manager.support.share.markdown"), together(
            _copy(t("manager.support.share.copy_markdown"), summary,
                  t("manager.support.share.copy_markdown_hint")))),
        panel.note(t("manager.support.share.markdown_help")),
        (panel.READ, (t("manager.support.share.checksum"),
                      panel.copyable_value(made.checksum,
                                           t("manager.support.share.copy_checksum")))),
    ])


def calls(draft: Draft) -> wizard.Calls:
    async def first(key: str | None) -> dict:
        if key == SHARE and draft.made is not None:
            return _share(draft)
        if key == PRIVACY and draft.made is not None:
            return _privacy(draft)
        return _what(draft)

    async def check(values: dict[str, Any], key: str) -> dict:
        if key == WHAT:
            for part in PARTS:
                setattr(draft, part, bool(values.get(part, True)))
            try:
                await run.io_bound(draft.gather)
            except package.PackageTooLargeError:
                found = _what(draft)
                found["errors"] = {"logs": t("manager.support.too_large")}
                return found
            return _privacy(draft)
        await run.io_bound(draft.refresh)
        return _share(draft)

    async def act(key: str, values: dict[str, Any], step: str) -> dict:
        return await first(step)

    async def finish(values: dict[str, Any], key: str) -> dict:
        return {"ok": True}

    async def job(job_id: str) -> dict:
        return {"state": "done"}

    async def progress() -> dict:
        return {"message": t("manager.support.gathering")}

    return wizard.Calls(first, check, act, finish, job, progress)


@on_page
async def open_support_package() -> bool:
    """The dialog over the page this is called from, until it is closed. Whatever was
    gathered is dropped when it is, so nothing waits on a later request."""
    draft = Draft()
    try:
        return await wizard.open_dialog(
            label=t("manager.support.title"), calls=calls(draft), under="support",
            aside=t("word.close"), close_when_done=True) is True
    finally:
        draft.drop()


__all__ = ["open_support_package", "calls"]

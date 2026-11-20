"""The questions asked before something VPinFE did not ship is let run.

A theme that is not from the VPinFE Theme List and an extension that is not included with
VPinFE both run code on this device, and VPinFE cannot check what it does. Each is asked
about here, in the same words wherever it is met, and answered with the Owner's yes: the
callers only go on to accept on a True.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from nicegui import ui

from common.i18n import t
from manager.extensions import ext_mark
from manager.kit import dialog as frame
from manager.kit import panel, verbs

__all__ = ["extension", "theme", "themes", "update"]


async def _ask(title: str, lines: Sequence[str], answer: str, *,
               rows: Sequence[tuple[Any, ...]] = (), closing: str = "",
               warning: str = "", understood: str = "") -> bool:
    with frame.opened(title, size="question") as box:
        for line in lines:
            ui.label(line).classes("manager-help")
        if rows:
            panel.facts(ui, rows)
        if closing:
            ui.label(closing).classes("manager-help")
        if warning:
            with ui.element("div").classes("manager-attention w-full"):
                ui.icon(ext_mark.GLYPH).classes("manager-attention-icon")
                ui.label(warning).classes("manager-attention-line")
        ticked = frame.tick(understood) if understood else None
        with frame.footer():
            frame.cancel(lambda: box.submit(False))
            frame.answer(answer, lambda: box.submit(True), icon=verbs.ACCEPT, needs=ticked)
    return bool(await box)


async def theme(name: str, *, source: str = "", installing: bool = False) -> bool:
    """Whether to let this theme run. `source` is where it comes from, empty for one found
    in the themes folder; `installing` is a theme not here yet, whose answer also installs
    it."""
    where = (t("manager.trust.theme_from", name=name, source=source) if source
             else t("manager.trust.theme_found", name=name))
    return await _ask(
        t("manager.trust.theme_install_title" if installing
          else "manager.trust.theme_trust_title"),
        [where, t("manager.trust.theme_risk")],
        t("manager.trust.trust_install" if installing else "manager.trust.trust"),
        closing=t("manager.trust.theme_install_trust" if installing
                  else "manager.trust.theme_use_trust"))


async def themes(found: Sequence[tuple[str, str]]) -> bool:
    """Whether to let all of these run, in one answer. Each is a name and where it comes
    from, empty for one found in the themes folder."""
    return await _ask(
        t("manager.trust.themes_title", count=len(found)),
        [t("manager.trust.themes_from"), t("manager.trust.themes_risk")],
        t("manager.trust.trust_all"),
        rows=[(name, source or t("manager.themes.in_folder")) for name, source in found],
        closing=t("manager.trust.themes_trust"))


async def extension(name: str, version: str, *, author: str, source: str, path: str,
                    reach: Sequence[tuple[Any, ...]] = (), replaces: str = "",
                    changed: int = 0, claims_team: bool = False,
                    added: Sequence[tuple[Any, ...]] = (), was: str = "",
                    dependencies: Sequence[tuple[Any, ...]] = ()) -> bool:
    """Whether to turn this unofficial extension on, asked every time it goes from off to on.

    `reach` is the rows saying what it can do, as the extension's panel draws them. `replaces`
    names the included extension whose name it uses, `changed` is how many of the files of
    one installed from the Extension List differ since, and `claims_team` is an extension
    that names the VPinFE team as its author or source. `added` is the rows for what a newer
    version asks for beyond what was trusted, and `was` the version that was. `dependencies` is
    a row for each dependency it fetches, which are code that runs with VPinFE.
    """
    lines = [t("manager.trust.ext_body", name=name)]
    if replaces:
        lines.append(t("manager.trust.ext_copy", name=replaces))
    if changed:
        lines.append(t("manager.trust.ext_changed", count=changed))
    if claims_team:
        lines.append(t("manager.trust.ext_claims_team"))
    if was and was != version:
        lines.append(t("manager.trust.access_body", was=was, version=version))
    rows: list[tuple[Any, ...]] = [(t("manager.ext_page.author"), author),
                                   (t("manager.ext_page.version_label"), version)]
    if source:
        rows.append((t("manager.ext_page.source"),
                     panel.link_out(ext_mark.source_shown(source), to=source, hint=source,
                                    rel="noopener noreferrer")))
    if path:
        rows.append((t("manager.ext_page.folder_path"), panel.path_value(path)))
    if reach:
        rows.append((panel.HEADING, t("manager.extensions.reach")))
        rows += reach
    else:
        rows.append((t("manager.extensions.reach"), t("word.none")))
    if added:
        rows.append((panel.HEADING, t("manager.trust.new_reach")))
        rows += added
    if dependencies:
        rows.append((panel.HEADING, t("manager.trust.dependencies")))
        rows += dependencies
        rows.append(panel.note(t("manager.trust.dependencies_from")))
    return await _ask(t("manager.trust.ext_title"), lines, t("manager.trust.ext_button"),
                      rows=rows, closing=t("manager.trust.ext_reported"),
                      warning=t("manager.trust.ext_unsupported"),
                      understood=t("manager.trust.ext_understand"))


async def update(name: str, version: str, *, was: str, author: str, address: str,
                 news: str = "", news_label: str = "", moved_to: str = "",
                 added: Sequence[tuple[Any, ...]] = (),
                 dependencies: Sequence[tuple[Any, ...]] = ()) -> bool:
    """Whether to update an unofficial extension to `version`, from its author's repository.

    `added` is the rows for what this version asks for beyond what `was` was trusted with,
    and `dependencies` a row for each dependency it adds or changes. With neither, the answer is a
    plain Update; with either, it needs the tick as well.
    `moved_to` is the address the repository now answers from, when it is not `address`.
    """
    lines = [t("manager.trust.update_body", name=name, version=version),
             t("manager.trust.update_unchecked", was=was)]
    if moved_to:
        lines.append(t("manager.trust.update_moved", address=ext_mark.source_shown(moved_to)))
    more = bool(added or dependencies)
    if added:
        lines += [t("manager.trust.update_more", was=was), t("manager.trust.ext_reported")]
    elif dependencies:
        lines.append(t("manager.trust.update_dependencies", count=len(dependencies), was=was,
                       version=version))
    rows: list[tuple[Any, ...]] = [
        (t("manager.ext_page.author"), author),
        (t("manager.ext_page.version_label"), version),
        (t("manager.ext_page.source"),
         panel.link_out(ext_mark.source_shown(moved_to or address), to=moved_to or address,
                        hint=moved_to or address, rel="noopener noreferrer"))]
    if news:
        rows.append((t("manager.extensions.whats_new"),
                     panel.link_out(news_label or news, to=news, hint=news,
                                    rel="noopener noreferrer")))
    if added:
        rows.append((panel.HEADING, t("manager.trust.new_reach")))
        rows += added
    if dependencies:
        rows.append((panel.HEADING, t("manager.trust.new_dependencies")))
        rows += dependencies
        rows.append(panel.note(t("manager.trust.dependencies_from")))
    return await _ask(
        t("manager.trust.update_title", name=name), lines,
        t("manager.trust.update_trust_button" if more else "manager.trust.update_button"),
        rows=rows, closing=t("manager.trust.update_runs", was=was),
        warning=t("manager.trust.ext_unsupported") if more else "",
        understood=t("manager.trust.ext_understand") if more else "")

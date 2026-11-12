"""The questions asked before something VPinFE did not ship is let run.

A theme that is not from the VPinFE Theme List and an extension that is not included with
VPinFE both run code on this device, and VPinFE cannot check what it does. Each is asked
about here, in the same words wherever it is met, and answered with the Owner's yes: the
callers only go on to accept on a True.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from nicegui import ui

from common.i18n import t
from manager.extensions import ext_mark
from manager.kit import dialog as frame
from manager.kit import panel, verbs

__all__ = ["chips", "extension", "new_access", "theme", "themes"]


def chips(words: Sequence[str]) -> Callable[[], None]:
    def draw() -> None:
        with ui.element("div").classes("manager-chips"):
            for word in words:
                ui.label(word).classes("manager-tier manager-tier--on")

    return draw


async def _ask(title: str, lines: Sequence[str], answer: str, *,
               rows: Sequence[tuple[Any, ...]] = (), closing: str = "") -> bool:
    with frame.opened(title, size="question") as box:
        for line in lines:
            ui.label(line).classes("manager-help")
        if rows:
            panel.facts(ui, rows)
        if closing:
            ui.label(closing).classes("manager-help")
        with frame.footer():
            frame.cancel(lambda: box.submit(False))
            frame.answer(answer, lambda: box.submit(True), icon=verbs.ACCEPT)
    return bool(await box)


def _capability_rows(uses: Sequence[str], remote: Sequence[str]) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    if uses:
        rows.append((t("manager.trust.uses"), chips(uses)))
    if remote:
        rows.append((t("manager.trust.remote"), chips(remote)))
    return rows


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
                    uses: Sequence[str], remote: Sequence[str] = (), replaces: str = "",
                    replaces_version: str = "") -> bool:
    """Whether to let this extension run. `uses` is what it says it uses, in words;
    `remote` is what a Remote can open; `replaces` names the built-in it stands in for,
    and `replaces_version` the one that comes with VPinFE."""
    lines = [t("manager.trust.ext_body", name=name)]
    if replaces:
        lines.append(t("manager.trust.replaces_version", name=replaces,
                       version=replaces_version) if replaces_version
                     else t("manager.trust.replaces", name=replaces))
    rows: list[tuple[Any, ...]] = [(t("manager.ext_page.author"), author),
                                   (t("manager.ext_page.version_label"), version)]
    if source:
        rows.append((t("manager.ext_page.source"),
                     panel.link_out(ext_mark.source_shown(source), to=source, hint=source,
                                    rel="noopener noreferrer")))
    if path:
        rows.append((t("manager.ext_page.folder_path"), panel.path_value(path)))
    rows += _capability_rows(uses or [t("word.none")], remote)
    return await _ask(t("manager.trust.ext_title"), lines, t("manager.trust.trust"),
                      rows=rows, closing=t("manager.trust.ext_trust"))


async def new_access(name: str, version: str, author: str, added: Sequence[str],
                     remote: Sequence[str] = (), *, was: str = "") -> bool:
    """Whether to let a newer version of an accepted extension use `added`, and let a Remote
    open `remote`, which the version accepted did not say. `was` is that version, when it
    is known. The author is shown again so an update says whose it is."""
    rows: list[tuple[Any, ...]] = [(t("manager.ext_page.author"), author)]
    if added or remote:
        rows.append((t("manager.trust.new_capabilities"), chips([*added, *remote])))
    body = (t("manager.trust.access_body", was=was, version=version) if was and was != version
            else t("manager.trust.access_body_to", version=version))
    return await _ask(t("manager.trust.access_title", name=name), [body],
                      t("manager.trust.trust"), rows=rows,
                      closing=t("manager.trust.access_stopped"))

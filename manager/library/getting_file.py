"""Getting the file a table is replaced with: from the release's page where there is one,
then dropped here or chosen.

What follows the file is the import confirmation, asked to replace the one table.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nicegui import run, ui

from common.i18n import t
from manager.kit import dialog as frame
from manager.kit import kind_words, panel, verbs
from manager.kit.on_page import on_page
from manager.library import import_dialog, mediasource, uploads


async def _stay(_current: str) -> tuple[str, str] | None:
    """The release was named by the act that opened this, so there is no other to pick."""
    return None


def release_of(vps_file_id: str, label: str) -> import_dialog.ReleaseMatch:
    return import_dialog.ReleaseMatch(
        _stay, kind_words.label("table"), kind_words.vps_name("table"),
        vps_file_id=vps_file_id, label=label)


def open_for(library: Any, *, game_id: str, game_dir: str, table_id: str,
                   page: str = "", site: str = "", title: str = "",
                   release: import_dialog.ReleaseMatch | None = None,
                   instead: bool = False, on_done: Callable[[], Any]) -> None:
    """Ask for the file that takes `table_id`'s place. `page` is where the release is
    fetched from; without one the dialog only takes the file. `on_done` runs once the
    import has been read back."""
    with frame.opened(title or t("manager.getting_file.title"), wide=True) as dialog:
        card = ui.column().classes("gap-2 w-full px-3")
        with card:
            if page:
                with ui.row().classes("items-center gap-2"):
                    ui.label(t("manager.getting_file.download"))
                    panel.link_out(site or page, to=page,
                                   hint=t("manager.discovery.open_on", site=site or page))()
                ui.label(t("manager.getting_file.then"))

            @on_page
            async def arrived(drop: uploads.Drop) -> None:
                dialog.close()
                await uploads.arrived(
                    library, drop.upload_id, source=drop.name,
                    file_count=drop.count, on_done=on_done, game_id=game_id,
                    game_dir=game_dir, replace_table=table_id, instead=instead,
                    on_imported=tie(library, game_id, release), release=release)

            heard = uploads.listener(arrived)
            with ui.column().classes("items-center gap-1 w-full manager-dropbox"):
                ui.label(t("manager.getting_file.drop_for", version=release.label)
                         if release is not None and release.label
                         else t("manager.mediasource.drop_file")) \
                    .classes("manager-help manager-drop-only")
                panel.action(t("manager.mediasource.choose_file"), heard, icon=verbs.FROM_FILE,
                             hint=t("manager.mediasource.choose_file.help"),
                             js="() => window.__managerChoose(false, emit, true)")()
        mediasource.take_drops(card, arrived)
        with frame.footer():
            frame.cancel(dialog.close)
    dialog.open()


def tie(library: Any, game_id: str, release: import_dialog.ReleaseMatch | None
        ) -> Callable[[dict[str, Any], dict[str, Any]], Any]:
    """Say, once the swap is done, that the table is the release the file was fetched as."""
    async def tied(report: dict[str, Any], _plan: dict[str, Any]) -> None:
        if release is None or not release.vps_file_id:
            return
        made = [*(str(one) for one in report.get("added_tables") or ()),
                *(str(one) for one in report.get("patched_tables") or ()),
                *(str(one["table"]["id"]) for one in report.get("replaced_tables") or ())]
        for table_id in made:
            await run.io_bound(library.set_table_source, game_id, table_id,
                               release.vps_file_id)

    return tied

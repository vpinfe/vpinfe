"""What is wrong with a game's tables, and the act each finding carries."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from nicegui import ui

from common.i18n import t
from manager.kit import game_tables, panel
from manager.workbench import core, game_table
from tests.support.workbench_patch import patch_workbench


async def _now(callback: Any, *args: Any, **kwargs: Any) -> Any:
    return callback(*args, **kwargs)


def _alert(line: str, act: Any, said: Any = (), more: Any = None) -> tuple[str, Any, str]:
    """The notice as its words: the lines under it, and the mark's line as `text [hint]`."""
    under = [*said, *([f"{more[0]} [{more[1]}]"] if more else [])]
    return (line, act, "\n".join(under))


def _table(table_id: str, **extra: Any) -> dict[str, Any]:
    return {"id": table_id, "filename": f"{table_id}.vpx", "version": "1.0",
            "authors": ["Someone"], "available": True, **extra}


def _context(*tables: dict[str, Any]) -> dict[str, Any]:
    library = Mock()
    library.vps_releases.return_value = [
        {"vps_file_id": "r-1", "version": "1.2", "url": "https://host.example/r-1"},
        {"vps_file_id": "r-2", "version": "2.0", "url": ""}]
    library.vps_entry.return_value = {"url": "https://vps.example/entry"}
    return {"library": library, "game": {"name": "Sample Game", "vps_id": "e-1",
                                         "folder": "/library/Sample Game"},
            "game_id": "g1", "tables": list(tables), "lens": "", "state": {},
            "rebuild": AsyncMock()}


class Findings(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        for patched in (
                patch("manager.kit.offload.run.io_bound", new=_now),
                patch.object(panel, "action",
                             new=lambda label, *_a, **_k: ("act", label)),
                patch.object(panel, "link_out",
                             new=lambda label, *, to, **_k: ("link", label, to)),
                patch_workbench("_alert", new=_alert)):
            patched.start()
            self.addCleanup(patched.stop)

    async def _found(self, *tables: dict[str, Any]) -> list[tuple[Any, Any, str]]:
        entries = await game_table._findings(_context(*tables), tables)
        self.assertTrue(all(label == core.FULL for label, _ in entries))
        return [alert for _, alert in entries]

    async def test_a_rom_that_is_not_installed_is_added_from_its_alert(self) -> None:
        found = await self._found(_table("a", dependencies={
            "pinmame": {"effective": "hustler", "installed": False}}))

        self.assertEqual(found, [(t("manager.workbench.rom_missing", rom="hustler"),
                                  ("act", t("word.add")), "")])

    async def test_a_file_that_is_gone_is_forgotten_from_its_alert(self) -> None:
        found = await self._found(_table("a", available=False))

        self.assertEqual(found, [(t("manager.workbench.file_absent"),
                                  ("act", t("word.forget")), "")])

    async def test_an_update_gives_the_version_at_the_tie_and_the_one_now(self) -> None:
        found = await self._found(_table("a", update_available=True, source={
            "vps_file_id": "r-1", "version": "1.2", "vps_version": "1.1"}))

        self.assertEqual(found, [(
            t("manager.workbench.newer_on_vps", theirs="1.2", ours="1.1"),
            ("link", t("manager.workbench.get_version", version="1.2"),
             "https://host.example/r-1"), "")])

    async def test_a_release_with_no_page_links_to_its_entry(self) -> None:
        found = await self._found(_table("a", update_available=True, source={
            "vps_file_id": "r-2", "version": "2.0"}))

        self.assertEqual(found[0][1][2], "https://vps.example/entry")

    async def test_an_update_vps_could_not_be_read_for_says_so_in_place_of_the_link(
            self) -> None:
        for failing in ("vps_releases", "vps_entry"):
            with self.subTest(failing=failing):
                tables = (_table("a", update_available=True, source={
                    "vps_file_id": "r-9", "version": "3.0", "vps_version": "2.5"}),)
                context = _context(*tables)
                getattr(context["library"], failing).side_effect = TimeoutError("timed out")

                with patch_workbench("_state",
                                  new=lambda text, level, hint="": (text, level, hint)), \
                        self.assertLogs("vpinfe.manager.workbench", level="WARNING"):
                    entries = await game_table._findings(context, tables)

                self.assertEqual([alert for _, alert in entries], [(
                    t("manager.workbench.newer_on_vps", theirs="3.0", ours="2.5"),
                    (t("manager.workbench.vps_failed"), "unknown",
                     t("said.why.timed_out")), "")])

    async def test_a_fault_nothing_here_fixes_carries_no_act(self) -> None:
        found = await self._found(_table("a", source={
            "base": {"file": "base.vpx", "available": False}}))

        self.assertEqual(found, [(t("manager.workbench.source_gone"), None, "")])

    async def test_a_script_naming_flexdmd_is_not_a_finding(self) -> None:
        """No content folder says nothing: the script names its own."""
        found = await self._found(_table("a", dependencies={
            "flexdmd": {"detected": True, "installed": False}}))

        self.assertEqual(found, [])

    async def test_a_choice_somebody_made_is_not_a_finding(self) -> None:
        self.assertEqual(await self._found(_table("a", hidden=True, default=False)), [])

    async def test_nothing_asks_the_catalog_without_an_update(self) -> None:
        context = _context(_table("a", available=False))

        await game_table._findings(context, context["tables"])

        context["library"].vps_releases.assert_not_called()

    async def test_a_notice_says_its_table_where_the_game_has_several(self) -> None:
        tables = (_table("a", available=False), _table("b", version="2.0"))
        context = _context(*tables)

        entries = await game_table._findings(context, tables)

        self.assertEqual([said for _, (_, _, said) in entries],
                         [game_tables.name_among(tables[0], list(tables))])

    def _rom(self, table_id: str, rom: str = "hustler", **extra: Any) -> dict[str, Any]:
        return _table(table_id, dependencies={
            "pinmame": {"effective": rom, "installed": False}}, **extra)

    async def test_a_rom_two_tables_share_is_one_notice_with_one_add(self) -> None:
        tables = (self._rom("a"), self._rom("b", version="2.0"))

        found = await self._found(*tables)

        self.assertEqual(found, [(t("manager.workbench.rom_missing", rom="hustler", count=2),
                                  ("act", t("word.add")), t("manager.workbench.cause_both"))])

    async def test_a_rom_every_one_of_three_tables_names_says_how_many(self) -> None:
        found = await self._found(self._rom("a"), self._rom("b", version="2.0"),
                                  self._rom("c", version="3.0"))

        self.assertEqual([said for _, _, said in found],
                         [t("manager.workbench.cause_all", count=3)])

    async def test_two_sets_are_two_notices_each_naming_its_table(self) -> None:
        tables = (self._rom("a", "hustler"), self._rom("b", "bighurt", version="2.0"))

        found = await self._found(*tables)

        self.assertEqual(
            found,
            [(t("manager.workbench.rom_missing", rom="hustler"), ("act", t("word.add")),
              game_tables.name_among(tables[0], list(tables))),
             (t("manager.workbench.rom_missing", rom="bighurt"), ("act", t("word.add")),
              game_tables.name_among(tables[1], list(tables)))])

    async def test_a_notice_about_some_of_several_names_them_one_to_a_line(self) -> None:
        tables = (self._rom("a"), self._rom("b", version="2.0"), self._rom("c", version="3.0"),
                  _table("d", version="4.0"))

        found = await self._found(*tables)

        self.assertEqual(found[0][2], "\n".join(
            game_tables.name_among(one, list(tables)) for one in tables[:3]))

    async def test_names_past_three_are_counted_and_the_rest_sit_behind_the_mark(
            self) -> None:
        tables = tuple(self._rom(name, version=f"{number}.0")
                       for number, name in enumerate("abcde", start=1)) + (
            _table("f", version="6.0"),)

        found = await self._found(*tables)

        names = [game_tables.name_among(one, list(tables)) for one in tables[:5]]
        self.assertEqual(found[0][2], "\n".join(
            [*names[:3], f"{t('said.and_more', count=2)} [{chr(10).join(names[3:])}]"]))

    async def test_the_files_gone_are_forgotten_one_at_a_time(self) -> None:
        tables = (_table("a", available=False), _table("b", available=False, version="2.0"))

        found = await self._found(*tables)

        self.assertEqual([(line, act) for line, act, _ in found],
                         [(t("manager.workbench.file_absent"), ("act", t("word.forget")))] * 2)
        self.assertEqual([said for _, _, said in found],
                         [game_tables.name_among(one, list(tables)) for one in tables])

    async def test_an_update_stays_one_notice_per_table(self) -> None:
        source = {"vps_file_id": "r-1", "version": "1.2", "vps_version": "1.1"}
        found = await self._found(_table("a", update_available=True, source=source),
                                  _table("b", update_available=True, source=source,
                                         version="2.0"))

        self.assertEqual(len(found), 2)

    async def test_a_fault_nothing_fixes_reads_in_the_plural_for_several_tables(
            self) -> None:
        for words, form in (("place_unreachable", "referenced"), ("unplayable", "keyed")):
            with self.subTest(words=words):
                tables = (_table("a", available=False, form=form),
                          _table("b", available=False, version="2.0", form=form))

                found = await self._found(*tables)

                self.assertEqual(
                    found, [(t(f"manager.workbench.{words}", count=2), None,
                             t("manager.workbench.cause_both"))])

    async def test_one_made_from_file_gone_is_one_notice_for_the_tables_made_from_it(
            self) -> None:
        base = {"base": {"file": "base.vpx", "available": False}}
        found = await self._found(_table("a", source=base),
                                  _table("b", source=base, version="2.0"),
                                  _table("c", source={"base": {"file": "other.vpx",
                                                               "available": False}},
                                         version="3.0"))

        self.assertEqual(len(found), 2)
        self.assertEqual(found[0][0], t("manager.workbench.source_gone", count=2))
        self.assertEqual(found[1][0], t("manager.workbench.source_gone"))

    async def test_a_games_one_table_has_no_line_under_its_notice(self) -> None:
        found = await self._found(self._rom("a"))

        self.assertEqual([said for _, _, said in found], [""])

    async def test_a_tables_own_tab_names_only_that_table(self) -> None:
        tables = (self._rom("a"), self._rom("b", version="2.0"))
        context = _context(*tables)

        entries = await game_table._findings(context, [tables[1]])

        self.assertEqual([said for _, (_, _, said) in entries],
                         [game_tables.name_among(tables[1], list(tables))])
        self.assertEqual(entries[0][1][0], t("manager.workbench.rom_missing", rom="hustler"))


# Made at import, outside any task, where NiceGUI still hands out the script's own page.
_PAGE = ui.element()


class OncePerPanel(unittest.IsolatedAsyncioTestCase):
    """The game's section carries its tables' findings only while the game is the
    subject. Under Tables the table's own section leads with them."""

    async def _drawn(self, lens: str, features: tuple[str, ...] = ()) -> list[Any]:
        context = _context(_table("a", available=False))
        context["lens"] = lens
        context["library"].vps_entry.return_value = {}
        context["library"].outside_links.return_value = []
        context["library"].discovery.return_value = {"features": list(features)}
        context["library"].dof_event_state.return_value = {"event": None, "blocked": ""}
        rows = Mock()
        with patch("manager.kit.offload.run.io_bound", new=_now), \
                patch_workbench("_findings",
                             new=AsyncMock(return_value=[("finding", "alert")])), \
                patch_workbench("_game_entries",
                             return_value=[("fact", "value")]) as entries, \
                patch_workbench("_rows", new=rows), \
                patch_workbench("_tables_block"), \
                patch_workbench("ui"), _PAGE:
            await game_table._game_block(context)
        self.dof_state = entries.call_args.args[-1]
        return list(rows.call_args.args[1])

    async def test_a_game_leads_with_its_tables_findings(self) -> None:
        self.assertEqual(await self._drawn(lens=""), [("finding", "alert"), ("fact", "value")])

    async def test_under_a_table_the_game_does_not_repeat_them(self) -> None:
        self.assertEqual(await self._drawn(lens="a"), [("fact", "value")])

    async def test_the_dof_event_is_asked_for_only_where_the_device_has_a_frontend(
            self) -> None:
        await self._drawn(lens="")
        self.assertIsNone(self.dof_state)

        await self._drawn(lens="", features=("library", "frontend"))
        self.assertEqual(self.dof_state, {"event": None, "blocked": ""})


if __name__ == "__main__":
    unittest.main()

"""What choosing tables for a game's file does and says.

The disk is pinned in `tests/games/test_file_move.py`. Pinned here is the reading ahead the
dialog does before anything moves, and the words each outcome gets.
"""

from __future__ import annotations

import unittest
from typing import Any

from common.i18n import t
from manager.library import file_move, move_picker, move_plan
from manager.library.move_plan import Slot

FOLDER = "Cactus"


def row(table_id: str, stem: str, own: dict | None = None, ini: bool = False) -> dict[str, Any]:
    return {"id": table_id, "stem": stem, "filename": f"{stem}.vpx",
            "folder_named": stem == FOLDER, "own": own, "ini": ini}


OWN = {"files": ["Cactus B.directb2s"], "size": 2_100_000, "modified": 1_788_000_000.0}


def backglass(*rows: dict[str, Any], game: bool = True, kind: str = "backglass",
              family: str = "asset", label: str = "Backglass",
              fallback: bool = True) -> Slot:
    return Slot(family, kind, label, {
        "folder": FOLDER,
        "game": {"files": [f"{FOLDER}.directb2s"], "size": 1, "modified": 1.0} if game else None,
        "tables": list(rows), "fallback": fallback, "to_game": True, "playing": False})


def three(own_b: bool = True) -> Slot:
    return backglass(row("t1", FOLDER), row("t2", "Cactus B", OWN if own_b else None),
                     row("t3", "Cactus C"), row("t4", "Cactus D"))


class WhatTheGameFileDoesTests(unittest.TestCase):
    def test_a_table_with_its_own_file_does_not_read_the_games(self) -> None:
        slot = three()

        self.assertEqual(move_plan.readers(slot), {"t3", "t4"})

    def test_the_table_named_like_the_folder_is_never_a_choice(self) -> None:
        slot = three()

        self.assertNotIn("t1", move_picker.choices_of([slot]))

    def test_leaving_the_ticks_as_they_were_changes_nothing(self) -> None:
        slot = three()
        choices = move_picker.choices_of([slot])

        self.assertEqual(move_picker.changed([slot], choices), [])
        found = move_plan.outcome(slot, move_plan.target(slot, choices))
        self.assertFalse(found.changes)

    def test_the_dialog_opens_saying_what_each_table_has_now(self) -> None:
        slot = three()

        found = move_plan.outcome(slot, move_plan.target(slot, move_picker.choices_of([slot])))

        self.assertEqual([found.ends[key] for key in ("t1", "t2", "t3", "t4")],
                         [move_plan.USES, move_plan.OWN, move_plan.USES, move_plan.USES])

    def test_ticking_nothing_says_which_tables_will_have_none(self) -> None:
        slot = three()

        found = move_plan.outcome(slot, [])

        self.assertEqual([found.ends[key] for key in ("t1", "t2", "t3", "t4")],
                         [move_plan.LOSES, move_plan.OWN, move_plan.LOSES, move_plan.LOSES])

    def test_ticking_every_reader_and_an_own_file_keeps_the_file_on_the_game(self) -> None:
        slot = three()

        found = move_plan.outcome(slot, ["t2", "t3", "t4"])

        self.assertTrue(found.stays)
        self.assertEqual(found.problem, "")
        self.assertEqual([found.ends[key] for key in ("t1", "t2", "t3", "t4")],
                         [move_plan.USES, move_plan.REPLACES, move_plan.USES, move_plan.USES])

    def test_the_only_table_of_a_game_is_given_the_file_not_left_on_the_game(self) -> None:
        slot = backglass(row("t2", "Cactus B"))

        found = move_plan.outcome(slot, ["t2"])

        self.assertFalse(found.stays)
        self.assertEqual(found.problem, "")
        self.assertEqual(found.ends["t2"], move_plan.GETS_OWN)

    def test_one_table_ticked_takes_the_file_and_the_others_lose_it(self) -> None:
        slot = three()

        found = move_plan.outcome(slot, ["t3"])

        self.assertFalse(found.stays)
        self.assertEqual([found.ends[key] for key in ("t1", "t2", "t3", "t4")],
                         [move_plan.LOSES, move_plan.OWN, move_plan.GETS_OWN, move_plan.LOSES])

    def test_two_of_three_ticked_get_a_copy_each(self) -> None:
        slot = three(own_b=False)

        found = move_plan.outcome(slot, ["t2", "t3"])

        self.assertEqual([found.ends[key] for key in ("t2", "t3", "t4")],
                         [move_plan.GETS_COPY, move_plan.GETS_COPY, move_plan.LOSES])

    def test_ticking_nothing_leaves_no_table_to_have_it(self) -> None:
        slot = three()

        found = move_plan.outcome(slot, [])

        self.assertEqual(found.problem, move_plan.NO_TABLE)
        self.assertEqual(move_picker.blocked([slot], {move_picker.slot_key(slot): found}),
                         [slot])

    def test_a_table_reading_some_of_several_files_is_half_ticked(self) -> None:
        backglass_slot = three()
        wheel = backglass(row("t1", FOLDER), row("t2", "Cactus B", OWN),
                          row("t3", "Cactus C", OWN), row("t4", "Cactus D"),
                          kind="wheel", family="media", label="Wheel")

        self.assertIs(move_picker.initial([backglass_slot, wheel], "t4"), True)
        self.assertIsNone(move_picker.initial([backglass_slot, wheel], "t3"))
        self.assertIs(move_picker.initial([backglass_slot, wheel], "t2"), False)


class WhichFilesGetWhichMenuItemTests(unittest.TestCase):
    """Rows are shaped as the two lenses answer: assets say `binding` game or table, media
    says `via`."""

    GAME = {"id": "g1", "folder": "/lib/Cactus", "table_count": 3}

    class Library:
        tables = {"g1": [{"id": "t1", "filename": "Cactus.vpx"},
                         {"id": "t2", "filename": "Cactus B.vpx"}]}

    def file(self, **row: Any) -> Any:
        from manager.library.file_acts import File
        return File({"id": "r", "game_id": "g1", "present": True, "path": "Cactus.directb2s",
                     "kind": "backglass"} | row, self.GAME)

    def labels(self, one: Any, family: str) -> list[str]:
        async def after(_ids: list[str]) -> None:
            return None
        return [verb.label for verb in file_move.verbs_for(self.Library(), [one], family, after)]

    def test_a_backglass_the_game_serves_offers_choosing_tables(self) -> None:
        one = self.file(binding="game")

        self.assertEqual(self.labels(one, "asset"), ["Move to a Table"])

    def test_a_backglass_a_table_has_of_its_own_offers_moving_to_the_game(self) -> None:
        one = self.file(binding="table", table="t2")

        self.assertEqual(self.labels(one, "asset"), ["Move to Game"])

    def test_a_file_nothing_reads_offers_neither(self) -> None:
        one = self.file(binding="orphaned", table="t2")

        self.assertEqual(self.labels(one, "asset"), [])

    def test_a_picture_follows_the_via_the_media_lens_gives(self) -> None:
        game = self.file(via="game", kind="wheel")
        table = self.file(via="table", kind="wheel", table="t2")

        self.assertEqual(self.labels(game, "media"), ["Move to a Table"])
        self.assertEqual(self.labels(table, "media"), ["Move to Game"])

    def test_a_pov_or_script_named_like_the_folder_is_offered_whichever_table_reads_it(
            self) -> None:
        pov = self.file(binding="game", kind="pov", path="Cactus.pov", table="")
        script = self.file(binding="game", kind="script", path="Cactus.vbs", table="")
        other = self.file(binding="table", kind="pov", path="Cactus B.pov", table="t2")

        self.assertEqual(self.labels(pov, "asset"), ["Move to a Table"])
        self.assertEqual(self.labels(script, "asset"), ["Move to a Table"])
        self.assertEqual(self.labels(other, "asset"), [])

    def test_a_kind_with_no_tables_to_choose_says_why_it_cannot(self) -> None:
        one = self.file(binding="game", kind="pup_pack")

        self.assertEqual(self.labels(one, "asset"), [])


class WhatEachRowSaysTests(unittest.TestCase):
    def lines(self, slot: Slot, row_id: str, chosen: list[str]) -> list[tuple[str, bool]]:
        found = move_picker.outcomes([slot], {key: key in chosen
                                              for key in ("t2", "t3", "t4")})
        return move_picker.lines([slot], row_id, found)

    def test_a_table_replacing_its_own_warns_and_says_where_its_own_is_kept(self) -> None:
        said = self.lines(three(), "t2", ["t2", "t3", "t4"])

        self.assertTrue(said[0][0].startswith("Replaces its own, "))
        self.assertTrue(said[0][1])
        self.assertEqual(said[1], (t("manager.file_move.row.kept"), False))

    def test_a_table_left_without_the_file_warns(self) -> None:
        said = self.lines(three(), "t4", ["t3"])

        self.assertEqual(said, [("Will have no backglass", True)])

    def test_a_table_that_keeps_reading_the_game_file_says_so_quietly(self) -> None:
        said = self.lines(three(), "t3", ["t2", "t3", "t4"])

        self.assertEqual(said, [("Uses it now", False)])

    def test_the_table_named_like_the_folder_follows_the_others(self) -> None:
        kept = self.lines(three(), "t1", ["t2", "t3", "t4"])
        lost = self.lines(three(), "t1", ["t3"])

        self.assertEqual(kept[0], ("Uses it now", False))
        self.assertEqual(kept[1], ("Same file as the game's backglass", False))
        self.assertEqual(lost[0], ("Will have no backglass", True))
        self.assertIn("only if Cactus D does", lost[1][0].replace("Cactus B and ", ""))

    def test_a_pov_for_a_table_with_a_settings_file_says_it_will_not_be_read(self) -> None:
        slot = backglass(row("t1", FOLDER), row("t2", "Cactus B", ini=True), row("t3", "Cactus C"),
                         kind="pov", label="POV", fallback=False)
        found = move_picker.outcomes([slot], {"t2": True, "t3": False})

        said = move_picker.lines([slot], "t2", found)

        self.assertEqual(said[-1], ("Has a settings file, so it will not read a POV", True))

    def test_the_settings_file_note_is_quiet_when_the_table_is_not_chosen(self) -> None:
        slot = backglass(row("t1", FOLDER), row("t2", "Cactus B", ini=True), row("t3", "Cactus C"),
                         kind="pov", label="POV", fallback=False)
        found = move_picker.outcomes([slot], {"t2": False, "t3": True})

        said = move_picker.lines([slot], "t2", found)

        self.assertEqual(said[-1][1], False)

    def test_several_files_are_said_together_per_table(self) -> None:
        wheel = backglass(row("t1", FOLDER), row("t2", "Cactus B", OWN), row("t3", "Cactus C"),
                          kind="wheel", family="media", label="Wheel")
        glass = backglass(row("t1", FOLDER), row("t2", "Cactus B", OWN), row("t3", "Cactus C"))
        slots = [glass, wheel]
        found = move_picker.outcomes(slots, {"t2": True, "t3": False})

        said = move_picker.lines(slots, "t2", found)

        self.assertIn("Replaces its own Backglass and Wheel", [text for text, _warn in said])

    def test_the_title_names_one_file_or_counts_several(self) -> None:
        self.assertEqual(move_picker.title([three()]), "Choose Tables for the Game Backglass")
        self.assertEqual(move_picker.title([three(), three(), three()]),
                         "Choose Tables for 3 Game Files")

    def test_a_kind_is_lower_case_in_a_sentence_unless_it_is_a_name(self) -> None:
        self.assertEqual(move_picker.noun("Backglass"), "backglass")
        self.assertEqual(move_picker.noun("POV"), "POV")
        self.assertEqual(move_picker.noun("ScoreView"), "ScoreView")

    def test_names_are_joined_with_and(self) -> None:
        self.assertEqual(move_picker.joined(["A"]), "A")
        self.assertEqual(move_picker.joined(["A", "B"]), "A and B")
        self.assertEqual(move_picker.joined(["A", "B", "C"]), "A, B and C")


class WhatIsSaidAfterwardsTests(unittest.TestCase):
    def moved(self, **extra: Any) -> dict[str, Any]:
        return {"stays": False, "uses": ["Cactus C"], "lost": [], "replaced": [],
                "moved": [{"table": "Cactus C", "file": "Cactus C.directb2s"}]} | extra

    def test_a_move_to_one_table_says_where_it_went(self) -> None:
        said, caption, warn = file_move.to_tables_words([(three(), self.moved())])

        self.assertEqual((said, caption, warn), ("Moved the backglass to Cactus C", "", False))

    def test_a_table_left_without_it_turns_the_message_amber(self) -> None:
        said, caption, warn = file_move.to_tables_words(
            [(three(), self.moved(lost=["Cactus D"]))])

        self.assertEqual(caption, "Cactus D has no backglass now")
        self.assertTrue(warn)

    def test_a_replaced_file_is_said_to_be_in_backups(self) -> None:
        replaced = [{"table": "Cactus B", "file": "Cactus B.directb2s", "kept": "x"}]
        _said, caption, _warn = file_move.to_tables_words(
            [(three(), self.moved(replaced=replaced))])

        self.assertEqual(caption, "What it replaced is kept in Backups")

    def test_two_copies_say_how_many(self) -> None:
        said, _caption, _warn = file_move.to_tables_words(
            [(three(), self.moved(uses=["Cactus C", "Cactus D"]))])

        self.assertEqual(said, "Copied the backglass to 2 tables")

    def test_a_move_to_the_game_with_nothing_replaced_says_every_table_uses_it(self) -> None:
        said, caption, warn = file_move.to_game_words(
            [("Backglass", {"replaced": [], "changed_for": []})])

        self.assertEqual((said, caption, warn),
                         ("Moved the backglass to the game",
                          "Every table without its own uses it", False))

    def test_a_move_to_the_game_over_a_file_warns_where_another_table_changes(self) -> None:
        said, caption, warn = file_move.to_game_words(
            [("Backglass", {"replaced": [{"file": "Cactus.directb2s"}],
                            "changed_for": ["Cactus C"]})])

        self.assertEqual(said, "Moved the backglass to the game")
        self.assertEqual(caption,
                         "The backglass it replaced is kept in Backups. "
                         "Cactus C uses this one now")
        self.assertTrue(warn)


if __name__ == "__main__":
    unittest.main()

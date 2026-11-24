import unittest
from types import SimpleNamespace
from typing import Any
from unittest import mock

from common.games import game_metadata as gm
from common.games.game_metadata import (
    as_string_list,
    game_themes,
    game_title,
    game_type,
)


class AsStringListTests(unittest.TestCase):
    def test_a_list_comes_back_as_a_list_of_strings(self) -> None:
        self.assertEqual(as_string_list(["Fantasy", "Magic"]), ["Fantasy", "Magic"])
        self.assertEqual(as_string_list(("Fantasy",)), ["Fantasy"])

    def test_absent_means_empty(self) -> None:
        absent: tuple[Any, ...] = ([], "", None)
        for value in absent:
            with self.subTest(value=value):
                self.assertEqual(as_string_list(value), [])

    def test_a_scalar_becomes_one_item(self) -> None:
        self.assertEqual(as_string_list("Fantasy"), ["Fantasy"])
        self.assertEqual(as_string_list(1990), ["1990"])

    def test_a_stringified_list_stays_one_item(self) -> None:
        """Parsing it back would mean inventing a syntax .info does not have, so the
        odd value stays visible rather than being guessed at."""
        self.assertEqual(as_string_list("['Fantasy', 'Magic']"), ["['Fantasy', 'Magic']"])



class LegacyMetadataFieldTests(unittest.TestCase):
    def test_metadata_display_helpers_handle_legacy_fields(self) -> None:
        game = SimpleNamespace(
            game_dir_name="Fallback",
            meta_config={
                "VPSdb": {
                    "name": "Legacy Name",
                    "theme": "['Music', 'Movies']",
                    "type": "SS",
                }
            },
        )

        self.assertEqual(game_title(game), "Legacy Name")
        self.assertEqual(game_themes(game), ["Music", "Movies"])
        self.assertEqual(game_type(game), "SS")


class FavoriteTests(unittest.TestCase):
    """A real boolean, and a producer at last."""

    def test_it_is_written_as_a_boolean(self) -> None:
        """The .info is JSON and the vpinfe block beside this one already stores real
        booleans; `Favorite: 0` was an INI-era habit carried into a format that has
        true. Nothing had ever written a non-zero, so no value on disk is at risk."""
        written = {}
        game: Any = SimpleNamespace(meta_config={"User": {"Favorite": 0}})

        with mock.patch.object(gm, "load_game_meta", return_value=game.meta_config), \
             mock.patch.object(gm, "persist_game_meta",
                               side_effect=lambda g, c: written.update(c)):
            stored = gm.set_game_favorite(game, True)

        self.assertIs(stored, True)
        self.assertIs(written["User"]["Favorite"], True)

    def test_an_old_zero_still_reads_false(self) -> None:
        """Every .info in the world holds 0, because the zero-fill was the only writer
        there has ever been. The reader coerces, so they do not need migrating."""
        self.assertIs(gm.play_record({"User": {"Favorite": 0}})["favorite"], False)
        self.assertIs(gm.play_record({"User": {"Favorite": 1}})["favorite"], True)
        self.assertIs(gm.play_record({"User": {"Favorite": True}})["favorite"], True)


class ThemeListTests(unittest.TestCase):
    """However a writer wrote them down, they come back as themes."""

    def test_a_repr_in_our_own_field_stays_visible(self) -> None:
        """`Info.Themes` is ours and is a JSON list, so a repr string there is a bad
        value rather than a format. `as_string_list` decided that deliberately - parsing
        it would mean inventing a syntax the file does not have - so the odd value shows
        as it is, and the .info is what gets fixed."""
        game = SimpleNamespace(
            meta_config={"Info": {"Themes": "['Fantasy', 'Magic']"}})

        self.assertEqual(gm.game_themes(game), ["['Fantasy', 'Magic']"])

    def test_the_legacy_field_reads_the_same_way(self) -> None:
        game = SimpleNamespace(meta_config={"VPSdb": {"theme": "['Space']"}})

        self.assertEqual(gm.game_themes(game), ["Space"])

    def test_a_plain_string_is_one_theme(self) -> None:
        game = SimpleNamespace(meta_config={"Info": {"Themes": "Fantasy"}})

        self.assertEqual(gm.game_themes(game), ["Fantasy"])

    def test_something_that_only_looks_like_a_list_is_left_alone(self) -> None:
        """Inventing a parse for it would be worse than showing what is there."""
        game = SimpleNamespace(meta_config={"Info": {"Themes": "[broken"}})

        self.assertEqual(gm.game_themes(game), ["[broken"])


class RetagTests(unittest.TestCase):
    """Rename, merge and delete are one sweep."""

    def _library(self, taglists):
        return [SimpleNamespace(meta_config={"User": {"Tags": list(t)}})
                for t in taglists]

    def _run(self, taglists, sources, into):
        games = self._library(taglists)

        def write(game, tags):
            game.meta_config["User"]["Tags"] = list(tags)
            return list(tags)

        with mock.patch.object(gm, "set_game_tags", side_effect=write):
            changed = gm.retag_library(games, sources, into)
        return changed, [g.meta_config["User"]["Tags"] for g in games]

    def test_two_spellings_fold_into_one(self) -> None:
        changed, out = self._run([["sci-fi"], ["Sci-Fi"], ["Other"]],
                                 ["sci-fi", "Sci-Fi"], "Sci-Fi")

        self.assertEqual(out, [["Sci-Fi"], ["Sci-Fi"], ["Other"]])
        self.assertEqual(changed, 1, "the game already holding the survivor did not change")

    def test_rename_is_one_source_into_a_new_name(self) -> None:
        _, out = self._run([["Wide Body"], ["Other"]], ["Wide Body"], "Widebody")

        self.assertEqual(out, [["Widebody"], ["Other"]])

    def test_delete_is_a_merge_into_nothing(self) -> None:
        _, out = self._run([["Wide Body", "Other"]], ["Wide Body"], "")

        self.assertEqual(out, [["Other"]])

    def test_the_survivor_keeps_the_place_the_source_had(self) -> None:
        """A merge should not reshuffle a list somebody arranged."""
        _, out = self._run([["A", "sci-fi", "Z"]], ["sci-fi"], "Sci-Fi")

        self.assertEqual(out, [["A", "Sci-Fi", "Z"]])

    def test_a_game_holding_both_ends_with_one(self) -> None:
        _, out = self._run([["sci-fi", "Sci-Fi"]], ["sci-fi"], "Sci-Fi")

        self.assertEqual(out, [["Sci-Fi"]])

    def test_renaming_a_tag_to_itself_writes_nothing(self) -> None:
        changed, out = self._run([["Wide Body"]], ["Wide Body"], "Wide Body")

        self.assertEqual((changed, out), (0, [["Wide Body"]]))


if __name__ == "__main__":
    unittest.main()

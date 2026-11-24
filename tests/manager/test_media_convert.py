"""The Media page's conversion: which rows are Unsupported or Won't Play, what the ask draws
for a plan, and what it says when the job ends."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from nicegui import ui

from common.games import media_convert
from common.host import tools
from common.i18n import t
from manager.kit import dropdown, views
from manager.library import file_acts, file_states, media
from manager.library import media_convert as page

ROOT = Path(__file__).resolve().parents[2]
CATALOGS = ROOT / "common" / "i18n" / "catalogs"

LOCAL = "install-local"
VP9_REFUSED = [{"device_id": LOCAL,
                "browser": {"library": LOCAL, "formats": {"vp9": False, "h264": True}}}]


def row(path: str, *, via: str = "", present: bool = True, game_id: str = "g1",
        codec: str | None = None, kind: str = "backglass_video") -> dict:
    return {"game_id": game_id, "path": path, "present": present, "via": via, "kind": kind,
            "video_codec": codec, "table": "", "serves": 1}


class ReasonTests(unittest.TestCase):
    """What the Media grid's reason column says of a row."""

    def reasons(self, found: list[dict], devices: list[dict] | None = None) -> list[str]:
        return [one["reason"] for one in media.rows(found, devices or [], LOCAL)]

    def test_a_file_in_a_format_vpinfe_cannot_use_is_unsupported(self) -> None:
        self.assertEqual(self.reasons([row("medias/wheel.tga", via="unsupported")]),
                         [file_states.UNSUPPORTED])

    def test_a_video_a_device_refuses_wont_play(self) -> None:
        self.assertEqual(self.reasons([row("medias/bg.webm", codec="vp9")], VP9_REFUSED),
                         [file_states.WONT_PLAY])

    def test_the_same_video_is_in_use_when_no_device_refuses_it(self) -> None:
        self.assertEqual(self.reasons([row("medias/bg.webm", codec="vp9")]),
                         [file_states.IN_USE])

    def test_a_video_no_browser_plays_has_no_codec_and_is_in_use(self) -> None:
        """A real DMD video is played by VPinFE itself, which the lens marks by giving it no
        codec."""
        self.assertEqual(self.reasons([row("medias/realdmd.mp4", codec=None,
                                           kind="real_dmd_video")], VP9_REFUSED),
                         [file_states.IN_USE])

    def test_an_absent_file_is_missing_whatever_its_codec(self) -> None:
        self.assertEqual(self.reasons([row("medias/bg.webm", present=False, codec="vp9")],
                                      VP9_REFUSED), [file_states.MISSING])


def _filter(view: str) -> list[str]:
    preset = media.VIEWS[view]
    assert isinstance(preset, views.Preset)
    return list(preset.filters["reason"]["values"])  # type: ignore[index]


class FilterTests(unittest.TestCase):
    def test_the_unsupported_view_lists_unsupported_and_nothing_else(self) -> None:
        self.assertEqual(_filter("manager.view.unsupported"), [file_states.UNSUPPORTED])

    def test_the_wont_play_view_lists_wont_play_and_nothing_else(self) -> None:
        self.assertEqual(_filter("manager.view.wont_play"), [file_states.WONT_PLAY])

    def test_neither_is_hidden_from_the_views_that_list_what_is_there(self) -> None:
        for view in ("manager.view.have", media.SOURCES):
            self.assertIn(file_states.WONT_PLAY, _filter(view))
        self.assertIn(file_states.UNSUPPORTED, _filter("manager.view.have"))

    def test_the_state_table_names_both_states_for_media_only(self) -> None:
        with_both = {one.token for one in file_states.table("manager.media.unused_meaning",
                                                            converts=True).states}
        without = {one.token for one in file_states.table("manager.media.unused_meaning").states}

        self.assertLessEqual({file_states.UNSUPPORTED, file_states.WONT_PLAY}, with_both)
        self.assertTrue({file_states.UNSUPPORTED, file_states.WONT_PLAY}.isdisjoint(without))


class ConvertibleTests(unittest.TestCase):
    def test_unsupported_and_wont_play_rows_that_are_there_are_converted_once(self) -> None:
        picked = [row("medias/a.tga", via="unsupported"),
                  row("medias/a.tga", via="unsupported"),
                  row("medias/b.webm", codec="vp9"),
                  row("medias/c.webm", codec="h264"),
                  row("medias/d.tga", via="unsupported", present=False)]

        self.assertEqual(page.convertible(picked, VP9_REFUSED, LOCAL),
                         [("g1", "medias/a.tga"), ("g1", "medias/b.webm")])

    def test_a_video_is_judged_from_its_codec_and_not_from_a_reason_already_on_the_row(
            self) -> None:
        self.assertEqual(page.convertible([row("medias/b.webm", codec="vp9")]), [])
        self.assertEqual(page.convertible([row("medias/b.webm", codec="vp9")],
                                          VP9_REFUSED, LOCAL), [("g1", "medias/b.webm")])

    def test_a_loose_file_is_never_a_wont_play_video(self) -> None:
        self.assertEqual(page.convertible([row("medias/b.webm", via="orphan", codec="vp9")],
                                          VP9_REFUSED, LOCAL), [])


async def _nothing_after(_ids: list[str]) -> None:
    return None


class StubLibrary:
    def discovery(self) -> dict:
        return {"install_id": LOCAL}


class TheMenuTests(unittest.TestCase):
    """What the Media page's menus offer for a file, in the grid and in the file panel."""

    def verbs(self, one: dict, devices: list[dict] | None = None) -> list[str]:
        shown = file_acts.File({"id": "g1:x", **one}, {"id": "g1"})
        return [verb.label for verb in file_acts.media_acts(
            StubLibrary(), [shown], {"devices": devices or []}, _nothing_after)]

    def test_an_unsupported_file_is_offered_convert_and_no_slot_act(self) -> None:
        self.assertEqual(self.verbs(row("medias/wheel.tga", via="unsupported", kind="wheel")),
                         [t("manager.media_convert.convert")])

    def test_a_video_a_device_refuses_is_offered_convert_from_the_raw_row(self) -> None:
        self.assertIn(t("manager.media_convert.convert"),
                      self.verbs(row("medias/bg.webm", codec="vp9"), VP9_REFUSED))

    def test_a_video_every_device_plays_is_not_offered_convert(self) -> None:
        self.assertNotIn(t("manager.media_convert.convert"),
                         self.verbs(row("medias/bg.webm", codec="h264"), VP9_REFUSED))

    def test_the_panel_draws_an_unsupported_file_as_itself(self) -> None:
        self.assertTrue(file_acts.is_standalone(row("medias/wheel.tga", via="unsupported")))
        self.assertTrue(file_acts.is_standalone(row("medias/x.png", via="orphan")))
        self.assertFalse(file_acts.is_standalone(row("medias/bg.webm")))


def item(action: str, kind: str, path: str = "medias/x", reason: str = "") -> dict:
    return {"game_id": "g1", "path": path, "action": action, "kind": kind, "reason": reason}


def plan_of(*items: dict, ffmpeg: str = tools.State.FOUND.value) -> dict:
    return {"items": list(items), "ffmpeg": ffmpeg, "options": [], "choices": {},
            "video_blocked": ""}


class TheAskTests(unittest.TestCase):
    def test_each_kind_the_plan_makes_is_counted_and_none_it_does_not(self) -> None:
        said = page.lines(plan_of(item(media_convert.CONVERT, media_convert.PICTURE),
                                  item(media_convert.CONVERT, media_convert.PICTURE),
                                  item(media_convert.SKIP, media_convert.SOUND)))

        self.assertEqual(said, [t("manager.media_convert.pictures", count=2)])

    def test_videos_say_whether_they_are_repackaged_or_re_encoded(self) -> None:
        said = page.lines(plan_of(item(media_convert.REPACKAGE, media_convert.VIDEO),
                                  item(media_convert.CONVERT, media_convert.VIDEO)))

        self.assertEqual(said[1], t("manager.media_convert.mixed", repackaged=1, encoded=1))

    def test_no_ffmpeg_leaves_the_videos_out_of_the_counts_and_gives_the_hint(self) -> None:
        plan = plan_of(item(media_convert.CONVERT, media_convert.PICTURE, "medias/a.tga"),
                       item(media_convert.SKIP, "", "medias/b.avi", "no ffmpeg"),
                       ffmpeg=tools.State.MISSING.value)

        self.assertEqual(page.lines(plan), [t("manager.media_convert.pictures", count=1)])
        self.assertEqual(page.left_out(plan), (1, "no ffmpeg"))
        self.assertTrue(page.needs_ffmpeg(plan))

    def test_a_picture_only_plan_needs_no_ffmpeg_and_gives_no_hint(self) -> None:
        plan = plan_of(item(media_convert.CONVERT, media_convert.PICTURE, "medias/a.tga"),
                       ffmpeg=tools.State.MISSING.value)

        self.assertFalse(page.needs_ffmpeg(plan))

    def test_an_ffmpeg_that_is_found_gives_no_hint(self) -> None:
        self.assertFalse(page.needs_ffmpeg(
            plan_of(item(media_convert.SKIP, "", "medias/b.avi", "x"))))

    def test_a_plan_that_leaves_everything_out_has_no_work(self) -> None:
        self.assertFalse(page.has_work(plan_of(item(media_convert.SKIP, "", reason="x"))))
        self.assertTrue(page.has_work(plan_of(item(media_convert.RENAME,
                                                   media_convert.PICTURE))))

    def test_a_reason_given_for_two_files_is_said_once(self) -> None:
        self.assertEqual(page.left_out(plan_of(item(media_convert.SKIP, "", "a", "same"),
                                               item(media_convert.SKIP, "", "b", "same"))),
                         (2, "same"))


def drawn(choice: dict, current: dict) -> ui.element:
    with ui.column() as body:
        page._draw_choice(choice, current, lambda *_: None)
    return body


def texts(body: ui.element) -> list[str]:
    return [one.text for one in body.descendants() if isinstance(one, ui.label)]


def selects(body: ui.element) -> list[dropdown.Select]:
    return [one for one in body.descendants() if isinstance(one, dropdown.Select)]


PICTURE_CHOICE = {"key": media_convert.PICTURE, "label": "Pictures", "options": [
    {"value": "png", "recommended": True, "why": "Lossless", "available": True},
    {"value": "webp", "recommended": False, "why": "Smaller", "available": True},
    {"value": "jpg", "recommended": False, "why": "No transparency", "available": False}]}


class OneChoiceTests(unittest.TestCase):
    def test_a_choice_is_one_select_over_what_can_be_made(self) -> None:
        body = drawn(PICTURE_CHOICE, {media_convert.PICTURE: "png"})

        found = selects(body)
        self.assertEqual(len(found), 1)
        self.assertEqual(len(found[0].options), 2)

    def test_the_recommended_option_is_marked_and_the_others_are_not(self) -> None:
        found = selects(drawn(PICTURE_CHOICE, {media_convert.PICTURE: "png"}))[0]

        labels = list(found.options.values()) if isinstance(found.options, dict) \
            else list(found.options)
        self.assertEqual(sum("Recommended" in str(one) for one in labels), 1)

    def test_a_preference_says_so_where_the_recommendation_would_and_never_both(self) -> None:
        choice = {**PICTURE_CHOICE, "options": [
            {"value": "png", "recommended": True, "preferred": True, "why": "", "available": True},
            {"value": "webp", "recommended": False, "preferred": False, "why": "",
             "available": True}]}
        other = {**PICTURE_CHOICE, "options": [
            {"value": "png", "recommended": True, "preferred": False, "why": "", "available": True},
            {"value": "webp", "recommended": False, "preferred": True, "why": "",
             "available": True}]}

        def named(one: dict) -> list[str]:
            found = selects(drawn(one, {media_convert.PICTURE: "png"}))[0]
            return [str(label) for label in (found.options.values()
                                             if isinstance(found.options, dict)
                                             else found.options)]

        self.assertEqual(named(choice), ["PNG - Your Preference", "WebP"])
        self.assertEqual(named(other), ["PNG - Recommended", "WebP - Your Preference"])

    def test_the_chosen_options_sentence_is_drawn_and_a_blocked_ones_says_why(self) -> None:
        said = texts(drawn(PICTURE_CHOICE, {media_convert.PICTURE: "png"}))

        self.assertIn("Pictures", said)
        self.assertIn("Lossless", said)
        self.assertNotIn("Smaller", said)
        self.assertIn("No transparency", said)

    def test_a_choice_with_nothing_to_pick_draws_no_select_and_says_why(self) -> None:
        none_made = {**PICTURE_CHOICE, "options": [
            {"value": "png", "recommended": True, "why": "Cannot be made here",
             "available": False}]}

        body = drawn(none_made, {media_convert.PICTURE: "png"})

        self.assertEqual(selects(body), [])
        self.assertIn("Cannot be made here", texts(body))


class OutcomeTests(unittest.TestCase):
    def test_a_clean_run_is_said_plainly(self) -> None:
        self.assertEqual(page.outcome({"converted": 2, "repackaged": 1, "renamed": 0,
                                       "skipped": []}),
                         (t("manager.media_convert.converted", count=2), "positive"))

    def test_a_file_repackaged_is_counted_once_as_it_is_in_converted(self) -> None:
        said, _level = page.outcome({"converted": 1, "repackaged": 1, "renamed": 1})

        self.assertEqual(said, t("manager.media_convert.converted", count=2))

    def test_skipped_files_make_it_a_warning_with_the_count(self) -> None:
        said, level = page.outcome({"converted": 1, "skipped": [{"reason": "x"},
                                                                 {"reason": "y"}]})

        self.assertEqual(level, "warning")
        self.assertIn("2", said)


class CatalogTests(unittest.TestCase):
    def test_every_key_the_page_names_is_in_the_catalog_with_its_hash(self) -> None:
        source = (ROOT / "manager" / "library" / "media_convert.py").read_text(encoding="utf-8")
        named = set(re.findall(r'"(manager\.media_convert\.[a-z_]+)"', source))
        catalog = json.loads((CATALOGS / "en.json").read_text(encoding="utf-8"))
        hashes = json.loads((CATALOGS / "en.hashes.json").read_text(encoding="utf-8"))

        self.assertTrue(named)
        self.assertEqual(sorted(named - catalog.keys()), [])
        self.assertEqual(sorted(named - hashes.keys()), [])

    def test_every_option_the_plan_can_offer_has_a_word(self) -> None:
        for (key, value), word in page._WORDS.items():
            self.assertNotEqual(t(word), word, (key, value))


if __name__ == "__main__":
    unittest.main()

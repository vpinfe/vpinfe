"""Create Support Package: the steps, what each one draws, and what the inspector marks."""

from __future__ import annotations

import asyncio
import io
import types
import unittest
import zipfile
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, patch

from nicegui import ui

from common.support import package
from common.support.draft import Draft
from manager.system import support as dialog
from manager.system import support_inspect
from tests.supportpackage.test_package import _Folder

_PAGE = ui.element()


async def _now(function: Callable[..., Any], *args: Any) -> Any:
    return function(*args)


def _labels(draw: Callable[[], None]) -> list[str]:
    with _PAGE:
        holder = ui.element("div")
        with holder:
            draw()
    return [one.text for one in holder.descendants() if isinstance(one, ui.label)]


class _Drafted(_Folder):
    def setUp(self) -> None:
        super().setUp()
        self.write_logs()
        self.draft = Draft()
        started = patch.object(dialog, "run", types.SimpleNamespace(io_bound=_now))
        started.start()
        self.addCleanup(started.stop)


class WalkTests(_Drafted):
    def test_it_opens_on_what_goes_in_with_every_part_on(self) -> None:
        step = asyncio.run(dialog.calls(self.draft).first(None))

        self.assertEqual(step["step"], "what")
        self.assertEqual([field["key"] for field in step["fields"]],
                         ["logs", "previous_log", "settings"])
        self.assertTrue(all(field["value"] for field in step["fields"]))
        self.assertEqual([one["label"] for one in step["steps"]],
                         ["Include", "Privacy", "Share"])

    def test_next_reads_the_device_and_opens_privacy(self) -> None:
        step = asyncio.run(dialog.calls(self.draft).check(
            {"logs": True, "previous_log": False, "settings": True}, "what"))

        self.assertEqual(step["step"], "privacy")
        assert self.draft.made is not None
        self.assertIsNone(self.draft.made.part(package.PREVIOUS_LOG))
        self.assertTrue(step["steps"][0]["done"])

    def test_a_package_too_large_to_make_stays_on_the_first_step(self) -> None:
        with patch.object(Draft, "gather", side_effect=package.PackageTooLargeError):
            step = asyncio.run(dialog.calls(self.draft).check({}, "what"))

        self.assertEqual(step["step"], "what")
        self.assertIn("logs", step["errors"])

    def test_privacy_goes_on_to_the_last_step_that_ends_it(self) -> None:
        calls = dialog.calls(self.draft)
        asyncio.run(calls.check({}, "what"))

        step = asyncio.run(calls.check({}, "privacy"))

        self.assertEqual(step["step"], "share")
        self.assertEqual(step["confirm"], "Done")
        self.assertEqual(asyncio.run(calls.run({}, "share")), {"ok": True})

    def test_a_word_hidden_just_before_next_is_hidden_in_what_next_hands_over(self) -> None:
        calls = dialog.calls(self.draft)
        asyncio.run(calls.check({}, "what"))
        self.draft.hide_word("opened")

        asyncio.run(calls.check({}, "privacy"))

        assert self.draft.made is not None
        log = self.draft.made.part(package.LOG)
        assert log is not None
        self.assertNotIn("opened", log.text)

    def test_closing_the_dialog_drops_the_draft_however_it_ends(self) -> None:
        made: list[Draft] = []

        def draft() -> Draft:
            made.append(Draft())
            return made[0]

        for outcome in (AsyncMock(return_value=True), AsyncMock(side_effect=RuntimeError)):
            made.clear()
            with patch.object(dialog, "Draft", draft), \
                    patch.object(dialog.wizard, "open_dialog", outcome), \
                    patch.object(Draft, "drop", autospec=True) as drop:
                try:
                    asyncio.run(dialog.open_support_package())
                except RuntimeError:
                    pass
            drop.assert_called_once_with(made[0])

    def test_going_back_reopens_a_step_already_read(self) -> None:
        calls = dialog.calls(self.draft)
        asyncio.run(calls.check({}, "what"))

        self.assertEqual(asyncio.run(calls.first("privacy"))["step"], "privacy")
        self.assertEqual(asyncio.run(calls.first("what"))["step"], "what")


class DrawingTests(_Drafted):
    def setUp(self) -> None:
        super().setUp()
        self.draft.gather()

    def test_privacy_says_what_was_replaced_by_kind(self) -> None:
        words = _labels(lambda: dialog._draw_privacy(self.draft))

        self.assertIn("Private Details Replaced", words)
        self.assertIn("Hide Table Names", words)
        self.assertIn("Device Name", words)

    def test_share_offers_the_zip_and_both_places_it_goes(self) -> None:
        words = _labels(lambda: dialog._draw_share(self.draft))

        for expected in ("Package", "Discord", "GitHub", "Markdown", "Checksum"):
            self.assertIn(expected, words)

    def test_the_share_step_copies_the_summary_as_it_is(self) -> None:
        assert self.draft.made is not None

        with patch.object(dialog, "_copy", wraps=dialog._copy) as copy:
            _labels(lambda: dialog._draw_share(self.draft))

        markdown = [call for call in copy.call_args_list
                    if call.args[0] == "Copy as Markdown"]
        self.assertEqual(markdown[0].args[1], self.draft.made.summary)


class InspectorTests(_Drafted):
    def setUp(self) -> None:
        super().setUp()
        self.draft.gather()
        self.inspector = support_inspect.Inspector(self.draft, lambda: None)
        started = patch.object(support_inspect, "run", types.SimpleNamespace(io_bound=_now))
        started.start()
        self.addCleanup(started.stop)

    def rows(self, name: str) -> list[tuple[str, int, str]]:
        self.inspector.file = name
        return self.inspector._rows()[0]

    def test_a_replaced_value_is_marked_and_its_original_is_on_hover_only(self) -> None:
        html = self.inspector._html(self.rows(package.LOG), False)

        self.assertIn('class="support-mark"', html)
        self.assertIn('title="Device Name - cabinet-zed"', html)
        self.assertNotIn(">cabinet-zed<", html)

    def test_a_log_line_can_be_picked_and_a_kept_file_cannot(self) -> None:
        log = self.inspector._html(self.rows(package.LOG), False)
        readme = self.inspector._html(self.rows(package.README), False)

        self.assertIn('data-line="0"', log)
        self.assertNotIn("data-line", readme)

    def test_a_removed_line_cannot_be_picked_again(self) -> None:
        self.draft.remove_line(package.LOG, 0)
        self.draft.reassemble()

        html = self.inspector._html(self.rows(package.LOG), False)

        self.assertIn("support-line--removed", html)
        self.assertNotIn('data-line="0"', html)

    def test_text_in_a_line_is_escaped(self) -> None:
        html = self.inspector._html([(package.LOG, 0, "<script>alert(1)</script>")], False)

        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_a_search_lists_the_files_it_found_each_line_in(self) -> None:
        self.inspector.needle = "sign-in code"

        rows, capped = self.inspector._rows()

        self.assertEqual({name for name, _, _ in rows}, {package.LOG})
        self.assertFalse(capped)
        self.assertIn(f"{package.LOG}:", self.inspector._html(rows, True))

    def test_a_hostile_file_name_and_original_cannot_break_out_of_an_attribute(self) -> None:
        assert self.draft.made is not None
        self.draft.made.originals["[[host-1]]"] = '"><img src=x onerror=alert(1)>'

        html = self.inspector._html([('a"><b>.log', 0, "x [[host-1]] y")], False)

        self.assertNotIn("<img", html)
        self.assertNotIn('"><b>', html)
        self.assertIn("&quot;", html)

    def test_a_blank_line_is_not_pickable(self) -> None:
        html = self.inspector._html([(package.LOG, 3, "")], False)

        self.assertNotIn("data-line", html)

    def test_a_line_removed_from_the_screen_is_removed_from_the_zip(self) -> None:
        with _PAGE:
            self.inspector.draw()
        self.inspector.file = package.LOG
        before = [one for one in self.draft.made.parts if one.name == package.LOG][0].text \
            if self.draft.made else ""
        self.inspector.picked = (package.LOG, 1)

        asyncio.run(self.inspector._remove())

        assert self.draft.made is not None
        with zipfile.ZipFile(io.BytesIO(self.draft.made.data)) as archive:
            after = archive.read(package.LOG).decode("utf-8").split("\n")
        self.assertEqual(after[1], package.REMOVED_LINE)
        self.assertEqual(len(after), len(before.split("\n")))
        self.assertEqual(after[0], before.split("\n")[0])
        self.assertIsNone(self.inspector.picked)

    def test_leaving_out_with_no_file_chosen_does_nothing(self) -> None:
        with _PAGE:
            self.inspector.draw()
        self.inspector.file = ""

        asyncio.run(self.inspector._leave_out())

        self.assertEqual(self.draft.left_out, [])

    def test_choosing_a_file_clears_a_search(self) -> None:
        with _PAGE:
            self.inspector.draw()
        self.inspector.needle = "sign-in code"

        self.inspector._choose_file(types.SimpleNamespace(value=package.PREVIOUS_LOG))

        self.assertEqual(self.inspector.needle, "")
        self.assertEqual(self.inspector.file, package.PREVIOUS_LOG)

    def test_the_counts_are_the_marks_the_files_hold_by_kind(self) -> None:
        assert self.draft.made is not None
        marks = [found for one in self.draft.made.parts
                 for found in package.redact.PLACEHOLDER.finditer(one.text)
                 if package.redact.kind_of(found.group(0)) == package.redact.HOST]

        counts = dict(support_inspect.replaced_counts(self.draft.made))

        self.assertGreater(len(marks), 0)
        self.assertEqual(counts[package.redact.HOST], len(marks))


if __name__ == "__main__":
    unittest.main()

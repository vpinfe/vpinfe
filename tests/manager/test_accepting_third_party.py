"""What the Manager says about a theme or extension VPinFE did not ship."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from common.security import accepted
from manager.extensions import ext_page
from manager.frontend import themes
from manager.kit import panel
from manager.security import accept


def _theme(**more) -> dict:
    return {"key": "k", "name": "Name", "author": "someone", "version": "1.0",
            "installed_version": "", "installed": False, "active": False,
            "update_available": False, "type": "", "preview": "", **more}


def _trust(origin: str, *, accepted_: bool, install_accepted: bool = False,
           source: str = "https://git.example.net/t.json") -> dict:
    return {"origin": origin, "accepted": accepted_, "install_accepted": install_accepted,
            "source": source}


class TheMarkOnATheme(unittest.TestCase):
    def test_a_theme_from_the_theme_list_has_none(self) -> None:
        theme = _theme(installed=True, trust=_trust(accepted.STOCK, accepted_=True))

        self.assertEqual(themes.trust_state(theme), "")
        self.assertFalse(themes.stopped(theme))

    def test_one_installed_from_the_theme_list_and_not_accepted_is_stopped(self) -> None:
        theme = _theme(installed=True, trust=_trust(accepted.STOCK, accepted_=False))

        self.assertEqual(themes.trust_state(theme), themes.UNACCEPTED)
        self.assertTrue(themes.stopped(theme))

    def test_a_theme_with_no_word_on_trust_has_none(self) -> None:
        self.assertEqual(themes.trust_state(_theme()), "")

    def test_one_installed_and_not_accepted_is_stopped(self) -> None:
        theme = _theme(installed=True, trust=_trust(accepted.SOURCE, accepted_=False))

        self.assertEqual(themes.trust_state(theme), themes.UNACCEPTED)
        self.assertTrue(themes.stopped(theme))

    def test_one_installed_and_accepted_is_only_unofficial(self) -> None:
        theme = _theme(installed=True, trust=_trust(accepted.SOURCE, accepted_=True))

        self.assertEqual(themes.trust_state(theme), themes.UNOFFICIAL)
        self.assertFalse(themes.stopped(theme))

    def test_one_not_installed_yet_is_unofficial_and_not_stopped(self) -> None:
        theme = _theme(trust=_trust(accepted.SOURCE, accepted_=False))

        self.assertEqual(themes.trust_state(theme), themes.UNOFFICIAL)
        self.assertFalse(themes.stopped(theme))

    def test_the_words_are_the_ones_the_catalog_keeps(self) -> None:
        self.assertEqual(themes.TRUST.word(themes.UNACCEPTED), "Not Trusted")
        self.assertEqual(themes.TRUST.word(themes.UNOFFICIAL), "Unofficial")

    def test_the_row_carries_it_for_the_grid(self) -> None:
        theme = _theme(installed=True, trust=_trust(accepted.SOURCE, accepted_=False))

        self.assertEqual(themes.rows([theme])[0]["trust"], themes.UNACCEPTED)


class TheQuestions(unittest.IsolatedAsyncioTestCase):
    async def asked(self, call) -> tuple[str, list[str], str, dict]:
        with patch.object(accept, "_ask", new=AsyncMock(return_value=True)) as ask:
            self.assertTrue(await call)
        (title, lines, answer), kept = ask.call_args
        return title, lines, answer, kept

    async def test_installing_a_theme_from_another_source_says_where_it_is_from(self) -> None:
        title, lines, answer, kept = await self.asked(
            accept.theme("Mine", source="https://git.example.net/t.json", installing=True))

        self.assertEqual(title, "Install an Unofficial Theme?")
        self.assertEqual(lines[0], "Mine comes from https://git.example.net/t.json, "
                                   "not the VPinFE Theme List.")
        self.assertEqual(answer, "Trust and Install")
        self.assertEqual(kept["closing"], "Install it only if you trust where it came from.")

    async def test_using_one_already_here_does_not_say_install(self) -> None:
        title, lines, answer, kept = await self.asked(
            accept.theme("Mine", source="https://git.example.net/t.json"))

        self.assertEqual(title, "Trust an Unofficial Theme?")
        self.assertEqual(answer, "Trust")
        self.assertNotIn("nstall", kept["closing"])

    async def test_a_theme_found_in_the_folder_says_so(self) -> None:
        _title, lines, _answer, _kept = await self.asked(accept.theme("Mine"))

        self.assertEqual(lines[0],
                         "Mine was found in the themes folder, not installed from a theme source.")

    async def test_every_theme_question_warns_it_can_run_programs(self) -> None:
        for installing in (False, True):
            _title, lines, _answer, _kept = await self.asked(
                accept.theme("Mine", installing=installing))

            self.assertEqual(lines[1], "Themes can run programs on this device, and VPinFE "
                                       "can't check this one.")

    OWN: dict[str, Any] = {"author": "Example Author", "source": "https://github.com/example/tool",
           "path": "/found/tool"}

    async def test_an_extension_says_what_it_says_it_uses(self) -> None:
        title, lines, answer, kept = await self.asked(
            accept.extension("Tool", "1.0.0", reach=ext_page.reach_entries(["games:read"]),
                             **self.OWN))

        self.assertEqual(title, "Trust an Unofficial Extension?")
        self.assertIn("isn't made or endorsed by the VPinFE team", lines[0])
        self.assertIs(kept["rows"][-2][0], panel.HEADING)
        self.assertEqual(kept["rows"][-2][1], "What It Can Do")
        self.assertEqual(kept["rows"][-1][0], "Your Library")
        self.assertEqual(answer, "Trust and Turn On")

    async def test_the_answer_waits_for_the_tick_that_says_it_is_unsupported(self) -> None:
        _title, _lines, _answer, kept = await self.asked(
            accept.extension("Tool", "1.0.0", **self.OWN))

        self.assertEqual(kept["understood"],
                         "I understand the VPinFE team won't support this setup while it's on")
        self.assertIn("won't support this setup", kept["warning"])

    async def test_it_is_shown_whose_it_is_and_where_it_comes_from(self) -> None:
        _title, _lines, _answer, kept = await self.asked(
            accept.extension("Tool", "1.0.0", **self.OWN))

        self.assertEqual(kept["rows"][:2], [("Author", "Example Author"), ("Version", "1.0.0")])
        self.assertEqual([one[0] for one in kept["rows"][:4]],
                         ["Author", "Version", "Source", "Folder Path"])
        self.assertEqual(kept["rows"][4], ("What It Can Do", "None"))

    async def test_a_copy_a_changed_one_and_one_claiming_the_team_each_say_so(self) -> None:
        _title, lines, _answer, _kept = await self.asked(
            accept.extension("Tool", "2.0.0", replaces="Tool", changed=3,
                             claims_team=True, **self.OWN))

        self.assertEqual(lines[1:], [
            "It uses the name of the Tool that comes with VPinFE, but it isn't that one.",
            "It started out as an official VPinFE extension, but 3 of its files have changed, "
            "so it is no longer the official one.",
            "It says it's from the VPinFE team, but it isn't."])

    async def test_a_newer_version_says_what_it_was_and_what_it_adds(self) -> None:
        _title, lines, _answer, kept = await self.asked(
            accept.extension("Tool", "2.0.0", reach=ext_page.reach_entries(["games:read"]),
                             added=ext_page.reach_entries(["games:write"]), was="1.0.0",
                             **self.OWN))

        self.assertEqual(lines[-1], "Updated from 1.0.0 to 2.0.0.")
        self.assertEqual(kept["rows"][-2][1], "What It Can Now Also Do")

    async def test_more_access_at_the_same_version_does_not_claim_an_update_between_them(
            self) -> None:
        _title, lines, _answer, _kept = await self.asked(
            accept.extension("Tool", "1.0.0", added=ext_page.reach_entries(["games:write"]),
                             was="1.0.0", **self.OWN))

        self.assertEqual(len(lines), 1)


if __name__ == "__main__":
    unittest.main()

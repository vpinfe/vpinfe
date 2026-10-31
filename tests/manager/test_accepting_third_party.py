"""What the Manager says about a theme or extension VPinFE did not ship."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from common.security import accepted
from manager.frontend import themes
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
        self.assertEqual(themes.TRUST.word(themes.UNACCEPTED), "Not Accepted")
        self.assertEqual(themes.TRUST.word(themes.UNOFFICIAL), "Unofficial")

    def test_the_row_carries_it_for_the_grid(self) -> None:
        theme = _theme(installed=True, trust=_trust(accepted.SOURCE, accepted_=False))

        self.assertEqual(themes.rows([theme])[0]["trust"], themes.UNACCEPTED)

    def test_a_stopped_theme_has_something_to_do_so_its_row_has_a_menu(self) -> None:
        theme = _theme(installed=True, active=True,
                       trust=_trust(accepted.SOURCE, accepted_=False))

        self.assertFalse(themes.rows([theme])[0][themes.grid.NO_MENU])


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
        self.assertEqual(answer, "Accept and Install")
        self.assertEqual(kept["closing"], "Install it only if you trust where it came from.")

    async def test_using_one_already_here_does_not_say_install(self) -> None:
        title, lines, answer, kept = await self.asked(
            accept.theme("Mine", source="https://git.example.net/t.json"))

        self.assertEqual(title, "Use an Unofficial Theme?")
        self.assertEqual(answer, "Accept")
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

    OWN = {"author": "Example Author", "source": "https://github.com/example/tool",
           "path": "/found/tool"}

    async def test_an_extension_says_what_it_says_it_uses(self) -> None:
        title, lines, _answer, kept = await self.asked(
            accept.extension("Tool", "1.0.0", uses=["Read Your Library"], **self.OWN))

        self.assertEqual(title, "Run an Unofficial Extension?")
        self.assertIn("isn't from VPinFE", lines[0])
        self.assertEqual(kept["rows"][-1][0], "Reported Capabilities")
        self.assertEqual(kept["closing"], "Run it only if you trust where it came from.")

    async def test_it_is_shown_whose_it_is_and_where_it_comes_from(self) -> None:
        _title, _lines, _answer, kept = await self.asked(
            accept.extension("Tool", "1.0.0", uses=[], **self.OWN))

        self.assertEqual(kept["rows"][:2], [("Author", "Example Author"), ("Version", "1.0.0")])
        self.assertEqual([one[0] for one in kept["rows"]],
                         ["Author", "Version", "Source", "Folder Path", "Reported Capabilities"])

    async def test_one_that_replaces_a_built_in_says_which_and_what_version(self) -> None:
        _title, lines, _answer, _kept = await self.asked(
            accept.extension("Tool", "2.0.0", uses=[], replaces="Tool",
                             replaces_version="1.0.0", **self.OWN))

        self.assertEqual(lines[-1], "Replaces the Tool 1.0.0 that comes with VPinFE.")

    async def test_a_newer_version_says_what_it_was_and_who_made_it(self) -> None:
        _title, lines, _answer, kept = await self.asked(
            accept.new_access("Tool", "2.0.0", "Example Author", ["Read Your Library"],
                              was="1.0.0"))

        self.assertEqual(lines, ["Updated from 1.0.0 to 2.0.0."])
        self.assertEqual(kept["rows"][0], ("Author", "Example Author"))
        self.assertEqual(kept["rows"][1][0], "New Capabilities")

    async def test_a_newer_version_with_no_known_earlier_one_says_only_where_it_went(self) -> None:
        _title, lines, _answer, _kept = await self.asked(
            accept.new_access("Tool", "2.0.0", "Example Author", ["Read Your Library"]))

        self.assertEqual(lines, ["Updated to 2.0.0."])

    async def test_more_access_at_the_same_version_does_not_claim_an_update_between_them(
            self) -> None:
        _title, lines, _answer, _kept = await self.asked(
            accept.new_access("Tool", "1.0.0", "Example Author", ["Read Your Library"],
                              was="1.0.0"))

        self.assertEqual(lines, ["Updated to 1.0.0."])


if __name__ == "__main__":
    unittest.main()

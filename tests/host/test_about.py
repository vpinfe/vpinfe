from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from common import i18n
from common.host import about

# Names a person reads the same in every language.
NAMES = {"VPinFE", "Python"}


def _plain(said: str) -> bool:
    return any("a" <= letter.lower() <= "z" for letter in said)


class AboutTests(unittest.TestCase):
    def setUp(self) -> None:
        about.reset_for_tests()

    def tearDown(self) -> None:
        about.reset_for_tests()

    def test_every_fact_answers_something(self) -> None:
        """A blank sends the reader looking for the reason it is blank. A machine that
        cannot answer says so in words."""
        for group in about.details():
            self.assertTrue(group["heading"])
            self.assertTrue(group["facts"], group["heading"])
            for label, value in group["facts"]:
                self.assertTrue(label)
                self.assertTrue(str(value).strip(), label)

    def test_the_text_carries_what_the_screen_shows(self) -> None:
        """The point of the page is the copy button, so the two renderings hold the same
        answer - a field on screen and absent from the paste is the failure this
        catches."""
        text = about.as_text()

        for group in about.details():
            self.assertIn(group["heading"], text)
            for label, value in group["facts"]:
                self.assertIn(label, text)
                self.assertIn(str(value), text)

    def test_each_library_folder_is_listed(self) -> None:
        from common.games.library_folders import KIND_GAME, Location
        from common.i18n import t

        held = [Location("l1", "/games/first"), Location("l2", "/games/one", kind=KIND_GAME)]
        with mock.patch("common.games.library_folders.configured", return_value=held):
            facts = next(group["facts"] for group in about.details(refresh=True)
                         if group["heading"] == t("about.heading.locations"))

        folder = t("about.fact.library_folder")
        self.assertEqual([value for label, value in facts if label == folder],
                         ["/games/first", "/games/one"])

    def test_a_path_is_marked_and_a_placeholder_is_not(self) -> None:
        """A client draws the marked values as paths, with their end and Copy path. "Not
        set" is a word, and a Copy button on it would copy a word."""
        from common.games.library_folders import Location
        from common.i18n import t

        with mock.patch("common.games.library_folders.configured",
                        return_value=[Location("l1", "/games/first")]):
            held = {label: value for group in about.details(refresh=True)
                    for label, value in group["facts"]}
        self.assertIsInstance(held[t("about.fact.library_folder")], about.PathValue)
        self.assertIsInstance(held[t("about.fact.configuration")], about.PathValue)
        self.assertEqual(Path(str(held[t("about.fact.settings_file")])).name, "vpinfe.json")

        with mock.patch("common.games.library_folders.configured", return_value=[]):
            unset = [value for group in about.details(refresh=True)
                     for label, value in group["facts"] if label == t("about.fact.library_folder")]
        self.assertEqual([str(value) for value in unset], [t("about.not_set")])
        self.assertNotIsInstance(unset[0], about.PathValue)

    def test_the_route_says_which_facts_are_paths(self) -> None:
        from fastapi.testclient import TestClient

        import httpapi
        from common.i18n import t

        client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        groups = client.get("/about").json()["groups"]
        facts = {fact["label"]: fact for group in groups for fact in group["facts"]}

        self.assertTrue(facts[t("about.fact.settings_file")]["path"])
        self.assertFalse(facts[t("word.version")]["path"])

    def test_it_is_read_once(self) -> None:
        """Every field costs a subprocess or a config read, and none of them changes
        while VPinFE is running."""
        first = about.details()

        self.assertIs(about.details(), first)
        self.assertIsNot(about.details(refresh=True), first)

    def test_the_words_around_each_value_are_the_readers(self) -> None:
        """Under the pseudo-locale, a heading, label or fallback in plain letters was
        written in place rather than looked up."""
        self.addCleanup(i18n.set_language, i18n.language())
        i18n.set_language("qps")
        with mock.patch.object(about, "_browser_path", return_value=""), \
                mock.patch("common.runtime.log_setup.log_file", return_value=None), \
                mock.patch.object(about.platform, "machine", return_value=""), \
                mock.patch.object(about, "_install_context",
                                  return_value={"reason": "source_build"}):
            groups = about.details(refresh=True)

        words = [group["heading"] for group in groups]
        words += [label for group in groups for label, _ in group["facts"]]
        facts = {(group["heading"], label): value
                 for group in groups for label, value in group["facts"]}
        words += [value for (_, label), value in facts.items()
                  if label in (i18n.t("about.fact.architecture"), i18n.t("about.fact.build"),
                               i18n.t("about.fact.log_file"))]
        browser = [value for (heading, _), value in facts.items()
                   if heading == i18n.t("about.heading.browser")]

        self.assertEqual([said for said in words if said not in NAMES and _plain(said)], [])
        self.assertEqual(len(browser), 3)
        self.assertEqual([said for said in browser if _plain(said)], [])


class WindowsFileVersionTests(unittest.TestCase):
    def test_the_path_goes_in_the_environment_and_not_in_the_script(self) -> None:
        path = "C:/Games\u2019; Start-Process calc; \u2018.exe"
        with mock.patch.object(about.subprocess, "run") as run:
            run.return_value = mock.Mock(stdout="10.8.1\n", stderr="")
            self.assertEqual(about._windows_file_version(path), "10.8.1")
        command = run.call_args.args[0]
        self.assertNotIn("calc", " ".join(command))
        self.assertEqual(run.call_args.kwargs["env"]["VPINFE_FILE"], path)


if __name__ == "__main__":
    unittest.main()

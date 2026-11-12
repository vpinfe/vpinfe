"""The Themes grid: what each row says, and what the panel leaves out."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, Mock, patch

from manager.frontend import theme_card, theme_sources, themes
from tests.support.clicks import press


def _theme(**more) -> dict:
    return {"key": "k", "name": "Name", "author": "someone", "version": "1.0",
            "installed_version": "", "installed": False, "active": False,
            "update_available": False, "layouts": [], "preview": "", **more}


class ARow(unittest.TestCase):
    def test_each_state_is_one_of_four(self) -> None:
        self.assertEqual(
            [themes.ACTIVE, themes.UPDATE, themes.INSTALLED, themes.AVAILABLE],
            [themes.status(_theme(installed=True, active=True)),
             themes.status(_theme(installed=True, update_available=True)),
             themes.status(_theme(installed=True)),
             themes.status(_theme())])

    def test_made_for_says_only_what_the_manifest_declared(self) -> None:
        self.assertEqual(["cab", "both", ""], [row["made_for"] for row in themes.rows(
            [_theme(key="a", layouts=["cab"]),
             _theme(key="b", layouts=["cab", "desktop"]),
             _theme(key="c", layouts=[])])])

    def test_a_layout_with_a_picture_of_its_own_is_asked_for_by_name(self) -> None:
        theme = _theme(preview="/p", preview_version="main",
                       previews={"cab": {"url": "/c", "version": "cv"},
                                 "desktop": {"url": "/d", "version": "dv"}})

        self.assertEqual("/api/v1/themes/k/preview?size=256&v=main",
                         theme_card.preview(theme, 256))
        self.assertEqual("/api/v1/themes/k/preview?size=256&v=dv&layout=desktop",
                         theme_card.preview(theme, 256, "desktop"))
        self.assertEqual("/api/v1/themes/k/preview?v=cv&layout=cab",
                         theme_card.preview(theme, None, "cab"))

    def test_a_layout_without_one_gets_the_main_picture(self) -> None:
        theme = _theme(preview="/p", preview_version="main")

        self.assertEqual(theme_card.preview(theme, 256),
                         theme_card.preview(theme, 256, "desktop"))
        self.assertEqual([], theme_card.pictures(theme))

    def test_an_update_reads_as_the_version_you_have_and_the_one_on_offer(self) -> None:
        said = theme_card.version_said(_theme(installed=True, update_available=True,
                                              installed_version="1.7", version="1.9"))
        self.assertIn("1.7", said)
        self.assertIn("1.9", said)


class ThePictures(unittest.TestCase):
    def _framed(self, theme: dict) -> tuple[list[str], list[str]]:
        frames: list[str] = []
        captions: list[str] = []

        def label(text: str) -> Mock:
            captions.append(text)
            return Mock()

        with patch.object(themes, "_frame",
                          side_effect=lambda _theme, layout="": frames.append(layout)), \
                patch.object(themes, "ui"), patch.object(themes.ui, "label", new=label):
            themes._pictures(theme)
        return frames, captions

    def test_two_pictures_are_a_pair_in_the_order_listed_with_a_caption_each(self) -> None:
        theme = _theme(previews={"cab": {"url": "/c", "version": "c"},
                                 "desktop": {"url": "/d", "version": "d"}})

        self.assertEqual((["cab", "desktop"], ["Cabinet", "Desktop"]), self._framed(theme))

    def test_one_picture_or_none_of_its_own_is_one_frame_without_a_caption(self) -> None:
        one = _theme(previews={"cab": {"url": "/c", "version": "c"}})

        self.assertEqual(([""], []), self._framed(one))
        self.assertEqual(([""], []), self._framed(_theme()))


class WhereItComesFrom(unittest.TestCase):
    def test_a_repository_or_a_file_in_one_reads_as_owner_and_repo(self) -> None:
        for url in ("https://github.com/owner/repo",
                    "https://raw.githubusercontent.com/owner/repo/master/themes.json",
                    "https://github.com/owner/repo/raw/refs/heads/master/manifest.json",
                    "https://git.example.net/owner/repo/raw/branch/main/themes.json"):
            with self.subTest(url=url):
                self.assertEqual("owner/repo", themes.repo_name(url))

    def test_a_file_in_a_repository_links_to_the_repository_s_page(self) -> None:
        for url, page in (
                ("https://raw.githubusercontent.com/owner/repo/master/themes.json",
                 "https://github.com/owner/repo"),
                ("https://git.example.net/owner/repo/raw/branch/main/themes.json",
                 "https://git.example.net/owner/repo"),
                ("https://example.net/vpinfe/catalogs/themes.json",
                 "https://example.net/vpinfe/catalogs/themes.json")):
            with self.subTest(url=url):
                self.assertEqual(page, themes.repo_page(url))

    def test_any_other_address_is_shown_whole(self) -> None:
        url = "https://example.net/vpinfe/catalogs/themes.json"
        self.assertEqual(url, themes.repo_name(url))

    def test_a_row_carries_its_registry_repository_and_author(self) -> None:
        row = themes.rows([_theme(
            registry="https://raw.githubusercontent.com/owner/catalog/master/themes.json",
            url="https://github.com/owner/theme")])[0]
        self.assertEqual(("owner/catalog", "owner/theme", "someone"),
                         (row["registry"], row["repository"], row["author"]))

    def test_a_theme_no_source_offers_has_neither(self) -> None:
        row = themes.rows([_theme(registry="", url="")])[0]
        self.assertEqual(("", ""), (row["registry"], row["repository"]))


LISTS, THEMES = theme_sources.LISTS, theme_sources.THEMES


class WhenASourceTroublesThePage(unittest.TestCase):
    def _drawn(self, sources: dict | None) -> tuple[list[str], list[str]]:
        said: list[str] = []
        went: list[str] = []

        def label(text: str) -> Mock:
            said.append(text)
            return Mock()

        def link(text: str, to: str) -> Mock:
            went.append(to)
            return Mock()

        with patch.object(themes.ui, "row"), patch.object(themes.panel, "link", new=link), \
                patch.object(themes.ui, "label", new=label):
            themes._finding(sources)
        return said, went

    def test_a_source_that_cannot_be_read_is_one_finding_however_many_there_are(self) -> None:
        bad, fine = {"level": "bad"}, {"level": ""}

        self.assertEqual(([], []), self._drawn({LISTS: [fine], THEMES: []}))
        self.assertEqual((["1 theme source could not be read"],
                          [themes.settings.address_for("themes")]),
                         self._drawn({LISTS: [fine, bad], THEMES: []}))
        self.assertEqual((["2 theme sources could not be read"],
                          [themes.settings.address_for("themes")]),
                         self._drawn({LISTS: [bad], THEMES: [bad, fine]}))

    def test_a_warning_is_not_a_source_that_cannot_be_read(self) -> None:
        self.assertEqual(([], []),
                         self._drawn({LISTS: [{"level": "warn"}, {"level": "unknown"}]}))

    def test_sources_that_could_not_be_listed_draw_no_finding(self) -> None:
        self.assertEqual(([], []), self._drawn(None))

    def _empty(self, sources: dict | None) -> list:
        with patch.object(themes.panel, "action",
                          side_effect=lambda text, *a, **k: ("action", text)):
            return themes._empty(Mock(), Mock(), sources)

    def test_with_no_source_at_all_the_empty_page_offers_to_add_one(self) -> None:
        rows = self._empty({LISTS: [], THEMES: []})

        self.assertEqual([("action", "Add Source"), ("action", "Add the VPinFE Theme List")],
                         [row[1] for row in rows[1:]])

    def test_with_sources_that_offer_nothing_it_offers_to_add_another(self) -> None:
        rows = self._empty({LISTS: [{"level": "bad"}], THEMES: []})

        self.assertEqual([("action", "Add Source")], [row[1] for row in rows[1:]])

    def test_sources_that_could_not_be_listed_are_not_an_empty_library_of_sources(self) -> None:
        self.assertEqual(1, len(self._empty(None)))


class TheRepositoryFact(unittest.IsolatedAsyncioTestCase):
    async def _entries(self, theme: dict) -> list:
        shown: list = []
        with patch.object(themes, "_found", return_value=theme), \
                patch.object(themes, "_status_value", return_value="status"), \
                patch.object(themes.backup_rows, "listed", new=AsyncMock(return_value=[])), \
                patch.object(themes, "ui"), \
                patch.object(themes.panel, "link_out", side_effect=lambda text, to: (text, to)), \
                patch.object(themes.panel, "note", side_effect=lambda text: ("note", text)), \
                patch.object(themes.panel, "facts",
                             side_effect=lambda _ui, entries: shown.extend(entries)):
            await themes.details({"library": Mock()})
        return shown

    async def test_a_theme_no_source_offers_says_so(self) -> None:
        entries = await self._entries(_theme(key="k", url=""))

        self.assertIn(("Repository", "None"), entries)
        self.assertIn(("note", "No source offers this theme, so it gets no updates"), entries)

    async def test_a_theme_a_source_offers_links_to_its_repository(self) -> None:
        entries = await self._entries(_theme(key="k", url="https://github.com/owner/theme"))

        self.assertIn(("Repository", ("owner/theme", "https://github.com/owner/theme")),
                      entries)
        self.assertNotIn("None", [value for _label, value in entries if _label == "Repository"])


class TheChangelog(unittest.TestCase):
    def test_a_template_placeholder_is_not_news(self) -> None:
        self.assertEqual("", themes.changes_worth_showing(
            _theme(change_log="What changed in this version?")))

    def test_it_is_shown_before_installing_and_with_an_update_waiting(self) -> None:
        self.assertEqual("Faster", themes.changes_worth_showing(_theme(change_log="Faster")))
        self.assertEqual("Faster", themes.changes_worth_showing(
            _theme(change_log="Faster", installed=True, update_available=True)))

    def test_it_is_old_news_on_the_copy_you_run(self) -> None:
        self.assertEqual("", themes.changes_worth_showing(
            _theme(change_log="Faster", installed=True)))


class Installing(unittest.IsolatedAsyncioTestCase):
    async def _install(self, outcome: object) -> tuple[Mock, Mock, AsyncMock]:
        downloading, notify, again = Mock(), Mock(), AsyncMock()
        self.notification = Mock(return_value=downloading)
        with patch.object(themes.ui, "notification", new=self.notification), \
                patch.object(themes.ui, "notify", new=notify), \
                patch.object(themes.run, "io_bound", new=AsyncMock(side_effect=outcome)):
            await press(themes._install, Mock(), [{"key": "carousel-desktop"}], again)
        return downloading, notify, again

    async def test_the_download_notice_stays_until_the_install_is_over(self) -> None:
        await self._install([None])

        self.notification.assert_called_once()
        self.assertEqual((True, None), (self.notification.call_args.kwargs["spinner"],
                                        self.notification.call_args.kwargs["timeout"]))

    async def test_the_download_notice_goes_once_the_theme_is_in(self) -> None:
        downloading, notify, again = await self._install([None])

        downloading.dismiss.assert_called_once_with()
        self.assertEqual("positive", notify.call_args.kwargs["type"])
        again.assert_awaited_once()

    async def test_the_download_notice_goes_when_the_install_fails(self) -> None:
        downloading, notify, again = await self._install(OSError("down"))

        downloading.dismiss.assert_called_once_with()
        self.assertEqual("negative", notify.call_args.kwargs["type"])
        again.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

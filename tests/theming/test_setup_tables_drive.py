"""Setup's Tables step, driven as a person would on a fresh instance: a folder of game
folders ends in Finish and becomes the library, a folder of loose tables goes through the
importer's own pages and ends in a frontend with those games, None yet makes the folder
picked, and another frontend opens the importer's first page.

Slow: boots real instances and a real browser.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from common.i18n import t
from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import write_game
from tests.support.live_instance import LiveInstance
from tests.support.manager_walk import ManagerWalk

CARD = ".manager-wizard-card"
TITLE = f"{CARD} .manager-wizard-title"
FORWARD = f"{CARD} .manager-wizard-forward"
RADIO = f"{CARD} .manager-wizard-pick"
FOUND = f"{CARD} .manager-wizard-found"
CARD_UP = ("[...document.querySelectorAll('.manager-wizard-card')].some(card =>"
           " getComputedStyle(card.closest('.q-dialog__inner')).transform === 'none'"
           " && card.getBoundingClientRect().width > 0)")
CARD_GONE = f"!document.querySelector({json.dumps(CARD)})"
TEXT_OF = "(() => { const el = document.querySelector(%s); return el ? el.innerText : null; })()"
ASKED = t("manager.setup.tables.title")
PENDING = {("device", "setup_pending"): True}


class SetupTablesDrive(unittest.TestCase):
    seen: dict[str, Any] = {}

    @classmethod
    def setUpClass(cls) -> None:
        binary = chromium_path()
        if not binary:
            raise unittest.SkipTest("no Chromium on this machine")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            folders = root / "of-games"
            for name in ("Alpha", "Bravo"):
                write_game(folders, name)
            pile = root / "pile"
            pile.mkdir()
            for name in ("Charlie", "Delta", "Echo"):
                (pile / f"{name}.vpx").write_bytes(b"x" * 2048)
            fresh = root / "fresh"
            fresh.mkdir()
            cls.seen = {}
            for name, drive in (("folders", cls._of_game_folders), ("loose", cls._loose),
                                ("none", cls._none_yet), ("frontend", cls._frontend),
                                ("again", cls._again)):
                library = root / f"library-{name}"
                library.mkdir()
                if name == "again":
                    write_game(library, "Foxtrot")
                with LiveInstance(library, extra_settings=None if name == "again" else PENDING
                                  ) as instance:
                    cls.seen[name] = asyncio.run(cls._drive(
                        instance, binary, drive,
                        {"folders": folders, "pile": pile, "fresh": fresh,
                         "copies": root / "copies"}))

    @classmethod
    async def _drive(cls, instance: LiveInstance, binary: str, how: Any,
                     paths: dict[str, Path]) -> dict[str, Any]:
        seen: dict[str, Any] = {}
        instance.wait_for_api()
        async with BrowserSession(binary) as browser:
            walk = ManagerWalk(browser, instance)

            async def title() -> str:
                return (await browser.evaluate(TEXT_OF % json.dumps(TITLE)) or "").strip()

            async def forward_says() -> str:
                return (await browser.evaluate(
                    f"document.querySelector({json.dumps(FORWARD + ' .block')}).innerText")
                ).strip()

            async def forward() -> None:
                before = await title()
                if before in (t("manager.setup.vpx.title_one"),
                              t("manager.setup.vpx.title_many")):
                    # Next waits for an answer where the program is not found, which
                    # depends on the machine, so the walk answers the same way everywhere.
                    await choose(t("manager.setup.vpx.none"))
                await walk.act(
                    lambda: browser.click(FORWARD),
                    mark=f"window.fromTitle = {json.dumps(before)}",
                    until=(f"(document.querySelector({json.dumps(TITLE)}) || {{innerText: ''}})"
                           ".innerText.trim() !== window.fromTitle"))
                await walk.drawn()

            async def click_text(selector: str, text: str) -> None:
                await browser.click(selector, nth=int(await browser.wait_for(
                    "(() => { const i = [...document.querySelectorAll("
                    f"{json.dumps(selector)})].findIndex(el => el.innerText.includes("
                    f"{json.dumps(text)})); return i >= 0 ? i + 1 : 0; }})()")) - 1)

            async def choose(label: str) -> None:
                await walk.act(
                    lambda: click_text(RADIO, label),
                    until=(f"[...document.querySelectorAll({json.dumps(RADIO)})].some(el =>"
                           " el.getAttribute('aria-checked') === 'true' &&"
                           f" el.innerText.includes({json.dumps(label)}))"))

            async def type_the_path(text: str) -> None:
                field = f"{CARD} input.q-field__native"
                shown = (f"[...document.querySelectorAll({json.dumps(field)})]"
                         ".filter(el => el.getBoundingClientRect().width > 0)")
                await browser.wait_for(f"{shown}.length > 0")
                nth = int(await browser.evaluate(
                    f"[...document.querySelectorAll({json.dumps(field)})]"
                    ".indexOf(" + shown + ".at(-1))"))
                await browser.click(field, nth=nth)
                await browser.evaluate(
                    f"document.querySelectorAll({json.dumps(field)})[{nth}].select()")
                await browser.send("Input.insertText", {"text": text})

            async def at_tables(address: str = "/", asked: str = ASKED) -> None:
                await walk.visit(address)
                await browser.wait_for(CARD_UP, timeout=60)
                await walk.drawn()
                for _ in range(8):
                    if await title() == asked:
                        return
                    await forward()
                raise AssertionError(f"never reached Tables: {await title()!r}")

            helpers = {"title": title, "forward_says": forward_says, "forward": forward,
                       "choose": choose, "type_the_path": type_the_path, "walk": walk,
                       "browser": browser, "instance": instance, "paths": paths,
                       "at_tables": at_tables, "click_text": click_text}
            seen.update(await how(helpers))
            seen["log"] = instance.output(tail=None)
        return seen

    @classmethod
    async def _of_game_folders(cls, h: dict[str, Any]) -> dict[str, Any]:
        seen: dict[str, Any] = {}
        browser, walk = h["browser"], h["walk"]
        await h["at_tables"]()
        seen["choices"] = await browser.evaluate(
            f"[...document.querySelectorAll({json.dumps(RADIO)})].map(el => el.innerText.trim())")
        seen["before_typing"] = await h["forward_says"]()
        await h["type_the_path"](str(h["paths"]["folders"]))
        await browser.wait_for(f"!!document.querySelector({json.dumps(FOUND)})", timeout=30)
        seen["found"] = await browser.evaluate(TEXT_OF % json.dumps(FOUND))
        seen["forward"] = await h["forward_says"]()
        await walk.act(lambda: browser.click(FORWARD), mark="window.beforeFinish = true",
                       until="window.beforeFinish !== true")
        seen["locations"] = cls_locations(h["instance"])
        seen["pending"] = "setup_pending" in h["instance"].api("/api/v1")
        return seen

    @classmethod
    async def _loose(cls, h: dict[str, Any]) -> dict[str, Any]:
        seen: dict[str, Any] = {"titles": []}
        browser = h["browser"]
        await h["at_tables"]()
        await h["type_the_path"](str(h["paths"]["pile"]))
        await browser.wait_for(f"!!document.querySelector({json.dumps(FOUND)})", timeout=30)
        seen["found"] = await browser.evaluate(TEXT_OF % json.dumps(FOUND))
        seen["forward"] = await h["forward_says"]()
        while await h["forward_says"]() == t("word.next") and len(seen["titles"]) < 8:
            await h["forward"]()
            seen["titles"].append(await h["title"]())
        seen["last"] = await h["forward_says"]()
        await browser.click(FORWARD)
        try:
            await browser.wait_for("location.search.includes('view=jobs')", timeout=60)
        except TimeoutError as exc:
            now = await browser.evaluate(
                "location.href + '\\n' + (document.querySelector('.manager-wizard-card')"
                " || document.body).innerText")
            raise AssertionError(f"{exc}\n{now}\n{h['instance'].output()[-1500:]}") from exc
        seen["where"] = await browser.evaluate("location.search")
        seen["card_left"] = await browser.evaluate(
            f"!document.querySelector({json.dumps(CARD)})")
        for _ in range(300):
            if len(h["instance"].api("/api/v1/games").get("games", [])) >= 3:
                break
            await asyncio.sleep(0.2)
        seen["pending"] = "setup_pending" in h["instance"].api("/api/v1")
        seen["games"] = len(h["instance"].api("/api/v1/games").get("games", []))
        return seen

    @classmethod
    async def _none_yet(cls, h: dict[str, Any]) -> dict[str, Any]:
        seen: dict[str, Any] = {}
        browser, walk = h["browser"], h["walk"]
        await h["at_tables"]()
        await h["choose"](t("manager.setup.tables.none"))
        seen["before_typing"] = await h["forward_says"]()
        await h["type_the_path"](str(h["paths"]["fresh"]))
        await browser.wait_for(
            f"!document.querySelector({json.dumps(FORWARD)}).disabled"
            f" && !document.querySelector({json.dumps(FORWARD)}).classList"
            ".contains('disabled')", timeout=30)
        seen["forward"] = await h["forward_says"]()
        await walk.act(lambda: browser.click(FORWARD), mark="window.beforeFinish = true",
                       until="window.beforeFinish !== true")
        seen["locations"] = cls_locations(h["instance"])
        return seen

    @classmethod
    async def _again(cls, h: dict[str, Any]) -> dict[str, Any]:
        seen: dict[str, Any] = {}
        browser = h["browser"]
        await h["walk"].visit("/manager?view=settings")
        await h["click_text"]("button", t("manager.setup.open"))
        await browser.wait_for(CARD_UP, timeout=60)
        await h["walk"].drawn()
        for _ in range(8):
            if await h["title"]() == t("manager.setup.tables.title_have"):
                break
            await h["forward"]()
        seen["card"] = await browser.evaluate(TEXT_OF % json.dumps(CARD))
        seen["games_before"] = len(h["instance"].api("/api/v1/games").get("games", []))
        await h["walk"].act(
            lambda: h["click_text"](f"{CARD} button", t("manager.setup.tables.import")),
            until=(f"(document.querySelector({json.dumps(TITLE)}) || {{innerText: ''}})"
                   f".innerText.trim() !== {json.dumps(t('manager.setup.tables.title_have'))}"))
        seen["next_title"] = await h["title"]()
        seen["games_after"] = len(h["instance"].api("/api/v1/games").get("games", []))
        return seen

    @classmethod
    async def _frontend(cls, h: dict[str, Any]) -> dict[str, Any]:
        seen: dict[str, Any] = {}
        await h["at_tables"]()
        await h["choose"](t("manager.setup.tables.frontend"))
        seen["forward"] = await h["forward_says"]()
        await h["forward"]()
        seen["next_title"] = await h["title"]()
        return seen

    def test_a_folder_of_game_folders_says_what_it_found_and_ends_in_finish(self) -> None:
        seen = self.seen["folders"]

        self.assertEqual(t("word.next"), seen["before_typing"])
        self.assertIn(t("manager.setup.tables.found_folders", count=2), seen["found"])
        self.assertEqual(t("word.finish"), seen["forward"])

    def test_a_folder_of_game_folders_becomes_the_library_and_setup_is_done(self) -> None:
        seen = self.seen["folders"]

        self.assertTrue(any(path.endswith("of-games") for path in seen["locations"]),
                        seen["locations"])
        self.assertFalse(seen["pending"])

    def test_the_answers_are_a_folder_another_frontend_and_none_yet(self) -> None:
        shown = self.seen["folders"]["choices"]

        self.assertEqual(3, len(shown))
        for label, text in zip((t("manager.setup.tables.folder"),
                                t("manager.setup.tables.frontend"),
                                t("manager.setup.tables.none")), shown, strict=True):
            self.assertIn(label, text)

    def test_a_folder_of_loose_tables_goes_on_to_the_importer(self) -> None:
        seen = self.seen["loose"]

        self.assertIn(t("manager.setup.tables.found_loose", count=3), seen["found"])
        self.assertEqual(t("word.next"), seen["forward"])
        self.assertTrue(seen["titles"])

    def test_the_loose_tables_arrive_as_games_and_setup_is_done(self) -> None:
        seen = self.seen["loose"]

        self.assertGreaterEqual(seen["games"], 3)
        self.assertFalse(seen["pending"])

    def test_an_import_that_starts_closes_setup_onto_the_jobs_view(self) -> None:
        seen = self.seen["loose"]

        self.assertIn("view=jobs", seen["where"])
        self.assertTrue(seen["card_left"])

    def test_none_yet_waits_for_a_folder_and_then_finishes(self) -> None:
        seen = self.seen["none"]

        self.assertEqual(t("word.finish"), seen["forward"])
        self.assertTrue(any(path.endswith("fresh") for path in seen["locations"]),
                        seen["locations"])

    def test_another_frontend_opens_the_importers_first_page(self) -> None:
        seen = self.seen["frontend"]

        self.assertEqual(t("word.next"), seen["forward"])
        self.assertNotEqual(ASKED, seen["next_title"])
        self.assertTrue(seen["next_title"])

    def test_run_again_shows_what_is_there_and_never_imports_on_its_own(self) -> None:
        seen = self.seen["again"]

        self.assertIn(t("manager.setup.tables.have", count=1), seen["card"])
        self.assertIn(t("manager.setup.tables.import"), seen["card"])
        self.assertEqual(seen["games_before"], seen["games_after"])
        self.assertNotEqual(t("manager.setup.tables.title_have"), seen["next_title"])

    def test_nothing_waiting_for_finish_failed_to_start(self) -> None:
        for name, seen in self.seen.items():
            with self.subTest(name=name):
                self.assertNotIn("did not start", seen["log"])


def cls_locations(instance: LiveInstance) -> list[str]:
    return [one["path"] for one in instance.api("/api/v1/locations")["locations"]]


if __name__ == "__main__":
    unittest.main()

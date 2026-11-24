"""The folder picker in a real browser: New folder (the row it names a folder in, the folder
it makes and goes into, and the picker that has no way to make one), and its path field -
typing or pasting a path, the mark that says whether Enter can go there, Escape, and Places.

Slow: serves a bare page that opens the picker over a real folder on disk, and drives it.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

from common.i18n import t
from tests.support.browser_session import BrowserSession
from tests.support.manager_walk import ManagerWalk
from tests.theming.test_grid_selection_drive import _heard, drive_page, exit_with_parent

TYPED = "Games"
MANY = 80
DIALOG = ".q-dialog"
# Layout height, which the dialog's opening transition does not scale.
HEIGHT = f"document.querySelector('{DIALOG} .q-card').offsetHeight"
# How far the Cancel row is below the bottom of the window; above zero it is cut off.
CLIPPED = ("(() => { const b = [...document.querySelectorAll('" + DIALOG + " button')].pop();"
           " return Math.round(b.getBoundingClientRect().bottom - innerHeight); })()")
ASIDE = f"{DIALOG} .manager-dialog-aside"
NAMING = f"{DIALOG} .manager-folder-list input"
SHOWS = "!!document.querySelector(%s)"
GONE = "!document.querySelector(%s)"
FIELD = f"{DIALOG} input.manager-path-input"
PATH_SHOWN = f"((document.querySelector('{FIELD}') || {{}}).value || '')"
PLACES = f'{DIALOG} button[aria-label="{t("manager.folder_picker.places")}"]'
MARK = f"{DIALOG} .manager-value-state"
MARK_ICON = f"((document.querySelector('{MARK}') || {{}}).innerText || '').trim()"
SELECTION = ("(() => { const el = document.querySelector(%s);"
             " return {from: el.selectionStart, to: el.selectionEnd, length: el.value.length,"
             " focused: document.activeElement === el}; })()")
MENU_ITEMS = ".q-menu .manager-menu-item"
MENU_TEXTS = (f"[...document.querySelectorAll('{MENU_ITEMS}')]"
              ".map(el => el.querySelector('.nicegui-column > div').innerText)")
BUTTONS = ("[...document.querySelectorAll('" + DIALOG + " button')]"
           ".map(b => b.innerText.replace(/^[a-z_]+\\s*/, '').trim())")


class _Page:
    def __init__(self, port: int) -> None:
        self.port = port

    def manager_url(self, path: str = "/") -> str:
        return f"http://127.0.0.1:{self.port}{path}"


def serve(port: int) -> None:
    """A button that opens the picker over a folder holding one folder, and another that
    opens it over a bare function that lists but cannot make."""
    from nicegui import app, ui

    from common.media import folder_browse
    from manager.kit import api, appearance, folder_picker

    api.local_base_url = lambda: "http://127.0.0.1:9"
    app.add_static_files(appearance.BASE_MOUNT, str(appearance.BASE_CSS.parent))
    base = Path(os.environ["VPINFE_CONFIG_DIR"]) / "browse"
    (base / "Existing").mkdir(parents=True, exist_ok=True)
    (base / "Existing" / "settings.ini").write_text("[a]\n")
    (base / "Existing" / "notes.txt").write_text("notes\n")
    (base / "Existing" / ".hidden.ini").write_text("[a]\n")
    for number in range(MANY):
        (base / "Many" / f"Folder {number:03}").mkdir(parents=True, exist_ok=True)
    heard: dict = {"picked": "unasked", "base": str(base), "home": str(Path.home())}

    class Folders:
        def folders(self, path: str, kind: str = "dir", suffixes: tuple = (),
                    hidden: bool = False) -> dict:
            return folder_browse.listing(path, kind, tuple(suffixes), hidden)

        def make_folder(self, parent: str, name: str) -> dict:
            return folder_browse.make_folder(parent, name)

    def only_listing(path: str, kind: str = "dir", suffixes: tuple = (),
                     hidden: bool = False) -> dict:
        return folder_browse.listing(path, kind, tuple(suffixes), hidden)

    @ui.page("/")
    def page() -> None:
        appearance.apply_mode()

        async def open_with(lister, start: Path = base, **wants) -> None:
            heard["picked"] = "open"
            heard["picked"] = await folder_picker.pick_path(lister, str(start), **wants)

        ui.button("Make", on_click=lambda: open_with(Folders().folders)).props("id=make")
        ui.button("Bare", on_click=lambda: open_with(only_listing)).props("id=bare")
        ui.button("File", on_click=lambda: open_with(
            only_listing, base / "Existing", kind="file", suffixes=(".ini",))).props("id=file")
        ui.button("Many", on_click=lambda: open_with(only_listing, base / "Many")).props(
            "id=many")

    @app.get("/heard")
    def said() -> dict:
        return {**heard, "made": (base / TYPED).is_dir(),
                "default_left": (base / t("manager.folder_picker.new_folder_name")).exists()}

    ui.run(port=port, show=False, reload=False, title="Folder picker drive")


class FolderPickerDrive(unittest.TestCase):
    seen: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        cls.seen = drive_page("tests.theming.test_folder_picker_drive", cls._drive)

    @classmethod
    async def _drive(cls, binary: str, port: int) -> dict:
        seen: dict = {}
        async with BrowserSession(binary) as browser:
            walk = ManagerWalk(browser, _Page(port))
            await walk.visit("/")

            async def click_text(selector: str, text: str) -> None:
                await browser.click(selector, nth=int(await browser.wait_for(
                    "(() => { const i = [...document.querySelectorAll("
                    f"{json.dumps(selector)})].findIndex(el => el.innerText.includes("
                    f"{json.dumps(text)})); return i >= 0 ? i + 1 : 0; }})()")) - 1)

            async def type_path(text: str) -> None:
                """Into the path field with its text replaced, as a person who has clicked
                into it and selected all."""
                await browser.click(FIELD)
                await browser.evaluate(f"document.querySelector({json.dumps(FIELD)}).select()")
                if text:
                    await browser.send("Input.insertText", {"text": text})
                else:
                    await browser.evaluate("document.execCommand('delete')")

            await walk.act(lambda: browser.click("#bare"), until=SHOWS % json.dumps(DIALOG))
            seen["bare_buttons"] = await browser.evaluate(BUTTONS)
            seen["bare_aside"] = await browser.evaluate(SHOWS % json.dumps(ASIDE))
            seen["few_height"] = await browser.evaluate(HEIGHT)
            await walk.act(lambda: click_text(f"{DIALOG} button", t("word.cancel")),
                           until=GONE % json.dumps(DIALOG))

            await walk.act(lambda: browser.click("#many"), until=SHOWS % json.dumps(DIALOG))
            seen["many_height"] = await browser.evaluate(HEIGHT)
            seen["many_clipped"] = await browser.evaluate(CLIPPED)
            seen["many_rows"] = await browser.evaluate(
                f"document.querySelectorAll('{DIALOG} .manager-folder-list .manager-source-row')"
                ".length")
            await walk.act(lambda: click_text(f"{DIALOG} button", t("word.cancel")),
                           until=GONE % json.dumps(DIALOG))

            await walk.act(lambda: browser.click("#make"), until=SHOWS % json.dumps(ASIDE))
            seen["buttons"] = await browser.evaluate(BUTTONS)
            seen["aside"] = await browser.evaluate(
                "document.querySelector(" + json.dumps(ASIDE) + ").innerText")
            await walk.act(lambda: browser.click(ASIDE), until=SHOWS % json.dumps(NAMING))
            seen["named"] = await browser.evaluate(
                "(() => { const el = document.querySelector(" + json.dumps(NAMING) + ");"
                " return {value: el.value, focused: document.activeElement === el,"
                " from: el.selectionStart, to: el.selectionEnd}; })()")
            await browser.wait_for(
                "document.activeElement === document.querySelector(" + json.dumps(NAMING) + ")")
            await browser.send("Input.insertText", {"text": TYPED})
            await walk.act(lambda: browser.press("Enter", "Enter"),
                           until=GONE % json.dumps(NAMING))
            await browser.wait_for(PATH_SHOWN + f".endsWith({json.dumps(TYPED)})")
            seen["inside"] = await browser.evaluate(PATH_SHOWN)
            await walk.act(lambda: click_text(f"{DIALOG} button", t(
                "manager.folder_picker.accept")), until=GONE % json.dumps(DIALOG))
            seen["heard"] = _heard(port)

            # The path field: what it holds, selecting on focus, typing and pasting.
            await walk.act(lambda: browser.click("#make"), until=SHOWS % json.dumps(DIALOG))
            seen["field_start"] = await browser.evaluate(PATH_SHOWN)
            await browser.click(FIELD)
            await browser.wait_for(SELECTION % json.dumps(FIELD) + ".focused")
            seen["selected_on_focus"] = await browser.evaluate(SELECTION % json.dumps(FIELD))
            existing = str(Path(seen["heard"]["base"]) / "Existing")
            await browser.send("Input.insertText", {"text": existing})
            await browser.wait_for(MARK_ICON + " !== ''")
            seen["mark_folder"] = await browser.evaluate(MARK_ICON)
            await walk.act(lambda: browser.press("Enter", "Enter"),
                           until=PATH_SHOWN + f" === {json.dumps(existing)} && " + MARK_ICON +
                           " === ''")
            seen["typed_folder"] = await browser.evaluate(PATH_SHOWN)

            # A path that is not there says so on the mark, and Escape puts the path back.
            await type_path(existing + "/Nowhere")
            await browser.wait_for(MARK_ICON + " !== ''")
            seen["mark_missing"] = await browser.evaluate(MARK_ICON)
            await browser.press("Escape", "Escape")
            await browser.wait_for(PATH_SHOWN + f" === {json.dumps(existing)}")
            seen["escaped"] = await browser.evaluate(PATH_SHOWN)
            seen["still_open"] = await browser.evaluate(SHOWS % json.dumps(DIALOG))

            # A quoted path, as Windows' Copy as path gives it, and a bare name.
            await type_path('"' + seen["heard"]["base"] + '"')
            await walk.act(lambda: browser.press("Enter", "Enter"),
                           until=PATH_SHOWN + f" === {json.dumps(seen['heard']['base'])}")
            seen["pasted_quoted"] = await browser.evaluate(PATH_SHOWN)
            await type_path("Existing")
            await walk.act(lambda: browser.press("Enter", "Enter"),
                           until=PATH_SHOWN + f" === {json.dumps(existing)}")
            seen["bare_name"] = await browser.evaluate(PATH_SHOWN)

            # Places.
            await walk.act(lambda: browser.click(PLACES), until=SHOWS % json.dumps(MENU_ITEMS))
            seen["menu"] = await browser.evaluate(MENU_TEXTS)
            seen["menu_rows"] = await browser.evaluate(
                f"[...document.querySelectorAll('{MENU_ITEMS}')].map(el => ["
                "el.querySelector('.manager-menu-mark').innerText,"
                " (el.querySelector('.manager-menu-sub') || {}).innerText || ''])")
            seen["menu_on_here"] = await browser.evaluate(
                f"[...document.querySelectorAll('{MENU_ITEMS}')]"
                ".filter(el => el.classList.contains('manager-menu-on')).length")
            await walk.act(lambda: click_text(MENU_ITEMS, t("word.home")),
                           until=GONE % json.dumps(".q-menu"))
            await browser.wait_for(PATH_SHOWN + f" === {json.dumps(seen['heard']['home'])}")
            seen["home_path"] = await browser.evaluate(PATH_SHOWN)
            await walk.act(lambda: browser.click(PLACES), until=SHOWS % json.dumps(MENU_ITEMS))
            seen["menu_on_home"] = await browser.evaluate(
                f"[...document.querySelectorAll('{MENU_ITEMS}')]"
                ".filter(el => el.classList.contains('manager-menu-on'))"
                ".map(el => el.querySelector('.nicegui-column > div').innerText)")
            await browser.press("Escape", "Escape")
            await walk.act(lambda: click_text(f"{DIALOG} button", t("word.cancel")),
                           until=GONE % json.dumps(DIALOG))

            # A file picker: a typed file path, a refused kind, and Enter on the chosen file.
            ini = str(Path(seen["heard"]["base"]) / "Existing" / "settings.ini")
            await walk.act(lambda: browser.click("#file"), until=SHOWS % json.dumps(DIALOG))
            seen["file_start"] = await browser.evaluate(PATH_SHOWN)
            await type_path(str(Path(seen["heard"]["base"]) / "Existing" / "notes.txt"))
            await browser.wait_for(MARK_ICON + " !== ''")
            seen["mark_wrong_suffix"] = await browser.evaluate(MARK_ICON)
            await type_path("")
            await browser.wait_for(MARK_ICON + " === ''")
            seen["mark_empty"] = await browser.evaluate(MARK_ICON)
            await type_path(str(Path(seen["heard"]["base"]) / "Existing" / ".hidden.ini"))
            await browser.wait_for(MARK_ICON + " === 'cancel'")
            seen["mark_unlisted"] = await browser.evaluate(MARK_ICON)
            await type_path(ini)
            await browser.wait_for(MARK_ICON + " === 'check_circle'")
            seen["mark_file"] = await browser.evaluate(MARK_ICON)
            await browser.press("Enter", "Enter")
            await browser.wait_for(MARK_ICON + " === ''")
            await walk.drawn()
            seen["file_chosen"] = await browser.evaluate(PATH_SHOWN)
            await walk.act(lambda: browser.press("Enter", "Enter"),
                           until=GONE % json.dumps(DIALOG))
            seen["file_picked"] = _heard(port)["picked"]
        return seen

    def test_the_picker_that_cannot_make_one_does_not_offer_it(self) -> None:
        self.assertFalse(self.seen["bare_aside"])
        self.assertNotIn(t("manager.folder_picker.new_folder"), self.seen["bare_buttons"])

    def test_the_dialog_is_as_tall_for_a_folder_with_two_entries_as_for_one_with_eighty(
            self) -> None:
        self.assertGreater(self.seen["many_rows"], 10, "the full folder was not listed")
        self.assertEqual(self.seen["few_height"], self.seen["many_height"])

    def test_the_buttons_of_a_full_folder_are_inside_the_window(self) -> None:
        self.assertLessEqual(self.seen["many_clipped"], 0)

    def test_new_folder_is_offered_beside_the_answer(self) -> None:
        self.assertEqual(self.seen["aside"].split()[-2:],
                         t("manager.folder_picker.new_folder").split())

    def test_the_row_is_named_and_selected_for_typing(self) -> None:
        named = self.seen["named"]
        self.assertEqual(named["value"], t("manager.folder_picker.new_folder_name"))
        self.assertTrue(named["focused"])
        self.assertEqual((named["from"], named["to"]), (0, len(named["value"])))

    def test_typing_replaces_the_name_and_enter_makes_the_folder(self) -> None:
        self.assertTrue(self.seen["heard"]["made"])
        self.assertFalse(self.seen["heard"]["default_left"])

    def test_the_picker_goes_into_the_folder_it_made(self) -> None:
        self.assertTrue(self.seen["inside"].endswith(TYPED))
        self.assertEqual(self.seen["heard"]["picked"],
                         str(Path(self.seen["heard"]["base"]) / TYPED))

    def test_the_field_holds_the_folder_you_are_in(self) -> None:
        self.assertEqual(self.seen["field_start"], self.seen["heard"]["base"])

    def test_clicking_into_the_field_selects_the_whole_path(self) -> None:
        picked = self.seen["selected_on_focus"]
        self.assertTrue(picked["focused"])
        self.assertEqual((picked["from"], picked["to"]), (0, picked["length"]))

    def test_a_typed_folder_gets_a_check_and_enter_opens_it(self) -> None:
        self.assertEqual(self.seen["mark_folder"], "check_circle")
        self.assertEqual(self.seen["typed_folder"],
                         str(Path(self.seen["heard"]["base"]) / "Existing"))

    def test_a_path_that_is_not_there_gets_a_cross_and_escape_puts_the_path_back(self) -> None:
        self.assertEqual(self.seen["mark_missing"], "cancel")
        self.assertEqual(self.seen["escaped"], str(Path(self.seen["heard"]["base"]) / "Existing"))
        self.assertTrue(self.seen["still_open"], "Escape closed the dialog with a path typed")

    def test_a_pasted_path_in_quotes_is_taken_without_them(self) -> None:
        self.assertEqual(self.seen["pasted_quoted"], self.seen["heard"]["base"])

    def test_a_bare_name_is_looked_for_in_the_folder_you_are_in(self) -> None:
        self.assertEqual(self.seen["bare_name"], str(Path(self.seen["heard"]["base"]) / "Existing"))

    def test_places_lists_each_place_once_with_no_folders_above(self) -> None:
        menu = self.seen["menu"]
        self.assertEqual(len(menu), len(set(menu)))
        self.assertTrue(any(t("word.home") in one for one in menu))
        self.assertNotIn(Path(self.seen["heard"]["base"]).name, menu)
        self.assertFalse(any(sub for _icon, sub in self.seen["menu_rows"]))

    def test_places_wear_their_icon(self) -> None:
        rows = dict(zip(self.seen["menu"], self.seen["menu_rows"], strict=True))
        self.assertEqual(rows[t("word.home")][0], "home")
        self.assertEqual(rows["/"][0], "storage")

    def test_a_folder_that_is_no_place_leaves_every_place_unticked(self) -> None:
        self.assertEqual(self.seen["menu_on_here"], 0)

    def test_choosing_a_place_lists_it_and_ticks_it(self) -> None:
        self.assertEqual(self.seen["home_path"], self.seen["heard"]["home"])
        self.assertEqual(self.seen["menu_on_home"], [t("word.home")])

    def test_a_file_of_the_wrong_kind_is_refused_on_the_mark(self) -> None:
        self.assertEqual(self.seen["mark_wrong_suffix"], "cancel")
        self.assertEqual(self.seen["mark_file"], "check_circle")

    def test_a_file_the_list_does_not_offer_is_refused_and_an_empty_field_has_no_mark(self) -> None:
        self.assertEqual(self.seen["mark_unlisted"], "cancel")
        self.assertEqual(self.seen["mark_empty"], "")

    def test_a_file_path_opens_its_folder_with_the_file_chosen_and_enter_takes_it(self) -> None:
        ini = str(Path(self.seen["heard"]["base"]) / "Existing" / "settings.ini")
        folder = str(Path(self.seen["heard"]["base"]) / "Existing")
        self.assertEqual(self.seen["file_start"], folder)
        self.assertEqual(self.seen["file_chosen"], ini)
        self.assertEqual(self.seen["file_picked"], ini)


if __name__ == "__main__":
    if sys.argv[1:2] == ["serve"]:
        exit_with_parent()
        serve(int(sys.argv[2]))
    else:
        unittest.main()

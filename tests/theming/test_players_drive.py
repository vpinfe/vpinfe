"""Frontend › Players, driven as a person would: add a player, rename them, change their
initials, put them up and take them down, choose a VPinPlay user id, share it through the
consent dialog, show and save its card, read what they played, and remove them with the
card offered first. The owner takes that account from its card. Guests join by initials
and by a card file, and sign out together.

Slow: boots a real instance and a real browser. VPinPlay talks to a stand-in served here.
"""

from __future__ import annotations

import asyncio
import io
import json
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from PIL import Image

from tests.support import pictures, vpinplay_stub
from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import game_info, write_game
from tests.support.live_instance import LiveInstance
from tests.support.manager_walk import ManagerWalk

USER_ID = "player-one"
CARD_FILE = f"vpinplay-{USER_ID}.svg"

SHOWN = "(el => el.getClientRects().length > 0)"
DIALOG = ".q-dialog"
FOOTER = f"{DIALOG} .manager-dialog-footer .q-btn"
# What a control says: its words, or its icon's name where it has none.
SAYS = ("(el => { const copy = el.cloneNode(true);"
        " copy.querySelectorAll('.q-icon, .material-icons').forEach(i => i.remove());"
        " const icon = el.querySelector('.q-icon, .material-icons');"
        " return copy.textContent.trim() || (icon ? icon.textContent.trim() : ''); })")
# The index of the first shown element under `%s` that says `%s`, or -1.
AT = (f"(() => [...document.querySelectorAll(%s)].findIndex(el => {SHOWN}(el)"
      f" && {SAYS}(el) === %s))()")
TEXT = "(el => el ? el.innerText.trim().replace(/\\s+/g, ' ') : null)(document.querySelector(%s))"
WORK = ".manager-section-work"
RAIL = ".manager-section-row"
ACTS = f"{WORK} .manager-slot-actions .q-btn"
SHOWN_ACTS = f"[...document.querySelectorAll('{ACTS}')].filter({SHOWN})"
TITLE = ("(el => el ? el.innerText.trim() : null)"
         "(document.querySelector('.manager-panel-header .manager-workbench-title'))")
RAIL_NAMES = (f"[...document.querySelectorAll('{RAIL}')].filter({SHOWN})"
              ".map(el => el.innerText.trim().split('\\n')[0])")
# A dialog that has finished opening: Quasar scales it in, and a click on the way lands
# somewhere else.
DIALOG_UP = ("[...document.querySelectorAll('.q-dialog .q-card')].some(card =>"
             " getComputedStyle(card.closest('.q-dialog__inner')).transform === 'none'"
             " && card.getBoundingClientRect().width > 0)")
DIALOG_TEXT = ("(() => { const box = [...document.querySelectorAll('.q-dialog')].pop();"
               " return box ? box.innerText.trim() : null; })()")


NAME_LINES = ".manager-cell-identifier .manager-cell-lines"
CIRCLE = ".manager-cell-identifier .manager-cell-art-box--circle"
PERSON = f"{WORK} .manager-slot-art--person"
NOTICE = ".q-notification"


def _cell(player_id: str, part: str) -> str:
    return f".ag-row[row-id={json.dumps(player_id)}] {part}"


def _centre(data: bytes) -> tuple[int, int, int]:
    """The color at the middle of a picture."""
    with Image.open(io.BytesIO(data)) as shown:
        rgb = shown.convert("RGB")
        return cast(tuple[int, int, int], rgb.getpixel((rgb.width // 2, rgb.height // 2)))


def _text(selector: str) -> str:
    return TEXT % json.dumps(selector)


class PlayersDrive(unittest.TestCase):
    seen: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        if not chromium_path():
            raise unittest.SkipTest("no Chromium on this machine")
        server = vpinplay_stub.start()
        try:
            with TemporaryDirectory() as tmp, TemporaryDirectory() as saved:
                write_game(Path(tmp), "Alpha", info=game_info(
                    "Alpha", vps_id="", game_id="alpha",
                    Info={"Manufacturer": "Bally", "Year": "1992"}))
                instance = LiveInstance(Path(tmp))
                settings = instance.config_dir / "extension_settings"
                settings.mkdir(parents=True, exist_ok=True)
                (settings / "vpinplay.json").write_text(json.dumps(
                    {"endpoint": vpinplay_stub.endpoint(server),
                     "sync_on_exit": "false"}), encoding="utf-8")
                with instance:
                    cls.seen = asyncio.run(cls._drive(instance, Path(saved)))
        finally:
            vpinplay_stub.stop(server)

    @classmethod
    async def _drive(cls, instance: LiveInstance, saved: Path) -> dict:
        seen: dict = {}
        instance.wait_for_api()
        owner = instance.api("/api/v1/players")["players"][0]["id"]

        def roster() -> dict[str, dict]:
            return {one["id"]: one for one in instance.api("/api/v1/players")["players"]}

        def named(name: str) -> str:
            return next(key for key, one in roster().items() if one["name"] == name)

        async with BrowserSession(chromium_path() or "") as browser:
            await browser.send("Emulation.setDeviceMetricsOverride",
                               {"width": 1600, "height": 1000, "deviceScaleFactor": 1,
                                "mobile": False})
            await browser.send("Page.setDownloadBehavior",
                               {"behavior": "allow", "downloadPath": str(saved)})
            walk = ManagerWalk(browser, instance)

            async def press(selector: str, says: str) -> None:
                found = AT % (json.dumps(selector), json.dumps(says))
                nth = await browser.wait_for(f"(at => at >= 0 ? at + 1 : 0)({found})")
                await browser.click(selector, nth=int(nth) - 1)

            async def type_into(selector: str, nth: int, text: str) -> None:
                await browser.click(selector, nth=nth)
                await browser.evaluate(
                    f"document.querySelectorAll({json.dumps(selector)})[{nth}].select()")
                await browser.send("Input.insertText", {"text": text})
                await browser.evaluate("document.activeElement.blur()")

            async def open_player(player_id: str) -> None:
                # Once a redraw has landed on its own row, or the landing takes it back.
                await walk.drawn()
                await browser.click(_cell(player_id, ".ag-cell[col-id=initials]"))
                held = roster()[player_id]
                await browser.wait_for(
                    f"{TITLE} === {json.dumps(held['name'] or held['initials'] or 'No name')}")

            async def open_section(name: str) -> None:
                await press(RAIL, name)
                await browser.wait_for(
                    f"[...document.querySelectorAll('{RAIL}.manager-section-on')]"
                    f".some(el => el.innerText.trim().startsWith({json.dumps(name)}))")

            async def pick(selector: str, path: Path) -> None:
                document = await browser.send("DOM.getDocument", {"depth": -1, "pierce": True})
                picker = await browser.send("DOM.querySelector", {
                    "nodeId": document["root"]["nodeId"], "selector": selector})
                await browser.send("DOM.setFileInputFiles",
                                   {"nodeId": picker["nodeId"], "files": [str(path)]})

            async def use_picked(path: Path) -> None:
                """Offer `path` to the picture dialog and press its verb."""
                await pick(f"{DIALOG} .q-uploader input[type=file]", path)
                verb = f"[...document.querySelectorAll('{FOOTER}')].pop()"
                await browser.wait_for(
                    f"(b => !!b && !b.disabled && !b.classList.contains('disabled'))({verb})")
                await browser.evaluate(f"{verb}.click()")

            async def add(which: str) -> None:
                await walk.drawn()
                await press(".manager-grid-bar .q-btn", "add")
                await press(".q-menu .q-item", which)
                await browser.wait_for(DIALOG_UP)

            await walk.visit("/manager?view=players")
            seen["purpose"] = await browser.wait_for(_text(".manager-page-purpose"))
            owner_name = _text(_cell(owner, NAME_LINES))
            seen["alone"] = await browser.wait_for(owner_name)

            # A key with no `claimed` marker resolves once against the stub; this id
            # is free, so it draws as state 2.
            urllib.request.urlopen(urllib.request.Request(
                instance.manager_url(f"/api/v1/players/{owner}/accounts/vpinplay"),
                method="PUT", data=json.dumps({"values": {
                    "user_id": "old-key-holder", "key": "k" * 64}}).encode(),
                headers={"Content-Type": "application/json"}), timeout=10).close()
            for _ in range(100):
                if not instance.api(
                        f"/api/v1/players/{owner}/accounts/vpinplay")["claimed"]:
                    break
                await asyncio.sleep(0.05)
            await open_player(owner)
            await open_section("VPinPlay")
            seen["resolved_state2"] = await browser.wait_for(
                f"(said => said && said.includes('old-key-holder') ? said : null)"
                f"({_text(WORK)})")
            seen["resolved_state2_acts"] = await browser.evaluate(f"{SHOWN_ACTS}.map({SAYS})")
            await open_section("Details")

            # A kept player, and what the dialog refuses.
            await add("Add Player")
            await type_into(f"{DIALOG} input", 0, "Jordan")
            await type_into(f"{DIALOG} input", 1, "ab")
            await press(FOOTER, "Add")
            seen["refused"] = await browser.wait_for(
                _text(f"{DIALOG} .q-field--error .q-field__messages"))
            await type_into(f"{DIALOG} input", 1, "abc")
            await press(FOOTER, "Add")
            await browser.wait_for(f"{TITLE} === 'Jordan'")
            jordan = named("Jordan")
            jordan_name = _text(_cell(jordan, NAME_LINES))
            seen["added"] = roster()[jordan]
            seen["jordan_row"] = await browser.wait_for(jordan_name)
            seen["owner_row"] = await browser.wait_for(owner_name)

            # Renamed and given other initials in the panel, written as each is left.
            await type_into(f"{WORK} input", 0, "Sam")
            await browser.wait_for(f"{TITLE} === 'Sam'")
            await type_into(f"{WORK} input", 1, "xyz")
            await browser.wait_for(
                f"{_text(_cell(jordan, '.ag-cell[col-id=initials]'))} === 'XYZ'")
            seen["renamed"] = roster()[jordan]

            # A picture: added, replaced and undone, removed and undone.
            red, blue = saved / "red.png", saved / "blue.png"
            red.write_bytes(pictures.png(80, 60, pictures.RED))
            blue.write_bytes(pictures.png(80, 60, pictures.BLUE))
            in_row = json.dumps(_cell(jordan, f"{CIRCLE} img"))

            def stored() -> tuple[int, int, int] | None:
                try:
                    with urllib.request.urlopen(instance.manager_url(
                            f"/api/v1/players/{jordan}/picture"), timeout=10) as held:
                        return _centre(held.read())
                except urllib.error.HTTPError:
                    return None

            async def stored_is(color: tuple[int, int, int] | None) -> bool:
                for _ in range(100):
                    now = stored()
                    if (now is None and color is None) or (
                            now is not None and color is not None
                            and all(abs(a - b) < 16 for a, b in zip(now, color, strict=True))):
                        return True
                    await asyncio.sleep(0.05)
                return False

            async def noticed(said: str) -> None:
                await browser.wait_for(
                    f"[...document.querySelectorAll('{NOTICE}')]"
                    f".some(n => n.innerText.includes({json.dumps(said)}))")

            async def undo_notice(said: str) -> None:
                await browser.evaluate(
                    f"[...document.querySelectorAll('{NOTICE}')]"
                    f".find(n => n.innerText.includes({json.dumps(said)}))"
                    ".querySelector('.q-btn').click()")

            seen["row_before"] = await browser.wait_for(_text(_cell(jordan, CIRCLE)))
            seen["slot_before"] = await browser.wait_for(_text(PERSON))
            seen["initials_px"] = await browser.evaluate(
                f"(el => el ? getComputedStyle(el).fontSize : null)(document.querySelector("
                f"{json.dumps(_cell(jordan, '.manager-circle-initials'))}))")
            seen["owner_lines_h"] = await browser.evaluate(
                f"(el => el ? el.getBoundingClientRect().height : null)(document.querySelector("
                f"{json.dumps(_cell(owner, NAME_LINES + ' > span'))}))")
            seen["owner_row_h"] = await browser.evaluate(
                f"(el => el ? el.getBoundingClientRect().height : null)(document.querySelector("
                f"{json.dumps(_cell(owner, '.manager-cell-identifier'))}))")
            seen["acts_before"] = await browser.evaluate(f"{SHOWN_ACTS}.map({SAYS})")
            await press(ACTS, "Add")
            await browser.wait_for(DIALOG_UP)
            await use_picked(red)
            await browser.wait_for(f"!document.querySelector('{DIALOG}')")
            await browser.wait_for(f"!!document.querySelector({in_row})")
            await browser.wait_for(f"!!document.querySelector('{PERSON} img')")
            await noticed("Picture added")
            seen["added_red"] = await stored_is(pictures.RED)
            seen["acts_after"] = await browser.evaluate(f"{SHOWN_ACTS}.map({SAYS})")
            seen["row_circle"] = await browser.evaluate(
                f"(el => el ? [getComputedStyle(el).borderRadius, el.getBoundingClientRect()"
                f".width, el.getBoundingClientRect().height] : null)("
                f"document.querySelector({json.dumps(_cell(jordan, CIRCLE))}))")

            await press(ACTS, "Replace")
            await browser.wait_for(DIALOG_UP)
            await use_picked(blue)
            await browser.wait_for(f"!document.querySelector('{DIALOG}')")
            await noticed("Picture replaced")
            seen["replaced_blue"] = await stored_is(pictures.BLUE)
            await undo_notice("Picture replaced")
            seen["undone_to_red"] = await stored_is(pictures.RED)
            await browser.wait_for(
                f"(img => !!img && img.src.includes('v={roster()[jordan]['picture_version']}'))"
                f"(document.querySelector('{PERSON} img'))")

            await press(ACTS, "Remove")
            await noticed("Picture removed")
            await browser.wait_for(f"!document.querySelector({in_row})")
            seen["removed_picture"] = roster()[jordan]["picture_version"]
            seen["acts_removed"] = await browser.evaluate(f"{SHOWN_ACTS}.map({SAYS})")
            await undo_notice("Picture removed")
            await browser.wait_for(f"!!document.querySelector({in_row})")
            seen["removal_undone"] = await stored_is(pictures.RED)
            await browser.wait_for(
                f"(img => !!img && img.src.includes('v={roster()[jordan]['picture_version']}'))"
                f"(document.querySelector('{PERSON} img'))")

            # Up, and down again.
            await browser.click(f"{WORK} .q-toggle")
            await browser.wait_for(f"{jordan_name}.includes('Up')")
            seen["up"] = {key: one["up"] for key, one in roster().items()}
            await browser.click(f"{WORK} .q-toggle")
            await browser.wait_for(f"!{jordan_name}.includes('Up')")
            seen["down"] = {key: one["up"] for key, one in roster().items()}

            # A guest with the same initials: both rows say so.
            await add("Add Guest")
            seen["guest_dialog"] = await browser.wait_for(DIALOG_TEXT)
            await type_into(f"{DIALOG} input:not([type=file])", 0, "xyz")
            await press(FOOTER, "Add")
            await browser.wait_for(f"{TITLE} === 'XYZ'")
            seen["guest_slot"] = await browser.wait_for(_text(PERSON))
            seen["guest_acts"] = await browser.evaluate(f"{SHOWN_ACTS}.map({SAYS})")
            guest = next(key for key, one in roster().items() if one["guest"])
            seen["guest_row"] = await browser.wait_for(
                _text(_cell(guest, NAME_LINES)))
            seen["shared"] = [await browser.wait_for(
                f"(said => said && said.includes('Same') ? said : null)"
                f"({_text(_cell(one, '.ag-cell[col-id=initials]'))})")
                for one in (jordan, guest)]
            chip = _cell(jordan, ".ag-cell[col-id=initials] .manager-tier--warn")
            seen["shared_tip"] = await browser.evaluate(
                f"document.querySelector({json.dumps(chip)}).title")

            # A VPinPlay account on Jordan, through all three states.
            await open_player(jordan)
            seen["rail"] = await browser.evaluate(RAIL_NAMES)
            await open_section("VPinPlay")
            seen["state1"] = await browser.wait_for(_text(WORK))
            seen["state1_acts"] = await browser.evaluate(f"{SHOWN_ACTS}.map({SAYS})")

            # Choose a user id: checked live, Available once typed, no key made.
            await press(ACTS, "Choose a User ID")
            await browser.wait_for(DIALOG_UP)
            seen["choose_title"] = await browser.wait_for(DIALOG_TEXT)
            seen["choose_input"] = await browser.evaluate(
                f"(box => box && ['spellcheck', 'autocorrect', 'autocapitalize', 'autocomplete']"
                f".map(name => box.getAttribute(name)))(document.querySelector('{DIALOG} input'))")
            await type_into(f"{DIALOG} input", 0, USER_ID)
            seen["choose_checked"] = await browser.wait_for(
                f"(said => said && said.includes('Available') ? said : null)({DIALOG_TEXT})")
            await press(FOOTER, "Choose")
            await browser.wait_for(f"!document.querySelector('{DIALOG}')")

            seen["state2"] = await browser.wait_for(
                f"(said => said && said.includes({json.dumps(USER_ID)}) ? said : null)"
                f"({_text(WORK)})")
            seen["state2_acts"] = await browser.evaluate(f"{SHOWN_ACTS}.map({SAYS})")

            # Share, while unclaimed: the consent dialog, then claimed.
            await browser.click(f"{WORK} .q-toggle")
            await browser.wait_for(DIALOG_UP)
            seen["consent"] = await browser.wait_for(DIALOG_TEXT)
            await press(FOOTER, "Share")
            await browser.wait_for(f"!document.querySelector('{DIALOG}')")

            seen["state3"] = await browser.wait_for(
                f"(said => said && said.includes('Your Page') ? said : null)"
                f"({_text(WORK)})")
            seen["state3_acts"] = await browser.evaluate(f"{SHOWN_ACTS}.map({SAYS})")

            # The card: hidden until Show is pressed, then large in the media viewer.
            await browser.click(f"{WORK} .manager-source-thumb")
            await browser.wait_for(DIALOG_UP)
            seen["viewer_has_image"] = await browser.evaluate(
                f"!!document.querySelector('{DIALOG} img')")
            await press(f"{DIALOG} .q-btn", "close")
            await browser.wait_for(f"!document.querySelector('{DIALOG}')")

            await press(ACTS, "Save Card")
            seen["saved"] = await _saved(saved / CARD_FILE)

            # What Jordan played, which the owner's panel has no section for.
            records = instance.config_dir / "player_records"
            records.mkdir(exist_ok=True)
            (records / f"{jordan}.json").write_text(json.dumps({
                "schema": 1, "player": jordan, "games": {"alpha": {
                    "play_count": 3, "play_time_seconds": 1500,
                    "last_played": "2026-09-01T20:00:00+00:00", "rating": 4,
                    "best_score": {"rom": "alpha", "section": "GRAND CHAMPION", "rank": 1,
                                   "initials": "XYZ", "score": 1234560}}}}),
                encoding="utf-8")
            await open_section("Plays")
            seen["plays"] = await browser.wait_for(_text(f"{WORK} .manager-member-row"))

            # A guest joining with the card Jordan's account makes.
            card = saved / "joining.svg"
            with urllib.request.urlopen(instance.manager_url(
                    f"/api/v1/players/{jordan}/accounts/vpinplay/card"), timeout=10) as held:
                card.write_bytes(held.read())
            await add("Add Guest")
            await pick(f"{DIALOG} .q-uploader input[type=file]", card)
            await browser.wait_for(f"{TITLE} === {json.dumps(USER_ID)}")
            carded = named(USER_ID)
            seen["carded"] = roster()[carded]
            seen["carded_share"] = instance.api(
                f"/api/v1/players/{carded}/accounts/vpinplay")["share"]

            await open_player(owner)
            seen["owner_rail"] = await browser.evaluate(RAIL_NAMES)

            # The owner takes that account from its card, and is asked the second time,
            # when the account already holds a key.
            await open_section("VPinPlay")
            await pick(f"{WORK} .q-uploader input[type=file]", saved / CARD_FILE)
            await browser.wait_for(f"{_text(WORK)}.includes('Your Page')")
            seen["owner_account"] = instance.api(
                f"/api/v1/players/{owner}/accounts/vpinplay")
            # Initials, so a card can be made of what is held and Save Card is offered.
            urllib.request.urlopen(urllib.request.Request(
                instance.manager_url(f"/api/v1/players/{owner}"), method="PATCH",
                data=json.dumps({"initials": "OWN"}).encode(),
                headers={"Content-Type": "application/json"}), timeout=10).close()
            await open_section("Details")
            await open_section("VPinPlay")
            await walk.drawn()
            await pick(f"{WORK} .q-uploader input[type=file]", saved / CARD_FILE)
            await browser.wait_for(DIALOG_UP)
            seen["use_again"] = await browser.wait_for(DIALOG_TEXT)
            await press(FOOTER, "Use a Card")
            await browser.wait_for(f"!document.querySelector('{DIALOG}')")

            # Removing Jordan offers the card first.
            await open_player(jordan)
            await press(".manager-panel-header .q-btn", "more_vert")
            await press(".q-menu .q-item", "Remove")
            await browser.wait_for(DIALOG_UP)
            seen["remove"] = await browser.wait_for(DIALOG_TEXT)
            (saved / CARD_FILE).unlink()
            await press(FOOTER, "Save Card")
            seen["saved_again"] = await _saved(saved / CARD_FILE)
            await press(FOOTER, "Remove")
            await browser.wait_for(
                f"!document.querySelector({json.dumps(_cell(jordan, '.ag-cell'))})")
            seen["removed"] = jordan not in roster()

            # Every guest at once.
            await walk.drawn()
            await press(".manager-grid-bar .q-btn", "remove")
            await browser.wait_for(DIALOG_UP)
            seen["sign_out"] = await browser.wait_for(DIALOG_TEXT)
            await press(FOOTER, "Sign Out")
            await browser.wait_for(
                f"!document.querySelector({json.dumps(_cell(guest, '.ag-cell'))})")
            seen["after"] = list(roster())
            seen["page_log"] = [line for line in browser.page_log if "error" in line.lower()]
        return seen

    def test_the_page_says_the_two_rules(self) -> None:
        self.assertEqual(self.seen["purpose"],
                         "Pick who is up. A score goes to the player whose "
                         "initials match.")

    def test_a_household_of_one_shows_no_up(self) -> None:
        self.assertEqual(self.seen["alone"], "No name Owner")

    def test_a_key_with_no_marker_resolves_to_state_two_when_available(self) -> None:
        said = self.seen["resolved_state2"]
        self.assertIn("Not on VPinPlay until you share", said)
        self.assertIn("old-key-holder", said)
        self.assertEqual(self.seen["resolved_state2_acts"],
                         ["Change User ID", "Use a Card"])

    def test_initials_that_are_not_three_are_refused_in_the_dialog(self) -> None:
        self.assertEqual(self.seen["refused"], "Initials are three characters")

    def test_a_player_added_is_kept_and_not_up(self) -> None:
        added = self.seen["added"]
        self.assertEqual((added["name"], added["initials"], added["owner"], added["up"]),
                         ("Jordan", "ABC", False, False))
        self.assertEqual(self.seen["jordan_row"], "Jordan")
        self.assertEqual(self.seen["owner_row"], "No name Owner Up")

    def test_the_panel_renames_and_changes_initials(self) -> None:
        self.assertEqual((self.seen["renamed"]["name"], self.seen["renamed"]["initials"]),
                         ("Sam", "XYZ"))

    def test_a_picture_is_added_in_the_row_and_in_details(self) -> None:
        self.assertEqual(self.seen["row_before"], "XYZ")
        self.assertEqual(self.seen["slot_before"], "XYZ")
        self.assertEqual(self.seen["acts_before"], ["Add"])
        self.assertTrue(self.seen["added_red"])
        self.assertEqual(self.seen["acts_after"], ["Replace", "Remove"])

    def test_the_initials_fit_the_circle_and_the_name_keeps_one_line(self) -> None:
        self.assertEqual(self.seen["initials_px"], "13px")
        self.assertLess(self.seen["owner_lines_h"], self.seen["owner_row_h"] * 1.5)

    def test_the_row_draws_the_picture_in_a_circle(self) -> None:
        radius, width, height = self.seen["row_circle"]
        self.assertEqual((width, height), (40, 40))
        self.assertEqual(radius, "50%")

    def test_a_replaced_picture_is_put_back_by_undo(self) -> None:
        self.assertTrue(self.seen["replaced_blue"])
        self.assertTrue(self.seen["undone_to_red"])

    def test_a_removed_picture_goes_and_undo_brings_it_back(self) -> None:
        self.assertIsNone(self.seen["removed_picture"])
        self.assertEqual(self.seen["acts_removed"], ["Add"])
        self.assertTrue(self.seen["removal_undone"])

    def test_a_guest_has_no_picture_to_set(self) -> None:
        self.assertEqual(self.seen["guest_slot"], "XYZ")
        self.assertEqual(self.seen["guest_acts"], [])

    def test_up_goes_on_and_off(self) -> None:
        self.assertTrue(all(self.seen["up"].values()))
        self.assertEqual(sum(self.seen["down"].values()), 1)

    def test_a_guest_is_told_they_are_forgotten_and_joins_up(self) -> None:
        self.assertIn("A guest is forgotten when VPinFE closes", self.seen["guest_dialog"])
        self.assertIn("Use a Card", self.seen["guest_dialog"])
        self.assertIn("Just Initials", self.seen["guest_dialog"])
        self.assertEqual(self.seen["guest_row"], "XYZ Guest Up")

    def test_shared_initials_are_marked_on_both_rows(self) -> None:
        self.assertEqual(self.seen["shared"], ["XYZ Same initials", "XYZ Same initials"])
        self.assertEqual(self.seen["shared_tip"],
                         "Same initials as XYZ - their scores go to neither")

    def test_a_kept_player_s_rail_has_an_account_and_plays(self) -> None:
        self.assertEqual(self.seen["rail"], ["Details", "VPinPlay", "Plays"])

    def test_the_owner_has_no_plays_of_their_own(self) -> None:
        self.assertEqual(self.seen["owner_rail"], ["Details", "VPinPlay"])

    def test_state_one_offers_choosing_and_a_card_only(self) -> None:
        self.assertIn("No VPinPlay account", self.seen["state1"])
        self.assertEqual(self.seen["state1_acts"], ["Choose a User ID", "Use a Card"])

    def test_choosing_checks_live_and_shows_it_lower_cased(self) -> None:
        self.assertIn("Choose a User ID for “VPinPlay”", self.seen["choose_title"])
        self.assertIn("Available", self.seen["choose_checked"])
        self.assertIn(USER_ID, self.seen["choose_checked"])

    def test_the_id_input_is_left_as_typed(self) -> None:
        self.assertEqual(self.seen["choose_input"], ["false", "off", "off", "off"])

    def test_state_two_is_text_not_an_input_with_change_and_a_card(self) -> None:
        self.assertIn("Not on VPinPlay until you share", self.seen["state2"])
        self.assertEqual(self.seen["state2_acts"],
                         ["Change User ID", "Use a Card"])

    def test_share_while_unclaimed_asks_first_and_names_what_becomes_public(self) -> None:
        said = self.seen["consent"]
        self.assertIn(f"Share as “{USER_ID}” on “VPinPlay”?", said)
        self.assertIn("Anyone can see:", said)
        self.assertIn("VPinPlay can't rename or delete an account", said)

    def test_state_three_is_claimed_with_a_page_and_a_card_section(self) -> None:
        said = self.seen["state3"]
        self.assertIn("Your Page", said)
        self.assertIn("Played games, ratings, scores and play times go public on VPinPlay",
                      said)
        self.assertIn("Anyone holding this card can play as you on VPinPlay", said)
        self.assertEqual(self.seen["state3_acts"], ["Save Card", "Use a Card"])

    def test_the_card_stays_hidden_until_shown_large(self) -> None:
        self.assertTrue(self.seen["viewer_has_image"])

    def test_a_card_saved_is_the_card(self) -> None:
        for saved in (self.seen["saved"], self.seen["saved_again"]):
            self.assertIn("VPINPLAY_PAYLOAD", saved)
            self.assertIn(USER_ID, saved)

    def test_plays_show_count_time_when_and_best(self) -> None:
        plays = self.seen["plays"]
        self.assertIn("Alpha", plays)
        self.assertIn("Bally 1992", plays)
        self.assertIn("3 plays · 25 min", plays)
        self.assertIn("Best 1,234,560", plays)

    def test_the_owner_takes_a_claimed_account_from_its_card(self) -> None:
        account = self.seen["owner_account"]
        self.assertEqual((account["user_id"], account["claimed"], account["share"]),
                         (USER_ID, True, False))

    def test_a_card_over_a_held_key_is_asked_about_first(self) -> None:
        said = self.seen["use_again"]
        self.assertIn("Use this card for “OWN”?", said)
        self.assertIn("It replaces the VPinPlay account they hold here", said)
        self.assertIn("Save Card", said)

    def test_a_card_file_joins_a_guest_who_shares(self) -> None:
        self.assertEqual((self.seen["carded"]["guest"], self.seen["carded"]["initials"],
                          self.seen["carded"]["up"]), (True, "XYZ", True))
        self.assertTrue(self.seen["carded_share"])

    def test_removing_offers_the_card_first_and_removes(self) -> None:
        said = self.seen["remove"]
        self.assertIn("Remove “Sam”?", said)
        self.assertIn("Without their card, their VPinPlay account can never be used "
                      "again", said)
        self.assertIn("Save Card", said)
        self.assertTrue(self.seen["removed"])

    def test_sign_out_guests_signs_every_guest_out(self) -> None:
        self.assertIn("Sign out every guest?", self.seen["sign_out"])
        self.assertIn(USER_ID, self.seen["sign_out"])
        self.assertEqual(len(self.seen["after"]), 1)

    def test_nothing_failed_in_the_browser(self) -> None:
        self.assertEqual(self.seen["page_log"], [])


async def _saved(path: Path) -> str:
    """The file a download wrote, once the browser has finished writing it."""
    for _ in range(600):
        if path.exists() and path.stat().st_size and not Path(f"{path}.crdownload").exists():
            return path.read_text(encoding="utf-8")
        await asyncio.sleep(0.05)
    raise TimeoutError(f"never saved: {path}")


if __name__ == "__main__":
    unittest.main()

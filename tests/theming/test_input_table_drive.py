"""Hardware > Input, driven as a person would: a key or a controller button names itself in
the live line, a row listens for the next press until a click lands anywhere else, a press
another row holds asks before it moves, a hold is picked from a chip, and a row goes back to
what it shipped with.

Slow: boots a real instance and a real browser. Chrome has no controller, so one is stood
in for on `navigator.getGamepads`.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from manager.settings.binding_editor import HOLD_AT_MS, RING_AFTER_MS
from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import game_info, write_game
from tests.support.live_instance import LiveInstance
from tests.support.manager_walk import ManagerWalk

PAGE = "/manager?view=settings&page=hardware.input"

# A press or a click that must change nothing is waited out for a round trip to the server
# and the redraw it would cause, which lands well inside this.
NOTHING_FOLLOWS_S = 0.4
# The page reads a controller every 40ms, so a button pressed has been seen by this.
PAD_READ_S = 0.3
# Past the point a ring starts to fill, so one that was going to show has.
RING_SHOWN_BY_S = RING_AFTER_MS / 1000 + 0.35
# Past the length that makes a press a hold.
HELD_PAST_S = HOLD_AT_MS / 1000 + 0.2

# What an element says: its words, without the names of the icons beside them.
SAYS = ("(el => { if (!el) return null; const copy = el.cloneNode(true);"
        " copy.querySelectorAll('.q-icon, .material-icons').forEach(i => i.remove());"
        " return copy.textContent.trim().replace(/\\s+/g, ' '); })")
ROW = ("[...document.querySelectorAll('.manager-binding-row')].find(row =>"
       " row.querySelector('.manager-binding-label').textContent.trim() === %s)")
LABELS = ("[...document.querySelectorAll('.manager-binding-row .manager-binding-label')]"
          ".map(el => el.textContent.trim())")
CHIPS = (f"(row => [...row.querySelectorAll('.manager-binding-field .manager-member-chip')]"
         f".map({SAYS}))({ROW})")
CHIP_AT = ("(() => { const chips = [...document.querySelectorAll('.manager-member-chip')];"
           f" return chips.findIndex(chip => chip.closest('.manager-binding-row') === ({ROW})"
           f" && ({SAYS})(chip) === %s); }})()")
MARK_AT = ("(() => { const marks = [...document.querySelectorAll(%s)];"
           f" return marks.findIndex(mark => mark.closest('.manager-binding-row') === ({ROW}));"
           " })()")
REMOVE_AT = ("(() => { const marks = [...document.querySelectorAll('.manager-chip-remove')];"
             f" return marks.findIndex(mark => mark.closest('.manager-binding-row') === ({ROW})"
             f" && ({SAYS})(mark.closest('.manager-chip-holder')"
             ".querySelector('.manager-member-chip')) === %s); })()")
LINE = ("(el => el ? [...el.children].filter(part => !part.classList.contains('q-icon'))"
        ".map(part => part.textContent.trim()).join(' ') : null)"
        "(document.querySelector('.manager-binding-live'))")
HELP = ("(el => el ? el.textContent.trim().replace(/\\s+/g, ' ') : null)"
        "(document.querySelector('.manager-binding-send-words'))")
LISTENING = "((window.__bindingListen || {}).row || '')"
LISTENING_ROWS = ("[...document.querySelectorAll('.manager-binding-row--listening')]"
                  ".map(row => row.querySelector('.manager-binding-label').textContent.trim())")
RING_IN = f"(row => !!row.querySelector('.manager-binding-ring'))({ROW})"
RINGS = "document.querySelectorAll('.manager-binding-ring').length"
GAP = ("(() => { const label = document.querySelector('.manager-binding-label');"
       " label.scrollIntoView({block: 'center'});"
       " const box = label.getBoundingClientRect();"
       " return {x: box.right + 5, y: box.top + box.height / 2}; })()")
DIALOG_UP = ("[...document.querySelectorAll('.q-dialog .q-card')].some(card =>"
             " getComputedStyle(card.closest('.q-dialog__inner')).transform === 'none'"
             " && card.getBoundingClientRect().width > 0)")
DIALOG_GONE = "document.querySelector('.q-dialog') === null"
# Each piece of text a dialog shows, in order, apart from its icons' names.
DIALOG_TEXT = ("(() => { const card = document.querySelector('.q-dialog .q-card');"
               " if (!card) return null;"
               " const found = document.createTreeWalker(card, NodeFilter.SHOW_TEXT);"
               " const parts = [];"
               " while (found.nextNode()) {"
               "   const text = found.currentNode.textContent.trim();"
               "   const icon = found.currentNode.parentElement.closest('.q-icon');"
               "   if (text && !icon) parts.push(text);"
               " }"
               " return parts.join(' '); })()")
BUTTONS = "[...document.querySelectorAll('.q-dialog .manager-dialog-footer .q-btn')]"
MENU = "document.querySelector('.q-menu')"
LIT = ("[...document.querySelectorAll('.manager-binding-label.manager-binding-lit')]"
       ".map(el => el.innerText.trim())")
PAD = """(() => {
  window.__pad = {index: 0, mapping: 'standard', id: 'Stand-in',
                  buttons: Array.from({length: 17}, () => ({pressed: false}))};
  Object.defineProperty(navigator, 'getGamepads',
                        {configurable: true, value: () => [window.__pad]});
})()"""


def _held(button: int, down: bool) -> str:
    return f"window.__pad.buttons[{button}] = {{pressed: {json.dumps(down)}}}"


class InputTableDrive(unittest.TestCase):
    seen: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        if not chromium_path():
            raise unittest.SkipTest("no Chromium on this machine")
        with TemporaryDirectory() as tmp:
            write_game(Path(tmp), "Alpha", info=game_info("Alpha", vps_id="", game_id="alpha"))
            with LiveInstance(Path(tmp)) as instance:
                cls.seen = asyncio.run(cls._drive(instance))

    @classmethod
    async def _drive(cls, instance: LiveInstance) -> dict:
        instance.wait_for_api()
        seen: dict = {}

        def stored(key: str) -> list[str]:
            value = instance.api("/api/v1/config/settings")["input"][key]
            return value.split(",") if isinstance(value, str) else list(value)

        async with BrowserSession(chromium_path() or "") as browser:
            await browser.send("Emulation.setDeviceMetricsOverride",
                               {"width": 1600, "height": 1000, "deviceScaleFactor": 1,
                                "mobile": False})
            walk = ManagerWalk(browser, instance)

            async def row(label: str) -> None:
                nth = await browser.evaluate(f"{LABELS}.indexOf({json.dumps(label)})")
                await browser.click(".manager-binding-row .manager-binding-label", nth=nth)

            async def chip(label: str, words: str) -> None:
                nth = await browser.evaluate(CHIP_AT % (json.dumps(label), json.dumps(words)))
                await browser.click(".manager-member-chip", nth=nth)

            async def mark(selector: str, label: str) -> None:
                nth = await browser.evaluate(MARK_AT % (json.dumps(selector),
                                                        json.dumps(label)))
                await browser.click(selector, nth=nth)

            async def take_off(label: str, words: str) -> None:
                nth = await browser.evaluate(REMOVE_AT % (json.dumps(label), json.dumps(words)))
                await browser.click(".manager-chip-remove", nth=nth)

            async def listening(label: str) -> None:
                await browser.wait_for(
                    f"(row => !!row && row.classList.contains('manager-binding-row--listening')"
                    f" && {LISTENING} === row.dataset.row)({ROW % json.dumps(label)})")

            async def stopped() -> None:
                await browser.wait_for(f"!{LISTENING} && !{LISTENING_ROWS}.length")
                await walk.drawn()

            async def click_at(x: float, y: float) -> None:
                for event in ("mousePressed", "mouseReleased"):
                    await browser.send("Input.dispatchMouseEvent",
                                       {"type": event, "x": x, "y": y, "button": "left",
                                        "clickCount": 1})

            async def key(event: str, code: str) -> None:
                await browser.send("Input.dispatchKeyEvent",
                                   {"type": event, "key": code, "code": code,
                                    "windowsVirtualKeyCode": 0})

            async def answer(button: str) -> None:
                await browser.wait_for(DIALOG_UP)
                nth = await browser.evaluate(
                    f"{BUTTONS}.findIndex(el => ({SAYS})(el) === {json.dumps(button)})")
                await browser.click(".q-dialog .manager-dialog-footer .q-btn", nth=nth)
                await browser.wait_for(DIALOG_GONE)
                await walk.drawn()

            async def enter() -> None:
                await browser.wait_for(DIALOG_UP)
                await browser.press("Enter", "Enter")
                await browser.wait_for(DIALOG_GONE)
                await walk.drawn()

            async def ignored_press() -> dict:
                """A key pressed where it is not a press to report, and what the line says
                before and after."""
                before = await browser.evaluate(LINE)
                await browser.press("x", "KeyX")
                await asyncio.sleep(NOTHING_FOLLOWS_S)
                return {"before": before, "after": await browser.evaluate(LINE),
                        "lit": await browser.evaluate(LIT)}

            await walk.visit(PAGE)
            await browser.wait_for(f"{LABELS}.length > 5")
            await browser.evaluate(PAD)
            seen["headings"] = await browser.evaluate(
                "[...document.querySelectorAll('.manager-bindings .manager-fact-heading')]"
                ".map(el => el.textContent.trim())")
            seen["labels"] = await browser.evaluate(LABELS)
            seen["shipped"] = {label: await browser.evaluate(CHIPS % json.dumps(label))
                               for label in seen["labels"]}
            seen["idle"] = await browser.evaluate(LINE)

            await browser.key_down("ArrowLeft", "ArrowLeft")
            await browser.wait_for(f"{LINE}.includes('Previous')")
            seen["heard_key"] = await browser.evaluate(LINE)
            seen["lit_key"] = await browser.evaluate(LIT)
            await browser.key_up("ArrowLeft", "ArrowLeft")
            await browser.wait_for(f"{LINE} === {json.dumps(seen['idle'])}")
            seen["let_go_key"] = {"line": await browser.evaluate(LINE),
                                  "lit": await browser.evaluate(LIT)}

            await browser.key_down("ArrowLeft", "ArrowLeft")
            await browser.key_down("ArrowRight", "ArrowRight")
            await browser.wait_for(f"{LINE}.includes('Next')")
            await browser.key_up("ArrowRight", "ArrowRight")
            await browser.wait_for(f"{LINE}.includes('Previous')")
            seen["still_down"] = await browser.evaluate(LINE)
            await browser.key_up("ArrowLeft", "ArrowLeft")
            await browser.wait_for(f"{LINE} === {json.dumps(seen['idle'])}")

            await browser.key_down("MetaLeft", "MetaLeft")
            await browser.key_down("KeyC", "KeyC")
            await browser.wait_for(f"{LINE}.includes('C')")
            await browser.key_up("MetaLeft", "MetaLeft")
            await browser.wait_for(f"{LINE} === {json.dumps(seen['idle'])}")
            seen["lost_keyup"] = await browser.evaluate(LINE)

            await browser.evaluate(_held(14, True))
            await browser.wait_for(f"{LINE}.includes('D-pad Left')")
            seen["heard_pad"] = await browser.evaluate(LINE)
            await browser.evaluate(_held(14, False))
            await browser.wait_for(f"{LINE} === {json.dumps(seen['idle'])}")
            seen["let_go_pad"] = await browser.evaluate(LINE)

            await browser.evaluate(_held(14, True))
            await browser.wait_for(f"{LINE}.includes('D-pad Left')")
            await browser.evaluate(
                "Object.defineProperty(navigator, 'getGamepads',"
                " {configurable: true, value: () => [null]})")
            await browser.evaluate(
                "window.dispatchEvent(Object.assign(new Event('gamepaddisconnected'),"
                " {gamepad: {index: 0}}))")
            await browser.wait_for(f"{LINE} === {json.dumps(seen['idle'])}")
            seen["unplugged_pad"] = await browser.evaluate(LINE)
            await browser.evaluate(PAD)

            await browser.evaluate("window.__pad.mapping = ''")
            await browser.evaluate(_held(5, True))
            await browser.wait_for(f"{LINE}.includes('Button 5')")
            seen["heard_numbered_pad"] = await browser.evaluate(LINE)
            await browser.evaluate(_held(5, False))
            await browser.evaluate("window.__pad.mapping = 'standard'")

            await browser.key_down("KeyZ", "KeyZ")
            await browser.wait_for(f"{LINE}.startsWith('Z')")
            seen["heard_nothing"] = await browser.evaluate(LINE)
            await browser.key_up("KeyZ", "KeyZ")

            await row("Next")
            await listening("Next")
            seen["prompt"] = await browser.evaluate(LINE)
            seen["prompt_help"] = await browser.evaluate(HELP)
            seen["listening_rows"] = await browser.evaluate(LISTENING_ROWS)
            await browser.click(".manager-bindings .manager-fact-heading")
            await stopped()
            seen["away_heading"] = {"line": await browser.evaluate(LINE),
                                    "stored": stored("next")}

            await row("Next")
            await listening("Next")
            await browser.click(".manager-binding-live")
            await stopped()
            seen["away_live_line"] = await browser.evaluate(LINE)

            await row("Next")
            await listening("Next")
            where = await browser.evaluate(GAP)
            await click_at(where["x"], where["y"])
            await stopped()
            seen["away_gap"] = await browser.evaluate(LINE)

            await row("Next")
            await listening("Next")
            await row("Menu")
            await listening("Menu")
            seen["to_another_row"] = {"listening": await browser.evaluate(LISTENING_ROWS),
                                      "line": await browser.evaluate(LINE)}
            await browser.click(".manager-bindings .manager-fact-heading")
            await stopped()

            await row("Next")
            await listening("Next")
            await chip("Next", "Right Arrow")
            await asyncio.sleep(NOTHING_FOLLOWS_S)
            seen["own_chip"] = {"listening": await browser.evaluate(LISTENING_ROWS),
                                "menu": await browser.evaluate(f"!!{MENU}")}
            await browser.click(".manager-bindings .manager-fact-heading")
            await stopped()

            await row("Next")
            await listening("Next")
            await chip("Back", "B")
            await browser.wait_for(f"!!{MENU}")
            await stopped()
            seen["other_chip"] = {"listening": await browser.evaluate(LISTENING),
                                  "menu": await browser.evaluate(f"!!{MENU}")}
            await browser.click(".manager-bindings .manager-fact-heading")
            await browser.wait_for(f"!{MENU}")
            await walk.drawn()

            await row("Next")
            await listening("Next")
            await browser.press("n", "KeyN")
            await stopped()
            seen["added"] = stored("next")
            seen["next_chips"] = await browser.evaluate(CHIPS % json.dumps("Next"))

            await row("Select")
            await listening("Select")
            await browser.press("Escape", "Escape")
            await browser.wait_for(DIALOG_UP)
            seen["esc_listening"] = await browser.evaluate(LISTENING)
            seen["move_asked"] = await browser.evaluate(DIALOG_TEXT)
            seen["move_buttons"] = await browser.evaluate(f"{BUTTONS}.map({SAYS})")
            seen["dialog_open"] = await ignored_press()
            await answer("Cancel")
            seen["after_declined"] = {"select": stored("select"), "back": stored("back")}

            await row("Select")
            await listening("Select")
            await browser.press("Escape", "Escape")
            await enter()
            seen["moved"] = {"select": stored("select"), "back": stored("back")}
            seen["moved_line"] = await browser.evaluate(LINE)
            seen["back_chips"] = await browser.evaluate(CHIPS % json.dumps("Back"))

            await chip("Quit", "Esc, held 1.5s")
            await browser.wait_for(f"!!{MENU}")
            seen["menu_open"] = await ignored_press()
            seen["hold_stops"] = await browser.evaluate(
                f"[...{MENU}.querySelectorAll('.q-btn')].map({SAYS})")
            nth = await browser.evaluate(
                f"[...{MENU}.querySelectorAll('.q-btn')].findIndex(el => ({SAYS})(el) === '1s')")
            await browser.click(".q-menu .q-btn", nth=nth)
            await browser.wait_for(f"!{MENU}")
            await walk.drawn()
            seen["held_for"] = stored("exit")
            seen["reverted_mark"] = await browser.evaluate(
                f"!!({ROW % json.dumps('Quit')}).querySelector('.manager-revert')")

            await mark(".manager-revert", "Quit")
            await walk.drawn()
            seen["quit_reverted"] = stored("exit")

            await mark(".manager-revert", "Back")
            await answer("Revert")
            seen["back_reverted"] = {"select": stored("select"), "back": stored("back")}

            await chip("Take Picture", "Nothing mapped")
            await listening("Take Picture")
            await browser.press("p", "KeyP")
            await stopped()
            await chip("Take Picture", "P")
            await listening("Take Picture")
            seen["picture_menu"] = await browser.evaluate(f"!!{MENU}")
            await browser.click(".manager-bindings .manager-fact-heading")
            await stopped()

            await row("Take Picture")
            await listening("Take Picture")
            seen["picture_prompt"] = await browser.evaluate(LINE)
            seen["picture_help"] = await browser.evaluate(HELP)
            await browser.evaluate(_held(3, True))
            await asyncio.sleep(RING_SHOWN_BY_S)
            seen["picture_ring"] = await browser.evaluate(RINGS)
            await browser.evaluate(_held(3, False))
            await stopped()
            seen["picture"] = stored("take_picture")
            seen["picture_chips"] = await browser.evaluate(CHIPS % json.dumps("Take Picture"))

            await row("Menu")
            await listening("Menu")
            await key("keyDown", "KeyJ")
            await browser.wait_for(RING_IN % json.dumps("Menu"))
            seen["ring_in_row"] = True
            await key("keyUp", "KeyJ")
            await stopped()
            seen["ring_gone"] = await browser.evaluate(RINGS)
            seen["tapped"] = stored("menu")

            await row("Menu")
            await listening("Menu")
            await key("keyDown", "KeyK")
            await asyncio.sleep(HELD_PAST_S)
            await key("keyUp", "KeyK")
            await stopped()
            seen["kept_down"] = stored("menu")

            await row("Menu")
            await listening("Menu")
            await key("keyDown", "ShiftLeft")
            await browser.evaluate(_held(5, True))
            await asyncio.sleep(PAD_READ_S)
            await browser.evaluate(_held(5, False))
            seen["mixed_listening"] = await browser.evaluate(LISTENING)
            await key("keyUp", "ShiftLeft")
            await browser.wait_for(DIALOG_UP)
            seen["mixed_asked"] = await browser.evaluate(DIALOG_TEXT)
            await answer("Cancel")
            seen["mixed_stored"] = stored("menu")

            await browser.evaluate(_held(10, True))
            await row("Menu")
            await listening("Menu")
            await browser.evaluate(_held(11, True))
            await asyncio.sleep(0.2)
            await browser.evaluate(_held(11, False))
            await stopped()
            await browser.evaluate(_held(10, False))
            seen["already_down"] = stored("menu")

            await row("Next")
            await listening("Next")
            await take_off("Next", "N")
            await walk.drawn()
            seen["removed"] = stored("next")
            seen["listening_after_remove"] = await browser.evaluate(LISTENING_ROWS)
            await browser.click(".manager-bindings .manager-fact-heading")
            await stopped()
        return seen

    def test_the_rows_are_gathered_under_their_headings(self) -> None:
        self.assertEqual(self.seen["headings"], ["Navigation", "Menus", "During Play"])
        self.assertEqual(self.seen["labels"],
                         ["Previous", "Next", "Page Previous", "Page Next", "Select", "Back",
                          "Quit", "Menu", "Collection Menu", "Tutorial", "Take Picture"])

    def test_every_row_reads_what_it_ships_with(self) -> None:
        shipped = self.seen["shipped"]
        self.assertEqual(shipped["Previous"], ["Left Arrow", "Left Shift", "D-pad Left",
                                               "Left Trigger"])
        self.assertEqual(shipped["Back"], ["Esc", "B", "Button 1"])
        self.assertEqual(shipped["Quit"], ["Esc, held 1.5s", "Q", "Button 1, held 1.5s"])
        self.assertEqual(shipped["Take Picture"], ["Nothing mapped"])

    def test_no_word_of_a_controller_is_the_layout_s_name(self) -> None:
        self.assertFalse([chip for chips in self.seen["shipped"].values() for chip in chips
                          if "standard" in chip.lower()])

    def test_the_line_waits_until_something_is_pressed(self) -> None:
        self.assertEqual(self.seen["idle"],
                         "Press anything to see what it does")

    def test_a_key_pressed_names_itself_and_what_it_does(self) -> None:
        self.assertEqual(self.seen["heard_key"], "Left Arrow · Previous")
        self.assertEqual(self.seen["lit_key"], ["Previous"])

    def test_a_key_let_go_leaves_the_line_and_the_row_it_lit(self) -> None:
        self.assertEqual(self.seen["let_go_key"],
                         {"line": "Press anything to see what it does", "lit": []})

    def test_letting_go_of_one_of_two_keys_shows_the_one_still_down(self) -> None:
        self.assertEqual(self.seen["still_down"], "Left Arrow · Previous")

    def test_a_key_whose_release_the_browser_never_sends_goes_when_command_is_let_go(self) -> None:
        self.assertEqual(self.seen["lost_keyup"], "Press anything to see what it does")

    def test_a_pad_unplugged_while_a_button_is_down_leaves_the_line(self) -> None:
        self.assertEqual(self.seen["unplugged_pad"], "Press anything to see what it does")

    def test_a_controller_button_pressed_names_itself_too(self) -> None:
        self.assertEqual(self.seen["heard_pad"], "D-pad Left · Previous")

    def test_a_controller_button_let_go_leaves_the_line(self) -> None:
        self.assertEqual(self.seen["let_go_pad"], "Press anything to see what it does")

    def test_a_numbered_pad_reads_as_a_button_and_fires_what_the_standard_button_does(self) -> None:
        self.assertEqual(self.seen["heard_numbered_pad"],
                         "Button 5 · Page Next")

    def test_a_press_nothing_holds_says_so(self) -> None:
        self.assertEqual(self.seen["heard_nothing"], "Z · Does nothing here")

    def test_a_clicked_row_listens_and_the_line_says_what_for_and_how(self) -> None:
        self.assertEqual(self.seen["prompt"], "Press a button or key for Next")
        self.assertEqual(self.seen["prompt_help"], "Click away to stop")
        self.assertEqual(self.seen["listening_rows"], ["Next"])

    def test_a_row_that_cannot_be_held_says_the_same_beside_the_switch(self) -> None:
        self.assertEqual(self.seen["picture_prompt"], "Press a button or key for Take Picture")
        self.assertEqual(self.seen["picture_help"], "Click away to stop")

    def test_a_click_on_a_heading_a_gap_or_the_line_stops_the_listening(self) -> None:
        idle = "Press anything to see what it does"
        self.assertEqual(self.seen["away_heading"]["line"], idle)
        self.assertEqual(self.seen["away_live_line"], idle)
        self.assertEqual(self.seen["away_gap"], idle)

    def test_stopping_without_a_press_changes_nothing(self) -> None:
        self.assertEqual(self.seen["away_heading"]["stored"],
                         ["key:ArrowRight", "key:ShiftRight", "pad:standard/button:15",
                          "pad:standard/button:7"])

    def test_a_click_on_another_row_moves_the_listening_to_it(self) -> None:
        self.assertEqual(self.seen["to_another_row"]["listening"], ["Menu"])
        self.assertTrue(self.seen["to_another_row"]["line"].startswith(
            "Press a button or key for Menu"))

    def test_a_click_on_a_chip_of_the_listening_row_keeps_listening(self) -> None:
        self.assertEqual(self.seen["own_chip"], {"listening": ["Next"], "menu": False})

    def test_a_click_on_a_chip_of_another_row_stops_and_opens_its_menu(self) -> None:
        self.assertEqual(self.seen["other_chip"], {"listening": "", "menu": True})

    def test_escape_is_a_press_like_any_other(self) -> None:
        self.assertEqual(self.seen["esc_listening"], "")
        self.assertTrue(self.seen["move_asked"].startswith("Move “Esc” to “Select”?"))

    def test_a_press_while_a_question_is_open_leaves_the_line_as_it_was(self) -> None:
        self.assertEqual(self.seen["dialog_open"]["after"], self.seen["dialog_open"]["before"])
        self.assertNotIn("X", self.seen["dialog_open"]["after"])

    def test_a_press_while_a_menu_is_open_leaves_the_line_as_it_was(self) -> None:
        self.assertEqual(self.seen["menu_open"]["after"], self.seen["menu_open"]["before"])
        self.assertNotIn("X", self.seen["menu_open"]["after"])

    def test_a_key_down_long_enough_shows_the_ring_and_is_gone_when_let_go(self) -> None:
        self.assertTrue(self.seen["ring_in_row"])
        self.assertEqual(self.seen["ring_gone"], 0)
        self.assertIn("key:KeyJ", self.seen["tapped"])

    def test_a_key_kept_down_is_stored_as_a_hold(self) -> None:
        held = [one for one in self.seen["kept_down"] if one.startswith("key:KeyK@hold:")]
        self.assertEqual(len(held), 1)
        self.assertGreaterEqual(int(held[0].rpartition(":")[2]), 1000)

    def test_a_row_that_cannot_be_held_shows_no_ring(self) -> None:
        self.assertEqual(self.seen["picture_ring"], 0)

    def test_a_key_held_when_a_pad_button_is_pressed_makes_no_mixed_chord(self) -> None:
        self.assertEqual(self.seen["mixed_listening"], "menu")
        self.assertTrue(self.seen["mixed_asked"].startswith("Move “Left Shift” to “Menu”?"))
        self.assertFalse([one for one in self.seen["mixed_stored"] if one.startswith("chord")])

    def test_a_button_already_down_when_listening_began_is_not_part_of_the_press(self) -> None:
        self.assertIn("pad:standard/button:11", self.seen["already_down"])
        self.assertFalse([one for one in self.seen["already_down"] if "chord" in one])

    def test_a_press_nobody_holds_is_added_to_the_row(self) -> None:
        self.assertEqual(self.seen["added"][-1], "key:KeyN")
        self.assertEqual(self.seen["next_chips"][:2], ["Right Arrow", "Right Shift"])
        self.assertIn("N", self.seen["next_chips"])

    def test_a_press_somebody_holds_asks_to_move_it(self) -> None:
        self.assertEqual(self.seen["move_asked"],
                         "Move “Esc” to “Select”? Esc is mapped to Back. Cancel Move")
        self.assertEqual(self.seen["move_buttons"], ["Cancel", "Move"])

    def test_declining_to_move_changes_nothing(self) -> None:
        self.assertNotIn("key:Escape", self.seen["after_declined"]["select"])
        self.assertIn("key:Escape", self.seen["after_declined"]["back"])

    def test_enter_answers_the_question_to_move(self) -> None:
        self.assertIn("key:Escape", self.seen["moved"]["select"])

    def test_moving_takes_it_from_the_row_that_had_it(self) -> None:
        self.assertIn("key:Escape", self.seen["moved"]["select"])
        self.assertNotIn("key:Escape", self.seen["moved"]["back"])
        self.assertNotIn("Esc", self.seen["back_chips"])
        self.assertEqual(self.seen["moved_line"], "Esc · Moved from Back to Select")

    def test_a_chip_offers_how_long_it_is_held(self) -> None:
        self.assertEqual(self.seen["hold_stops"],
                         ["No hold", "0.5s", "1s", "1.5s", "2s", "3s"])
        self.assertIn("key:Escape@hold:1000", self.seen["held_for"])
        self.assertTrue(self.seen["reverted_mark"])

    def test_a_row_goes_back_to_what_it_shipped_with(self) -> None:
        self.assertIn("key:Escape@hold:1500", self.seen["quit_reverted"])
        self.assertNotIn("key:Escape@hold:1000", self.seen["quit_reverted"])

    def test_reverting_a_row_asks_before_taking_a_press_back_from_another(self) -> None:
        self.assertIn("key:Escape", self.seen["back_reverted"]["back"])
        self.assertNotIn("key:Escape", self.seen["back_reverted"]["select"])

    def test_an_input_heard_during_play_has_no_hold_to_choose(self) -> None:
        self.assertFalse(self.seen["picture_menu"])

    def test_a_controller_button_is_stored_as_the_layout_and_read_by_its_number(
            self) -> None:
        self.assertIn("pad:standard/button:3", self.seen["picture"])
        self.assertIn("Button 3", self.seen["picture_chips"])

    def test_a_binding_is_taken_off_with_its_mark(self) -> None:
        self.assertNotIn("key:KeyN", self.seen["removed"])

    def test_taking_a_binding_off_leaves_its_row_listening(self) -> None:
        self.assertEqual(self.seen["listening_after_remove"], ["Next"])


if __name__ == "__main__":
    unittest.main()

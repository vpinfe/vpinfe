"""The Remote's table controls on a phone: what Now offers while a table runs, the sheet
behind it, a control the table's settings cannot press, and the sound controls that
repeat under a held thumb.

Nothing here presses a control. A press sends a key to whatever is in front on the device
the test runs on, so the one gesture the page makes - a hold on a sound control - has its
events counted and swallowed before they reach the install. The press itself is covered
against the API in tests/api/test_play_controls.py.
"""

from __future__ import annotations

import asyncio
import json
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

from common.i18n import t
from tests.support.browser_session import OVERFLOWING, BrowserSession, chromium_path
from tests.support.library import game_info, write_game
from tests.support.live_instance import LiveInstance
from tests.support.manager_walk import ManagerWalk

LONG = ("Alpha Intentionally Very Long Remake Edition With Extra Words And A Version "
        "Number v2.3.1-final-final-REALLY")
LAUNCH_POLL_S = 0.25
# Longer than a few repeat intervals, so a press still in flight has arrived.
REPEAT_SETTLE_S = 0.5
ACTIONS = ("[...document.querySelectorAll('.remote-action')]"
           ".map(el => (el.querySelector('.block')?.innerText || '').trim())")
NOW_KEYS = ("[...document.querySelectorAll('.remote-key')]"
            ".filter(el => el.getBoundingClientRect().width > 0)"
            ".map(el => el.innerText.trim())")
SHOWN = ("[...document.querySelectorAll('.remote-sheet')]"
         ".filter(el => el.getBoundingClientRect().width > 0)")
MARK = ("(() => { document.querySelectorAll('[data-drive]')"
        ".forEach(el => el.removeAttribute('data-drive'));"
        f" const open = {SHOWN};"
        " if (open.length) open[open.length - 1].setAttribute('data-drive', '1');"
        " return open.length; })()")
SHEET_KEYS = ("[...document.querySelectorAll('[data-drive] .remote-key')]"
              ".filter(el => el.getBoundingClientRect().width > 0)"
              ".map(el => ({text: el.innerText.trim(),"
              " off: el.disabled || el.classList.contains('disabled'),"
              " wide: Math.round(el.getBoundingClientRect().width),"
              " repeats: el.dataset.repeatControl || ''}))")
HEADINGS = ("[...document.querySelectorAll('[data-drive] .manager-card-title')]"
            ".filter(el => el.getBoundingClientRect().height > 0)"
            ".map(el => el.textContent.trim())")
SHEET_TEXT = "document.querySelector('[data-drive]')?.innerText || ''"
PAGE_WIDE = "document.documentElement.scrollWidth - document.documentElement.clientWidth"
SHEET_WIDE = ("(() => { const el = document.querySelector('[data-drive]');"
              " return el ? el.scrollWidth - el.clientWidth : 0; })()")
FOLD = "document.querySelector('[data-drive] .manager-disclosure .q-item')"
HELD = "window.heldControls"
KEPT = "window.keptControls"
CATCH = """
window.heldControls = [];
window.keptControls = [];
const real = window.emitEvent;
window.emitEvent = (name, args) => {
  if (name === 'remote_control') { window.heldControls.push(args.id); return; }
  if (name === 'remote_control_down') { window.keptControls.push('down ' + args.id); return; }
  if (name === 'remote_control_up') { window.keptControls.push('up ' + args.id); return; }
  return real.apply(window, arguments);
};
"""


def _put(instance: LiveInstance, path: str, body: dict) -> None:
    urllib.request.urlopen(urllib.request.Request(
        instance.manager_url(f"/api/v1/{path}"), data=json.dumps(body).encode(),
        method="PUT", headers={"Content-Type": "application/json"}), timeout=10).close()


def _install(program_dir: Path) -> dict[str, str]:
    ini = program_dir / "VPinballX.ini"
    ini.write_text("[Input]\nMapping.Credit1 = Key;30\nMapping.ExtraBall = Joy;3\n")
    program = program_dir / "VPinballX_BGFX"
    program.write_text("#!/bin/sh\nsleep 120\n")
    program.chmod(0o755)
    return {"bin_path": str(program), "ini_path": str(ini)}


class RemoteControlsDrive(unittest.TestCase):
    seen: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        if not chromium_path():
            raise unittest.SkipTest("no Chromium on this machine")
        with TemporaryDirectory() as root_dir, TemporaryDirectory() as program_dir:
            root = Path(root_dir)
            write_game(root, "Alpha", info=game_info(LONG, vps_id="", game_id="alpha-id"))
            install = _install(Path(program_dir))
            with LiveInstance(root) as instance:
                instance.wait_for_api()
                cls.seen = asyncio.run(cls._drive(instance, install))

    @classmethod
    async def _drive(cls, instance: LiveInstance, install: dict[str, str]) -> dict:
        seen: dict = {}
        listed = instance.api("/api/v1/config/launchers")
        first = next(one for one in listed if one["app"] == "vpx" and one["enabled"])
        _put(instance, f"config/launchers/{first['id']}", {**first, "settings": install})
        try:
            instance.post("/api/v1/games/alpha-id/launch", {})
        except urllib.error.HTTPError as refused:
            raise AssertionError(refused.read().decode()) from refused
        deadline = time.time() + 30
        while time.time() < deadline:
            if instance.api("/api/v1/play/state").get("launching"):
                break
            await asyncio.sleep(LAUNCH_POLL_S)
        seen["controls"] = instance.api("/api/v1/play/controls")
        try:
            async with BrowserSession(chromium_path() or "") as browser:
                await browser.as_phone()
                walk = ManagerWalk(browser, instance)
                await walk.visit("/remote?screen=now")
                await browser.wait_for(f"{ACTIONS}.length > 0")
                await walk.drawn()
                seen["actions"] = await browser.evaluate(ACTIONS)
                seen["now keys"] = await browser.evaluate(NOW_KEYS)
                seen["wide"] = await browser.evaluate(PAGE_WIDE)
                seen["leaks in now"] = await browser.evaluate(OVERFLOWING)

                label = t("manager.remote.table_controls")
                if label not in seen["actions"]:
                    raise AssertionError(json.dumps(seen))
                await browser.tap(".remote-action", nth=seen["actions"].index(label))
                await browser.wait_for(f"{MARK} > 0")
                seen["headings"] = await browser.evaluate(HEADINGS)
                seen["keys"] = await browser.evaluate(SHEET_KEYS)
                seen["text"] = await browser.evaluate(SHEET_TEXT)
                seen["sheet wide"] = await browser.evaluate(SHEET_WIDE)
                seen["leaks in the sheet"] = await browser.evaluate(OVERFLOWING)
                await browser.evaluate(CATCH)
                seen["held"] = await cls._hold(browser, 0.7)
                seen["quick"] = await cls._hold(browser, 0.0)
                seen["kept"] = await cls._keep(browser, 1.2)
                seen["kept briefly"] = await cls._keep(browser, 0.0)
                await browser.tap("[data-drive] .manager-disclosure .q-item")
                await browser.wait_for(f"!!{FOLD} && {HEADINGS}.length > 4")
                seen["folded headings"] = await browser.evaluate(HEADINGS)
                seen["folded wide"] = await browser.evaluate(SHEET_WIDE)
                seen["folded keys"] = await browser.evaluate(SHEET_KEYS)
                seen["folded text"] = await browser.evaluate(SHEET_TEXT)
                await browser.escape()
                await browser.wait_for(f"{SHOWN}.length === 0")
        finally:
            instance.post("/api/v1/play/stop", {})
        return seen

    @staticmethod
    async def _keep(browser: BrowserSession, seconds: float) -> list[str] | None:
        """What a thumb of `seconds` on the first control that follows it sent, or None
        when the sheet has none."""
        found = await browser.evaluate(
            "[...document.querySelectorAll('[data-drive] .remote-key')]"
            ".findIndex(el => el.dataset.holdControl && !el.disabled"
            " && !el.classList.contains('disabled'))")
        if found < 0:
            return None
        await browser.evaluate(f"{KEPT}.length = 0")
        await browser.hold("[data-drive] .remote-key", seconds, nth=found)
        await asyncio.sleep(REPEAT_SETTLE_S)
        return await browser.evaluate(KEPT)

    @staticmethod
    async def _hold(browser: BrowserSession, seconds: float) -> list[str] | None:
        """The controls a hold of `seconds` on the first repeating key asked for, or None
        when the sheet has no such key to hold."""
        found = await browser.evaluate(
            "[...document.querySelectorAll('[data-drive] .remote-key')]"
            ".findIndex(el => el.dataset.repeatControl && !el.disabled"
            " && !el.classList.contains('disabled'))")
        if found < 0:
            return None
        await browser.evaluate(f"{HELD}.length = 0")
        await browser.hold("[data-drive] .remote-key", seconds, nth=found)
        await asyncio.sleep(REPEAT_SETTLE_S)
        return await browser.evaluate(HELD)

    def test_now_offers_pause_then_the_sheet_and_quit_last(self) -> None:
        self.assertTrue(any(key.endswith(t("manager.remote.pause"))
                            for key in self.seen["now keys"]), self.seen["now keys"])
        actions = self.seen["actions"]
        self.assertEqual(actions[-2:], [t("manager.remote.table_controls"),
                                        t("manager.remote.quit_table")], actions)

    def test_a_long_name_is_cut_off_where_it_is_and_nothing_scrolls_sideways(self) -> None:
        for where in ("leaks in now", "leaks in the sheet"):
            with self.subTest(where=where):
                self.assertEqual(self.seen[where], [])

    def test_the_page_has_no_sideways_scroll(self) -> None:
        for where in ("wide", "sheet wide", "folded wide"):
            self.assertLessEqual(self.seen[where], 0, where)

    def test_the_sheet_is_grouped_and_has_no_pause(self) -> None:
        self.assertEqual(self.seen["headings"],
                         [t("controls.group.play"), t("controls.group.game"),
                          t("controls.group.coins"), t("controls.group.sound")])
        names = [one["text"] for one in self.seen["keys"]]
        self.assertTrue(any(t("controls.credit_1") in name for name in names), names)
        self.assertFalse(any(t("manager.remote.pause") in name for name in names), names)

    def test_nudge_the_service_menu_service_switches_and_debug_sit_behind_one_fold(self) -> None:
        self.assertEqual(self.seen["folded headings"][-4:],
                         [t("controls.group.nudge"), t("controls.group.menu"),
                          t("controls.group.service"), t("controls.group.debug")])

    def test_nudge_leads_the_fold_and_the_coin_door_and_menu_keys_follow(self) -> None:
        names = [one["text"] for one in self.seen["folded keys"]][len(self.seen["keys"]):]
        self.assertEqual([next((label for label in (t("controls.nudge_left"),
                                                    t("controls.nudge_center"),
                                                    t("controls.nudge_right"))
                                if label in name), "") for name in names[:3]],
                         [t("controls.nudge_left"), t("controls.nudge_center"),
                          t("controls.nudge_right")], names)
        self.assertEqual([next((label for label in (t("controls.coin_door"),
                                                    t("controls.menu_up"))
                                if label in name), "") for name in names[3:5]],
                         [t("controls.coin_door"), t("controls.menu_up")], names)

    def test_a_control_the_settings_cannot_press_is_off_with_its_reason(self) -> None:
        listed = {one["id"]: one for one in self.seen["controls"]["controls"]}
        extra = listed["extra_ball"]
        self.assertFalse(extra["available"])
        self.assertTrue(extra["reason"])
        off = [one["text"] for one in self.seen["keys"] if one["off"]]
        self.assertTrue(any(t("controls.extra_ball") in name for name in off), off)

    def test_the_keys_stay_in_one_even_grid(self) -> None:
        widths = {one["wide"] for one in self.seen["folded keys"]}
        self.assertLessEqual(max(widths) - min(widths), 1, self.seen["folded keys"])

    def test_each_cause_a_control_is_off_is_said_once(self) -> None:
        if not self.seen["controls"]["press"]["available"]:
            self.skipTest("this device cannot send keys, so one message covers all")
        notes = self.seen["controls"]["notes"]
        self.assertTrue(notes, self.seen["controls"])
        for note in notes:
            self.assertEqual(self.seen["folded text"].count(note["text"]), 1, note)

    def test_a_device_that_cannot_send_keys_says_so_once(self) -> None:
        press = self.seen["controls"]["press"]
        if press["available"]:
            self.skipTest("this device can send keys")
        from common.host import key_simulator

        said = key_simulator.words(press["reason"])
        self.assertEqual(self.seen["text"].count(said), 1, self.seen["text"])
        self.assertTrue(all(one["off"] for one in self.seen["keys"]))

    def test_only_the_sound_controls_repeat(self) -> None:
        if not self.seen["controls"]["press"]["available"]:
            self.skipTest("this device cannot send keys, so the sound controls are off")
        repeating = {one["repeats"] for one in self.seen["keys"] if one["repeats"]}
        self.assertEqual(repeating, {"volume_down", "volume_up"})

    def test_a_hold_repeats_and_a_tap_is_one_press(self) -> None:
        if self.seen["controls"].get("press", {}).get("available") is False:
            self.skipTest("this device cannot send keys, so the sound controls are off")
        self.assertIsNotNone(self.seen["held"], "no sound control to hold")
        self.assertTrue(2 <= len(self.seen["held"]) <= 6, self.seen["held"])
        self.assertEqual(len(self.seen["quick"]), 1, self.seen["quick"])

    def test_a_flipper_stays_down_for_as_long_as_the_thumb_is_on_it(self) -> None:
        if self.seen["controls"].get("press", {}).get("available") is False:
            self.skipTest("this device cannot send keys, so the hand controls are off")
        kept = self.seen["kept"]
        if kept is None:
            self.skipTest("no hand control is mapped to a key in this settings file")
        self.assertTrue(kept[0].startswith("down "), kept)
        self.assertTrue(kept[-1].startswith("up "), kept)
        self.assertEqual(sum(one.startswith("up ") for one in kept), 1, kept)
        self.assertGreaterEqual(sum(one.startswith("down ") for one in kept), 2, kept)

    def test_a_tap_on_a_flipper_is_a_press_and_a_release(self) -> None:
        if self.seen["kept"] is None:
            self.skipTest("no hand control is mapped to a key in this settings file")
        briefly = self.seen["kept briefly"]
        self.assertEqual([one.split()[0] for one in briefly], ["down", "up"], briefly)


if __name__ == "__main__":
    unittest.main()

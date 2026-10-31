"""What core does for a contract 2 theme without being asked, and what a theme turns off.

Driven in a real browser: the defaults are decided in the page, from the contract the
backend reports and the config the theme ships, and neither half shows without the other.
"""

from __future__ import annotations

import asyncio
import json
import unittest

from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import TempTree, write_game
from tests.support.live_instance import LiveInstance

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100fd5c9a6b0000000049454e44ae426082")

CAPABILITIES = ("core_audio", "core_layout", "core_preload", "core_launch_indicator",
                "core_attract")

READ = ("JSON.stringify(Object.fromEntries("
        f"{json.dumps(list(CAPABILITIES))}.map(n => [n, vpin.enabled(n)])))")

# What a window's media element holds, by the file it shows.
SHOWN = "document.querySelector('[data-vpin-media] img')?.src || ''"


def _info(name: str) -> dict:
    return {"Info": {"Name": name}, "VPinFE": {}, "User": {}}


@unittest.skipIf(chromium_path() is None, "no Chromium on this machine")
class CoreDefaultsDriveTests(TempTree):
    BOOTS_PER_TEST = "The contract and the theme's config are read once, at boot."

    def setUp(self) -> None:
        super().setUp()
        write_game(self.root, "Alpha Table", info=_info("Alpha Table"),
                   medias={"wheel.png": PNG, "table.png": PNG,
                           "bg.png": PNG, "dmd.png": PNG})

    def _capabilities(self, *, contract_1: bool = False, config: dict | None = None) -> dict:
        async def run(instance: LiveInstance):
            theme = instance.config_dir / "frontend_themes" / instance.theme
            if contract_1:
                manifest = json.loads((theme / "manifest.json").read_text())
                manifest.pop("min_vpinfe")
                (theme / "manifest.json").write_text(json.dumps(manifest))
            if config is not None:
                (theme / "config.json").write_text(json.dumps(config))
            async with BrowserSession(chromium_path() or "") as browser:
                await browser.navigate(instance.theme_url("playfield"))
                await browser.wait_for("document.body.dataset.ready === 'true'", timeout=90.0)
                return json.loads(await browser.evaluate(READ))

        with LiveInstance(self.root) as instance:
            return asyncio.run(run(instance))

    def test_contract_2_has_every_one_on(self) -> None:
        self.assertEqual(self._capabilities(), dict.fromkeys(CAPABILITIES, True))

    def test_contract_1_has_them_off(self) -> None:
        self.assertEqual(self._capabilities(contract_1=True), dict.fromkeys(CAPABILITIES, False))

    def test_a_contract_1_theme_can_turn_one_on(self) -> None:
        found = self._capabilities(contract_1=True, config={"preload": {"enabled": True}})

        self.assertTrue(found["core_preload"])
        self.assertFalse(found["core_audio"])

    def test_a_contract_2_theme_turns_off_only_what_it_names(self) -> None:
        found = self._capabilities(config={"launch_indicator": {"enabled": False},
                                           "attract": {"enabled": False}})

        self.assertEqual(found, {**dict.fromkeys(CAPABILITIES, True),
                                 "core_launch_indicator": False, "core_attract": False})

    # -- the pages core draws -----------------------------------------------

    def _display(self, window: str) -> tuple[str, list]:
        async def run(instance: LiveInstance):
            async with BrowserSession(chromium_path() or "") as playfield:
                await playfield.navigate(instance.theme_url("playfield"))
                await playfield.wait_for("document.body.dataset.ready === 'true'", timeout=90.0)
                async with BrowserSession(chromium_path() or "") as browser:
                    await browser.navigate(
                        instance.url(f"/core/index_{window}.html?window={window}"
                                     f"&wsPort={instance.ports['ws']}"
                                     f"&themeAssetsPort={instance.ports['manager']}"))
                    src = await browser.wait_for(SHOWN, timeout=90.0)
                    return src, list(browser.failed_requests)

        with LiveInstance(self.root, windows=("playfield", window)) as instance:
            return asyncio.run(run(instance))

    def test_core_draws_the_backglass_and_the_score_view(self) -> None:
        for window in ("backglass", "scoreview"):
            with self.subTest(window=window):
                src, failed = self._display(window)
                self.assertTrue(src.endswith(f"/{window}"), src)
                self.assertEqual(failed, [])

    # -- the launch indicator -----------------------------------------------

    def _indicator(self, config: dict | None, *events: str) -> int:
        async def run(instance: LiveInstance):
            theme = instance.config_dir / "frontend_themes" / instance.theme
            if config is not None:
                (theme / "config.json").write_text(json.dumps(config))
            async with BrowserSession(chromium_path() or "") as browser:
                await browser.navigate(instance.theme_url("playfield"))
                await browser.wait_for("document.body.dataset.ready === 'true'", timeout=90.0)
                for event in events:
                    await browser.evaluate(f"vpin.handleEvent({{type: {json.dumps(event)}}})")
                return await browser.evaluate(
                    "document.querySelectorAll('.vpinfe-launch-indicator').length")

        with LiveInstance(self.root) as instance:
            return asyncio.run(run(instance))

    def test_a_launch_shows_the_indicator(self) -> None:
        self.assertEqual(self._indicator(None, "TableLaunching"), 1)

    def test_the_indicator_is_seen_while_the_page_fades_out(self) -> None:
        # The page fades to nothing during a launch, and anything inside it with it, so
        # the indicator counts as shown only when no ancestor's opacity hides it.
        async def run(instance: LiveInstance):
            async with BrowserSession(chromium_path() or "") as browser:
                await browser.navigate(instance.theme_url("playfield"))
                await browser.wait_for("document.body.dataset.ready === 'true'", timeout=90.0)
                await browser.evaluate("vpin.handleEvent({type: 'TableLaunching'})")
                return await browser.wait_for(
                    "(() => { const el = document.querySelector('.vpinfe-launch-indicator');"
                    " if (!el) return false; let seen = 1;"
                    " for (let n = el; n; n = n.parentElement)"
                    " seen *= Number(getComputedStyle(n).opacity);"
                    " return seen > 0.99 && 'seen'; })()", timeout=15.0)

        with LiveInstance(self.root) as instance:
            self.assertEqual(asyncio.run(run(instance)), "seen")

    def test_a_second_launching_message_does_not_add_another(self) -> None:
        self.assertEqual(self._indicator(None, "TableLaunching", "TableLaunching"), 1)

    def test_running_takes_it_down(self) -> None:
        self.assertEqual(self._indicator(None, "TableLaunching", "TableRunning"), 0)

    def test_a_launch_that_never_ran_takes_it_down_when_it_completes(self) -> None:
        self.assertEqual(self._indicator(None, "TableLaunching", "TableLaunchComplete"), 0)

    def test_a_theme_that_draws_its_own_turns_it_off(self) -> None:
        self.assertEqual(
            self._indicator({"launch_indicator": {"enabled": False}}, "TableLaunching"), 0)


if __name__ == "__main__":
    unittest.main()

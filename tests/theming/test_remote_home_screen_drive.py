"""The Remote can be put on a phone's Home Screen: a running install serves the manifest and
the icons it names, and the page itself links them, so Safari opens it without its address
and navigation bars.
"""

from __future__ import annotations

import json
import unittest
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.support.library import game_info, write_game
from tests.support.live_instance import LiveInstance


class RemoteHomeScreenDrive(unittest.TestCase):
    instance: LiveInstance

    @classmethod
    def setUpClass(cls) -> None:
        held = TemporaryDirectory()
        cls.addClassCleanup(held.cleanup)
        write_game(Path(held.name), "Alpha",
                   info=game_info("Alpha", vps_id="", game_id="alpha-id"))
        cls.instance = LiveInstance(Path(held.name))
        cls.instance.__enter__()
        cls.addClassCleanup(cls.instance.__exit__, None, None, None)
        cls.instance.wait_for_api()

    def _get(self, path: str):
        with urllib.request.urlopen(self.instance.url(path), timeout=10) as answer:
            return answer.headers.get_content_type(), answer.read()

    def test_the_manifest_is_served_as_a_manifest_and_names_icons_that_are_served(self) -> None:
        kind, body = self._get("/remote.webmanifest")
        manifest = json.loads(body)

        self.assertEqual(kind, "application/manifest+json")
        self.assertEqual(manifest["display"], "standalone")
        for icon in manifest["icons"]:
            with self.subTest(icon=icon["src"]):
                self.assertEqual(self._get(icon["src"])[0], "image/png")

    def test_the_remote_page_links_the_manifest_and_the_touch_icon(self) -> None:
        _, body = self._get("/remote")
        page = body.decode("utf-8")

        self.assertIn('href="/remote.webmanifest"', page)
        self.assertIn('rel="apple-touch-icon"', page)
        self.assertIn('name="apple-mobile-web-app-capable"', page)


if __name__ == "__main__":
    unittest.main()

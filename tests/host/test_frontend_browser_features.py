"""What the page's feature probe reports, as the host keeps and serves it."""

from __future__ import annotations

import unittest
from typing import Any

from common.host import frontend_browser as fb

from .test_frontend_browser import BUNDLED_145, _Isolated

FEATURES: dict[str, Any] = {
    "webgl": True, "webgl2": True, "webgpu": False, "offscreen_canvas": True,
    "renderer": "ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)",
    "decodes": {"h264": {"supported": True, "smooth": True, "power_efficient": True},
                "vp9": {"supported": False, "smooth": False, "power_efficient": False}},
    "gamepads": 1, "took_ms": 212,
    "screen": {"scale": 2, "width": 1920, "height": 1080, "extended": True},
}


class FeatureTests(_Isolated):
    def test_what_the_page_found_is_kept_and_served(self) -> None:
        fb.record({**BUNDLED_145, "features": FEATURES})
        found = fb.current()["features"]
        self.assertIs(found["webgl2"], True)
        self.assertIs(found["webgpu"], False)
        self.assertEqual(found["decodes"]["h264"],
                         {"supported": True, "smooth": True, "power_efficient": True})
        self.assertEqual(found["screen"]["scale"], 2.0)
        self.assertEqual((found["gamepads"], found["took_ms"]), (1, 212))

    def test_a_report_without_them_has_none_so_it_reads_as_not_tested(self) -> None:
        fb.record(BUNDLED_145)
        self.assertIsNone(fb.current()["features"])

    def test_a_value_that_is_not_what_it_should_be_is_not_kept(self) -> None:
        fb.record({**BUNDLED_145, "features": {
            "webgl": "yes", "renderer": "GPU\x00\n" + "x" * 500, "gamepads": 10**9,
            "decodes": {"h264": "fast", "av1": {"supported": True}},
            "screen": {"scale": 99, "width": "wide"}}})
        found = fb.current()["features"]
        self.assertIsNone(found["webgl"])
        self.assertEqual(len(found["renderer"]), fb.RENDERER_CHARS)
        self.assertNotIn("\x00", found["renderer"])
        self.assertIsNone(found["gamepads"])
        self.assertIsNone(found["decodes"]["h264"])
        self.assertNotIn("av1", found["decodes"])
        self.assertEqual((found["screen"]["scale"], found["screen"]["width"]), (None, None))

    def test_a_renderer_keeps_only_the_characters_a_chip_name_is_made_of(self) -> None:
        fb.record({**BUNDLED_145, "features": {"renderer": "**[click](http://x)** |a| `b` #c _d_"}})
        found = fb.current()["features"]["renderer"]
        for mark in "*[]|`#_":
            self.assertNotIn(mark, found)
        self.assertEqual(fb.record({**BUNDLED_145, "features": {
            "renderer": FEATURES["renderer"]}}) and fb.current()["features"]["renderer"],
            FEATURES["renderer"])

    def test_a_processor_renderer_is_told_from_a_graphics_chip(self) -> None:
        for name in ("ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device), SwiftShader driver)",
                     "llvmpipe (LLVM 15, 256 bits)", "Microsoft Basic Render Driver"):
            self.assertTrue(fb.software_renderer(name), name)
        for name in (FEATURES["renderer"], "NVIDIA GeForce RTX 3060/PCIe/SSE2", ""):
            self.assertFalse(fb.software_renderer(name), name)


if __name__ == "__main__":
    unittest.main()

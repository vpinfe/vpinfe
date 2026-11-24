"""The Input page says, under Take Picture, what stops it on this device."""

from __future__ import annotations

import unittest

from manager.settings import settings
from tests.support.catalogs import served

ABLE = {"available": True, "via": "wtype", "reason": None}
DEAF = {"available": False, "via": "",
        "reason": {"key": "keys.hear.no_input_group", "params": {}, "fix": "user",
                   "remedy": {"key": "keys.hear.join_input_group", "params": {}}}}
MUTE = {"available": False, "via": "",
        "reason": {"key": "keys.send.no_session", "params": {}, "fix": "none",
                   "remedy": None}}
BOUND = {"input": {"take_picture": "key:KeyP,pad:0/button:7"}}
PADS_ONLY = {"input": {"take_picture": "pad:0/button:7"}}


class FindingTests(unittest.TestCase):
    def test_a_device_that_hears_and_presses_says_nothing(self) -> None:
        self.assertEqual(settings.play_findings({"press": ABLE, "hear": ABLE}, BOUND), [])

    def test_keys_not_heard_matter_only_where_a_key_is_bound(self) -> None:
        found = {"press": ABLE, "hear": DEAF}

        self.assertEqual(settings.play_findings(found, {}), [])
        self.assertEqual(settings.play_findings(found, PADS_ONLY), [])
        said = settings.play_findings(found, BOUND)
        self.assertEqual([text for text, _ in said],
                         [served()["keys.hear.not_heard"]])
        self.assertIn(served()["keys.hear.join_input_group"], said[0][1])

    def test_a_device_that_cannot_pause_a_table_says_so_bound_or_not(self) -> None:
        said = settings.play_findings({"press": MUTE, "hear": ABLE}, {})

        self.assertEqual(said, [(served()["keys.send.not_sent"],
                                 served()["keys.send.no_session"])])

    def test_the_findings_go_on_take_picture_and_nowhere_else(self) -> None:
        schema = [{"name": "input", "options": [{"key": "back"}, {"key": "take_picture"}]},
                  {"name": "tools", "options": [{"key": "take_picture"}]}]

        drawn = settings.with_play_input(schema, {"press": MUTE, "hear": ABLE}, {})

        self.assertNotIn("findings", drawn[0]["options"][0])
        self.assertEqual(len(drawn[0]["options"][1]["findings"]), 1)
        self.assertNotIn("findings", drawn[1]["options"][0])


if __name__ == "__main__":
    unittest.main()

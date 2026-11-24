"""A control of the running table is pressed by the app's own word for it.

The key is the app's and is read from its settings file at the press. A route that took a key,
or an id that was one, would let a caller press anything on the device.
"""

from __future__ import annotations

import unittest

from common import apps
from common.host import keys as host_keys
from httpapi import play

# Names a request would carry a key under.
KEY_FIELDS = frozenset({"key", "keys", "code", "key_code", "scancode", "keycode", "chord"})


class ControlsAreNamedNotKeyedTests(unittest.TestCase):
    def test_an_app_that_declares_controls_offers_some(self) -> None:
        declared = [app for app in apps.all_apps() if app.controls is not None]

        self.assertTrue(declared, "at least one app declares controls, or this checks nothing")
        for app in apps.all_apps():
            with self.subTest(app=app.id):
                if app.controls is None:
                    continue
                self.assertTrue(app.controls.offered())

    def test_the_generic_app_declares_none(self) -> None:
        generic = apps.get("generic")

        if generic is None:
            self.fail("the generic app is registered")
        self.assertIsNone(generic.controls)

    def test_no_id_is_a_key_code(self) -> None:
        for app in apps.all_apps():
            if app.controls is None:
                continue
            for one in app.controls.offered():
                with self.subTest(app=app.id, control=one.id):
                    self.assertNotIn(one.id, host_keys.BY_CODE)
                    self.assertRegex(one.id, r"^[a-z][a-z0-9_]*$")

    def test_no_play_route_takes_a_key(self) -> None:
        for route in play.router.routes:
            body = getattr(route, "body_field", None)
            fields = set()
            if body is not None:
                annotation = getattr(body.field_info, "annotation", None)
                for member in getattr(annotation, "__args__", (annotation,)):
                    fields |= set(getattr(member, "model_fields", {}))
            dependant = getattr(route, "dependant", None)
            params = {one.name for one in (*dependant.path_params, *dependant.query_params)
                      } if dependant is not None else set()
            with self.subTest(path=getattr(route, "path", "")):
                self.assertFalse((fields | params) & KEY_FIELDS)

    def test_a_press_route_names_its_control_in_the_path_only(self) -> None:
        paths = {getattr(route, "path", "") for route in play.router.routes}

        self.assertIn("/play/controls/{control_id}", paths)


if __name__ == "__main__":
    unittest.main()

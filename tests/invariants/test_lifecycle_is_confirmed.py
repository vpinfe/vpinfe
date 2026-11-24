"""Nothing we ship asks the backend to quit or power off behind the confirm.

The confirmation is drawn in the browser, because the bridge to a window only goes one
way and the backend cannot raise a dialog. So `requestLifecycle` is where the question
gets asked, and `close_app` / `shutdown_system` - the 2.x spellings, kept so an old theme
keeps working - go straight past it.

The main menu used those, which is the one surface on a cabinet that can shut the machine
down. Confirm Before Exit did nothing from it, and the setting looked broken rather than
bypassed.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from common.runtime import lifecycle

STATIC = Path(__file__).resolve().parent.parent.parent / "frontend" / "static"

# The bridge methods that act without asking. Core defines and dispatches them; anything
# else calling one is going around the confirm.
UNCONFIRMED = ("close_app", "shutdown_system")

REQUEST = re.compile(r"""requestLifecycle\(\s*["'](?P<scope>\w+)["']\s*,"""
                     r"""\s*["'](?P<action>\w+)["']""")


def _pages():
    for path in sorted(STATIC.rglob("*")):
        if path.suffix in {".js", ".html"} and path.name != "vpinfe-core.js":
            yield path


class LifecycleGoesThroughTheConfirmTests(unittest.TestCase):
    def test_no_page_calls_a_bridge_method_that_skips_it(self) -> None:
        offenders = []
        for path in _pages():
            text = path.read_text(encoding="utf-8", errors="ignore")
            for name in UNCONFIRMED:
                for match in re.finditer(rf'\bcall\(\s*["\']{name}["\']', text):
                    line = text[:match.start()].count("\n") + 1
                    offenders.append(f"{path.name}:{line} calls {name} directly")

        self.assertEqual(offenders, [], "use vpin.requestLifecycle(scope, action), which "
                                        "asks first when the user asked to be asked:\n  "
                                        + "\n  ".join(offenders))

    def test_every_request_names_a_pair_the_backend_allows(self) -> None:
        """A pair the backend does not know is logged and dropped, so the button does
        nothing and nothing on screen says why."""
        allowed = set(lifecycle.offered())
        found, offenders = [], []
        for path in sorted(STATIC.rglob("*")):
            if path.suffix not in {".js", ".html"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for match in REQUEST.finditer(text):
                line = text[:match.start()].count("\n") + 1
                found.append(f"{path.name}:{line}")
                if (match["scope"], match["action"]) not in allowed:
                    offenders.append(f"{path.name}:{line} asks to {match['action']} "
                                     f"the {match['scope']}")

        self.assertEqual(offenders, [], "\n  ".join(offenders))
        # The menu's quit and shutdown are two of them; an empty sweep passes and
        # measures nothing.
        self.assertGreaterEqual(sum(name.startswith("mainmenu.js:") for name in found), 2)


if __name__ == "__main__":
    unittest.main()

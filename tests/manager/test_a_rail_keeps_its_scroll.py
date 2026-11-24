"""A rail drawn again with the view lands where the last one was scrolled to."""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest

from manager.kit import panel

# A document with one rail at a time. Choosing a row replaces the rail, as a redraw does.
_DRIVER = """
const snippet = %(snippet)s;
let rail = null, timers = [];
const make = () => {
  const listeners = [];
  return {scrollHeight: 900, clientHeight: 300, scrollTop: 0, listeners,
          addEventListener: (name, fn) => listeners.push([name, fn])};
};
global.window = {};
global.document = {getElementById: () => rail};
global.setTimeout = fn => timers.push(fn);
const draw = () => { rail = make(); eval(snippet); while (timers.length) timers.shift()(); };
const scrollTo = top => { rail.scrollTop = top; rail.listeners.forEach(([, fn]) => fn()); };
const seen = [];
draw(); seen.push(rail.scrollTop);
scrollTo(400);
draw(); seen.push(rail.scrollTop);
process.stdout.write(JSON.stringify(seen));
"""


@unittest.skipUnless(shutil.which("node"), "needs node")
class ARailKeepsItsScrollTests(unittest.TestCase):
    def _run(self, key: str) -> list[int]:
        script = _DRIVER % {"snippet": json.dumps(panel.KEEP_RAIL_SCROLL_JS % {
            "key": json.dumps(key), "id": json.dumps("c7")})}
        done = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                              check=True)
        return json.loads(done.stdout)

    def test_a_new_rail_is_put_back_where_the_old_one_stood(self) -> None:
        self.assertEqual([0, 400], self._run("settings"))


if __name__ == "__main__":
    unittest.main()

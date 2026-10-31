"""A page whose script failed to load reloads once, and then says so instead of looping.

The guard is script the browser runs, so it is run here under Node against a stand-in
for the few browser objects it touches. Skips, naming the reason, when node is missing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest

from common.i18n import t
from manager.kit import script_failed

DRIVE = """
const vm = require('vm');
const SCRIPT = %(script)s;
const NOTE = %(note)s;
const WINDOW = %(window)d;

function page({stored, storage = 'works'}) {
  const listeners = {};
  const store = stored === undefined ? {} : {[NOTE]: String(stored)};
  const made = [];
  const element = (tag) => ({
    tag, className: '', textContent: '', children: [], listeners: {},
    setAttribute() {}, append(...kids) { this.children.push(...kids); },
    addEventListener(name, run) { this.listeners[name] = run; },
  });
  const body = {appendChild(child) { made.push(child); }};
  const sandbox = {
    window: {addEventListener(name, run) { (listeners[name] ||= []).push(run); }},
    location: {href: 'http://vpinfe.test/manager', reloads: 0,
               reload() { this.reloads += 1; }},
    document: {createElement: element, body},
    sessionStorage: storage === 'works' ? {
      getItem: (key) => (key in store ? store[key] : null),
      setItem: (key, value) => { store[key] = value; },
    } : {
      getItem() { throw new Error('blocked'); },
      setItem() { throw new Error('blocked'); },
    },
    URL, String, Number, Date, RegExp,
  };
  vm.runInNewContext(SCRIPT, sandbox);
  const fire = (name, event) => (listeners[name] || []).forEach((run) => run(event));
  return {
    sandbox, store, made,
    rejected: (message) => fire('unhandledrejection', {reason: new TypeError(message)}),
    errored: (tag, address) => fire('error', {target: {tagName: tag, src: address}}),
    leave: () => fire('pagehide', {}),
    seen: () => ({
      reloads: sandbox.location.reloads,
      noted: store[NOTE] !== undefined,
      shown: made.map((box) => ({
        title: box.children[0].textContent,
        detail: box.children[1].textContent,
        button: box.children[2].textContent,
        role: box.className,
      })),
    }),
    click: () => made[0] && made[0].children[2].listeners.click(),
  };
}

const out = {};
let p = page({});
p.rejected('Importing a module script failed.');
out.first = p.seen();
p.rejected('Importing a module script failed.');
out.same_page_again = p.seen();

p = page({stored: Date.now() - 1000});
p.rejected('Importing a module script failed.');
out.repeat = p.seen();
p.click();
out.clicked = p.seen().reloads;

p = page({stored: Date.now() - WINDOW - 1000});
p.rejected('Failed to fetch dynamically imported module: http://x/y.js');
out.long_ago = p.seen();

p = page({storage: 'blocked'});
p.rejected('Importing a module script failed.');
out.no_storage = p.seen();

p = page({});
p.rejected('Something else entirely');
p.errored('IMG', 'http://vpinfe.test/_nicegui/1/x.png');
p.errored('SCRIPT', 'http://vpinfe.test/static/other.js');
out.ignored = p.seen();

p = page({stored: Date.now() - 1000});
p.errored('SCRIPT', 'http://vpinfe.test/_nicegui/3.16.0/components/abc123/link.js');
out.script_element = p.seen();

p = page({});
p.errored('LINK', 'http://vpinfe.test/_nicegui/3.16.0/static/quasar.css');
out.link_element = p.seen();

p = page({});
p.leave();
p.rejected('Importing a module script failed.');
out.leaving = p.seen();

process.stdout.write(JSON.stringify(out));
"""


class ScriptFailed(unittest.TestCase):
    out: dict

    def setUp(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not on PATH, so the reload guard did not run")
        drive = DRIVE % {"script": json.dumps(script_failed.script()),
                         "note": json.dumps(script_failed.NOTE_KEY),
                         "window": script_failed.WINDOW_MS}
        run = subprocess.run([node, "-e", drive], capture_output=True, text=True,
                             timeout=60, check=False)
        self.assertEqual(0, run.returncode, run.stderr)
        self.out = json.loads(run.stdout)

    def test_the_first_failure_reloads_and_says_nothing(self) -> None:
        self.assertEqual({"reloads": 1, "noted": True, "shown": []}, self.out["first"])

    def test_a_page_acts_on_its_first_failure_only(self) -> None:
        self.assertEqual(self.out["first"], self.out["same_page_again"])

    def test_a_failure_right_after_a_reload_stops_and_says_what_failed(self) -> None:
        seen = self.out["repeat"]

        self.assertEqual(0, seen["reloads"])
        self.assertEqual(1, len(seen["shown"]))
        self.assertEqual(
            {"title": t("manager.page.script_failed"),
             "detail": "Importing a module script failed.",
             "button": t("manager.page.reload")},
            {key: seen["shown"][0][key] for key in ("title", "detail", "button")})

    def test_the_reload_button_reloads(self) -> None:
        self.assertEqual(1, self.out["clicked"])

    def test_a_failure_long_after_the_last_reload_reloads_again(self) -> None:
        self.assertEqual((1, []), (self.out["long_ago"]["reloads"],
                                   self.out["long_ago"]["shown"]))

    def test_a_browser_that_keeps_no_note_is_not_sent_round_a_loop(self) -> None:
        seen = self.out["no_storage"]

        self.assertEqual((0, 1), (seen["reloads"], len(seen["shown"])))

    def test_only_a_failed_page_script_counts(self) -> None:
        self.assertEqual({"reloads": 0, "noted": False, "shown": []}, self.out["ignored"])

    def test_a_failed_script_element_names_the_file(self) -> None:
        seen = self.out["script_element"]

        self.assertEqual(0, seen["reloads"])
        self.assertEqual(t("manager.page.script_failed_file", file="link.js"),
                         seen["shown"][0]["detail"])

    def test_a_failure_while_the_page_is_being_left_is_ignored(self) -> None:
        self.assertEqual({"reloads": 0, "noted": False, "shown": []}, self.out["leaving"])

    def test_a_failed_stylesheet_counts_too(self) -> None:
        self.assertEqual(1, self.out["link_element"]["reloads"])


if __name__ == "__main__":
    unittest.main()

"""A date column's window filter: the model it keeps and the rows it passes.

The component is script the browser runs, so it is run here under Node against a stand-in
for the few DOM calls it makes. Skips, naming the reason, when node is missing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest

from manager.kit import grid

STAND_IN = """
const element = () => ({
  children: [], listeners: {}, className: '', textContent: '', checked: false,
  appendChild(child) { this.children.push(child); },
  append(...kids) { this.children.push(...kids); },
  addEventListener(name, run) { this.listeners[name] = run; },
});
globalThis.document = {createElement: element};
globalThis.window = globalThis;
"""

DRIVE = """
%s
%s
const params = {windows: [{value: 7, label: 'Past Week'}, {value: 30, label: 'Past Month'}],
                words: {any: 'Any time'}, getValue: node => node.added,
                filterChangedCallback: () => { globalThis.changed = (globalThis.changed|0) + 1; }};
const filter = new window.HubWindowFilter();
filter.init(params);
const ago = days => {
  const day = new Date(); day.setDate(day.getDate() - days);
  return day.toISOString().slice(0, 10) + 'T00:00:00Z';
};
const passes = days => filter.doesFilterPass({node: {added: ago(days)}});
const out = {};
out.words = filter.radios.map(([days]) => days);
out.inactive = filter.isFilterActive();
out.model_none = filter.getModel();
filter.setModel({days: 7});
out.week = [passes(0), passes(3), passes(10)];
out.model_week = filter.getModel();
out.active = filter.isFilterActive();
filter.setModel({days: 30});
out.month = [passes(10), passes(40)];
filter.radios[1][1].listeners.change();
out.picked = [filter.getModel(), globalThis.changed];
filter.setModel(null);
out.cleared = [filter.getModel(), filter.isFilterActive()];
process.stdout.write(JSON.stringify(out));
"""


class WindowFilter(unittest.TestCase):
    def setUp(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not on PATH, so the window filter did not run")
        run = subprocess.run([node, "-e", DRIVE % (STAND_IN, grid._CHOICE_FILTER_JS)],
                             capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(0, run.returncode, run.stderr)
        self.out = json.loads(run.stdout)

    def test_it_offers_any_time_and_each_window(self) -> None:
        self.assertEqual([0, 7, 30], self.out["words"])

    def test_with_no_window_it_filters_nothing(self) -> None:
        self.assertEqual((False, None), (self.out["inactive"], self.out["model_none"]))

    def test_a_window_passes_the_releases_added_within_that_many_days(self) -> None:
        self.assertEqual([True, True, False], self.out["week"])
        self.assertEqual([True, False], self.out["month"])

    def test_the_model_is_the_window_in_days(self) -> None:
        self.assertEqual(({"days": 7}, True), (self.out["model_week"], self.out["active"]))

    def test_choosing_a_window_reports_the_change(self) -> None:
        self.assertEqual([{"days": 7}, 1], self.out["picked"])

    def test_an_empty_model_clears_it(self) -> None:
        self.assertEqual([None, False], self.out["cleared"])

    def test_the_column_option_hands_the_windows_and_words_to_the_component(self) -> None:
        option = grid.window_filter([("Past Week", 7)])

        self.assertEqual(grid.WINDOW_FILTER, option[":filter"])
        self.assertEqual([{"value": 7, "label": "Past Week"}], option["filterParams"]["windows"])

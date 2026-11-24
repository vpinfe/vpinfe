// An empty library waiting on its folders: every window says so until one answers.

import { test, mock } from "node:test";
import assert from "node:assert/strict";

import { loadCore } from "./support/load-core.js";

function core({ entries = [], waiting = [] } = {}) {
  const { VPinFECore, browser } = loadCore({ windowName: "table" });
  const vpin = new VPinFECore();
  vpin.init();
  const asked = [];
  vpin.call = vpin.callInternal = async (method) => {
    asked.push(method);
    if (method === "get_tables") return JSON.stringify({ entries });
    if (method === "library_waiting") return waiting.shift() ?? [];
    return true;
  };
  return { vpin, root: browser.document.documentElement, asked };
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

test("an empty library covers the window with what it waits on, until one answers", async () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const { vpin, root } = core({ waiting: [["nas.lan"], ["nas.lan"], []] });
    await vpin.getTableData();
    await settle();

    assert.match(root.dataset.vpinfeWaiting, /Waiting for your tables\nnas\.lan/);
    assert.deepEqual([...vpin.libraryWaiting], ["nas.lan"]);

    mock.timers.tick(5000);
    await settle();
    assert.ok(root.dataset.vpinfeWaiting, "still waiting after one more ask");

    mock.timers.tick(5000);
    await settle();
    assert.equal(root.dataset.vpinfeWaiting, undefined);
    assert.deepEqual([...vpin.libraryWaiting], []);
  } finally {
    mock.timers.reset();
  }
});

test("a theme that draws its own still reads what the library waits on", async () => {
  const { vpin, root } = core({ waiting: [["nas.lan"]] });
  vpin._capabilities.core_waiting = false;
  await vpin.getTableData();
  await settle();

  assert.equal(root.dataset.vpinfeWaiting, undefined);
  assert.deepEqual([...vpin.libraryWaiting], ["nas.lan"]);
});

test("a library with games in it never asks", async () => {
  const { vpin, asked } = core({ entries: [{ table: { id: "t1" } }] });
  await vpin.getTableData();
  await settle();

  assert.ok(!asked.includes("library_waiting"));
});

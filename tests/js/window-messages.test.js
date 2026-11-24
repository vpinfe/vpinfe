// Window message types, which shipped half-written once already.
//
// `92dfaa7` and `eaf6662` taught vpinfe-core.js to broadcast both spellings and the
// Python half was never written, so docs/theme.md told themes to listen for names the
// backend did not emit. It reached the cabinet looking healthy because installed themes
// used the 2.x spellings. PAR-24.

import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";

import { fixture, loadCore, newCore, REPO_ROOT } from "./support/load-core.js";
import { codeFor } from "./support/browser.js";

const { context } = loadCore();
const ALIASES = context.MESSAGE_TYPE_ALIASES;
const canonical = context.canonicalMessageType;

// The five message types, in the one spelling that exists. They named a row all along,
// and a row is a table, so the 3.0 Game* spellings were withdrawn along with the alias
// that carried them - see PAR-24.
const MESSAGES = ["TableIndexUpdate", "TableDataChange", "TableLaunching",
                  "TableRunning", "TableLaunchComplete"];

describe("every window message has exactly one spelling", () => {
  test("nothing is aliased, so nothing can arrive under a second name", () => {
    assert.deepEqual(Object.keys(ALIASES), [],
      "an entry here means a message is broadcast twice; the map is empty on purpose");
  });

  test("a message type normalizes to itself", () => {
    for (const name of MESSAGES) {
      assert.equal(canonical(name), name);
    }
  });

  test("an unrelated message type passes through untouched", () => {
    assert.equal(canonical("SomeThemeSpecificMessage"), "SomeThemeSpecificMessage");
  });
});

describe("the Python side broadcasts what the docs promise", () => {
  // The half that was missing once. Asserted against the source because the broadcast
  // crosses a socket this harness does not stand up. With no alias to fall back on, a
  // spelling that drifts here reaches a theme as silence rather than as a second copy.
  const LAUNCH_TRIO = ["TableLaunching", "TableRunning", "TableLaunchComplete"];

  test("play_events emits the spelling the JS side recognizes", () => {
    const events = readFileSync(
      path.join(REPO_ROOT, "frontend", "play_events.py"), "utf8");

    for (const name of LAUNCH_TRIO) {
      assert.ok(events.includes(name),
        `play_events.py does not emit ${name}, so a theme listening for it gets no `
        + "launch events");
    }
  });

  test("the theme doc names what the code emits", () => {
    const doc = readFileSync(path.join(REPO_ROOT, "docs", "theme.md"), "utf8");

    for (const name of MESSAGES) {
      assert.ok(doc.includes(name), `${name} is emitted but not documented`);
    }
  });
});

describe("a data change the backend raised carries the wheel position", () => {
  // The backend has no index to send - the wheel lives in the browser. Every shipped
  // theme assigns message.index to its wheel on this message, so an undefined there
  // sends the player back to nowhere the moment a finished game refreshes the payload.
  //
  // And the number alone is not enough. The refresh re-derives order and membership, so
  // a session that just ended moves its game up a LastRun wheel: what has to survive is
  // the game the player was standing on, not the slot it happened to be in.
  const BUILT = fixture("theme_payload.json").contract1;
  const ROW = { Alpha: BUILT[0], Beta: BUILT[1], Gamma: BUILT[2] };
  const rows = (...names) => JSON.stringify(names.map(name => ROW[name]));

  /** A core holding `before`, handed `after` by the refresh this message triggers. */
  const refreshWith = async (spelling, before, after,
                             { index, at = 0, windowName = "table" } = {}) => {
    const { vpin } = newCore({ windowName });
    let payload = before;
    vpin.call = (method) => Promise.resolve(method === "get_tables" ? payload : null);
    await vpin.getTableData();
    await vpin.handleEvent({ type: "TableIndexUpdate", index: at });

    payload = after;
    const message = index === undefined ? { type: spelling } : { type: spelling, index };
    await vpin.handleEvent(message);
    return { message, vpin };
  };

  test("TableDataChange follows its game when the refresh reorders", async () => {
    const { message, vpin } = await refreshWith(
      "TableDataChange", rows("Alpha", "Beta", "Gamma"), rows("Gamma", "Alpha", "Beta"),
      { at: 1 });

    assert.equal(message.index, 2, "Beta moved to the end, and the wheel went with it");
    assert.equal(vpin.getCurrentTableIndex(), 2);
  });

  test("TableDataChange with an index keeps the one it was sent", async () => {
    // A collection or filter change originates in the browser and knows its own index.
    const { message } = await refreshWith(
      "TableDataChange", rows("Alpha", "Beta"), rows("Alpha", "Beta"), { index: 1, at: 0 });

    assert.equal(message.index, 1);
  });

  test("a game that left the list leaves the wheel where it was", async () => {
    const { message } = await refreshWith(
      "TableDataChange", rows("Alpha", "Beta", "Gamma"), rows("Alpha", "Gamma"), { at: 1 });

    assert.equal(message.index, 1);
  });

  for (const windowName of ["table", "bg", "dmd"]) {
    test(`the ${windowName} window stays inside a list that shrank under it`, async () => {
      const { message, vpin } = await refreshWith(
        "TableDataChange", rows("Alpha", "Beta", "Gamma"), rows("Alpha", "Beta"),
        { at: 2, windowName });

      assert.equal(message.index, 1);
      assert.equal(vpin.getCurrentTableIndex(), 1);
    });
  }

  test("an unrelated message is left alone", async () => {
    const { vpin } = newCore({ windowName: "table" });
    vpin.call = () => Promise.resolve("[]");
    const message = { type: "TableLaunchComplete" };

    await vpin.handleEvent(message);

    assert.equal(message.index, undefined);
  });
});

describe("every index path announces itself the same way", () => {
  // Three paths reached the wheel and only one of them said anything about the move:
  // moveTo sent previous/direction/moving, while paging and restore sent a bare index.
  // So the one path that jumps fourteen positions was the one a theme could not place.
  //
  // Driven through init and a real keypress, because both of the paths under test are
  // private - reaching them any other way would test the test.
  const started = async ({ count = 5, pageIndex = 3, initial = 0 } = {}) => {
    const { VPinFECore, browser } = loadCore({ windowName: "table" });
    const listeners = {};
    browser.window.addEventListener = (type, fn) => { (listeners[type] ||= []).push(fn); };
    const vpin = new VPinFECore();
    const sent = [];
    vpin.call = (method) => {
      if (method === "get_tables") return Promise.resolve("[]");
      if (method === "get_page_index") return Promise.resolve(pageIndex);
      if (method === "get_initial_table_index") return Promise.resolve(initial);
      if (method === "get_theme_contract") return Promise.resolve(1);
      return Promise.resolve(null);
    };
    vpin.init();
    await browser.WebSocket.instances.at(-1).onopen();
    vpin.isController = () => true;
    vpin.frontendInputEnabled = true;
    vpin.tableData = Array.from({ length: count }, () => ({}));
    // Captured after startup so the restore broadcast is separated from the rest.
    vpin.sendMessageToAllWindowsIncSelf = (m) => sent.push(m);
    const press = async (key) => {
      const event = { key, code: codeFor(key), repeat: false, target: null, preventDefault() {} };
      await Promise.all((listeners.keydown || []).map(fn => fn(event)));
    };
    return { vpin, sent, press };
  };

  test("a step says step", async () => {
    const { vpin, sent } = await started();

    vpin.moveBy(1);

    assert.equal(sent[0].reason, "step");
    assert.equal(sent[0].source, "user");
    assert.equal(sent[0].previous, 0);
    assert.equal(sent[0].direction, "next");
  });

  test("a move says which group it landed in", async () => {
    const { vpin, sent } = await started();
    vpin.tableData = [{ group: "A" }, { group: "B" }];
    vpin.groupBy = "letter";

    vpin.moveBy(1);

    assert.equal(sent[0].group, "B");
    assert.equal(sent[0].groupKind, "letter");
  });

  test("an order with no groups says so rather than guessing", async () => {
    const { vpin, sent } = await started();
    vpin.tableData = [{ group: null }, { group: null }];
    vpin.groupBy = "";

    vpin.moveBy(1);

    assert.equal(sent[0].group, "");
    assert.equal(sent[0].groupKind, "");
  });

  test("a page says page, and carries where it came from", async () => {
    const { vpin, sent, press } = await started({ pageIndex: 3 });
    vpin._currentTableIndex = 0;

    await press("PageDown");
    await new Promise(resolve => setTimeout(resolve, 0));

    const paged = sent.find(m => m.reason === "page");
    assert.ok(paged, "paging must announce itself as a page");
    assert.equal(paged.index, 3);
    assert.equal(paged.previous, 0, "a theme cannot tell a jump from a step without this");
  });

  test("previous is captured before the index moves", async () => {
    // The trap that made restore worth routing: it assigned _currentTableIndex before
    // broadcasting, so a naive route through moveTo makes previous report the
    // destination. moveTo defaults previous from the field, so the caller must capture.
    const { vpin, sent } = await started();
    vpin._currentTableIndex = 1;

    vpin.moveTo(4, { previous: vpin._currentTableIndex, reason: "restore" });

    assert.equal(sent[0].reason, "restore");
    assert.equal(sent[0].index, 4);
    assert.equal(sent[0].previous, 1, "previous reported the destination");
  });
});

// Attract mode: core moves the wheel when nobody has, and gives it back when someone does.
//
// The timer logic, on a fake clock. The bridge answering for the person's settings and the
// theme's files, and a key reaching core in a real page, are driven in
// tests/theming/test_attract_drive.py.

import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { loadCore, plain } from "./support/load-core.js";

const IDLE_MS = 1000;
const STEP_MS = 1000;
// A press that was not swallowed acts on the very next frame.
const PRESS_ACTS_WITHIN_MS = 500;

const BRIDGE = {
  get_tables: "[]",
  get_theme_assets_port: 8000,
  get_initial_table_index: 0,
  get_theme_config: {},
  get_keymapping: {},
  get_joymaping: {},
  get_mainmenu_config: {},
  get_monitors: [],
  get_collections: [],
};

// A controller core past its bridge handshake, on a clock the test turns, with the
// keydown listener core installed captured so a press arrives the way the browser sends it.
async function attract(t, { settings = {}, contract = 2, theme = {}, games = 4 } = {}) {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const { VPinFECore, browser } = loadCore({ windowName: "table" });
  const listeners = {};
  browser.window.addEventListener = (type, fn) => { (listeners[type] ||= []).push(fn); };
  const vpin = new VPinFECore();
  vpin.call = async (method) => (method === "get_theme_contract" ? contract
    : method === "get_theme_config" ? theme : BRIDGE[method]);
  const current = { enabled: true, idle_ms: IDLE_MS, step_ms: STEP_MS, order: "in_order",
                    on_return: "restore", audio: false, ...settings };
  vpin.callInternal = async (method) => (method === "get_attract_config" ? current : null);
  vpin.sendMessageToAllWindowsIncSelf = () => {};
  // Three seconds in, core probes what the browser can play; the stub has no video element
  // to ask, and the clock here crosses that mark.
  vpin._playbackProbed = true;
  vpin.init();
  await browser.WebSocket.instances.at(-1).onopen();
  vpin.isController = () => true;
  vpin.frontendInputEnabled = true;
  vpin.tableData = Array.from({ length: games }, (_, i) => ({ id: `g${i}` }));

  const moves = [];
  const was = vpin.moveTo.bind(vpin);
  vpin.moveTo = (index, options = {}) => {
    moves.push({ index, source: options.source || "user", reason: options.reason || "" });
    return was(index, options);
  };
  // Settings are read once at the handshake, before this core was made a controller; ask
  // again the way the Manager does when a setting is saved.
  browser.WebSocket.instances.at(-1).receive({ type: "AttractSettingsChanged" });
  await Promise.resolve();
  await Promise.resolve();

  const press = async (key = "ArrowRight") => {
    const event = { key, code: key, repeat: false, target: null, preventDefault() {} };
    await Promise.all((listeners.keydown || []).map((fn) => fn(event)));
    await Promise.all((listeners.keyup || []).map((fn) => fn({ key, code: key, target: null })));
  };
  // A step at a time: a timer armed while the clock turns is not run by the same turn.
  const tick = (ms) => { for (let left = ms; left > 0; left -= 1) t.mock.timers.tick(1); };
  return { vpin, moves, press, tick };
}

const byAttract = (moves) => moves.filter((move) => move.source === "attract");

describe("attract mode on a fake clock", () => {
  test("nothing moves before the idle time is up", async (t) => {
    const { moves, tick } = await attract(t);
    tick(IDLE_MS - 1);
    assert.equal(moves.length, 0);
  });

  test("in order walks the whole list and wraps", async (t) => {
    const { moves, tick } = await attract(t);
    tick(IDLE_MS);
    tick(STEP_MS * 3);
    assert.deepEqual(plain(byAttract(moves).map((move) => move.index)), [1, 2, 3, 0]);
  });

  test("random visits every other game before repeating one", async (t) => {
    const { moves, tick } = await attract(t, { settings: { order: "random" } });
    tick(IDLE_MS);
    tick(STEP_MS * 2);
    assert.deepEqual(plain(byAttract(moves).map((move) => move.index)).sort(), [1, 2, 3]);
  });

  test("a press puts the wheel back and does nothing else", async (t) => {
    const { vpin, moves, press, tick } = await attract(t);
    tick(IDLE_MS);
    tick(STEP_MS);
    assert.ok(vpin.attractActive);

    await press();
    tick(PRESS_ACTS_WITHIN_MS);

    assert.equal(vpin.attractActive, false);
    assert.equal(vpin._currentTableIndex, 0);
    const byUser = moves.filter((move) => move.source === "user");
    assert.deepEqual(plain(byUser.map((move) => move.reason)), ["restore"]);
  });

  test("a press with attract idle is an ordinary one", async (t) => {
    const { moves, press } = await attract(t);

    await press();

    const byUser = moves.filter((move) => move.source === "user");
    assert.equal(byUser.length, 1);
    assert.notEqual(byUser[0].reason, "restore");
  });

  test("stay leaves the wheel where attract stopped it and swallows the press", async (t) => {
    const { vpin, moves, press, tick } = await attract(t, { settings: { on_return: "stay" } });
    tick(IDLE_MS);
    tick(STEP_MS);
    const at = vpin._currentTableIndex;

    await press();
    tick(PRESS_ACTS_WITHIN_MS);

    assert.equal(vpin.attractActive, false);
    assert.equal(vpin._currentTableIndex, at);
    assert.deepEqual(plain(moves.filter((move) => move.source === "user")), []);
  });

  test("it starts again after the press", async (t) => {
    const { vpin, press, tick } = await attract(t);
    tick(IDLE_MS);
    await press();
    assert.equal(vpin.attractActive, false);

    tick(IDLE_MS);

    assert.ok(vpin.attractActive);
  });

  test("a theme can hold it off and let go", async (t) => {
    const { vpin, moves, tick } = await attract(t);
    vpin.suspendAttract("theme");
    tick(IDLE_MS + STEP_MS * 3);
    assert.equal(moves.length, 0);

    vpin.resumeAttract("theme");
    tick(IDLE_MS);

    assert.ok(vpin.attractActive);
  });

  test("it runs again only when every reason has let go", async (t) => {
    const { vpin, moves, tick } = await attract(t);
    vpin.suspendAttract("a");
    vpin.suspendAttract("b");
    vpin.resumeAttract("a");

    tick(IDLE_MS + STEP_MS * 3);

    assert.equal(moves.length, 0);
    assert.equal(vpin.attractActive, false);

    vpin.resumeAttract("b");
    tick(IDLE_MS);

    assert.ok(vpin.attractActive);
  });

  test("suspending stops the wheel that is moving", async (t) => {
    const { vpin, moves, tick } = await attract(t);
    tick(IDLE_MS);
    tick(STEP_MS);
    assert.ok(vpin.attractActive);
    vpin.suspendAttract("theme");
    const before = moves.length;

    tick(STEP_MS * 5);

    assert.equal(moves.length, before);
    assert.equal(vpin.attractActive, false);
  });

  test("the person's switch turns it off", async (t) => {
    const { moves, tick } = await attract(t, { settings: { enabled: false } });
    tick(IDLE_MS + STEP_MS * 3);
    assert.equal(moves.length, 0);
  });

  test("a contract 1 theme gets none unless it asks", async (t) => {
    const { moves, tick } = await attract(t, { contract: 1 });
    tick(IDLE_MS + STEP_MS * 3);
    assert.equal(moves.length, 0);
  });

  test("a contract 1 theme can ask for it", async (t) => {
    const { moves, tick } = await attract(t, { contract: 1, theme: { attract: { enabled: true } } });
    tick(IDLE_MS);
    assert.ok(byAttract(moves).length > 0);
  });

  test("a contract 2 theme can turn it off for itself", async (t) => {
    const { moves, tick } = await attract(t, { theme: { attract: { enabled: false } } });
    tick(IDLE_MS + STEP_MS * 3);
    assert.equal(moves.length, 0);
  });
});

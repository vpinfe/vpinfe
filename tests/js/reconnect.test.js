// The bridge going away is not the end of the frontend.
//
// A cabinet that sleeps wakes up with its three sockets closed at 1006 and, before this,
// nothing that reconnected them. The windows stayed on screen showing the last game, so
// everything looked right - and no button reached the backend, because every action goes
// out over that socket. The same hole swallowed a backend restart under a running
// frontend.
//
// Found by sleeping the real cabinet, not by a test, which is the reason this file exists.

import { test, describe } from "node:test";
import assert from "node:assert/strict";

import { newCore } from "./support/load-core.js";

const BRIDGE_DEFAULTS = {
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

async function connectedCore() {
  const { vpin, browser } = newCore({ windowName: "playfield" });
  vpin.call = (method) => Promise.resolve(BRIDGE_DEFAULTS[method]);
  vpin.init();
  await browser.WebSocket.instances.at(-1).onopen();
  return { vpin, browser };
}

const sockets = (browser) => browser.WebSocket.instances.length;

describe("reconnecting to the bridge", () => {
  test("a dropped socket is retried", async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    const { vpin, browser } = await connectedCore();
    const before = sockets(browser);

    vpin._ws.onclose();
    t.mock.timers.tick(2000);

    assert.ok(sockets(browser) > before, "the cabinet woke up and nothing reconnected");
  });

  test("it backs off rather than hammering a bridge that is gone", async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    const { vpin, browser } = await connectedCore();

    // Every attempt fails immediately, the way a sleeping backend answers.
    for (let i = 0; i < 4; i++) {
      vpin._ws.onclose();
      t.mock.timers.tick(60000);
    }

    assert.ok(vpin._reconnectDelayMs <= 10000,
              `backoff ran away to ${vpin._reconnectDelayMs}ms`);
    assert.ok(vpin._reconnectDelayMs > 500, "it never backed off at all");
  });

  test("one close schedules one attempt", async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    const { vpin, browser } = await connectedCore();
    const before = sockets(browser);

    vpin._ws.onclose();
    vpin._ws.onclose();
    vpin._ws.onclose();
    t.mock.timers.tick(60000);

    assert.equal(sockets(browser), before + 1, "a close storm opened a socket each time");
  });

  test("a reconnect re-reads the state instead of assuming it survived", async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    const { vpin, browser } = await connectedCore();
    const asked = [];
    vpin.call = (method) => { asked.push(method); return Promise.resolve(BRIDGE_DEFAULTS[method]); };

    vpin._ws.onclose();
    t.mock.timers.tick(2000);
    await browser.WebSocket.instances.at(-1).onopen();

    assert.ok(asked.includes("get_tables"), "came back without re-reading the library");
    assert.ok(asked.includes("get_theme_config"), "came back without re-reading the config");
  });

  test("a successful connect clears the backoff", async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    const { vpin, browser } = await connectedCore();

    vpin._ws.onclose();
    t.mock.timers.tick(2000);
    await browser.WebSocket.instances.at(-1).onopen();

    assert.equal(vpin._reconnectDelayMs, 0,
                 "the next outage would start at the previous ceiling");
  });
});

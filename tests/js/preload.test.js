// Core preloading, judged by how many requests it makes.
//
// The measurement behind this capability: a held key repeats about 30 times a second, and
// Revolution fetched 12 images per step, so a two-second hold asked the browser for
// roughly 700 images that were obsolete before they decoded. The fix is not fetching less
// per step - it is not fetching until the wheel stops. So the assertion that matters is a
// count over a burst, not the contents of one batch.

import { test, describe } from "node:test";
import assert from "node:assert/strict";

import { newCore, fixture } from "./support/load-core.js";

const ROWS = fixture("theme_payload.json").contract1;
const SETTLE_MS = 20;

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

// The fixture is four games, and a burst has to cross more than that: on a library small
// enough to wrap, the already-asked-for set would hide a per-step fetcher behind its own
// deduplication. The folder name is what varies because that, not the full path, is what
// the served URL is built from.
function library(count) {
  const folder = ROWS[0].tableDirName;
  const template = JSON.stringify(ROWS[0]);
  return Array.from({ length: count }, (_unused, index) =>
    JSON.parse(template.replaceAll(folder, `${folder} #${index}`)));
}

/** A core whose theme config came off the bridge, the way a real theme's does. */
async function coreWithConfig(themeConfig, rows = library(60)) {
  const { vpin, browser } = newCore({ windowName: "table" });
  vpin.call = (method) => Promise.resolve(
    method === "get_theme_config" ? themeConfig : BRIDGE_DEFAULTS[method]);
  vpin.init();
  await browser.WebSocket.instances.at(-1).onopen();

  vpin.tableData = rows;
  vpin.themeAssetsPort = 8000;
  vpin._preloadSettleMs = SETTLE_MS;
  browser.Image.requested.length = 0;
  return { vpin, requested: browser.Image.requested };
}

/** One wheel step, by the same call a theme makes. */
function step(vpin, index) {
  vpin.sendMessageToAllWindowsIncSelf({ type: "TableIndexUpdate", index });
}

const settled = () => new Promise((resolve) => setTimeout(resolve, SETTLE_MS * 4));

describe("core preloading", () => {
  test("a burst of steps produces one batch, not one batch per step", async () => {
    const { vpin, requested } = await coreWithConfig({ preload: { enabled: true } });

    for (let index = 0; index < 30; index++) step(vpin, index);
    await settled();

    // Three indices x three kinds. Fetching per step would be ninety.
    assert.ok(requested.length <= 9,
      `a 30-step burst should settle into one batch, asked for ${requested.length}`);
    assert.ok(requested.length > 0, "and it does have to fetch something");
  });

  test("it fetches the selection and both neighbors", async () => {
    const { vpin, requested } = await coreWithConfig({ preload: { enabled: true } });

    step(vpin, 5);
    await settled();

    for (const index of [4, 5, 6]) {
      const url = vpin.getImageURL(index, "playfield");
      assert.ok(requested.includes(url), `index ${index} should have been preloaded`);
    }
  });

  test("it is off unless the theme asks for it", async () => {
    const { vpin, requested } = await coreWithConfig({});

    assert.equal(vpin.enabled("core_preload"), false);
    step(vpin, 5);
    await settled();

    assert.equal(requested.length, 0,
      "a theme that preloads for itself must not get a second set of requests");
  });

  test("it never asks for the missing-media placeholder", async () => {
    // The real fixture, because one of its four games is deliberately bare.
    const { vpin, requested } = await coreWithConfig({ preload: { enabled: true } }, ROWS);

    for (let index = 0; index < ROWS.length; index++) {
      step(vpin, index);
      await settled();
    }

    assert.ok(requested.length > 0, "the fixture does have media to fetch");
    assert.ok(!requested.some((url) => url.includes("file_missing")),
      "the placeholder is already local; fetching it is pure waste");
  });

  test("it does not re-fetch what it has already asked for", async () => {
    const { vpin, requested } = await coreWithConfig({ preload: { enabled: true } });

    step(vpin, 5);
    await settled();
    const first = requested.length;
    step(vpin, 5);
    await settled();

    assert.equal(requested.length, first, "the same neighbors should not be re-requested");
  });

  test("the ends of the library are not walked off", async () => {
    const { vpin } = await coreWithConfig({ preload: { enabled: true } }, ROWS);

    step(vpin, 0);
    await settled();
    step(vpin, ROWS.length - 1);
    await settled();

    // A reach past either end would throw inside the listener and be swallowed, so the
    // assertion is that the selection still tracked rather than that nothing was logged.
    assert.equal(vpin.getCurrentTableIndex(), ROWS.length - 1);
  });

  test("a contract 1 theme names its kinds in its own vocabulary", async () => {
    const { vpin, requested } = await coreWithConfig(
      { preload: { enabled: true, kinds: ["table"] } });

    step(vpin, 5);
    await settled();

    assert.deepEqual([...vpin._preloadKinds], ["playfield"]);
    assert.ok(requested.includes(vpin.getImageURL(5, "playfield")),
      "`table` has to reach the playfield here like it does everywhere else");
  });
});

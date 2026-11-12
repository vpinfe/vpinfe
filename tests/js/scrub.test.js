// Protects: (d) browsing a large library by holding a key leaves every screen, the audio
// and the backend alone until the wheel stops, and then brings them up to date once.
//
// A scrub that never settled would leave the backglass, the sound and the backend on the
// table the hold started from, so what is asserted is the end of it as much as the middle.

import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";

import { loadCore, plain } from "./support/load-core.js";
import { codeFor } from "./support/browser.js";

const stillHeld = [];
afterEach(() => {
  for (const letGo of stillHeld.splice(0)) letGo();
});

const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// The repeat curve and the scrub compressed, so a hold costs a fifth of a second. The
// shape is under test - a delay, a long hold, a settle - not the numbers.
async function started({ scrubAware = false } = {}) {
  const { VPinFECore, browser } = loadCore({ windowName: "table" });
  const listeners = {};
  browser.window.addEventListener = (type, fn) => { (listeners[type] ||= []).push(fn); };
  const vpin = new VPinFECore();
  const everyone = [];
  const toSelf = [];
  vpin.call = (method, ...args) => {
    if (method === "send_event") toSelf.push(plain(args[1]));
    if (method === "send_event_all_windows_incself") everyone.push(plain(args[0]));
    if (method === "get_tables") return Promise.resolve("[]");
    if (method === "get_initial_table_index") return Promise.resolve(0);
    if (method === "get_theme_contract") return Promise.resolve(2);
    return Promise.resolve(null);
  };
  vpin.init();
  await browser.WebSocket.instances.at(-1).onopen();
  vpin.isController = () => true;
  vpin.frontendInputEnabled = true;
  vpin.tableData = Array.from({ length: 400 }, () => ({}));
  Object.assign(vpin, { repeatDelayMs: 5, repeatStartMs: 5, repeatFloorMs: 5,
                        scrubAfterMs: 40, scrubSettleMs: 15, scrubAware });
  const selections = [];
  vpin.onSelection((index) => selections.push(index));
  const fire = (type, key, repeat = false) => {
    const event = { key, code: codeFor(key), repeat, target: null, preventDefault() {} };
    return Promise.all((listeners[type] || []).map((fn) => fn(event)));
  };
  stillHeld.push(() => (listeners.blur || []).forEach((fn) => fn()));
  return {
    vpin, browser, everyone, toSelf, selections,
    down: () => fire("keydown", "ArrowRight"),
    up: () => fire("keyup", "ArrowRight"),
  };
}

describe("a long hold", () => {
  test("announces to every window until the scrub starts, then once at the end", async () => {
    const { vpin, everyone, selections, down, up } = await started();

    await down();
    await pause(30);
    const before = everyone.length;
    await pause(120);
    const during = everyone.length;
    const heard = selections.length;
    await up();
    await pause(60);

    assert.ok(before >= 2, "the first steps of a hold are announced as usual");
    assert.equal(during, everyone.length - 1, "nothing is announced while the scrub runs");
    assert.equal(selections.length, heard + 1, "the selection changes once, at the settle");
    const last = everyone.at(-1);
    assert.equal(last.reason, "settle");
    assert.equal(last.index, vpin.getCurrentTableIndex());
    assert.equal(last.moving, false);
    assert.ok(vpin.getCurrentTableIndex() > before, "the wheel kept travelling");
  });

  test("a press while the settle waits settles where the wheel stopped, then steps", async () => {
    const { vpin, everyone, down, up } = await started();

    await down();
    await pause(120);
    await up();
    const stopped = vpin.getCurrentTableIndex();
    vpin.moveTo(stopped + 1, { direction: "next" });
    await pause(60);

    const settles = everyone.filter((message) => message.reason === "settle");
    assert.equal(settles.length, 1);
    assert.equal(settles[0].index, stopped);
    assert.equal(everyone.at(-1).reason, "step");
    assert.equal(everyone.at(-1).index, stopped + 1);
  });

  test("launching right after the release settles first", async () => {
    const { vpin, everyone, down, up } = await started();

    await down();
    await pause(120);
    await up();
    vpin.launchTable(vpin.getCurrentTableIndex());

    assert.equal(everyone.at(-1).reason, "settle", "windows and audio catch up before the launch");
  });

  test("a theme that draws the wheel small has the pictures ahead and behind fetched on a single step", async () => {
    const { vpin, browser } = await started({ scrubAware: true });
    vpin.getThumbnailURL = (index) => `/media/t${index}/wheel?size=256`;
    browser.Image.requested.length = 0;

    vpin.moveTo(5, { direction: "next" });

    const asked = browser.Image.requested;
    assert.ok(asked.includes("/media/t6/wheel?size=256"), "the next card is on its way");
    assert.ok(asked.includes("/media/t19/wheel?size=256"), "so is the one a second further on");
    assert.ok(asked.includes("/media/t2/wheel?size=256"), "and the one behind, for a reversal");
  });

  test("a theme that does not draw the wheel small has nothing fetched for it", async () => {
    const { vpin, browser } = await started();
    vpin.getThumbnailURL = (index) => `/media/t${index}/wheel?size=256`;
    browser.Image.requested.length = 0;

    vpin.moveTo(5, { direction: "next" });

    assert.equal(browser.Image.requested.length, 0);
  });

  test("a hold let go of before the scrub starts settles nothing", async () => {
    const { everyone, down, up } = await started();

    await down();
    await pause(20);
    await up();
    await pause(60);

    assert.ok(everyone.length > 0);
    assert.ok(everyone.every((message) => message.reason !== "settle"));
  });

  test("a theme that draws the wheel hears every step, in its own window alone", async () => {
    const { vpin, everyone, toSelf, down, up } = await started({ scrubAware: true });

    await down();
    await pause(150);
    const announced = everyone.length;
    await up();
    await pause(60);

    const steps = toSelf.filter((message) => message.scrubbing);
    assert.ok(steps.length >= 3, "a step reaches the wheel while the scrub runs");
    assert.ok(steps.every((message) => message.type === "TableIndexUpdate" && message.moving));
    assert.equal(steps.at(-1).index, vpin.getCurrentTableIndex());
    assert.equal(announced, everyone.length - 1, "the other windows still hear only the settle");
  });

  test("a theme that does not hear the steps is sent nothing for them", async () => {
    const { toSelf, down, up } = await started();

    await down();
    await pause(150);
    await up();
    await pause(60);

    assert.equal(toSelf.filter((message) => message.scrubbing).length, 0);
  });

  test("core_scrub off announces every step as it always did", async () => {
    const { vpin, everyone, down, up } = await started();
    vpin._capabilities.core_scrub = false;

    await down();
    await pause(150);
    const during = everyone.length;
    await up();
    await pause(60);

    assert.ok(during > 6, "every step reaches every window");
    assert.ok(everyone.every((message) => message.reason !== "settle"));
  });
});

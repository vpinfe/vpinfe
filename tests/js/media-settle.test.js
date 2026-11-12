// Protects: (c) a quick run of presses draws the window's media once at the start and once
// when the run pauses, not once per press.

import { test, describe } from "node:test";
import assert from "node:assert/strict";

import { newCore, fixture } from "./support/load-core.js";

const ROWS = fixture("theme_payload.json").contract1;
const STEPS = ROWS.map((_, index) => index);

const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function playfield() {
  const { vpin, browser } = newCore({ windowName: "playfield" });
  vpin.call = (method) => Promise.resolve(method === "get_tables" ? "[]" : null);
  vpin.init();
  await browser.WebSocket.instances.at(-1).onopen();
  const drawn = [];
  const stage = { children: [], getAttribute: () => "",
    replaceChildren(...kids) { this.children = kids; drawn.push(vpin._currentTableIndex); } };
  browser.document._queryAll["[data-vpin-media]"] = [stage];
  vpin.tableData = ROWS;
  vpin.themeAssetsPort = 8000;
  vpin.mediaPriorities = { ...vpin.mediaPriorities, playfield: "image" };
  vpin.mediaSettleMs = 30;
  return { vpin, drawn, step: (index) => vpin.handleEvent({ type: "TableIndexUpdate", index }) };
}

describe("window media during a run of presses", () => {
  test("the first press draws at once and the presses after it draw once the run pauses", async () => {
    assert.ok(STEPS.length >= 4, "the fixture has games to step through");
    const { drawn, step } = await playfield();

    await step(STEPS[0]);
    assert.equal(drawn.length, 1, "the first press draws at once");
    await step(STEPS[1]);
    await step(STEPS[2]);
    await step(STEPS[3]);
    assert.equal(drawn.length, 1, "the presses in the run leave the picture as it was");

    await pause(80);
    assert.deepEqual(drawn, [STEPS[0], STEPS[3]], "the run draws once, at the game it ended on");
  });

  test("a press after a pause draws at once", async () => {
    const { drawn, step } = await playfield();

    await step(STEPS[0]);
    await pause(60);
    await step(STEPS[1]);

    assert.equal(drawn.length, 2);
  });

  test("a settle of 0 draws on every press", async () => {
    const { vpin, drawn, step } = await playfield();
    vpin.mediaSettleMs = 0;

    await step(STEPS[0]);
    await step(STEPS[1]);
    await step(STEPS[2]);

    assert.equal(drawn.length, 3);
  });
});

// Which window the splash page plays its video in, run from the page's own script, and
// that a video which never plays does not hold the page.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import vm from "node:vm";

import { loadCore, REPO_ROOT } from "./support/load-core.js";

const PAGE = path.join(REPO_ROOT, "frontend", "static", "splash.html");
const SCRIPT = [...readFileSync(PAGE, "utf8").matchAll(/<script>([\s\S]*?)<\/script>/g)]
  .map((match) => match[1]).join("\n");

const WINDOWS = { 1: ["table", "bg", "dmd"], 2: ["playfield", "backglass", "scoreview"] };

function element(id, hidden) {
  const classes = new Set(hidden ? ["hidden"] : []);
  const listeners = {};
  return {
    id, style: {}, played: false, classes, listeners,
    classList: { add: (name) => classes.add(name), remove: (name) => classes.delete(name) },
    load() {},
    async play() { this.played = true; },
    addEventListener(type, fn) { listeners[type] = fn; },
  };
}

async function splashIn(contract, windowName, { play } = {}) {
  const { VPinFECore, context, browser } = loadCore({ windowName });
  const video = element("splash", false);
  const image = element("splash-image", true);
  const source = element("splash-source");
  if (play) video.play = play;
  Object.assign(browser.document._byId, {
    "splash-root": element("splash-root"), splash: video,
    "splash-source": source, "splash-image": image,
  });
  const delays = [];
  const timeout = context.setTimeout;
  context.setTimeout = (fn, ms, ...rest) => { delays.push(ms); return timeout(fn, ms, ...rest); };
  const sent = [];
  const bridge = {
    get_theme_contract: contract, get_theme_windows: WINDOWS[contract],
    get_splashscreen_enabled: "true",
    get_cab_mode: false, get_playfield_rotation: 0, get_theme_assets_port: 8000,
    get_tables: JSON.stringify([]), get_theme_config: {}, get_monitors: [],
  };
  VPinFECore.prototype.call = (method, ...args) => {
    if (method === "send_event_all_windows_incself") sent.push(args[0].type);
    return Promise.resolve(bridge[method]);
  };

  vm.runInContext(SCRIPT, context, { filename: PAGE });
  await browser.WebSocket.instances.at(-1).onopen();
  await new Promise((resolve) => setTimeout(resolve, 10));
  return { video, image, source, delays, sent };
}

for (const contract of [1, 2]) {
  const [controller, ...others] = WINDOWS[contract];

  test(`at contract ${contract} the video plays in ${controller}, the controller`, async () => {
    const { video, image } = await splashIn(contract, controller);

    assert.equal(video.played, true);
    assert.ok(image.classes.has("hidden"));
  });

  for (const windowName of others) {
    test(`at contract ${contract} ${windowName} shows the still`, async () => {
      const { video, image } = await splashIn(contract, windowName);

      assert.equal(video.played, false);
      assert.ok(!image.classes.has("hidden"));
    });
  }
}

test("a video that never settles still has the page leave after the fallback", async () => {
  const { delays } = await splashIn(2, "playfield", { play: () => new Promise(() => {}) });

  assert.ok(delays.includes(15000), "the fallback is armed before play() is awaited");
});

test("a video the browser refuses to play ends the splash at once", async () => {
  const { sent } = await splashIn(2, "playfield", { play: () => Promise.reject(new Error("blocked")) });

  assert.deepEqual(sent, ["SplashFadeOut"]);
});

test("a video file that fails to load ends the splash at once", async () => {
  const { source, sent } = await splashIn(2, "playfield", { play: () => new Promise(() => {}) });
  assert.deepEqual(sent, []);

  source.listeners.error();

  assert.deepEqual(sent, ["SplashFadeOut"]);
});

test("a video that plays to the end ends the splash", async () => {
  const { video, sent } = await splashIn(2, "playfield");

  video.listeners.ended();

  assert.deepEqual(sent, ["SplashFadeOut"]);
});

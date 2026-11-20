// Protects: (d) a person who picked a table is shown that table while it starts, and a
// launch never ends up with a window showing nothing because the screen was switched off
// or the table has no video.

import { test, describe } from "node:test";
import assert from "node:assert/strict";

import { newCore, fixture } from "./support/load-core.js";

const PAYLOAD = fixture("theme_payload.json").contract2;

const BRIDGE_DEFAULTS = {
  get_theme_assets_port: 8000,
  get_audio_muted: false,
  get_initial_table_index: 0,
  get_theme_config: {},
  get_keymapping: {},
  get_joymaping: {},
  get_mainmenu_config: {},
  get_monitors: [],
  get_collections: [],
};

const VIDEO = { from: "table", media: "loading", kind: "video" };
const IMAGE = { from: "table", media: "loading_image", kind: "image" };
const PLAYFIELD = { from: "table", media: "playfield", kind: "image" };
const SOUND = { from: "table", media: "audio_launch" };

const SCREEN = (extra = {}) => ({ mode: "video", repeat: false, rungs: [VIDEO, PLAYFIELD],
                                  audio: [SOUND], ...extra });

// Four tables: the first with a Loading Video and a Playfield picture, the second with a
// Playfield picture alone, the third with neither, the fourth with a Loading Image.
function entries() {
  const base = PAYLOAD.entries[0];
  const entry = (id, media) => ({ ...base, table: { ...base.table, id }, media });
  return [
    entry("with-video", ["playfield", "loading", "audio_launch"]),
    entry("picture-only", ["playfield"]),
    entry("nothing", []),
    entry("with-image", ["playfield", "loading_image"]),
  ];
}

async function window_({ windowName = "table", capabilities = {} } = {}) {
  const { vpin, browser } = newCore({ windowName });
  vpin.call = (method) => {
    if (method === "get_theme_contract") return Promise.resolve(2);
    if (method === "get_tables") {
      return Promise.resolve(JSON.stringify({ ...PAYLOAD, entries: entries(), count: 4 }));
    }
    return Promise.resolve(BRIDGE_DEFAULTS[method]);
  };
  vpin.init();
  await browser.WebSocket.instances.at(-1).onopen();
  Object.assign(vpin._capabilities, { core_launch_dim: true, core_launch_indicator: true,
                                       core_loading_screen: true, ...capabilities });
  const page = () => browser.document.body.children;
  const kinds = () => page().map((one) => one.className);
  const screen = () => page().find((one) => one.className === "vpinfe-loading-screen");
  return { vpin, browser, kinds, screen, root: browser.document.documentElement };
}

async function launch(vpin, table_id, loading_screen) {
  await vpin.handleEvent({ type: "TableLaunching", table_id, loading_screen });
}

describe("the controller window", () => {
  test("plays the table's Loading Video", async () => {
    const { vpin, screen, kinds } = await window_();
    await launch(vpin, "with-video", SCREEN());

    const [player] = screen().children;
    assert.match(player.src, /\/media\/with-video\/loading$/);
    assert.equal(player.loop, false, "it stops on its last frame");
    assert.ok(!kinds().includes("vpinfe-launch-indicator"), "no spinner over a video");
  });

  test("repeats the video when asked", async () => {
    const { vpin, screen } = await window_();
    await launch(vpin, "with-video", SCREEN({ repeat: true }));
    assert.equal(screen().children[0].loop, true);
  });

  test("shows the Playfield picture when the table has no video", async () => {
    const { vpin, screen } = await window_();
    await launch(vpin, "picture-only", SCREEN());
    assert.match(screen().children[0].src, /\/media\/picture-only\/playfield$/);
  });

  test("falls back to a spinner on dark when the table has neither", async () => {
    const { vpin, screen, kinds, root } = await window_();
    await launch(vpin, "nothing", SCREEN());

    assert.equal(screen(), undefined);
    assert.ok(kinds().includes("vpinfe-launch-indicator"));
    assert.equal(root.dataset.vpinfeLaunching, "true");
  });

  test("Image skips the video", async () => {
    const { vpin, screen } = await window_();
    await launch(vpin, "with-video", SCREEN({ mode: "image", rungs: [PLAYFIELD] }));
    assert.match(screen().children[0].src, /\/media\/with-video\/playfield$/);
  });

  test("shows the table's Loading Image ahead of its Playfield picture", async () => {
    const { vpin, screen } = await window_();
    await launch(vpin, "with-image", SCREEN({ mode: "image", rungs: [IMAGE, PLAYFIELD] }));
    assert.match(screen().children[0].src, /\/media\/with-image\/loading_image$/);
  });

  test("shows a file the theme or the person named, the same for every table", async () => {
    const { vpin, screen } = await window_();
    await launch(vpin, "nothing", SCREEN({ rungs: [{ url: "/launch_media/intro.mp4", kind: "video" }] }));
    assert.equal(screen().children[0].src, "/launch_media/intro.mp4");
  });

  test("turns a theme's own file for a cabinet and leaves the person's alone", async () => {
    const { vpin, screen } = await window_();
    vpin.layout = { cabinet: true, uprightRotation: 90, surface: "landscape" };
    const theirs = { url: "/themes/T/loading.mp4", kind: "video", turn: true };
    await launch(vpin, "nothing", SCREEN({ rungs: [theirs] }));
    assert.equal(screen().children[0].style.transform, "rotate(90deg)");
    await vpin.handleEvent({ type: "TableRunning" });

    await launch(vpin, "nothing", SCREEN({ rungs: [{ url: "/launch_media/a.mp4", kind: "video" }] }));
    assert.ok(!screen().children[0].style.transform);
  });

  test("Spinner shows only the spinner, and Off shows only the dim", async () => {
    for (const [mode, spinner] of [["spinner", true], ["off", false]]) {
      const { vpin, screen, kinds, root } = await window_();
      await launch(vpin, "with-video", SCREEN({ mode, rungs: [] }));
      assert.equal(screen(), undefined, mode);
      assert.equal(kinds().includes("vpinfe-launch-indicator"), spinner, mode);
      assert.equal(root.dataset.vpinfeLaunching, "true", mode);
    }
  });

  test("a video that cannot play gives way to the picture, and a picture that cannot load to the spinner", async () => {
    const { vpin, screen, kinds } = await window_();
    await launch(vpin, "with-video", SCREEN());
    screen().children[0].listeners.error[0]();
    assert.match(screen().children[0].src, /\/media\/with-video\/playfield$/);

    screen().children[0].listeners.error[0]();
    assert.equal(screen(), undefined);
    assert.ok(kinds().includes("vpinfe-launch-indicator"));
  });

  test("a launch that does not say which table has no screen", async () => {
    const { vpin, screen, kinds } = await window_();
    await launch(vpin, "", SCREEN());
    assert.equal(screen(), undefined);
    assert.ok(kinds().includes("vpinfe-launch-indicator"));
  });

  test("the dark page background is asked for only while a screen is in force", async () => {
    const { vpin, root } = await window_();
    await launch(vpin, "with-video", SCREEN());
    assert.equal(root.dataset.vpinfeLoadingScreen, "video");
    await vpin.handleEvent({ type: "TableRunning" });
    assert.equal(root.dataset.vpinfeLoadingScreen, undefined);
  });

  test("it comes down when the table is running", async () => {
    const { vpin, screen } = await window_();
    await launch(vpin, "with-video", SCREEN());
    await vpin.handleEvent({ type: "TableRunning" });
    assert.equal(screen(), undefined);
  });

  test("a launch that never ran takes it down when it completes", async () => {
    const { vpin, screen } = await window_();
    await launch(vpin, "with-video", SCREEN());
    await vpin.handleEvent({ type: "TableLaunchComplete" });
    assert.equal(screen(), undefined);
  });

  test("a theme that switched the capability off keeps the dim and the spinner", async () => {
    const { vpin, screen, kinds } = await window_({ capabilities: { core_loading_screen: false } });
    await launch(vpin, "with-video", SCREEN({ mode: "off" }));
    assert.equal(screen(), undefined);
    assert.ok(kinds().includes("vpinfe-launch-indicator"), "Off does not reach a theme that opted out");
  });

  test("a launch message without the choice leaves the dim and the spinner as they were", async () => {
    const { vpin, screen, kinds } = await window_();
    await vpin.handleEvent({ type: "TableLaunching" });
    assert.equal(screen(), undefined);
    assert.ok(kinds().includes("vpinfe-launch-indicator"));
  });
});

describe("the launch audio", () => {
  test("plays while the table loads and stops when it runs", async () => {
    const { vpin } = await window_();
    await launch(vpin, "with-video", SCREEN());
    const sound = vpin._launchAudio;
    assert.equal(sound.paused, false);

    await vpin.handleEvent({ type: "TableRunning" });
    assert.equal(sound.paused, true);
    assert.ok(!vpin._launchAudio);
  });

  test("is silent when the frontend is muted or the person turned it off", async () => {
    const muted = await window_();
    muted.vpin.setAudioMuted(true);
    await launch(muted.vpin, "with-video", SCREEN());
    assert.ok(!muted.vpin._launchAudio);

    const off = await window_();
    await launch(off.vpin, "with-video", SCREEN({ audio: [] }));
    assert.ok(!off.vpin._launchAudio);
  });

  test("a table with none plays the next sound named, the same for every table", async () => {
    const { vpin } = await window_();
    await launch(vpin, "picture-only", SCREEN({ audio: [SOUND, { url: "/launch_media/hum.mp3" }] }));
    assert.equal(vpin._launchAudio.src, "/launch_media/hum.mp3");
  });

  test("a table with no launch audio plays nothing", async () => {
    const { vpin } = await window_();
    await launch(vpin, "picture-only", SCREEN());
    assert.ok(!vpin._launchAudio);
  });
});

describe("a window that is not the controller", () => {
  test("keeps its own art under Video and Image", async () => {
    for (const mode of ["video", "image"]) {
      const { vpin, kinds, root } = await window_({ windowName: "backglass" });
      await launch(vpin, "with-video", SCREEN({ mode }));
      assert.equal(root.dataset.vpinfeLaunching, undefined, mode);
      assert.ok(!kinds().includes("vpinfe-launch-indicator"), mode);
    }
  });

  test("is dimmed and shows the spinner under Spinner", async () => {
    const { vpin, kinds, root } = await window_({ windowName: "backglass" });
    await launch(vpin, "with-video", SCREEN({ mode: "spinner" }));
    assert.equal(root.dataset.vpinfeLaunching, "true");
    assert.ok(kinds().includes("vpinfe-launch-indicator"));
  });
});

describe("while a table runs", () => {
  test("the loading video and its sound are not paused by the quiet pass", async () => {
    const { vpin, browser } = await window_();
    const video = { paused: false, ended: false, closest: (s) => (s === ".vpinfe-loading-screen" ? {} : null),
                    pause() { this.paused = true; } };
    browser.document._queryAll["video, audio"] = [video];
    await launch(vpin, "with-video", SCREEN());
    assert.equal(video.paused, false);
  });
});

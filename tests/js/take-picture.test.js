// Take Picture while a table runs: the page hears it, and Back, from a gamepad and from
// any other producer, hands both to core, and plays what core's answer calls for.

import { test, describe } from "node:test";
import assert from "node:assert/strict";

import { loadCore } from "./support/load-core.js";

const ticks = () => new Promise(resolve => setTimeout(resolve, 5));

// A controller booted through the socket, so the gamepad poll runs, with core's own
// calls answered by `answers` and noted, and a sound card that notes what it plays.
async function playing({ answers = {} } = {}) {
  const { VPinFECore, browser } = loadCore({ windowName: "table" });
  const buttons = [{ pressed: false }, { pressed: false }];
  browser.navigator.getGamepads = () => [{ index: 0, buttons }];
  const played = [];
  browser.window.AudioContext = class {
    constructor() { this.currentTime = 0; this.sampleRate = 8000; this.state = "running";
                    this.destination = {}; }
    createGain() { return { gain: { value: 0, setValueAtTime() {},
                                    exponentialRampToValueAtTime() {} },
                            connect: (to) => to }; }
    createOscillator() {
      return { frequency: { value: 0 }, connect: (to) => to,
               start() { played.push("tone"); }, stop() {} };
    }
    createBuffer(_channels, length) {
      return { getChannelData: () => new Float32Array(length) };
    }
    createBufferSource() {
      return { buffer: null, connect: (to) => to, start() { played.push("burst"); } };
    }
  };
  const vpin = new VPinFECore();
  vpin.isController = () => true;
  const boot = { get_tables: "[]", get_theme_assets_port: 8000, get_initial_table_index: 0,
                 get_theme_config: {}, get_keymapping: {}, get_joymaping: {},
                 get_mainmenu_config: {}, get_monitors: [], get_collections: [] };
  vpin.call = (method) => Promise.resolve(method in boot ? boot[method] : null);
  const asked = [];
  const reports = [];
  vpin.callInternal = (method, ...args) => {
    if (method === "report_input_action") { reports.push(args[0]); return Promise.resolve({}); }
    if (method === "library_waiting") return Promise.resolve([]);
    if (method === "signin_pending") return Promise.resolve(null);
    if (method === "get_attract_config") return Promise.resolve({ enabled: false });
    asked.push(method);
    return Promise.resolve(answers[method] || { state: "ignored" });
  };
  vpin.init();
  await browser.WebSocket.instances.at(-1).onopen();
  vpin.frontendInputEnabled = true;
  vpin.contract = 2;
  vpin.joyButtonMap = { "0": ["take_picture"], "1": ["back", "select"] };
  const seen = [];
  vpin.inputHandlers.push((action) => { seen.push(action); });
  const press = async (index) => {
    buttons[index].pressed = true;
    browser.frames.step();
    await ticks();
    buttons[index].pressed = false;
    browser.frames.step();
    await ticks();
  };
  const remote = async (action) => {
    await vpin.handleEvent({ type: "InputAction", action, phase: "press" });
    await vpin.handleEvent({ type: "InputAction", action, phase: "release" });
    await ticks();
  };
  const launch = async () => {
    await vpin.handleEvent({ type: "TableLaunching" });
    await ticks();
  };
  return { vpin, asked, reports, seen, played, press, remote, launch };
}

describe("during play the page hears Take Picture and Back, and hands them to core", () => {
  test("a button bound to Take Picture asks core to take one", async () => {
    const { asked, seen, press, launch } = await playing();
    await launch();

    await press(0);

    assert.deepEqual(asked, ["take_picture"]);
    assert.deepEqual(seen, [], "a theme is not handed it");
  });

  test("Back asks core to resume, and nothing else on its button acts", async () => {
    const { asked, seen, press, launch } = await playing();
    await launch();

    await press(1);

    assert.deepEqual(asked, ["resume_play"]);
    assert.deepEqual(seen, []);
  });

  test("a press from the Remote does the same", async () => {
    const { asked, remote, launch } = await playing();
    await launch();

    await remote("take_picture");
    await remote("next");
    await remote("back");

    assert.deepEqual(asked, ["take_picture", "resume_play"]);
  });

  test("outside play Take Picture does nothing at all", async () => {
    const { asked, seen, press, remote } = await playing();

    await press(0);
    await remote("take_picture");

    assert.deepEqual(asked, []);
    assert.deepEqual(seen, []);
  });

  test("the play ends, and Back is the theme's again", async () => {
    const { vpin, asked, seen, press, launch } = await playing();
    await launch();
    await vpin.handleEvent({ type: "TableLaunchComplete" });
    await ticks();

    await press(1);

    assert.deepEqual(asked, []);
    assert.deepEqual(seen, ["back", "select"]);
  });
});

describe("what core answered is what is heard", () => {
  test("frozen plays the ready tone", async () => {
    const { played, press, launch } = await playing({
      answers: { take_picture: { state: "frozen" } } });
    await launch();

    await press(0);

    assert.deepEqual(played, ["tone", "tone"]);
  });

  test("taken plays the shutter", async () => {
    const { played, press, launch } = await playing({
      answers: { take_picture: { state: "taken" } } });
    await launch();

    await press(0);

    assert.deepEqual(played, ["burst", "burst"]);
  });

  test("anything else is silent", async () => {
    const { played, press, launch } = await playing({
      answers: { take_picture: { state: "not_paused" } } });
    await launch();

    await press(0);
    await press(1);

    assert.deepEqual(played, []);
  });
});

describe("a pad press during play is reported to core for extensions", () => {
  test("Take Picture and Back from the pad are reported by name", async () => {
    const { press, launch, reports } = await playing();
    await launch();

    await press(0);
    await press(1);

    assert.deepEqual(reports, ["take_picture", "back"]);
  });

  test("the table has the other buttons while it plays, so they are not reported", async () => {
    const { vpin, press, launch, reports } = await playing();
    vpin.joyButtonMap = { "0": ["left"] };
    await launch();

    await press(0);

    assert.deepEqual(reports, []);
  });

  test("a press from the Remote is not reported back", async () => {
    const { remote, launch, reports } = await playing();
    await launch();

    await remote("take_picture");

    assert.deepEqual(reports, []);
  });
});

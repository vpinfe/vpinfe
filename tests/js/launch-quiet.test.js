// Protects: (d) a table that is running gets the whole machine, so the frontend stops
// playing video and sound behind it, and picks up only what it stopped when the table ends.

import { test, describe } from "node:test";
import assert from "node:assert/strict";

import { loadCore } from "./support/load-core.js";

function media(playing) {
  return {
    paused: !playing,
    ended: false,
    played: 0,
    pause() { this.paused = true; },
    play() { this.paused = false; this.played += 1; return Promise.resolve(); },
  };
}

function animation(state, { spinner = false } = {}) {
  return {
    playState: state,
    effect: { target: { closest: (selector) => (spinner && selector === ".vpinfe-launch-indicator" ? {} : null) } },
    pause() { this.playState = "paused"; },
    play() { this.playState = "running"; },
  };
}

function window_({ capabilities = {} } = {}) {
  const { VPinFECore, browser } = loadCore({ windowName: "table" });
  const vpin = new VPinFECore();
  vpin.init();
  Object.assign(vpin._capabilities, capabilities);
  const listeners = {};
  browser.document.addEventListener = (name, fn) => { (listeners[name] ||= []).push(fn); };
  browser.document.removeEventListener = (name, fn) => {
    listeners[name] = (listeners[name] || []).filter((one) => one !== fn);
  };
  const playing = media(true);
  const stopped = media(false);
  const sound = media(true);
  browser.document._queryAll["video, audio"] = [playing, stopped, sound];
  return { vpin, browser, listeners, playing, stopped, sound };
}

describe("while a table runs", () => {
  test("what was playing is paused, and only that is started again", async () => {
    const { vpin, playing, stopped, sound } = window_();

    await vpin.handleEvent({ type: "TableLaunching" });
    assert.equal(playing.paused, true);
    assert.equal(sound.paused, true);

    await vpin.handleEvent({ type: "TableLaunchComplete" });
    assert.equal(playing.played, 1, "the video that was playing starts again");
    assert.equal(sound.played, 1);
    assert.equal(stopped.played, 0, "a video that was paused beforehand stays paused");
  });

  test("a video that starts mid-game is held, and starts again at the end", async () => {
    const { vpin, listeners } = window_();
    await vpin.handleEvent({ type: "TableLaunching" });

    const late = media(true);
    for (const fn of listeners.playing || []) fn({ target: late });
    assert.equal(late.paused, true);

    await vpin.handleEvent({ type: "TableLaunchComplete" });
    assert.equal(late.played, 1);
    assert.deepEqual(listeners.playing, [], "the listener goes when the game ends");
  });

  test("a running animation is paused, but the spinner core draws keeps turning", async () => {
    const { vpin, browser } = window_();
    const loop = animation("running");
    const spinner = animation("running", { spinner: true });
    const resting = animation("paused");
    browser.document.getAnimations = () => [loop, spinner, resting];

    await vpin.handleEvent({ type: "TableLaunching" });
    assert.equal(loop.playState, "paused");
    assert.equal(spinner.playState, "running");

    await vpin.handleEvent({ type: "TableLaunchComplete" });
    assert.equal(loop.playState, "running");
    assert.equal(resting.playState, "paused", "one that was paused beforehand stays paused");
  });

  test("a theme that turns it off keeps its media playing", async () => {
    const { vpin, playing } = window_({ capabilities: { core_launch_quiet: false } });

    await vpin.handleEvent({ type: "TableLaunching" });
    assert.equal(playing.paused, false);
  });

  test("core's own audio is left to the audio handler", async () => {
    const { vpin, browser } = window_();
    const own = media(true);
    vpin._audio = own;
    browser.document._queryAll["video, audio"] = [own];

    await vpin.handleEvent({ type: "TableLaunching" });
    await vpin.handleEvent({ type: "TableLaunchComplete" });

    assert.equal(own.played, 0, "starting it again is playTableAudio's decision");
  });
});

// The remote-launch overlay, driven the way the Manager drives it.
//
// Nothing reached this handler before: the EventSource stub swallowed its listeners, so
// the one field it reads was never checked against the payload that carries it. It read
// `table_name`; the state has `game_name`. Every theme has been shown "undefined" on a
// remote launch since the API started talking about games.

import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { newCore } from "./support/load-core.js";

function launching(core, browser, state) {
  const stream = browser.EventSource.instances.at(-1);
  const sent = [];
  core.sendMessageToAllWindowsIncSelf = (m) => sent.push(m);
  core.call = async () => null;
  stream.emit("play.state_changed", { state });
  return sent;
}

async function started() {
  // The subscribe happens on the controller once the socket is up, so this walks the
  // same path the app does rather than reaching for the private method.
  const { vpin, browser } = newCore();
  // get_tables is parsed, so it cannot answer null the way the rest can.
  vpin.call = async (name) => (name === "get_tables" ? "[]" : null);
  vpin.isController = () => true;
  vpin.init();
  await browser.WebSocket.instances.at(-1).onopen();
  return { vpin, browser };
}

describe("a launch started somewhere else", () => {
  test("the overlay is told the game's name", async () => {
    const { vpin, browser } = await started();

    const sent = launching(vpin, browser,
                           { launching: true, game_name: "Attack from Mars", source: "manager" });

    const message = sent.find((m) => m.type === "RemoteLaunching");
    assert.ok(message, "no RemoteLaunching went out");
    assert.equal(message.game_name, "Attack from Mars");
  });

  test("the 2.x spelling carries the same value", async () => {
    const { vpin, browser } = await started();

    const sent = launching(vpin, browser,
                           { launching: true, game_name: "Medieval Madness", source: "manager" });

    const message = sent.find((m) => m.type === "RemoteLaunching");
    assert.equal(message.table_name, "Medieval Madness",
                 "twelve themes read table_name and it shipped in 2.x");
  });

  test("our own launch is left to the bridge", async () => {
    const { vpin, browser } = await started();

    const sent = launching(vpin, browser,
                           { launching: true, game_name: "Taxi", source: "frontend" });

    assert.deepEqual(sent.filter((m) => m.type === "RemoteLaunching"), [],
                     "a launch from the wheel would raise the remote overlay twice");
  });

  test("a recording's launch is not a remote one", async () => {
    const { vpin, browser } = await started();

    const sent = launching(vpin, browser,
                           { launching: true, game_name: "Taxi", source: "capture" });

    assert.deepEqual(sent.filter((m) => m.type === "RemoteLaunching"), [],
                     "the themes' remote overlay would say Remote Launching over a recording");
  });
});

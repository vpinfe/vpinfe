// A recording run on this device, as the controller window shows it: one line while the
// run goes, and Exit asking Stop recording? rather than quitting.

import { test, describe } from "node:test";
import assert from "node:assert/strict";

import { loadCore } from "./support/load-core.js";

const settle = () => new Promise(resolve => setTimeout(resolve, 0));

const RUNNING = { id: "b41c07aa19e2", state: "running", reason: null, done: 3, of: 24,
                  game: { id: "6f1c9a4e", table_id: "", name: "Attack from Mars" } };

// A controller booted through the socket, so it opens the event stream the app opens,
// with the playfield turned `rotation` degrees and core's own calls noted.
async function controller({ rotation = 0 } = {}) {
  const { VPinFECore, browser } = loadCore({ windowName: "table" });
  const vpin = new VPinFECore();
  vpin.isController = () => true;
  const boot = { get_tables: "[]", get_playfield_orientation: "portrait",
                 get_playfield_rotation: rotation, get_monitors: [], get_theme_config: {} };
  const asked = [];
  vpin.call = async (method, ...args) => {
    asked.push(method);
    return method in boot ? boot[method] : null;
  };
  const internal = [];
  vpin.callInternal = async (method) => {
    if (method === "library_waiting") return [];
    if (method === "signin_pending") return null;
    if (method === "get_attract_config") return { enabled: false };
    internal.push(method);
    return { run: null };
  };
  vpin.init();
  await browser.WebSocket.instances.at(-1).onopen();
  const stream = browser.EventSource.instances.at(-1);
  const recording = async (run) => {
    stream.emit("capture.run_changed", { run });
    await settle();
  };
  const line = () =>
    browser.document.body.children.find(el => el.className === "vpinfe-recording");
  const said = () => line()?.children[0]?.textContent;
  const dialog = () =>
    browser.document.body.children.find(el => el.className === "vpinfe-confirm");
  const press = async (action) => {
    await vpin.handleEvent({ type: "InputAction", action, phase: "press" });
    await vpin.handleEvent({ type: "InputAction", action, phase: "release" });
    await settle();
  };
  return { vpin, stream, recording, line, said, dialog, press, asked, internal };
}

describe("the line over the wheel", () => {
  test("the stream the controller opens carries the run", async () => {
    const { stream } = await controller();

    assert.match(stream.url, /events=play\.state_changed,capture\.run_changed$/);
  });

  test("a run of many says how far it has come", async () => {
    const { recording, said } = await controller();

    await recording(RUNNING);

    assert.equal(said(), "Recording media - 4 of 24");
  });

  test("a run of one names its game", async () => {
    const { recording, said } = await controller();

    await recording({ ...RUNNING, done: 0, of: 1 });

    assert.equal(said(), "Recording “Attack from Mars”");
  });

  test("the line follows the run from game to game", async () => {
    const { recording, said } = await controller();
    await recording(RUNNING);

    await recording({ ...RUNNING, done: 4 });

    assert.equal(said(), "Recording media - 5 of 24");
  });

  test("it goes when the run ends", async () => {
    const { recording, line } = await controller();
    await recording(RUNNING);

    await recording(null);

    assert.equal(line(), undefined);
  });

  test("a paused run draws none: it waits for the Manager, and the wheel is the player's",
       async () => {
    const { recording, line } = await controller();
    await recording(RUNNING);

    await recording({ ...RUNNING, state: "paused" });

    assert.equal(line(), undefined);
  });

  test("it is hidden while a table is up, and back once the table closes", async () => {
    const { vpin, recording, line } = await controller();
    await recording(RUNNING);

    await vpin.handleEvent({ type: "TableLaunching" });
    assert.equal(line().hidden, true, "VPX has the screens and a recording is being made");

    await vpin.handleEvent({ type: "TableLaunchComplete" });
    assert.equal(line().hidden, false);
  });

  test("it faces the player on a cabinet whose screen the OS does not turn", async () => {
    const { recording, line } = await controller({ rotation: 90 });

    await recording(RUNNING);

    assert.equal(line().attributes["data-upright"], "90");
    assert.match(line().style.transform, /rotate\(90deg\)/);
  });
});

describe("Exit while a run goes", () => {
  test("asks Stop recording? instead of quitting", async () => {
    const { recording, press, dialog, asked } = await controller();
    await recording(RUNNING);

    await press("exit");

    assert.equal(dialog()?.children[0]?.children[0]?.textContent, "Stop recording?");
    assert.ok(!asked.includes("lifecycle_request"), "quitting would end the run with it");
  });

  test("Select stops the run", async () => {
    const { recording, press, dialog, internal } = await controller();
    await recording(RUNNING);
    await press("exit");

    await press("select");

    assert.deepEqual(internal, ["stop_recording"]);
    assert.equal(dialog(), undefined);
  });

  test("Back leaves it going", async () => {
    const { recording, press, dialog, internal } = await controller();
    await recording(RUNNING);
    await press("exit");

    await press("back");

    assert.deepEqual(internal, []);
    assert.equal(dialog(), undefined);
  });

  test("with no run going, Exit quits as it always has", async () => {
    const { recording, press, asked, internal } = await controller();
    await recording({ ...RUNNING, state: "paused" });

    await press("exit");

    assert.ok(asked.includes("lifecycle_request"));
    assert.deepEqual(internal, []);
  });
});

// A Manager listens for a controller button or key: while it does, the controller window
// reports each one to Python in the Manager's own selector form and does nothing else with
// it, and it lets go on its own when the Manager stops asking.

import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";

import { loadCore } from "./support/load-core.js";

const ticks = () => new Promise(resolve => setTimeout(resolve, 5));

const stillArmed = [];
afterEach(() => {
  for (const off of stillArmed.splice(0)) off();
});

async function controller({ mapping = "standard" } = {}) {
  const { VPinFECore, browser } = loadCore({ windowName: "table" });
  const buttons = [{ pressed: false }, { pressed: false }, { pressed: false }];
  // A browser lists a pad at its own index, empty slots before it.
  browser.navigator.getGamepads = () => [null, null, null, { index: 3, mapping, buttons }];
  const listeners = {};
  browser.window.addEventListener = (type, fn) => { (listeners[type] ||= []).push(fn); };
  const vpin = new VPinFECore();
  vpin.isController = () => true;
  const boot = { get_tables: "[]", get_theme_assets_port: 8000, get_initial_table_index: 0,
                 get_theme_config: {}, get_keymapping: {}, get_joymaping: {},
                 get_mainmenu_config: {}, get_monitors: [], get_collections: [] };
  vpin.call = (method) => Promise.resolve(method in boot ? boot[method] : null);
  const reported = [];
  vpin.callInternal = (method, ...args) => {
    if (method === "library_waiting") return Promise.resolve([]);
    if (method === "signin_pending") return Promise.resolve(null);
    if (method === "get_attract_config") return Promise.resolve({ enabled: false });
    if (method === "input_capture") reported.push(args);
    return Promise.resolve({});
  };
  vpin.init();
  await browser.WebSocket.instances.at(-1).onopen();
  vpin.frontendInputEnabled = true;
  vpin.contract = 2;
  vpin.joyButtonMap = { "0": ["next"] };
  const seen = [];
  vpin.inputHandlers.push((action) => { seen.push(action); });
  const arm = (armed = true, ttl_ms = 6000, who = {}) => {
    stillArmed.push(() => vpin.handleEvent({ type: "CaptureArm", armed: false }));
    return vpin.handleEvent({ type: "CaptureArm", armed, ttl_ms, ...who });
  };
  const hold = async (index, down) => {
    buttons[index].pressed = down;
    browser.frames.step();
    await ticks();
  };
  const key = async (type, code, repeat = false, target = null) => {
    const event = { key: code, code, repeat, target, prevented: false,
                    preventDefault() { event.prevented = true; } };
    await Promise.all((listeners[type] || []).map(fn => fn(event)));
    return event;
  };
  const press = (code, repeat, target) => key("keydown", code, repeat, target);
  const release = (code, target) => key("keyup", code, false, target);
  const line = () => browser.document.body.children.find(el => el.className === "vpinfe-capture");
  const words = () => line()?.children[0].children.map(el => el.textContent);
  const card = () => line()?.children[0];
  return { vpin, browser, listeners, reported, seen, arm, hold, buttons, press, release, line, words, card };
}

describe("a controller button is reported to a Manager that is listening", () => {
  test("a pad on the standard layout goes by the layout's name", async () => {
    const { reported, arm, hold } = await controller();
    await arm();

    await hold(1, true);
    await hold(1, false);

    assert.deepEqual(reported, [["pad:standard/button:1", "down"],
                                ["pad:standard/button:1", "up"]]);
  });

  test("any other pad goes by its number", async () => {
    const { reported, arm, hold } = await controller({ mapping: "" });
    await arm();

    await hold(0, true);

    assert.deepEqual(reported, [["pad:3/button:0", "down"]]);
  });

  test("what the button does here waits until the Manager is done", async () => {
    const { seen, arm, hold } = await controller();
    await arm();

    await hold(0, true);
    await hold(0, false);

    assert.deepEqual(seen, []);
  });

  test("a button already down when it starts is not reported until let go and pressed again",
       async () => {
         const { reported, arm, hold } = await controller();
         await hold(1, true);
         await arm();

         await hold(1, false);
         await hold(1, true);

         assert.deepEqual(reported, [["pad:standard/button:1", "down"]]);
       });

  test("nothing is reported while no Manager is listening", async () => {
    const { reported, hold } = await controller();

    await hold(1, true);
    await hold(1, false);

    assert.deepEqual(reported, []);
  });

  test("a Manager that stops listening gives the buttons back", async () => {
    const { reported, seen, arm, hold } = await controller();
    await arm();
    await arm(false);

    await hold(0, true);
    await hold(0, false);

    assert.deepEqual(reported, []);
    assert.deepEqual(seen, ["next"]);
  });

  test("it lets go on its own when the Manager stops asking", async () => {
    const { reported, seen, arm, hold } = await controller();
    await arm(true, 20);
    await new Promise(resolve => setTimeout(resolve, 60));

    await hold(0, true);
    await hold(0, false);

    assert.deepEqual(reported, []);
    assert.deepEqual(seen, ["next"]);
  });

  test("a wheel that is repeating stops when the Manager starts listening", async () => {
    const { vpin, seen, arm, hold } = await controller();
    vpin.repeatDelayMs = 20;
    await hold(0, true);
    assert.deepEqual(seen, ["next"]);

    await arm();
    await new Promise(resolve => setTimeout(resolve, 120));

    assert.deepEqual(seen, ["next"]);
  });

  test("asking again keeps it, and keeps what was down when it first started", async () => {
    const { reported, arm, hold } = await controller();
    await hold(1, true);
    await arm();
    await arm();

    await hold(1, false);

    assert.deepEqual(reported, []);
  });
});

describe("a key is reported to a Manager that is listening", () => {
  test("a key goes by the name of its place on the keyboard", async () => {
    const { reported, arm, press, release } = await controller();
    await arm();

    await press("KeyN");
    await release("KeyN");

    assert.deepEqual(reported, [["key:KeyN", "down"], ["key:KeyN", "up"]]);
  });

  test("what the key does here waits until the Manager is done", async () => {
    const { vpin, seen, arm, press, release } = await controller();
    vpin.keyActionMap = { next: ["keyn"] };
    await press("KeyN");
    await release("KeyN");
    assert.deepEqual(seen, ["next"]);
    await arm();

    const event = await press("KeyN");
    await release("KeyN");

    assert.deepEqual(seen, ["next"]);
    assert.equal(event.prevented, true);
  });

  test("the operating system's repeat is not another press", async () => {
    const { reported, arm, press } = await controller();
    await arm();

    await press("KeyN");
    await press("KeyN", true);

    assert.deepEqual(reported, [["key:KeyN", "down"]]);
  });

  test("a key typed in a text field is not reported and still types", async () => {
    const { reported, arm, press, release } = await controller();
    await arm();
    const field = { tagName: "INPUT", type: "text" };

    const down = await press("KeyN", false, field);
    const up = await release("KeyN", field);

    assert.deepEqual(reported, []);
    assert.equal(down.prevented, false);
    assert.equal(up.prevented, false);
  });

  test("a key with no place of its own is not reported", async () => {
    const { reported, arm, press } = await controller();
    await arm();

    await press("");

    assert.deepEqual(reported, []);
  });

  test("a window that is not the controller reports nothing", async () => {
    const { vpin, reported, arm, press } = await controller();
    vpin.isController = () => false;
    await arm();

    await press("KeyN");

    assert.deepEqual(reported, []);
  });

  test("nothing is reported while no Manager is listening", async () => {
    const { reported, press, release } = await controller();

    await press("KeyN");
    await release("KeyN");

    assert.deepEqual(reported, []);
  });
});

describe("the line the cabinet shows while input events go to a Manager", () => {
  const away = { browser: "Safari on Mac", device: "office-laptop" };

  test("it names the browser and the device", async () => {
    const { arm, words } = await controller();
    await arm(true, 6000, away);

    assert.deepEqual(words().slice(0, 2), [
      "Input events are going to the Manager in Safari on Mac at office-laptop",
      "Hold any button 5 seconds to take them back"]);
  });

  test("a page on the cabinet itself says so", async () => {
    const { arm, words } = await controller();
    await arm(true, 6000, { browser: "Chrome on Linux", device: "" });

    assert.equal(words()[0], "Input events are going to the Manager in Chrome on Linux on this device");
  });

  test("a holder with no name still has one", async () => {
    const { arm, words } = await controller();
    await arm(true, 6000, { device: "office-laptop" });

    assert.equal(words()[0], "Input events are going to the Manager in a browser at office-laptop");
  });

  test("it is gone once nothing asks", async () => {
    const { arm, line } = await controller();
    await arm(true, 6000, away);
    await arm(false);

    assert.equal(line(), undefined);
  });

  test("a window that is not the controller shows none", async () => {
    const { vpin, arm, line } = await controller();
    vpin.isController = () => false;
    await arm(true, 6000, away);

    assert.equal(line(), undefined);
  });

  test("it is turned to face the player like the recording line", async () => {
    const { arm, line } = await controller();
    await arm(true, 6000, away);

    assert.equal(line().attributes["data-upright"], "0");
  });
});

describe("a table playing keeps the cabinet's buttons", () => {
  const away = { browser: "Safari on Mac", device: "office-laptop" };

  test("an ask that arrives while a table starts is not taken", async () => {
    const { vpin, reported, seen, arm, hold, line } = await controller();
    await vpin.handleEvent({ type: "TableLaunching" });
    await arm(true, 6000, away);

    await hold(0, true);

    assert.deepEqual(reported, []);
    assert.equal(line(), undefined);
    assert.deepEqual(seen, []);
  });

  test("a table that starts takes the buttons back from a page that had them", async () => {
    const { vpin, reported, arm, hold, line } = await controller();
    await arm(true, 6000, away);
    await vpin.handleEvent({ type: "TableLaunching" });

    await hold(1, true);

    assert.deepEqual(reported, []);
    assert.equal(line(), undefined);
  });

  test("they go to the page again once the table closes and it asks again", async () => {
    const { vpin, reported, arm, hold } = await controller();
    await vpin.handleEvent({ type: "TableLaunching" });
    await vpin.handleEvent({ type: "TableLaunchComplete" });
    await arm(true, 6000, away);

    await hold(1, true);

    assert.deepEqual(reported, [["pad:standard/button:1", "down"]]);
  });
});

describe("a button held at the cabinet takes the buttons back", () => {
  const away = { browser: "Safari on Mac", device: "office-laptop" };
  const wait = (ms) => new Promise(resolve => setTimeout(resolve, ms));
  async function quickController() {
    const made = await controller();
    made.vpin.constructor.CAPTURE_TAKE_MS = 80;
    made.vpin.constructor.CAPTURE_SHOW_MS = 20;
    made.released = [];
    const was = made.vpin.callInternal;
    made.vpin.callInternal = (method, ...args) => {
      if (method === "input_capture_release") made.released.push(true);
      return was(method, ...args);
    };
    return made;
  }

  test("held long enough, it tells the Manager and stops sending", async () => {
    const { vpin, released, reported, arm, hold, line } = await quickController();
    await arm(true, 6000, away);

    await hold(2, true);
    await wait(140);

    assert.equal(released.length, 1);
    assert.equal(line(), undefined);
    await hold(2, false);
    await hold(1, true);
    assert.deepEqual(reported.filter(([token]) => token.endsWith("button:1")), []);
    assert.equal(vpin._captureArmed, false);
  });

  test("let go in time, nothing is taken", async () => {
    const { released, arm, hold, line } = await quickController();
    await arm(true, 6000, away);

    await hold(2, true);
    await wait(40);
    await hold(2, false);
    await wait(100);

    assert.equal(released.length, 0);
    assert.notEqual(line(), undefined);
  });

  test("the line says to keep holding, with a bar, once it is held a moment", async () => {
    const { arm, hold, words, card } = await quickController();
    await arm(true, 6000, away);

    await hold(2, true);
    assert.equal(words()[1], "Hold any button 5 seconds to take them back");
    await wait(40);
    // Drawn by the timer that shows it, which the next press also reaches.
    await hold(1, true);

    assert.equal(words()[1], "Keep holding to take them back");
    assert.equal(card().children[2].hidden, false);
  });

  test("a key held does the same", async () => {
    const { released, arm, press, release } = await quickController();
    await arm(true, 6000, away);

    await press("KeyN");
    await wait(140);
    await release("KeyN");

    assert.equal(released.length, 1);
  });

  test("an ask already on its way does not hand them straight back", async () => {
    const { reported, released, arm, hold } = await quickController();
    await arm(true, 6000, away);
    await hold(2, true);
    await wait(140);
    await hold(2, false);
    assert.equal(released.length, 1);

    await arm(true, 6000, away);
    await hold(1, true);

    assert.deepEqual(reported.filter(([token]) => token.endsWith("button:1")), []);
  });

  test("a failed release is tried once more", async () => {
    const made = await quickController();
    let calls = 0;
    const was = made.vpin.callInternal;
    made.vpin.callInternal = (method, ...args) => {
      if (method === "input_capture_release" && ++calls === 1) return Promise.reject(new Error("down"));
      return was(method, ...args);
    };
    await made.arm(true, 6000, away);

    await made.hold(2, true);
    await wait(140);

    assert.equal(calls, 2);
  });

  test("a window that loses focus while a button is held takes nothing", async () => {
    const { vpin, listeners, released, arm, hold, line } = await quickController();
    await arm(true, 6000, away);
    await hold(2, true);

    assert.equal(vpin._captureHeld.size, 1);
    for (const fn of listeners.blur) fn();
    await wait(140);

    assert.equal(released.length, 0);
    assert.equal(vpin._captureHeld.size, 0);
    assert.notEqual(line(), undefined);
  });

  test("a button already held when the buttons come to a page does not count", async () => {
    const { released, hold, arm } = await quickController();
    await hold(2, true);
    await arm(true, 6000, away);
    await wait(140);

    assert.equal(released.length, 0);
    await hold(2, false);
  });
});

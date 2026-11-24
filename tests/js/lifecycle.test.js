// Confirming before quitting, restarting or powering off.
//
// Core draws the dialog so that turning the setting on works on a theme written before
// it existed. What matters here is that nothing happens until the user answers, and that
// the buttons a cabinet actually has are the ones that answer it.

import { test, describe } from "node:test";
import assert from "node:assert/strict";

import { loadCore } from "./support/load-core.js";
import { codeFor } from "./support/browser.js";

// A controller core whose bridge answers instead of hanging, so a request can be
// followed all the way through rather than stopping at the first call.
function controller({ confirm = false } = {}) {
  const { VPinFECore, browser } = loadCore({ windowName: "table" });
  const listeners = {};
  browser.window.addEventListener = (type, fn) => { (listeners[type] ||= []).push(fn); };
  const vpin = new VPinFECore();
  vpin.init();
  vpin.isController = () => true;
  vpin.frontendInputEnabled = true;

  const asked = [];
  vpin.call = async (method, ...args) => {
    asked.push({ method, args });
    if (method === "lifecycle_needs_confirmation") {
      return { confirm, description: "Quit VPinFE" };
    }
    return true;
  };

  const press = async (key) => {
    const event = { key, code: codeFor(key), repeat: false, target: null,
                    preventDefault() {} };
    await Promise.all((listeners.keydown || []).map(fn => fn(event)));
  };
  const settle = () => new Promise(resolve => setTimeout(resolve, 0));
  const methods = () => asked.map(entry => entry.method);
  const dialog = () =>
    browser.document.body.children.find(el => el.className === "vpinfe-confirm");
  return { vpin, browser, press, settle, methods, asked, dialog };
}

describe("nothing is asked when the setting is off", () => {
  test("the request goes straight through", async () => {
    const { vpin, methods, dialog } = controller({ confirm: false });

    assert.equal(await vpin.requestLifecycle("vpinfe", "stop"), true);
    assert.deepEqual(methods(), ["lifecycle_needs_confirmation", "lifecycle_request"]);
    assert.equal(dialog(), undefined, "no dialog should have been drawn");
  });
});

describe("with the setting on, the user answers first", () => {
  test("nothing is requested until they do", async () => {
    const { vpin, press, settle, methods, dialog } = controller({ confirm: true });

    const pending = vpin.requestLifecycle("vpinfe", "stop");
    await settle();

    assert.deepEqual(methods(), ["lifecycle_needs_confirmation"],
      "the app must not be asked to quit before the user says so");
    assert.ok(dialog(), "the dialog should be on the page while it waits");

    await press("Escape");     // an unanswered dialog never resolves
    await pending;
  });

  test("select confirms it", async () => {
    const { vpin, press, settle, methods, asked, dialog } = controller({ confirm: true });

    const pending = vpin.requestLifecycle("vpinfe", "stop");
    await settle();
    await press("Enter");

    assert.equal(await pending, true);
    assert.deepEqual(methods(), ["lifecycle_needs_confirmation", "lifecycle_request"]);
    // The bridge is told the question was already put, so it does not ask again.
    assert.deepEqual(asked[1].args, ["vpinfe", "stop", "", true]);
    assert.equal(dialog(), undefined, "the dialog should be gone once answered");
  });

  // Escape is exit and b is back - the two the default keymap binds, and the two a
  // cabinet has buttons for.
  for (const key of ["Escape", "b"]) {
    test(`${key} cancels it, and the app keeps running`, async () => {
      const { vpin, press, settle, methods, dialog } = controller({ confirm: true });

      const pending = vpin.requestLifecycle("vpinfe", "stop");
      await settle();
      await press(key);

      assert.equal(await pending, false);
      assert.deepEqual(methods(), ["lifecycle_needs_confirmation"],
        "saying no must not reach the bridge at all");
      assert.equal(dialog(), undefined);
    });
  }

  test("a key that answers nothing leaves it up", async () => {
    // A cabinet has a handful of buttons and no keyboard. An unmapped press must not
    // count as an answer in either direction, or a flipper nudge quits the app.
    const { vpin, press, settle, methods, dialog } = controller({ confirm: true });

    const pending = vpin.requestLifecycle("vpinfe", "stop");
    await settle();
    await press("ArrowRight");
    await settle();

    assert.deepEqual(methods(), ["lifecycle_needs_confirmation"]);
    assert.ok(dialog(), "the dialog should still be waiting for a real answer");

    await press("Escape");
    await pending;
  });

  test("the key that answers does not also do its usual job", async () => {
    // Answering must not then be handled again as an ordinary press. Select is the
    // dangerous one: it is allowed through in modal mode, so without the confirm
    // claiming it, saying yes would also activate whatever is behind the dialog.
    const { vpin, press, settle, asked } = controller({ confirm: true });
    const acted = [];
    vpin.inputHandlers.push((action) => { acted.push(action); });

    const pending = vpin.requestLifecycle("vpinfe", "stop");
    await settle();
    await press("Enter");
    await settle();

    assert.equal(await pending, true);
    assert.deepEqual(acted, [], "the answer must not reach the theme as well");
    assert.equal(asked.filter(e => e.method === "lifecycle_request").length, 1,
      "answering once must request once");
  });

  test("the wheel does not move behind it", async () => {
    const { vpin, press, settle } = controller({ confirm: true });
    vpin.tableData = [{}, {}, {}];
    const before = vpin._currentTableIndex;

    const pending = vpin.requestLifecycle("vpinfe", "stop");
    await settle();
    await press("ArrowRight");

    assert.equal(vpin._currentTableIndex, before,
      "a confirm owns the input while it is up");

    await press("Escape");
    await pending;
  });

  test("the question is the wording the bridge gave", async () => {
    const { vpin, press, settle, dialog } = controller({ confirm: true });

    const pending = vpin.requestLifecycle("system", "stop");
    await settle();

    const text = dialog().children[0].children.map(el => el.textContent);
    assert.equal(text[0], "Quit VPinFE?");
    assert.match(text[1], /Select to confirm/);

    await press("Escape");
    await pending;
  });
});

// The overlay mechanism: one registry, one string, one toggle, one handler map.
//
// Three near-identical implementations preceded this, and they had already drifted -
// only two of the three told their iframe anything on open, and the state was three
// booleans for something that is one-of by construction. Every consumer re-derived it,
// so adding a fourth overlay was eleven edits across JavaScript, CSS and Python.
//
// What is asserted here is the shared behavior, once, rather than per overlay: the
// fade class, the frame created once and hidden rather than destroyed, mutual
// exclusion, and which handler receives an action while an overlay is open.

import { test, describe } from "node:test";
import assert from "node:assert/strict";

import { loadCore, newCore } from "./support/load-core.js";
import { codeFor } from "./support/browser.js";

// Dispatch is private and reached by a keypress, so this is the same shape input.test.js
// uses: capture the keydown listener core installs, then feed it an event.
function controller() {
  const { VPinFECore, browser } = loadCore({ windowName: "table" });
  const listeners = {};
  browser.window.addEventListener = (type, fn) => { (listeners[type] ||= []).push(fn); };
  const vpin = new VPinFECore();
  vpin.init();
  vpin.isController = () => true;
  vpin.frontendInputEnabled = true;
  const press = async (key) => {
    const event = { key, code: codeFor(key), repeat: false, target: null, preventDefault() {} };
    await Promise.all((listeners.keydown || []).map(fn => fn(event)));
  };
  return { vpin, press };
}

const OVERLAYS = [["menu", "menu-frame"],
                  ["collectionMenu", "collection-menu-frame"]];

describe("an overlay opens and closes the same way whichever it is", () => {
  for (const [name, frameId] of OVERLAYS) {
    test(`${name} opens, then closes`, async () => {
      const { vpin, browser } = newCore();
      const root = browser.document.getElementById("overlay-root");

      await vpin.toggleOverlay(name);
      assert.equal(vpin.overlay, name);
      assert.ok(root.classList.contains("active"), "faded in");
      const frame = browser.document.getElementById(frameId);
      assert.ok(frame, "the frame was created");
      assert.equal(frame.style.display, "block");

      await vpin.toggleOverlay(name);
      assert.equal(vpin.overlay, null);
      assert.ok(!root.classList.contains("active"), "faded out");
      assert.equal(frame.style.display, "none", "hidden, never destroyed");
    });
  }

  test("opening one closes the other", async () => {
    const { vpin } = newCore();

    await vpin.toggleOverlay("menu");
    await vpin.toggleOverlay("collectionMenu");

    // One string cannot hold two names, which is the point: three booleans could.
    assert.equal(vpin.overlay, "collectionMenu");
  });

  test("the frame is created once and reused", async () => {
    const { vpin, browser } = newCore();

    await vpin.toggleOverlay("menu");
    const first = browser.document.getElementById("menu-frame");
    await vpin.toggleOverlay("menu");
    await vpin.toggleOverlay("menu");

    assert.equal(browser.document.getElementById("menu-frame"), first);
  });

  test("the tutorial link is read from a contract 2 entry and from a contract 1 row", () => {
    const { vpin } = newCore();
    const link = "https://pinballprimer.github.io/example.html";
    vpin.tableData = [
      { game: { tutorial: ` ${link} ` } },
      { meta: { Info: { PinballPrimerTut: link } } },
      { game: { tutorial: "" }, meta: { Info: {} } },
    ];

    vpin._currentTableIndex = 0;
    assert.equal(vpin.getCurrentTutorialUrl(), link);
    vpin._currentTableIndex = 1;
    assert.equal(vpin.getCurrentTutorialUrl(), link);
    vpin._currentTableIndex = 2;
    assert.equal(vpin.getCurrentTutorialUrl(), "");
  });

  test("a tutorial with no URL leaves what is open alone", async () => {
    const { vpin } = newCore();
    vpin.getCurrentTutorialUrl = () => "";
    await vpin.toggleOverlay("menu");

    await vpin.toggleOverlay("tutorial");

    assert.equal(vpin.overlay, "menu",
      "prepare returning null must not close what was already up");
  });
});

describe("an overlay is told when it opens and when it closes", () => {
  test("open carries its own name, and the 2.x message rides behind it", async () => {
    const { vpin, browser } = newCore();

    await vpin.toggleOverlay("menu");

    // Fields, not deepEqual: the object is made inside the vm, so its prototype differs
    // from this one and strict deep equality would fail on that alone.
    const posted = browser.document.getElementById("menu-frame").posted;
    assert.equal(posted[0].event, "overlay_open");
    assert.equal(posted[0].overlay, "menu");
    assert.equal(posted.at(-1).event, "menu_open", "what mainmenu.js has always matched on");
  });

  test("close carries its own name, and reset state rides behind it", async () => {
    const { vpin, browser } = newCore();
    await vpin.toggleOverlay("menu");
    const frame = browser.document.getElementById("menu-frame");
    frame.posted.length = 0;

    await vpin.toggleOverlay("menu");

    assert.equal(frame.posted[0].event, "overlay_close");
    assert.equal(frame.posted[0].overlay, "menu");
    assert.equal(frame.posted.at(-1).event, "reset state");
  });
});

describe("an open overlay owns every action", () => {
  test("the action reaches the handler the open overlay registered", async () => {
    const { vpin, press } = controller();
    const seen = { menu: [], collectionMenu: [] };
    // Pushed directly: registerOverlayHandler awaits a bridge round trip the stub socket
    // never answers, and what is under test is the routing.
    vpin.overlayHandlers.menu = [(a) => seen.menu.push(a)];
    vpin.overlayHandlers.collectionMenu = [(a) => seen.collectionMenu.push(a)];
    vpin.overlay = "collectionMenu";

    await press("ArrowLeft");

    assert.deepEqual(seen.collectionMenu, ["previous"]);
    assert.deepEqual(seen.menu, [], "a closed overlay hears nothing");
  });

  test("registering against no overlay is refused rather than silently kept", async () => {
    const { vpin } = newCore();

    await vpin.registerOverlayHandler("nosuchoverlay", () => {});

    assert.equal(vpin.overlayHandlers.nosuchoverlay, undefined);
  });
});

// A browser asking to sign in, drawn by core over any theme: only on the controller
// window, never over a running table, and closed by the machine saying there is nothing
// left to show.

import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";

import { loadCore } from "./support/load-core.js";
import { codeFor } from "./support/browser.js";

const settle = (ms = 30) => new Promise(resolve => setTimeout(resolve, ms));

const ASKING = { id: "r1", address: "192.168.1.20", state: "waiting", code: "",
                 expires_in: 600 };
const SHOWN = { ...ASKING, state: "shown", code: "482915", expires_in: 590 };

const stillHeld = [];
afterEach(() => {
  for (const letGo of stillHeld.splice(0)) letGo();
});

// A core for one window with its keyboard listeners captured, and the calls it makes to
// the machine noted. `answers` is what a call is answered with.
function window_(name = "table", answers = {}) {
  const { VPinFECore, browser } = loadCore({ windowName: name });
  const listeners = {};
  browser.window.addEventListener = (type, fn) => { (listeners[type] ||= []).push(fn); };
  const vpin = new VPinFECore();
  vpin.init();
  if (name === "table") {
    vpin.isController = () => true;
    vpin.frontendInputEnabled = true;
  } else {
    vpin.isController = () => false;
  }
  const asked = [];
  vpin.callInternal = async (...call) => { asked.push(call); return answers[call[0]] ?? null; };
  vpin.call = async () => null;
  const push = (request) => vpin.handleEvent({ type: "SignInRequest", request });
  const frame = () => browser.document.getElementById("signin-frame");
  const press = async (key) => {
    const event = { key, code: codeFor(key), repeat: false, target: null,
                    preventDefault() {} };
    await Promise.all((listeners.keydown || []).map(fn => fn(event)));
  };
  const release = async (key) => {
    const event = { key, code: codeFor(key), target: null };
    await Promise.all((listeners.keyup || []).map(fn => fn(event)));
  };
  stillHeld.push(() => (listeners.blur || []).forEach(fn => fn()));
  return { vpin, browser, push, frame, press, release, asked, listeners };
}

describe("a request is drawn on the controller window", () => {
  test("it opens over the theme and hands the page the request", async () => {
    const { vpin, push, frame } = window_();

    await push(ASKING);
    await settle();

    assert.equal(vpin.overlay, "signIn");
    const opened = frame().posted.find(m => m.event === "overlay_open");
    assert.equal(opened.overlay, "signIn");
    assert.equal(opened.context.id, "r1");
    assert.equal(opened.context.address, "192.168.1.20");
    assert.equal(frame().style.width, "100vw");
  });

  test("a window that is not the controller draws nothing", async () => {
    const { vpin, push, browser } = window_("bg");

    await push(ASKING);
    await settle();

    assert.equal(vpin.overlay, null);
    assert.equal(browser.document.getElementById("signin-frame"), null);
  });

  test("Show's answer reaches the open page, which stays open", async () => {
    const { vpin, push, frame } = window_();
    await push(ASKING);
    await settle();

    await push(SHOWN);

    assert.equal(vpin.overlay, "signIn");
    const update = frame().posted.filter(m => m.event === "sign_in_state").at(-1);
    assert.equal(update.request.state, "shown");
    assert.equal(update.request.code, "482915");
  });

  test("the machine saying there is none closes it", async () => {
    const { vpin, push } = window_();
    await push(ASKING);
    await settle();

    await push(null);

    assert.equal(vpin.overlay, null);
  });

  test("a request that runs out closes itself", async () => {
    const { vpin, push } = window_();
    await push({ ...ASKING, expires_in: 0 });
    await settle();

    assert.equal(vpin.overlay, null);
  });

  test("a theme's own handlers never hear the message", async () => {
    const { vpin, push } = window_();
    const heard = [];
    vpin.registerEventHandler("SignInRequest", (message) => heard.push(message));

    await push(SHOWN);
    await settle();

    assert.deepEqual(heard, []);
  });
});

const ASKED = { id: "a1", kind: "approval", program: "Home", method: "DELETE",
                path: "/games/a/folder", body: "", long: false, expires_in: 3000 };

describe("a program's ask is drawn the same way", () => {
  test("it opens the same page with the ask and is closed by the machine saying there is none", async () => {
    const { vpin, push, frame } = window_();

    await push(ASKED);
    await settle();

    assert.equal(vpin.overlay, "signIn");
    const opened = frame().posted.find(m => m.event === "overlay_open");
    assert.equal(opened.context.kind, "approval");
    assert.equal(opened.context.path, "/games/a/folder");

    await push(null);
    assert.equal(vpin.overlay, null);
  });

  test("exit leaves it by the same call, with the ask's id", async () => {
    const { push, press, asked } = window_();
    await push(ASKED);
    await settle();

    await press("q");
    await settle();

    assert.deepEqual(asked.filter(c => c[0] === "signin_dismiss"), [["signin_dismiss", "a1"]]);
  });

  test("an ask that runs out closes itself", async () => {
    const { vpin, push } = window_();
    await push({ ...ASKED, expires_in: 0 });
    await settle();

    assert.equal(vpin.overlay, null);
  });
});

describe("it waits while a table is running", () => {
  for (const [launching, done] of [["TableLaunching", "TableLaunchComplete"],
                                   ["RemoteLaunching", "RemoteLaunchComplete"]]) {
    test(`${launching} holds it, and ${done} shows it`, async () => {
      const { vpin, push } = window_();
      await vpin.handleEvent({ type: launching });

      await push(ASKING);
      await settle();
      assert.equal(vpin.overlay, null, "nothing is drawn over a running table");

      await vpin.handleEvent({ type: done });
      await settle();
      assert.equal(vpin.overlay, "signIn");
    });
  }

  test("a request already waiting is asked for again when the window regains focus", async () => {
    const { vpin, listeners } = window_("table", { signin_pending: ASKING });

    await Promise.all(listeners.focus.map(fn => fn()));
    await settle();

    assert.equal(vpin.overlay, "signIn");
  });
});

describe("the buttons and the keyboard answer it", () => {
  test("every action but exit reaches the page, and the menu keys open nothing", async () => {
    const { vpin, push, press, release } = window_();
    const heard = [];
    vpin.overlayHandlers.signIn = [(action) => heard.push(action)];
    await push(ASKING);
    await settle();

    await press("ArrowRight");
    await release("ArrowRight");
    await press("m");
    await release("m");

    assert.deepEqual(heard.slice(0, 1), ["next"]);
    assert.equal(vpin.overlay, "signIn", "the main menu must not open over it");
  });

  test("exit closes the request on the machine, and the machine's answer closes the page", async () => {
    const { vpin, push, press, asked } = window_();
    await push(SHOWN);
    await settle();

    await press("q");
    await settle();

    assert.deepEqual(asked.filter(c => c[0] === "signin_dismiss"), [["signin_dismiss", "r1"]]);
    assert.ok(!asked.some(c => c[0] === "lifecycle_request"), "exit must not quit");
    assert.equal(vpin.overlay, "signIn", "it closes on the machine's answer, not before");

    await push(null);
    assert.equal(vpin.overlay, null);
  });

  test("a tap of the back key is the page's own back", async () => {
    const { vpin, push, press, release } = window_();
    const heard = [];
    vpin.overlayHandlers.signIn = [(action) => heard.push(action)];
    await push(ASKING);
    await settle();

    await press("Escape");
    await release("Escape");

    assert.deepEqual(heard, ["back"]);
  });
});

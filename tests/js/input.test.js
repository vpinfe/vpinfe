// Keyboard input: one dispatch, and it does not quit the app from inside a menu.
//
// These started as throwaway checks that asserted the defects existed. They are inverted
// here: each one now pins the fixed behavior, so the same evidence that found the bugs is
// the net that keeps them fixed.

import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";

import { loadCore, plain } from "./support/load-core.js";
import { codeFor } from "./support/browser.js";

// A test that presses without releasing leaves a hold repeating, and the runner waits
// for its timer. A key held with no keyup is a real state - it is what losing focus
// looks like - and core answers it on blur, so that is what this fires.
const stillHeld = [];
afterEach(() => {
  for (const letGo of stillHeld.splice(0)) letGo();
});

// A controller-window core with the keydown listener core installs captured, so a test
// can fire a key the way the browser would. Overlay flags are set directly: what is
// under test is what a keypress does while one is up, not how it got there.
function controller() {
  const { VPinFECore, browser } = loadCore({ windowName: "table" });
  const listeners = {};
  browser.window.addEventListener = (type, fn) => { (listeners[type] ||= []).push(fn); };
  const vpin = new VPinFECore();
  vpin.init();
  const socket = browser.WebSocket.instances[0];
  vpin.isController = () => true;
  vpin.frontendInputEnabled = true;

  // The listener is async, so a handler runs on a microtask - a synchronous assert
  // would look at the list before anything reached it.
  // Core navigation answers previous/next itself when on, so a test about what reaches
  // a theme turns it off; the tests for core navigation turn it back on deliberately.
  // Poked directly: there is no enableCoreNavigation() and never was. This line used to
  // call one through optional chaining, which silently did nothing.
  vpin._capabilities && (vpin._capabilities.core_navigation = false);

  // A browser reports both: `key` is what the key produced, `code` is which key it is.
  // Defaulting the code to the key made "q" arrive as code "q", which no real keyboard
  // sends - and the shipped bindings are codes, so those presses stopped matching.
  const press = async (key, { code = codeFor(key), repeat = false,
                              target = null } = {}) => {
    const event = { key, code, repeat, target, prevented: false,
                    preventDefault() { event.prevented = true; } };
    await Promise.all((listeners.keydown || []).map(fn => fn(event)));
    return event;
  };
  // The other half of a press. A test that only ever presses is describing a key that
  // is still down, which is a state core deliberately treats as a hold.
  const release = async (key, { code = codeFor(key) } = {}) => {
    const event = { key, code, target: null };
    await Promise.all((listeners.keyup || []).map(fn => fn(event)));
    return event;
  };
  const blur = () => (listeners.blur || []).forEach(fn => fn());
  stillHeld.push(blur);
  const calls = () => socket.sent.filter(m => m.type === "api_call").map(m => m.method);
  // Exit quits through the lifecycle request now, which asks whether to confirm before
  // it asks to quit. Either call means the quit path was entered, which is what these
  // tests are about - not the name of the method that carries it.
  const quits = async () => {
    // The request is its own async chain, so the send lands a microtask after the
    // keydown listener resolves. Without this, every one of these passes vacuously.
    await new Promise(resolve => setTimeout(resolve, 0));
    return calls().some(m => m === "close_app" || m === "lifecycle_request"
                             || m === "lifecycle_needs_confirmation");
  };
  return { vpin, browser, press, release, blur, calls, quits };
}

// The hold Quit asks for is 1500ms, which a test should not wait for. Shortened on the
// instance, so the one test that pins the shipped length still reads the real one.
const HOLD_MS = 20;
const quickHold = (vpin) => { vpin.keyHoldMap = { exit: [{ token: "escape", ms: HOLD_MS }] }; };
const heldFor = (ms = HOLD_MS) => new Promise(resolve => setTimeout(resolve, ms + 25));

describe("exit never quits VPinFE from inside an overlay", () => {
  // Quit is a held Escape and q, and an overlay's own Escape handler never runs because
  // nothing focuses the iframe - so a one-key exit from any menu is what this prevents.
  // Driven through vpin.overlay, the state itself. Setting it is not the same as opening
  // the overlay for real - the tutorial refuses to open without a url - and what is under
  // test is which branch the keypress takes, not the hosting.
  for (const [name, overlay] of [["main menu", "menu"],
                                 ["collection menu", "collectionMenu"],
                                 ["tutorial", "tutorial"]]) {
    test(`a held Escape with the ${name} up closes it instead of the app`, async () => {
      const { vpin, press, quits } = controller();
      quickHold(vpin);
      vpin.overlay = overlay;

      await press("Escape");
      await heldFor();

      assert.equal(await quits(), false,
        "quitting must not be reachable while an overlay is up");
      assert.equal(vpin.overlay, null, "the overlay should have closed");
    });

    test(`q with the ${name} up closes it instead of the app`, async () => {
      const { vpin, press, quits } = controller();
      vpin.overlay = overlay;

      await press("q");

      assert.equal(await quits(), false,
        "quitting must not be reachable while an overlay is up");
      assert.equal(vpin.overlay, null, "the overlay should have closed");
    });

    test(`a tap of Escape with the ${name} up is back, for the overlay to answer`, async () => {
      const { vpin, press, release, quits } = controller();
      vpin.overlay = overlay;
      const heard = [];
      vpin.overlayHandlers[overlay] = [(action) => { heard.push(action); }];

      await press("Escape");
      assert.deepEqual(heard, [], "a key with a hold says nothing until it is let go");
      await release("Escape");

      assert.deepEqual(heard, ["back"]);
      assert.equal(await quits(), false);
    });
  }

  test("exit still quits when no overlay is up", async () => {
    const { press, quits } = controller();

    await press("q");

    assert.equal(await quits(), true,
      "exit has to keep working where it was always meant to");
  });

  test("a held Escape quits when no overlay is up", async () => {
    const { vpin, press, quits } = controller();
    quickHold(vpin);

    await press("Escape");
    await heldFor();

    assert.equal(await quits(), true);
  });
});

describe("a bound key belongs to core", () => {
  test("a matched action prevents the browser default", async () => {
    const { press } = controller();

    assert.equal((await press("ArrowLeft")).prevented, true,
      "without this the arrows also scroll the theme's page");
  });

  test("an unbound key is left alone", async () => {
    const { press } = controller();

    assert.equal((await press("F9")).prevented, false,
      "core must not swallow keys it has no binding for");
  });
});

describe("a held control repeats, and the repeat is ours", () => {
  // The OS repeat used to be the whole mechanism, throttled to keep it from flooding the
  // wheel. It was the one producer a flipper and a phone could not match: the rate is a
  // machine setting, there is no ramp, and a held gamepad button did not repeat at all.
  // So the curve moved into core, and these say what that means at the keyboard.
  function handler() {
    const made = controller();
    // Contract 2, so the names below are core's own rather than the translated ones a
    // contract 1 theme is handed. What is under test is the repeat, not the naming.
    made.vpin.contract = 2;
    const seen = [];
    // Pushed directly: registerInputHandler awaits a bridge round-trip before it
    // registers anything, and what is under test here is dispatch.
    made.vpin.inputHandlers.push((action) => { seen.push(action); });
    return { ...made, seen };
  }

  test("the operating system's own repeat is ignored", async () => {
    const { press, seen } = handler();

    await press("ArrowLeft");
    await press("ArrowLeft", { repeat: true });
    await press("ArrowLeft", { repeat: true });

    assert.deepEqual(seen, ["previous"],
      "an OS repeat has no ramp and no agreement with any other control");
  });

  test("a repeat the OS sends is still claimed", async () => {
    // Not acting on it is not the same as leaving it to the browser: the arrows would
    // scroll the theme's page underneath for as long as the key was held.
    const { press } = handler();

    await press("ArrowLeft");

    assert.equal((await press("ArrowLeft", { repeat: true })).prevented, true);
  });

  test("holding it walks the wheel", async () => {
    const { vpin, press, seen } = handler();
    quickly(vpin);

    await press("ArrowLeft");
    await ticks();

    assert.ok(seen.length > 1, "a held control has to keep going, or it is a tap");
    assert.ok(seen.every(one => one === "previous"));
  });

  test("letting go stops it", async () => {
    const { vpin, press, release, seen } = handler();
    quickly(vpin);

    await press("ArrowLeft");
    await ticks();
    await release("ArrowLeft");
    const whenReleased = seen.length;
    await ticks();

    assert.equal(seen.length, whenReleased);
  });

  test("losing focus stops it too", async () => {
    // A window that loses focus mid-hold never sees the keyup, and the wheel would keep
    // travelling behind whatever took the focus.
    const { vpin, press, blur, seen } = handler();
    quickly(vpin);

    await press("ArrowLeft");
    await ticks();
    blur();
    const whenBlurred = seen.length;
    await ticks();

    assert.equal(seen.length, whenBlurred);
  });

  test("the other direction takes the hold over", async () => {
    // You cannot ask to go left and right at once, and a wheel that tried would answer
    // neither.
    const { vpin, press, seen } = handler();
    quickly(vpin);

    await press("ArrowLeft");
    await press("ArrowRight");
    await ticks();

    assert.equal(seen.filter(one => one === "previous").length, 1,
      "the first direction stopped repeating when the second was pressed");
    assert.ok(seen.filter(one => one === "next").length > 1);
  });

  test("select does not repeat, however long it is held", async () => {
    // Holding it would launch twice. A hold means "keep going", and going is something
    // only the four movement actions do.
    const { vpin, press, seen } = handler();
    quickly(vpin);

    await press("Enter");
    await ticks();

    assert.equal(seen.filter(one => one === "select").length, 1);
  });

  test("a held page button keeps its own steady pace while a step button speeds up", async () => {
    const { vpin, press, release, seen } = handler();
    quickly(vpin);
    vpin.pageRepeatMs = 40;
    const times = { page_next: [], next: [] };
    vpin.inputHandlers.push((action) => { if (times[action]) times[action].push(Date.now()); });

    await press("PageDown");
    await new Promise(resolve => setTimeout(resolve, 400));
    await release("PageDown");
    await press("ArrowRight");
    await new Promise(resolve => setTimeout(resolve, 400));
    await release("ArrowRight");

    // Leave out the press itself: the gap after it is the hold delay, not the pace.
    const repeats = times.page_next.slice(1);
    const gaps = repeats.slice(1).map((at, i) => at - repeats[i]);
    assert.ok(gaps.length >= 3, `a held page button has to keep going: ${gaps}`);
    assert.ok(gaps.every(gap => gap >= 30), `a page repeat came faster than its pace: ${gaps}`);
    assert.ok(times.next.length > times.page_next.length * 2,
      "the step button should still repeat much faster than the page button");
    assert.ok(seen.includes("page_next") && seen.includes("next"));
  });
});

describe("defaults agree across the boundary", () => {
  test("back is bound in the JavaScript fallback too", () => {
    const { vpin } = controller();

    assert.deepEqual([...vpin.keyActionMap.back], ["escape", "keyb"],
      "Python ships Escape and KeyB for back; an empty fallback meant back did nothing "
      + "until the bridge answered");
  });

  test("quit is a held Escape in the JavaScript fallback too", () => {
    const { vpin } = controller();

    assert.deepEqual([...vpin.keyActionMap.exit], ["keyq"]);
    assert.deepEqual(plain(vpin.keyHoldMap), { exit: [{ token: "escape", ms: 1500 }] },
      "a tap of Escape must be back before the bridge answers, not a one-key exit");
  });
});

describe("one dispatch, wherever focus is", () => {
  // Key events go to the focused document, so a touch or click inside an overlay used
  // to take them away from the window listener - which is why each overlay carried its
  // own hardcoded map. Core listens on the overlay's window too now, so the configured
  // bindings apply either way and those three maps could go.
  test("core listens on an overlay's own window when it opens", async () => {
    const { VPinFECore, browser } = loadCore({ windowName: "table" });
    browser.window.addEventListener = () => {};
    const framed = [];
    const original = browser.document.createElement;
    browser.document.createElement = (tag) => {
      const el = original.call(browser.document, tag);
      if (tag === "iframe") {
        el.contentWindow = {
          addEventListener: (type) => framed.push(type),
          postMessage: () => {},
        };
      }
      return el;
    };
    const vpin = new VPinFECore();
    vpin.init();
    vpin.call = () => Promise.resolve(undefined);

    await vpin.toggleMenu();

    assert.equal(framed.includes("keydown"), true,
      "without this, keys do nothing once focus is inside the overlay");
  });
});

describe("typing is typing, not input actions", () => {
  // b, c, m, q and t are bound by default, and core listens inside overlays now - so
  // without this guard the collection menu's save-filter box could not accept them,
  // and Enter would fire select instead of reaching the field.
  const field = (type = "text") => ({ tagName: "INPUT", type });

  for (const key of ["b", "c", "m", "q", "t", "Enter", "Escape"]) {
    test(`${key} typed into a text field is left to the field`, async () => {
      const { vpin, press, quits } = controller();
      const seen = [];
      vpin.inputHandlers.push((action) => { seen.push(action); });

      const event = await press(key, { target: field() });

      assert.deepEqual(seen, [], `${key} must reach the field, not the wheel`);
      assert.equal(event.prevented, false, "the field needs the browser default");
      assert.equal(await quits(), false, "typing must never quit the app");
    });
  }

  test("Escape in a dialog's field dismisses the dialog", async () => {
    const { vpin, press, quits } = controller();
    vpin.contract = 2;
    vpin.pushInputMode("text");
    const seen = [];
    vpin.inputHandlers.push((action) => { seen.push(action); });

    const event = await press("Escape", { target: field() });

    assert.deepEqual(seen, ["back"]);
    assert.equal(event.prevented, true);
    assert.equal(await quits(), false);
  });

  test("a letter bound to back is still typed into a dialog's field", async () => {
    const { vpin, press } = controller();
    vpin.contract = 2;
    vpin.pushInputMode("text");
    const seen = [];
    vpin.inputHandlers.push((action) => { seen.push(action); });

    const event = await press("b", { target: field() });

    assert.deepEqual(seen, []);
    assert.equal(event.prevented, false);
  });

  test("a held Escape in a dialog's field dismisses it once", async () => {
    const { vpin, press } = controller();
    vpin.contract = 2;
    vpin.pushInputMode("text");
    const seen = [];
    vpin.inputHandlers.push((action) => { seen.push(action); });

    await press("Escape", { target: field() });
    await press("Escape", { target: field(), repeat: true });

    assert.deepEqual(seen, ["back"]);
  });

  test("a checkbox is not a text field, so bindings still work", async () => {
    const { vpin, press } = controller();
    vpin.contract = 2;
    const seen = [];
    vpin.inputHandlers.push((action) => { seen.push(action); });

    await press("ArrowLeft", { target: field("checkbox") });

    assert.deepEqual([...seen], ["previous"]);
  });
});

describe("a theme is handed the action names its contract published", () => {
  // Twelve registry themes switch on `case "joyleft"`. Core dispatches `previous` now,
  // so contract 1 gets translated at the theme boundary and nothing else does.
  const themeSees = (contract) => {
    const { vpin, press } = controller();
    vpin.contract = contract;
    const seen = [];
    vpin.inputHandlers.push((action) => { seen.push(action); });
    return { seen, press };
  };

  test("contract 1 still receives joyleft", async () => {
    const { seen, press } = themeSees(1);
    await press("ArrowLeft");
    assert.deepEqual(seen, ["joyleft"], "every published theme switches on this");
  });

  test("contract 2 receives previous", async () => {
    const { seen, press } = themeSees(2);
    await press("ArrowLeft");
    assert.deepEqual([...seen], ["previous"]);
  });

  test("up merged into paging, which core consumes by default", async () => {
    // joyup and joypageup were the same intent under two names, so ArrowUp is a paging
    // action now - and core_paging is on, so core answers it and no theme sees it.
    // That is the merge working: carousel-desktop's dead page-jump cases were dead
    // precisely because core already owned the paging actions.
    const { seen, press } = themeSees(1);

    await press("ArrowUp");

    assert.deepEqual(seen, [], "core handles paging; the theme is not asked");
  });

  test("take picture is core's, and no contract is handed it", async () => {
    for (const contract of [1, 2]) {
      const { vpin, press } = controller();
      vpin.contract = contract;
      vpin.keyActionMap.take_picture = ["keyp"];
      const seen = [];
      vpin.inputHandlers.push((action) => { seen.push(action); });
      await press("p");
      assert.deepEqual(seen, [], `contract ${contract}`);
    }
  });
});

describe("a paging action moves the way its name says", () => {
  // Nothing asserted this before, and nothing could: core meant next by page_up while
  // its own menus meant previous, and two themes read the ambiguity the same wrong way
  // and shipped paging that ran backwards. The name states the direction now.
  const pagesTo = async (key) => {
    const { vpin, press } = controller();
    vpin._capabilities.core_paging = true;
    const asked = [];
    vpin.call = async (method, _index, direction) => {
      if (method === "get_page_index") asked.push(direction);
      return -1;
    };

    await press(key);
    await new Promise(resolve => setTimeout(resolve, 0));
    return asked;
  };

  test("PageUp pages backward", async () => {
    assert.deepEqual(await pagesTo("PageUp"), ["prev"]);
  });

  test("PageDown pages forward", async () => {
    assert.deepEqual(await pagesTo("PageDown"), ["next"]);
  });
});

// A controller with core navigation left on, which is its default.
function navigating(count = 3) {
  const { vpin, browser, press, release, calls } = controller();
  vpin._capabilities.core_navigation = true;
  vpin.tableData = Array.from({ length: count }, (_, i) => ({ tableDirName: `G${i}` }));
  const moves = [];
  vpin.sendMessageToAllWindowsIncSelf = (m) => moves.push(m);
  return { vpin, browser, press, release, calls, moves };
}

describe("core moves the selection so a theme does not have to", () => {
  test("next advances and announces where it went", async () => {
    const { vpin, press, moves } = navigating();

    await press("ArrowRight");

    assert.equal(vpin._currentTableIndex, 1);
    assert.equal(moves.at(-1).type, "TableIndexUpdate");
    assert.equal(moves.at(-1).index, 1);
    assert.equal(moves.at(-1).previous, 0);
    assert.equal(moves.at(-1).direction, "next");
  });

  test("it wraps both ways rather than sticking at the ends", async () => {
    const { vpin } = navigating();

    assert.equal(vpin.moveBy(-1), 2, "previous from the first goes to the last");
    assert.equal(vpin.moveBy(1), 0, "next from the last goes to the first");
  });

  test("an empty library does not move or announce", () => {
    const { vpin, moves } = navigating(0);

    assert.equal(vpin.moveBy(1), 0);
    assert.deepEqual(moves, [], "this is the undefined index two themes broadcast");
  });

  test("a theme can still opt out", async () => {
    const { vpin, press } = navigating();
    vpin._capabilities.core_navigation = false;
    const seen = [];
    vpin.contract = 2;
    vpin.inputHandlers.push((a) => seen.push(a));

    await press("ArrowRight");

    assert.deepEqual(seen, ["next"], "the raw action reaches the theme");
    assert.equal(vpin._currentTableIndex, 0, "and core has not moved anything");
  });
});

describe("what a keypress means depends on the mode", () => {
  test("a dialog keeps arrows off the menu behind it", async () => {
    // B2: the collection menu's save-filter dialog had no such state, so arrows drove
    // the menu underneath and Enter opened a dropdown instead of saving.
    const { vpin, press } = navigating();
    vpin.pushInputMode("modal");
    const seen = [];
    vpin.contract = 2;
    vpin.inputHandlers.push((a) => seen.push(a));

    await press("m");
    await press("t");

    assert.deepEqual(seen, [], "menu and tutorial must not open over a dialog");
  });

  test("typing reaches the field, and back still dismisses", async () => {
    const { vpin, press } = navigating();
    vpin.pushInputMode("text");
    const seen = [];
    vpin.contract = 2;
    vpin.inputHandlers.push((a) => seen.push(a));

    await press("m");
    await press("b");

    assert.deepEqual(seen, ["back"], "the menu must not open over a dialog's field");
  });

  test("a select that is not typed into the field finishes the dialog", async () => {
    const { vpin, press } = navigating();
    vpin.pushInputMode("text");
    const seen = [];
    vpin.contract = 2;
    vpin.inputHandlers.push((a) => seen.push(a));

    await press("ArrowRight");
    await press("Enter");

    assert.deepEqual(seen, ["select"]);
  });

  test("the base mode cannot be popped away", () => {
    const { vpin } = navigating();

    vpin.popInputMode("navigation");

    assert.equal(vpin.inputMode, "navigation");
  });

  test("pushing returns its own undo", () => {
    const { vpin } = navigating();

    const done = vpin.pushInputMode("modal");
    assert.equal(vpin.inputMode, "modal");
    done();

    assert.equal(vpin.inputMode, "navigation");
  });
});

describe("stepping and paging are one capability at contract 2", () => {
  // The fold happens where it is true: at contract 2 core_navigation owns all four
  // actions and a theme sees one knob. Below it the two stay separate,
  // because 2.x core pages for a theme and leaves the cursor to it - opposite defaults,
  // and preserving them is the only reason core_paging still exists.
  test("a contract 2 theme turns both off with one key", async () => {
    const { vpin, press } = controller();
    vpin.contract = 2;
    vpin._capabilities.core_navigation = false;
    vpin._capabilities.core_paging = true;   // ignored at contract 2
    vpin.tableData = [{}, {}, {}, {}, {}];
    const seen = [];
    vpin.inputHandlers.push((a) => seen.push(a));

    await press("ArrowLeft");
    await press("PageDown");

    assert.deepEqual(seen, ["previous", "page_next"],
      "one capability off must hand the theme every action it covers");
  });

  test("a contract 1 theme still pages while core leaves its cursor alone", async () => {
    const { vpin, press } = controller();
    vpin.contract = 1;
    vpin._capabilities.core_navigation = false;   // the contract 1 default
    vpin._capabilities.core_paging = true;        // 2.x parity
    vpin.tableData = [{}, {}, {}, {}, {}];
    const seen = [];
    vpin.inputHandlers.push((a) => seen.push(a));

    await press("ArrowLeft");

    // joyleft, not previous: a contract 1 theme is handed the spelling it was written
    // against. What matters here is that the action reached it at all.
    assert.deepEqual(seen, ["joyleft"], "a 2.x theme moves its own cursor");
  });

  test("neither takes an action outside navigation mode", async () => {
    const { vpin, press } = controller();
    vpin._capabilities.core_navigation = true;
    vpin._capabilities.core_paging = true;
    vpin.tableData = [{}, {}, {}, {}, {}];
    const before = vpin._currentTableIndex;
    vpin.pushInputMode("modal");

    await press("ArrowLeft");
    await press("PageDown");

    assert.equal(vpin._currentTableIndex, before,
      "a modal owns the input; neither the wheel nor a page may move behind it");
  });
});


describe("a tap of back at the root does nothing", () => {
  // Leaving is exit's - a held Escape or button 1, q, the menu's Quit - so back at the root
  // is swallowed whichever way Confirm Before Exit is set.
  const atRoot = (contract, { confirm = false } = {}) => {
    const { vpin, browser, press, release } = navigating();
    vpin.contract = contract;
    // Recorded and then let through, so a request that did go out would reach the
    // confirm dialog when the setting is on rather than stop at a stub.
    const asked = [];
    const request = vpin.requestLifecycle.bind(vpin);
    vpin.requestLifecycle = (scope, action, ...rest) => {
      asked.push([scope, action]);
      return request(scope, action, ...rest);
    };
    // The bridge as lifecycle.test.js has it.
    const sent = [];
    vpin.call = async (method, ...args) => {
      sent.push(method);
      if (method === "lifecycle_needs_confirmation") {
        return { confirm, description: "Quit VPinFE" };
      }
      return true;
    };
    const settle = () => new Promise(resolve => setTimeout(resolve, 0));
    const dialog = () =>
      browser.document.body.children.find(el => el.className === "vpinfe-confirm");
    return { vpin, press, release, asked, sent, settle, dialog };
  };

  for (const confirm of [false, true]) {
    const setting = confirm ? "on" : "off";

    test(`a contract 2 theme asks for nothing with the confirm ${setting}`, async () => {
      const { vpin, press, release, asked, sent, settle, dialog } = atRoot(2, { confirm });
      const seen = [];
      vpin.inputHandlers.push((a) => seen.push(a));

      await press("b");
      await release("b");
      // Escape waits for its release to say whether it was a tap, so a press alone
      // would leave this looking at a key that is still down.
      await press("Escape");
      await release("Escape");
      await settle();

      assert.deepEqual(asked, [], "no lifecycle request");
      assert.deepEqual(sent, [], "and nothing sent to the bridge for one");
      assert.equal(dialog(), undefined, "no confirm dialog is put up either");
      assert.deepEqual(seen, [], "the theme is not handed a back that core declined");
    });
  }

  test("q still quits from the same place", async () => {
    const { press, asked } = atRoot(2);

    await press("q");

    assert.deepEqual(asked, [["vpinfe", "stop"]],
                     "the tap that does nothing is back's, not every key's");
  });

  test("a contract 1 theme keeps its own back", async () => {
    const { vpin, press, asked } = atRoot(1);
    const seen = [];
    vpin.inputHandlers.push((a) => seen.push(a));

    await press("b");

    assert.deepEqual(asked, [], "taking back would break every theme that handles it");
    assert.deepEqual(seen, ["joyback"], "it reaches the theme under its own name");
  });

  // A field and a dialog both mean back for dismiss, so the theme hears it there.
  for (const mode of ["text", "modal"]) {
    test(`${mode === "text" ? "typing" : "a dialog"} still hears back`, async () => {
      const { vpin, press, asked } = atRoot(2);
      const seen = [];
      vpin.inputHandlers.push((a) => seen.push(a));
      vpin.pushInputMode(mode);

      await press("b");

      assert.deepEqual(asked, []);
      assert.deepEqual(seen, ["back"]);
    });
  }

  test("inside a list back comes out of the list, not the app", async () => {
    const { vpin, press, asked } = atRoot(2);
    vpin.pushList(vpin.createList([{ name: "Favorites" }], { kind: "collection" }));

    await press("b");

    assert.deepEqual(asked, [], "there was somewhere to go back to");
    assert.equal(vpin.atRoot, true);
  });
});

describe("an overlay over a list answers select and back itself", () => {
  // Core owns both while it holds a descended list, so without the overlay check a Back
  // meant to close the menu popped the list under it, and Select chose a row behind it.
  const over = () => {
    const { vpin, press } = navigating();
    vpin.contract = 2;
    vpin.pushList(vpin.createList([{ name: "Favorites" }], { kind: "collection" }));
    vpin.overlay = "menu";
    const heard = [];
    vpin.overlayHandlers.menu = [(action) => { heard.push(action); }];
    const chose = [];
    vpin.selectCurrent = () => { chose.push("row"); };
    return { vpin, press, heard, chose };
  };

  test("back reaches the overlay and leaves the list where it is", async () => {
    const { vpin, press, heard } = over();

    await press("b");

    assert.deepEqual(heard, ["back"]);
    assert.equal(vpin.atRoot, false, "the list under the overlay is not popped");
  });

  test("select reaches the overlay and chooses nothing behind it", async () => {
    const { press, heard, chose } = over();

    await press("Enter");

    assert.deepEqual(heard, ["select"]);
    assert.deepEqual(chose, []);
  });

  test("with no overlay the same keys are the list's", async () => {
    const { vpin, press, heard, chose } = over();
    vpin.overlay = null;

    await press("Enter");
    await press("b");

    assert.deepEqual(heard, []);
    assert.deepEqual(chose, ["row"]);
    assert.equal(vpin.atRoot, true, "back came out of the list");
  });
});

// Chords: what a cabinet actually asks for. Hold both flippers to exit.
//
// The design decision these pin: the members fire, and their repeat
// stops once every one of them is down. So both flippers move the wheel one step each
// way and then exit, instead of spinning it for the length of the hold - and no latency
// is added to a flipper press, which is the one thing that must not happen here.
describe("reading a chord out of a binding", () => {
  const { context } = loadCore({ windowName: "table" });
  const { parseChordBinding, downToken } = context;

  test("members are device selectors and the hold comes off the end", () => {
    const found = parseChordBinding(
      "exit", "chord(key:ShiftLeft+key:ShiftRight)@hold:1500");

    assert.equal(found.action, "exit");
    assert.equal(found.holdMs, 1500);
    assert.deepEqual([...found.members], ["key:shiftleft", "key:shiftright"]);
  });

  test("a chord can mix a key and a button", () => {
    const found = parseChordBinding("exit", "chord(key:Escape+pad:0/button:5)");

    assert.deepEqual([...found.members], ["key:escape", "pad:5"]);
    assert.equal(found.holdMs, 0);
  });

  test("one input is not a chord, and nor is the same one twice", () => {
    assert.equal(parseChordBinding("exit", "chord(key:a)"), null);
    assert.equal(parseChordBinding("exit", "chord(key:a+key:A)"), null);
  });

  test("anything that is not a chord is left alone", () => {
    for (const binding of ["key:Escape", "pad:0/button:3", "", "chord("]) {
      assert.equal(parseChordBinding("exit", binding), null, binding);
    }
  });

  test("a key folds to the case the single-key map already stores", () => {
    assert.equal(downToken("key:ShiftLeft"), "key:shiftleft");
    assert.equal(downToken("pad:0/button:5"), "pad:5");
    assert.equal(downToken("pad:0/axis:1+"), "");
  });
});

// A core with the listeners captured and a set of bindings already read in.
async function chorded(bindings) {
  const { VPinFECore, browser, context } = loadCore({ windowName: "table" });
  const listeners = {};
  browser.window.addEventListener = (type, fn) => { (listeners[type] ||= []).push(fn); };
  const vpin = new VPinFECore();
  vpin.init();
  vpin.isController = () => true;
  vpin.frontendInputEnabled = true;
  vpin._capabilities && (vpin._capabilities.core_navigation = false);
  // Contract 2, so an action reaches the handler under its own name rather than the
  // legacy one twelve published themes still match on.
  vpin.contract = 2;

  // Through the same pure function core builds its maps with, and from *this* core's
  // context: the method that calls it is private, and a second context would hand back
  // objects built against a different prototype.
  const maps = context.buildBindingMaps(bindings);
  vpin.keyActionMap = maps.keys;
  vpin.keyHoldMap = maps.keyHolds;
  vpin.joyButtonMap = maps.buttons;
  vpin.standardButtonMap = maps.standard;
  vpin.joyHoldMap = maps.buttonHolds;
  vpin.chordBindings = maps.chords;
  vpin.chordMembers = maps.members;

  // The theme's own handler is where an action lands when no overlay is up.
  const seen = [];
  vpin.inputHandlers = [(action) => { seen.push(action); }];
  const fire = async (type, code, repeat = false) => {
    const event = { key: code, code, repeat, target: null, prevented: false,
                    preventDefault() { event.prevented = true; } };
    await Promise.all((listeners[type] || []).map(fn => fn(event)));
    return event;
  };
  stillHeld.push(() => (listeners.blur || []).forEach(fn => fn()));
  return {
    vpin, seen, browser,
    blur: () => (listeners.blur || []).forEach(fn => fn()),
    down: (code, repeat = false) => fire("keydown", codeFor(code), repeat),
    up: (code) => fire("keyup", codeFor(code)),
  };
}

// The repeat curve, compressed so a test about holding does not cost most of a second.
// The shape is what is under test - a delay, then repeats - not the numbers, which are
// a feel decision and belong to the install.
function quickly(vpin) {
  vpin.repeatDelayMs = 5;
  vpin.repeatStartMs = 5;
  vpin.repeatFloorMs = 5;
  vpin.pageRepeatMs = 5;
}

// Long enough for several repeats at the compressed interval.
const ticks = () => new Promise(resolve => setTimeout(resolve, 60));

// The chord is on an action core forwards to the theme. `menu` would have worked and
// told us nothing: core opens the overlay itself, so the chord firing looks the same as
// the chord not firing from the handler's side.
const FLIPPERS = {
  previous: ["key:KeyA"],
  next: ["key:KeyB"],
  page_next: ["chord(key:KeyA+key:KeyB)"],
};

describe("a chord fires when every member is held", () => {
  test("the members keep their own bindings", () => {
    const maps =
      loadCore({ windowName: "table" }).context.buildBindingMaps(FLIPPERS);

    assert.deepEqual([...maps.keys.previous], ["keya"]);
    assert.deepEqual([...maps.keys.next], ["keyb"]);
    assert.equal(maps.chords.length, 1);
    // And the chord is not left in the single-key map, where it could never match.
    assert.ok(!(maps.keys.page_next || []).some(one => one.includes("chord")));
  });

  test("both members fire, and then the chord", async () => {
    const { seen, down } = await chorded(FLIPPERS);

    await down("a");
    await down("b");

    assert.deepEqual([...seen], ["previous", "next", "page_next"],
      "the members fire first - that is the decision, not an accident");
  });

  test("one member alone is not a chord", async () => {
    const { seen, down } = await chorded(FLIPPERS);

    await down("a");

    assert.deepEqual([...seen], ["previous"]);
  });

  test("it fires once, not on every repeat while it is held", async () => {
    const { seen, down } = await chorded(FLIPPERS);

    await down("a");
    await down("b");
    await down("a", true);
    await down("b", true);

    assert.equal(seen.filter(one => one === "page_next").length, 1);
  });

  test("a member stops repeating while the chord holds it", async () => {
    const { vpin, seen, down } = await chorded(FLIPPERS);
    quickly(vpin);

    await down("a");
    await down("b");
    const whileHeld = seen.filter(one => one === "previous").length;
    await ticks();

    assert.equal(seen.filter(one => one === "previous").length, whileHeld,
      "the wheel must not walk for the length of a hold");
  });

  test("letting go and pressing again fires it again", async () => {
    const { seen, down, up } = await chorded(FLIPPERS);

    await down("a");
    await down("b");
    await up("b");
    await down("b");

    assert.equal(seen.filter(one => one === "page_next").length, 2);
  });

  test("repeat comes back once a member is released", async () => {
    // Muted rather than cancelled: the player is still holding this one, and that is
    // still a request to keep going.
    const { vpin, seen, down, up } = await chorded(FLIPPERS);
    quickly(vpin);

    await down("a");
    await down("b");
    await ticks();
    const whileHeld = seen.filter(one => one === "previous").length;

    await up("b");
    await ticks();

    assert.ok(seen.filter(one => one === "previous").length > whileHeld,
      "a released chord must not leave its members muted");
  });
});

describe("a chord that asks to be held", () => {
  // `page_next` again, for the same reason: `exit` is what a cabinet really binds this
  // to, and core answers it itself - so the chord firing and the chord not firing look
  // identical from the theme handler.
  const HELD = {previous: ["key:KeyA"], next: ["key:KeyB"],
                page_next: ["chord(key:KeyA+key:KeyB)@hold:40"]};

  test("nothing fires before the hold is up", async () => {
    const { seen, down } = await chorded(HELD);

    await down("a");
    await down("b");

    assert.ok(!seen.includes("page_next"), `fired too early: ${seen}`);
  });

  test("it fires once the hold is up", async () => {
    const { seen, down } = await chorded(HELD);

    await down("a");
    await down("b");
    await new Promise(resolve => setTimeout(resolve, 80));

    assert.ok(seen.includes("page_next"), `never fired: ${seen}`);
  });

  test("letting go before the hold is up cancels it", async () => {
    const { seen, down, up } = await chorded(HELD);

    await down("a");
    await down("b");
    await up("b");
    await new Promise(resolve => setTimeout(resolve, 80));

    assert.ok(!seen.includes("page_next"),
      "a hold that was let go of must not arrive late");
  });
});

// A press from outside the browser: a phone, a button board. It reaches core as a
// broadcast message rather than as a keystroke, and has to land on the same dispatch -
// otherwise "the remote drives the wheel" is a second implementation of navigation.
describe("an action produced outside the browser", () => {
  function windows(controlling) {
    const { VPinFECore, browser } = loadCore({ windowName: "table" });
    // Captured rather than discarded: a press starts a hold, and blur is how core is
    // told to let go of one. Throwing the listeners away left every one of these tests
    // repeating after it had passed.
    const listeners = {};
    browser.window.addEventListener = (type, fn) => { (listeners[type] ||= []).push(fn); };
    const vpin = new VPinFECore();
    vpin.init();
    vpin.isController = () => controlling;
    vpin.frontendInputEnabled = true;
    vpin._capabilities && (vpin._capabilities.core_navigation = false);
    stillHeld.push(() => (listeners.blur || []).forEach(fn => fn()));
    const seen = [];
    vpin.inputHandlers.push((action) => { seen.push(action); });
    return { vpin, seen };
  }

  test("a press reaches the theme the way a keystroke does", async () => {
    // Compared against a real keypress rather than against a spelling: core hands a
    // theme the name that theme's contract uses, and pinning the string here would pass
    // while the two paths quietly drifted apart.
    const pressed = [];
    const keys = controller();
    keys.vpin.inputHandlers.push((action) => { pressed.push(action); });
    await keys.press("ArrowRight");

    const { vpin, seen } = windows(true);
    await vpin.handleEvent({ type: "InputAction", action: "next", phase: "press" });

    assert.deepEqual(seen, pressed);
    assert.equal(seen.length, 1);
  });

  test("only the controller window answers it", async () => {
    // Python broadcasts to every window because it cannot know which one is driving.
    // Dropping it here is what stops one press becoming three on a three-screen cab.
    const { vpin, seen } = windows(false);

    await vpin.handleEvent({ type: "InputAction", action: "next", phase: "press" });

    assert.deepEqual(seen, []);
  });

  test("a release does not press anything", async () => {
    const { vpin, seen } = windows(true);

    await vpin.handleEvent({ type: "InputAction", action: "next", phase: "release" });

    assert.deepEqual(seen, []);
  });

  test("holding it walks the wheel, and letting go stops it", async () => {
    // The reason a hold is a press and a release rather than the client sending steps
    // on a timer: the curve is here, so a thumb feels like a flipper feels like a key.
    const { vpin, seen } = windows(true);
    quickly(vpin);

    await vpin.handleEvent({ type: "InputAction", action: "next", phase: "press" });
    await ticks();
    const whileHeld = seen.length;
    await vpin.handleEvent({ type: "InputAction", action: "next", phase: "release" });
    await ticks();

    assert.ok(whileHeld > 1, "a held button has to keep going, or it is a tap");
    assert.equal(seen.length, whileHeld);
  });

  test("input that is suppressed for a launch stays suppressed", async () => {
    // The whole point of the suppression is that a table is coming up. A press that
    // walked in over the network while a launch was in flight would be the one input
    // path that ignored it.
    const { vpin, seen } = windows(true);
    vpin.frontendInputEnabled = false;

    await vpin.handleEvent({ type: "InputAction", action: "next", phase: "press" });

    assert.deepEqual(seen, []);
  });
});


// Booted through the socket, because the poll is started by the bridge-ready sequence
// and not by init(). Faking the loop instead would test the test.
async function padded({ mapping = "", bindings = null } = {}) {
  const { VPinFECore, browser, context } = loadCore({ windowName: "table" });
  const listeners = {};
  browser.window.addEventListener = (type, fn) => { (listeners[type] ||= []).push(fn); };
  const buttons = [{ pressed: false }, { pressed: false }];
  browser.navigator.getGamepads = () => [{ index: 0, mapping, buttons }];
  const vpin = new VPinFECore();
  vpin.isController = () => true;
  // Only the shapes the boot would otherwise throw on. The rest of the sequence
  // tolerates an empty answer on purpose.
  const answers = { get_tables: "[]", get_theme_assets_port: 8000,
                    get_initial_table_index: 0, get_theme_config: {},
                    get_keymapping: {}, get_joymaping: {}, get_mainmenu_config: {},
                    get_monitors: [], get_collections: [] };
  vpin.call = (method) => Promise.resolve(
    method in answers ? answers[method] : null);
  vpin.init();
  await browser.WebSocket.instances.at(-1).onopen();
  vpin.frontendInputEnabled = true;
  vpin.contract = 2;
  vpin._capabilities && (vpin._capabilities.core_navigation = false);
  vpin.joyButtonMap = { "0": ["previous"], "1": ["select"] };
  if (bindings) {
    const maps = context.buildBindingMaps(bindings);
    vpin.joyButtonMap = maps.buttons;
    vpin.standardButtonMap = maps.standard;
    vpin.joyHoldMap = maps.buttonHolds;
  }
  const asked = [];
  vpin.requestLifecycle = (scope, action) => { asked.push([scope, action]); };
  stillHeld.push(() => (listeners.blur || []).forEach(fn => fn()));
  const seen = [];
  vpin.inputHandlers.push((action) => { seen.push(action); });
  // The poll re-arms itself every frame, so the harness holds the callback and a test
  // steps it - which is the only way to produce a press edge and then a release edge.
  const poll = () => browser.frames.step();
  return { vpin, browser, seen, asked, buttons, poll };
}

// A cabinet flipper held down did nothing at all: only the press edge counted, and the
// keyboard's repeat came from the OS, which a gamepad has no equivalent of. This is the
// half of the hold engine that fixes the cabinet rather than the phone.
describe("a held gamepad button", () => {
  test("it repeats while it is down", async () => {
    const { vpin, seen, buttons, poll } = await padded();
    quickly(vpin);

    buttons[0].pressed = true;
    poll();
    await ticks();

    assert.ok(seen.length > 1, "holding a flipper did not scroll the wheel at all");
    assert.ok(seen.every(one => one === "previous"));
  });

  test("letting go stops it", async () => {
    const { vpin, seen, buttons, poll } = await padded();
    quickly(vpin);

    buttons[0].pressed = true;
    poll();
    await ticks();
    buttons[0].pressed = false;
    poll();
    const whenReleased = seen.length;
    await ticks();

    assert.equal(seen.length, whenReleased);
  });

  test("a button that is not movement fires once", async () => {
    const { vpin, seen, buttons, poll } = await padded();
    quickly(vpin);

    buttons[1].pressed = true;
    poll();
    await ticks();

    assert.deepEqual(seen, ["select"]);
  });
});

// One press and one hold per input, at any length. Escape is Back on a tap and Quit held,
// so a tap must never reach the hold's action.
describe("a key with a hold waits to be let go", () => {
  const BINDINGS = {
    back: ["key:Escape"],
    exit: [`key:Escape@hold:${HOLD_MS}`],
  };
  const holding = async (bindings = BINDINGS) => {
    const made = await chorded(bindings);
    const asked = [];
    made.vpin.requestLifecycle = (scope, action) => { asked.push([scope, action]); };
    return { ...made, asked };
  };

  test("let go early it is the press, and only the press", async () => {
    const { seen, asked, down, up } = await holding();

    await down("Escape");
    assert.deepEqual([...seen], [], "nothing fires on the way down");
    await up("Escape");
    await heldFor();

    assert.deepEqual([...seen], ["back"]);
    assert.deepEqual(asked, []);
  });

  test("kept down for the hold's length it is the hold, and only the hold", async () => {
    const { seen, asked, down, up } = await holding();

    await down("Escape");
    await heldFor();
    await up("Escape");

    assert.deepEqual(asked, [["vpinfe", "stop"]], "the hold is Quit");
    assert.deepEqual([...seen], [], "the press must not also fire when it is let go");
  });

  test("the operating system's repeat neither restarts it nor fires it twice", async () => {
    const { asked, down, up } = await holding();

    await down("Escape");
    await down("Escape", true);
    await down("Escape", true);
    await heldFor();
    await up("Escape");

    assert.equal(asked.length, 1);
  });

  test("one hold per key, whatever its length", async () => {
    const { vpin, asked, down, up } = await holding({
      exit: [`key:Escape@hold:${HOLD_MS}`],
      menu: [`key:Escape@hold:${HOLD_MS * 3}`],
    });

    await down("Escape");
    await heldFor(HOLD_MS * 3);
    await up("Escape");

    assert.deepEqual(asked, [["vpinfe", "stop"]],
      "the first declared owns the key; the longer one is never heard");
    assert.equal(vpin.overlay, null);
  });

  test("an unbound key is still left alone", async () => {
    const { seen, down, up } = await holding();

    const event = await down("F9");
    await up("F9");

    assert.equal(event.prevented, false);
    assert.deepEqual([...seen], []);
  });

  test("losing focus drops it without firing either", async () => {
    const { seen, asked, down, blur } = await holding();

    await down("Escape");
    blur();
    await heldFor();

    assert.deepEqual([...seen], []);
    assert.deepEqual(asked, [], "a release nobody saw is neither the press nor the hold");
  });

  test("the ring shows while it counts and goes when it is over", async () => {
    const { browser, down, up } = await holding();
    const ring = () => browser.document.body.children.filter(
      one => one.className === "vpinfe-hold");

    await down("Escape");
    assert.equal(ring().length, 1);
    assert.match(ring()[0].attributes.style, new RegExp(`--vpinfe-hold-ms: ${HOLD_MS}ms`));
    assert.deepEqual(ring()[0].children[0].children.map(one => one.attributes.class),
      ["vpinfe-hold-track", "vpinfe-hold-fill"], "a theme restyles these two");
    await up("Escape");

    assert.equal(ring().length, 0, "let go early, the ring goes without firing");
  });

  test("the ring is gone once the hold is reached", async () => {
    const { browser, down, up } = await holding();

    await down("Escape");
    await heldFor();

    assert.equal(browser.document.body.children.length, 0);
    await up("Escape");
  });

  test("a theme that draws its own ring turns core's off, and the hold still works", async () => {
    const { vpin, browser, asked, down, up } = await holding();
    vpin._capabilities.core_hold_ring = false;

    await down("Escape");
    assert.equal(browser.document.body.children.length, 0);
    await heldFor();
    await up("Escape");

    assert.equal(asked.length, 1);
  });
});

// The controller defaults are written against the standard layout, and a cabinet's encoder
// numbers its buttons by wiring. A standard binding fires only for a standard pad, or the
// layout's button 0 would be whatever the encoder calls button 0.
describe("a standard binding is for a pad that reports the standard layout", () => {
  const DEFAULTS = {
    select: ["pad:standard/button:0"],
    back: ["pad:standard/button:1"],
    exit: [`pad:standard/button:1@hold:${HOLD_MS}`],
    next: ["pad:standard/button:15"],
  };

  test("button 0 selects on a standard pad", async () => {
    const { buttons, seen, poll } = await padded({ mapping: "standard", bindings: DEFAULTS });

    buttons[0].pressed = true;
    poll();
    await ticks();

    assert.deepEqual(seen, ["select"]);
  });

  test("the same button on a pad that does not say it is standard does nothing", async () => {
    const { buttons, seen, asked, poll } = await padded({ mapping: "", bindings: DEFAULTS });

    buttons[0].pressed = true;
    buttons[1].pressed = true;
    poll();
    await ticks();

    assert.deepEqual(seen, []);
    assert.deepEqual(asked, []);
  });

  test("a numbered binding still fires on any pad", async () => {
    const { buttons, seen, poll } = await padded({
      mapping: "", bindings: { select: ["pad:0/button:0"] } });

    buttons[0].pressed = true;
    poll();
    await ticks();

    assert.deepEqual(seen, ["select"]);
  });

  test("button 1 tapped is Back, and held is Quit", async () => {
    const tapped = await padded({ mapping: "standard", bindings: DEFAULTS });
    tapped.buttons[1].pressed = true;
    tapped.poll();
    assert.deepEqual(tapped.seen, [], "nothing fires on the way down");
    tapped.buttons[1].pressed = false;
    tapped.poll();
    await ticks();
    assert.deepEqual(tapped.seen, ["back"]);
    assert.deepEqual(tapped.asked, []);

    const held = await padded({ mapping: "standard", bindings: DEFAULTS });
    held.buttons[1].pressed = true;
    held.poll();
    await heldFor();
    held.buttons[1].pressed = false;
    held.poll();
    await ticks();
    assert.deepEqual(held.asked, [["vpinfe", "stop"]]);
    assert.deepEqual(held.seen, [], "letting go after the hold is not also a Back");
  });

  test("a held button 1 on a pad that is not standard is not Quit", async () => {
    const { buttons, asked, poll } = await padded({ mapping: "", bindings: DEFAULTS });

    buttons[1].pressed = true;
    poll();
    await heldFor();

    assert.deepEqual(asked, []);
  });

  test("the hold shows the ring on a pad too", async () => {
    const { browser, buttons, poll } = await padded({ mapping: "standard", bindings: DEFAULTS });

    buttons[1].pressed = true;
    poll();
    const rings = () => browser.document.body.children.filter(
      one => one.className === "vpinfe-hold").length;
    assert.equal(rings(), 1);
    buttons[1].pressed = false;
    poll();

    assert.equal(rings(), 0);
  });
});

describe("a binding reads as a standard button, a hold, or neither", () => {
  const { buildBindingMaps } = loadCore({ windowName: "table" }).context;

  test("pad:standard is kept apart from a numbered pad", () => {
    const maps = buildBindingMaps({ select: ["pad:standard/button:0", "pad:2/button:3"] });

    assert.deepEqual([...maps.standard["0"]], ["select"]);
    assert.deepEqual([...maps.buttons["3"]], ["select"]);
    assert.equal(maps.buttons["0"], undefined);
  });

  test("a hold comes off the end and is not a plain press", () => {
    const maps = buildBindingMaps({
      exit: ["key:Escape@hold:1500", "key:KeyQ", "pad:standard/button:1@hold:1500"] });

    assert.deepEqual(plain(maps.keys.exit), ["keyq"]);
    assert.deepEqual(plain(maps.keyHolds.exit), [{ token: "escape", ms: 1500 }]);
    assert.deepEqual(plain(maps.buttonHolds["1"]), [{ action: "exit", ms: 1500, standard: true }]);
  });

  test("a hold of nothing is no hold", () => {
    const maps = buildBindingMaps({ exit: ["key:Escape@hold:0"] });

    assert.deepEqual(plain(maps.keyHolds), {});
    assert.deepEqual(plain(maps.keys), {});
  });
});

describe("a press the page hears is reported to core for extensions", () => {
  const field = () => ({ tagName: "INPUT", type: "text" });
  const reported = (calls) => calls().filter(m => m === "report_input_action");
  const settle = () => new Promise(resolve => setTimeout(resolve, 0));

  test("a bound key reports its action once", async () => {
    const { press, release, calls } = controller();

    await press("ArrowRight");
    await release("ArrowRight");
    await settle();

    assert.equal(reported(calls).length, 1);
  });

  test("an OS key repeat is not reported again", async () => {
    const { press, release, calls } = controller();

    await press("ArrowRight");
    await press("ArrowRight", { repeat: true });
    await release("ArrowRight");
    await settle();

    assert.equal(reported(calls).length, 1);
  });

  test("a keystroke typed into a text field is not reported", async () => {
    const { press, calls } = controller();

    await press("q", { target: field() });
    await press("Enter", { target: field() });
    await settle();

    assert.deepEqual(reported(calls), []);
  });

  test("an action core sent is not reported back", async () => {
    const { vpin, calls } = controller();

    await vpin.handleEvent({ type: "InputAction", action: "next", phase: "press" });
    await vpin.handleEvent({ type: "InputAction", action: "next", phase: "release" });
    await settle();

    assert.deepEqual(reported(calls), []);
  });
});

// The 2.x names a published theme still calls, and the window identity it reads.
//
// Every one of these is a compatibility shim with no expiry: nothing declares that a
// theme has stopped needing them. Until a contract governs the whole surface, these
// assertions are what stops one being dropped by accident.

import { test, describe } from "node:test";
import assert from "node:assert/strict";

import { newCore } from "./support/load-core.js";

describe("the vpin.* names 2.x themes read still answer", () => {
  // PAR-23. The payload behind each is identical; only the spelling moved.
  const RENAMED = [
    ["tableRotation", "playfieldRotation"],
    ["tableOrientation", "playfieldOrientation"],
  ];

  // Not aliases: these are the names 2.x published and 3.0 still uses, because they
  // address a row. Listed so a future rename of one has to delete it from here first.
  const UNCHANGED = [
    "tableData", "getTableMeta", "getTableData", "getTableCount",
    "getCurrentTableIndex", "playTableAudio", "stopTableAudio", "launchTable",
  ];

  for (const name of UNCHANGED) {
    test(`vpin.${name} is the real member, not a forwarder`, () => {
      const { vpin, context } = newCore();

      assert.notEqual(vpin[name], undefined,
        `${name} is a published name; dropping it breaks every 2.x theme`);
      assert.equal(context.VPINFE_RENAMED_MEMBERS[name], undefined,
        `${name} names a row, so it should not forward anywhere`);
    });
  }

  for (const [oldName, newName] of RENAMED) {
    test(`vpin.${oldName} reaches ${newName}`, () => {
      const { vpin } = newCore();

      assert.notEqual(vpin[oldName], undefined,
        `${oldName} is a published name; dropping it breaks every 2.x theme`);
      if (typeof vpin[newName] === "function") {
        assert.equal(typeof vpin[oldName], "function");
      }
    });
  }

  test("a legacy read returns the current value, not a stale copy", () => {
    const { vpin } = newCore();
    vpin.playfieldRotation = 270;

    assert.equal(vpin.tableRotation, 270);
  });

  test("a legacy write lands on the current member", () => {
    const { vpin } = newCore();
    vpin.tableRotation = 90;

    assert.equal(vpin.playfieldRotation, 90);
  });

  test("every renamed name reaches something that exists", () => {
    // getAllTables got into the map from the Python parser rename, where getAllTables ->
    // getAllGames is real. On vpin it was neither: calling it warned, named a
    // replacement that did not exist either, then threw. This is the check that would
    // have caught it, so a rename cannot be copied onto the wrong surface again.
    const { vpin, context } = newCore();

    for (const [oldName, newName] of Object.entries(context.VPINFE_RENAMED_MEMBERS)) {
      assert.notEqual(vpin[newName], undefined,
        `${oldName} forwards to vpin.${newName}, which does not exist`);
    }
  });

  test("using a legacy name is reported, so the log can answer who still needs it", () => {
    const { vpin } = newCore();
    const reported = [];
    vpin.call = (method, ...args) => { reported.push([method, ...args]); return Promise.resolve(); };

    void vpin.tableRotation;

    assert.ok(reported.some(([method]) => method === "report_deprecated_use"),
      "a shim nobody can observe is a shim nobody can ever retire");
  });
});

describe("window identity", () => {
  for (const [name, title] of [["table", "VPinFE Table"], ["bg", "VPinFE BG"],
                               ["dmd", "VPinFE DMD"]]) {
    test(`?window=${name} is detected and titles the window`, () => {
      const { vpin, browser } = newCore({ windowName: name });

      assert.equal(vpin._windowName, name);
      assert.equal(browser.window.name, name);
      assert.equal(browser.document.title, title);
    });
  }

  test("an unknown window is named rather than left undefined", () => {
    const { vpin } = newCore({ search: "" });

    assert.equal(vpin._windowName, "unknown");
  });

  test("the controller window is the one that owns audio and selection", () => {
    // Six comparisons against the literal "table" decide this today. When windows are
    // declared by the theme they collapse into one controller test, and this assertion
    // is what should survive that change unchanged.
    const controller = newCore({ windowName: "table" }).vpin;
    const secondary = newCore({ windowName: "bg" }).vpin;

    assert.equal(controller._windowName, "table");
    assert.notEqual(secondary._windowName, "table");
  });
});

describe("core behaviors have one stated default each", () => {
  // These used to disagree with themselves: core audio read true at construction and
  // false after init(), because the constructor and init() each decided separately.
  // CAPABILITIES states each default once and both paths read it.
  test("core paging is on, core audio is off, before init and after", async () => {
    const { vpin } = newCore();

    assert.equal(vpin.isCorePagingEnabled(), true, "opt out");
    assert.equal(vpin.isCoreAudioEnabled(), false, "opt in");
  });

  test("a theme can turn each of them off", () => {
    const { vpin } = newCore();
    vpin.enableCorePaging(false);
    vpin.enableCoreAudio(true);

    assert.equal(vpin.isCorePagingEnabled(), false);
    assert.equal(vpin.isCoreAudioEnabled(), true);
  });

  test("capabilities names what this build offers and whether it is on", () => {
    const { vpin } = newCore();

    assert.ok("core_paging" in vpin.capabilities);
    assert.ok("core_audio" in vpin.capabilities);
    assert.equal(vpin.capabilities.core_paging, true);
    assert.equal(vpin.enabled("no_such_capability"), false,
      "a name this build does not have reads false rather than throwing");
  });

  test("the reported set is a copy, not the live one", () => {
    const { vpin } = newCore();
    vpin.capabilities.core_paging = false;

    assert.equal(vpin.isCorePagingEnabled(), true,
      "a theme poking the report must not change what core does");
  });
});

describe("the 2.x overlay names still answer", () => {
  // Nine of them, and none is a plain rename: three booleans over one string, six methods
  // over two that take the overlay's name. The mechanism is covered by overlays.test.js;
  // what is checked here is that a theme written against the old surface still works.
  const FLAGS = [["menuUP", "menu"], ["collectionMenuUP", "collectionMenu"],
                 ["tutorialUP", "tutorial"]];

  for (const [flag, overlay] of FLAGS) {
    test(`${flag} reads the overlay string`, () => {
      const { vpin } = newCore();

      assert.equal(vpin[flag], false);
      vpin.overlay = overlay;
      assert.equal(vpin[flag], true);
      vpin.overlay = "menu";
      assert.equal(vpin[flag], overlay === "menu");
    });
  }

  test("the old toggle opens the overlay it names", async () => {
    const { vpin } = newCore();

    await vpin.toggleMenu();
    assert.equal(vpin.overlay, "menu");
    await vpin.toggleCollectionMenu();
    assert.equal(vpin.overlay, "collectionMenu", "and closes the other, as it always did");
  });

  test("the old register lands in the handler map", async () => {
    const { vpin } = newCore();
    // Stubbed because registration logs through the bridge, and an unconnected stub
    // socket rejects after the test ends rather than during it.
    vpin.call = () => Promise.resolve();
    const handler = () => {};

    await vpin.registerInputHandlerMenu(handler);

    // Length and identity: the array is made inside the vm, so deepEqual would compare
    // prototypes across realms and fail on that alone.
    assert.equal(vpin.overlayHandlers.menu.length, 1);
    assert.ok(vpin.overlayHandlers.menu.includes(handler));
  });

  test("assigning false closes the overlay rather than doing nothing", async () => {
    const { vpin } = newCore();
    await vpin.toggleMenu();

    vpin.menuUP = false;

    assert.equal(vpin.overlay, null);
  });
});

describe("registering an input handler does not require a theme global", () => {
  test("a theme that never declares windowName can still register", async () => {
    // A class body is strict mode, so the bare `windowName = ...` this used to do threw
    // a ReferenceError. Every published theme opens with `windowName = ""`, which hid it
    // - a theme reading vpin.windowName instead has no reason to declare it.
    const { vpin, browser } = newCore({ windowName: "table" });
    vpin.call = () => Promise.resolve("table");

    assert.equal("windowName" in browser.window, false, "nothing has declared it here");
    await assert.doesNotReject(() => vpin.registerInputHandler(() => {}));
    assert.equal(browser.window.windowName, "table",
      "and the global themes read still gets set");
  });
});

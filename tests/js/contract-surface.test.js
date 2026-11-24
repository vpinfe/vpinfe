// One declaration, the whole surface.
//
// Before this, `contract` governed the payload and nothing else: the vpin.* aliases, the
// media kind spellings and the dual-spelling window messages were unconditional, so none
// of them could ever be retired - nothing signalled that a theme had stopped needing
// them. A theme already says what it was written against; now that answer selects
// everything.

import { test, describe } from "node:test";
import assert from "node:assert/strict";

import { newCore } from "./support/load-core.js";

// What the bridge answers during init. Only the shapes that would otherwise throw - the
// rest of the sequence tolerates undefined on purpose.
const BRIDGE_DEFAULTS = {
  get_tables: "[]",
  get_theme_assets_port: 8000,
  get_initial_table_index: 0,
  get_theme_config: {},
  get_keymapping: {},
  get_joymaping: {},
  get_mainmenu_config: {},
  get_monitors: [],
  get_collections: [],
};

// Drives the real path: init() opens the (stubbed) socket, and opening it runs the same
// bridge-ready sequence a browser would. Faking the switch instead would test the test.
async function coreAtContract(level, answers = {}) {
  const { vpin, browser, context } = newCore({ windowName: "table" });
  const sent = [];
  vpin.call = (method, ...args) => {
    sent.push([method, ...args]);
    if (method === "get_theme_contract") {
      if (level === "unsupported") return Promise.reject(new Error("Method not allowed"));
      return Promise.resolve(level);
    }
    if (method in answers) return Promise.resolve(answers[method]);
    return Promise.resolve(BRIDGE_DEFAULTS[method]);
  };
  vpin.init();
  await browser.WebSocket.instances.at(-1).onopen();
  return { vpin, sent, context };
}

describe("a theme declaring nothing keeps every 2.x name", () => {
  test("the contract is 1 before the bridge has answered", () => {
    const { vpin } = newCore();

    assert.equal(vpin.contract, 1,
      "a theme touching vpin.* before the bridge is up must still find its names");
    assert.notEqual(vpin.tableData, undefined);
  });

  test("contract 1 keeps the aliases after the bridge answers", async () => {
    const { vpin } = await coreAtContract(1);

    assert.equal(vpin.contract, 1);
    for (const oldName of ["tableData", "getTableMeta", "launchTable"]) {
      assert.notEqual(vpin[oldName], undefined, `${oldName} must still answer`);
    }
  });

  test("a build too old to answer is treated as contract 1", async () => {
    const { vpin } = await coreAtContract("unsupported");

    assert.equal(vpin.contract, 1);
    assert.notEqual(vpin.tableData, undefined,
      "an unknown method must not cost a theme its names");
  });

  test("legacy media kinds resolve", async () => {
    const { vpin } = await coreAtContract(1, { get_theme_assets_port: 8000 });
    vpin.tableData = [{ TableImagePath: "/lib/Game (M 1990)/medias/table.png" }];

    const url = vpin.getImageURL(0, "table");
    assert.ok(url.endsWith("table.png"), `expected the playfield, got ${url}`);
  });
});

describe("a theme declaring contract 2 gets the current surface only", () => {
  test("the 2.x vpin.* names are gone, not merely discouraged", async () => {
    const { vpin } = await coreAtContract(2);

    assert.equal(vpin.contract, 2);
    for (const oldName of ["tableRotation", "tableOrientation"]) {
      assert.equal(vpin[oldName], undefined,
        `${oldName} must not answer at contract 2, or a theme can work by accident`);
    }
  });

  test("the row names are not 2.x names to drop", async () => {
    const { vpin } = await coreAtContract(2);

    // These read like 2.x spellings and are not: a row is a table, so they are current
    // at every contract. Dropping them here would break contract 2, not protect it.
    for (const name of ["tableData", "getTableMeta", "launchTable", "getTableCount"]) {
      assert.notEqual(vpin[name], undefined, `${name} is current, not legacy`);
    }
  });

  test("the current names still answer", async () => {
    const { vpin } = await coreAtContract(2);

    assert.notEqual(vpin.tableData, undefined);
    assert.equal(typeof vpin.getTableMeta, "function");
  });

  test("a legacy media kind stops resolving", async () => {
    const { vpin } = await coreAtContract(2, { get_theme_assets_port: 8000 });
    vpin.tableData = [{ game: { id: "g1" }, table: { id: "t1" }, media: ["playfield"] }];

    assert.ok(vpin.getImageURL(0, "playfield").includes("/media/t1/playfield"),
      "the canonical kind resolves");
    // An unknown kind answers with the missing-media placeholder rather than null, which
    // is the same thing a theme sees for art it does not have.
    assert.equal(vpin.getImageURL(0, "table"), "/core/images/file_missing.png",
      "`table` is contract 1's spelling and is not honoured at 2");
  });

  test("the reader is chosen by declaration, not by sniffing the payload", async () => {
    // A contract-1 row handed to a contract-2 theme used to fall through to the old
    // reader because the shape was inspected. It does not now: the theme said 2, so 2
    // is what it gets, and a mismatched payload fails visibly instead of half-working.
    const { vpin } = await coreAtContract(2, { get_theme_assets_port: 8000 });
    vpin.tableData = [{ PlayfieldImagePath: "/lib/G (M 1990)/medias/table.png" }];

    assert.equal(vpin.getImageURL(0, "playfield"), "/core/images/file_missing.png");
  });

  test("window messages go out once, under the current name only", async () => {
    const { vpin, sent } = await coreAtContract(2);
    sent.length = 0;
    vpin.sendMessageToAllWindows({ type: "TableIndexUpdate", index: 3 });

    const broadcasts = sent.filter(([m]) => m === "send_event_all_windows");
    assert.equal(broadcasts.length, 1, "no duplicate under the 2.x spelling");
    assert.equal(broadcasts[0][1].type, "TableIndexUpdate");
  });

  test("contract 1 gets one spelling, because there is only one", async () => {
    const { vpin, sent } = await coreAtContract(1);
    sent.length = 0;
    vpin.sendMessageToAllWindows({ type: "TableIndexUpdate", index: 3 });

    const types = sent.filter(([m]) => m === "send_event_all_windows").map(([, msg]) => msg.type);
    assert.deepEqual(types, ["TableIndexUpdate"],
      "no alias means no second copy - a theme that matched this always received it");
  });
});

describe("core navigation follows the VPinFE a theme says it needs", () => {
  // Measured before it was fixed: with the capability on, one press of `previous` on a
  // theme that still runs on 2.x moved core's game index and never reached the theme's
  // handler - #dispatchAction hits #shouldHandleCoreNavigation first and the branches
  // are else-if. Revolution, Trinidad and carousel-desktop drive their own collection
  // list with those two actions, so their picker exited onto an unrelated game.
  //
  // The contract here is what the backend derived from the theme's min_vpinfe, so a
  // theme reaches the second case by declaring "3.0" rather than by naming a contract.
  test("a theme that still runs on 2.x keeps previous and next", async () => {
    const { vpin } = await coreAtContract(1);

    assert.equal(vpin.enabled("core_navigation"), false);
  });

  test("a theme that needs 3.0 gets core navigation", async () => {
    const { vpin } = await coreAtContract(2);

    assert.equal(vpin.enabled("core_navigation"), true);
  });

  test("a theme that has not moved yet can still ask for it", async () => {
    const { vpin } = await coreAtContract(1,
      { get_theme_config: { navigation: { enabled: true } } });

    assert.equal(vpin.enabled("core_navigation"), true,
      "navigation.enabled is the only opt-in there is - no method exists");
  });

  // What is not asserted here: the press itself. These cover the seeding, through the
  // real init path; input.test.js covers dispatch given the flag. Joining the two needs
  // the keyboard harness, and the case that matters - a theme's own picker staying up -
  // is not something a green suite can show. It belongs on the cabinet.
});

describe("what the contract is for", () => {
  test("a theme can read which surface it is being served", async () => {
    const { vpin } = await coreAtContract(2);

    assert.equal(vpin.contract, 2);
  });

  test("a contract newer than this build serves the newest it has", async () => {
    const { vpin } = await coreAtContract(99);

    assert.equal(vpin.contract, 2, "clamped, not obeyed");
  });
});

describe("theme.json turns capabilities on and off", () => {
  test("core audio is off unless the theme asks for it", async () => {
    const { vpin } = await coreAtContract(1);

    assert.equal(vpin.isCoreAudioEnabled(), false);
  });

  test("the declared key switches it on", async () => {
    const { vpin } = await coreAtContract(1, { get_theme_config: { use_core_audio: true } });

    assert.equal(vpin.isCoreAudioEnabled(), true);
  });

  test("a nested key works too", async () => {
    const { vpin } = await coreAtContract(1,
      { get_theme_config: { audio: { enabled: true } } });

    assert.equal(vpin.isCoreAudioEnabled(), true);
  });

  test("the camelCase spellings earlier builds accepted still work", async () => {
    const { vpin } = await coreAtContract(1, { get_theme_config: { useCoreAudio: true } });

    assert.equal(vpin.isCoreAudioEnabled(), true,
      "themes are using these; they stay until contract 1 goes");
  });
});

describe("the controller window comes from the theme's list", () => {
  test("a theme that declares nothing keeps table as the controller", async () => {
    const { vpin } = await coreAtContract(1);

    assert.deepEqual([...vpin.windows], ["table", "bg", "dmd"]);
    assert.equal(vpin.isController(), true, "?window=table is the controller");
  });

  test("a secondary window is not the controller", async () => {
    const { vpin, browser } = newCore({ windowName: "bg" });
    vpin.call = (method) => Promise.resolve(
      method === "get_theme_contract" ? 1
      : method === "get_tables" ? "[]" : BRIDGE_DEFAULTS[method]);
    vpin.init();
    await browser.WebSocket.instances.at(-1).onopen();

    assert.equal(vpin.isController(), false);
  });

  test("at contract 2 the controller is the playfield window", async () => {
    const { vpin, browser } = newCore({ windowName: "playfield" });
    vpin.call = (method) => Promise.resolve(
      method === "get_theme_contract" ? 2
      : method === "get_theme_windows" ? ["playfield", "bg", "dmd"]
      : method === "get_tables" ? JSON.stringify({ entries: [], count: 0 })
      : BRIDGE_DEFAULTS[method]);
    vpin.init();
    await browser.WebSocket.instances.at(-1).onopen();

    assert.equal(vpin.isController(), true);
  });

  test("a theme can declare a window VPinFE never had", async () => {
    const { vpin, browser } = newCore({ windowName: "topper" });
    vpin.call = (method) => Promise.resolve(
      method === "get_theme_contract" ? 2
      : method === "get_theme_windows" ? ["playfield", "bg", "dmd", "topper"]
      : method === "get_tables" ? JSON.stringify({ entries: [], count: 0 })
      : BRIDGE_DEFAULTS[method]);
    vpin.init();
    await browser.WebSocket.instances.at(-1).onopen();

    assert.ok(vpin.windows.includes("topper"));
    assert.equal(vpin.isController(), false, "it is a display, not the controller");
    assert.equal(browser.document.title, "VPinFE Topper", "titled from its own name");
  });

  test("a build too old to report windows keeps the 2.x three", async () => {
    const { vpin, browser } = newCore();
    vpin.call = (method) => {
      if (method === "get_theme_windows") return Promise.reject(new Error("Method not allowed"));
      return Promise.resolve(method === "get_theme_contract" ? 1
                             : method === "get_tables" ? "[]" : BRIDGE_DEFAULTS[method]);
    };
    vpin.init();
    await browser.WebSocket.instances.at(-1).onopen();

    assert.deepEqual([...vpin.windows], ["table", "bg", "dmd"]);
    assert.equal(vpin.isController(), true);
  });
});

describe("a legacy message copy uses the same delivery as the original", () => {
  // The bug this prevents: the _incself copy was once sent without _incself, so it
  // reached bg and dmd but never came back to the playfield window.
  //
  // MESSAGE_TYPE_ALIASES is empty today - PAR-24 was withdrawn - so this drives the
  // machinery through an alias of its own rather than a real message. The machinery is
  // kept for the next message rename, and a kept mechanism with no test is how the bug
  // above comes back.
  for (const [method, wsMethod] of [
    ["sendMessageToAllWindows", "send_event_all_windows"],
    ["sendMessageToAllWindowsIncSelf", "send_event_all_windows_incself"],
  ]) {
    test(`${method} sends both spellings the same way`, async () => {
      const { vpin, sent, context } = await coreAtContract(1);
      context.MESSAGE_TYPE_ALIASES.RenamedForThisTest = "OldSpelling";
      sent.length = 0;

      try {
        vpin[method]({ type: "RenamedForThisTest", index: 1 });

        const used = sent.filter(([m]) => m.startsWith("send_event")).map(([m]) => m);
        assert.equal(used.length, 2, "the current name and the legacy one");
        assert.deepEqual([...new Set(used)], [wsMethod],
          "both by the same delivery, or one window never hears it");
      } finally {
        delete context.MESSAGE_TYPE_ALIASES.RenamedForThisTest;
      }
    });
  }
});

describe("a window knows its own name", () => {
  test("windowName is readable before the bridge answers", () => {
    // Every published theme awaits get_my_window_name to learn this, which is a round
    // trip for something core detected from the URL at construction.
    const { vpin } = newCore({ windowName: "bg" });

    assert.equal(vpin.windowName, "bg");
  });

  test("the controller's name is whatever the theme declared first", async () => {
    const { vpin } = await coreAtContract(2, { get_theme_windows: ["playfield", "topper"] });

    assert.equal(vpin.windowName, "table", "this window is still the one the URL named");
    assert.equal(vpin.isController(), false, "and it is not the declared controller");
  });
});

describe("the bootstrap page names any declared window", () => {
  for (const name of ["playfield", "topper", "bg"]) {
    test(`/app/${name} is detected as ${name}`, () => {
      // The regex here listed bg|dmd|table, so a declared `playfield` fell through to
      // 'unknown': the socket connected under no name, the bridge never saw the window,
      // and the screen stayed black with nothing in the log.
      const { vpin } = newCore({ search: "", pathname: `/app/${name}` });

      assert.equal(vpin.windowName, name);
    });
  }
});

// Core holds the list the player is moving, and the wheel is only one of them.
//
// The thing worth testing is not the cursor - NavigableList covers that - but what does
// *not* happen when a picker's cursor moves. Every consequence of the wheel moving hangs
// off _currentTableIndex and #selectionChanged, and a collection cursor must fire none of
// it. That was the whole difficulty of this change.

import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { newCore } from "./support/load-core.js";

function controller(games = 5) {
  const { vpin, context } = newCore();
  vpin.isController = () => true;
  // No socket here, and #selectionChanged reaches the bridge through #renderWindowMedia.
  // Left unstubbed it rejects after the test has ended, which node reports as the whole
  // file failing rather than as an assertion.
  vpin.call = async () => null;
  vpin.tableData = Array.from({ length: games }, (_, i) => ({ id: `g${i}` }));
  const sent = [];
  vpin.sendMessageToAllWindowsIncSelf = (m) => sent.push(m);
  return { vpin, sent, context };
}

describe("the list core is moving", () => {
  test("the wheel is what is on top until something is pushed", () => {
    const { vpin } = controller();

    assert.equal(vpin.atRoot, true);
    assert.equal(vpin.activeList().kind, "table");
  });

  test("a pushed list becomes the one the actions move", () => {
    const { vpin } = controller();
    vpin.pushList(vpin.createList([{ name: "Favorites" }, { name: "Played" }],
                                  { id: "collections", kind: "collection" }));

    vpin.moveBy(1);

    assert.equal(vpin.atRoot, false);
    assert.equal(vpin.activeList().cursor, 1);
  });

  test("moving a picker does not move the wheel", () => {
    const { vpin } = controller();
    vpin.moveBy(2);
    const wheelAt = vpin._currentTableIndex;
    vpin.pushList(vpin.createList([{ name: "A" }, { name: "B" }], { kind: "collection" }));

    vpin.moveBy(1);

    assert.equal(vpin._currentTableIndex, wheelAt,
                 "the selected game must not follow a collection cursor");
  });

  test("moving a picker fires nothing that follows the wheel", () => {
    const { vpin } = controller();
    const seen = [];
    vpin.onSelection((i) => seen.push(i));
    vpin.pushList(vpin.createList([{ name: "A" }, { name: "B" }], { kind: "collection" }));
    seen.length = 0;

    vpin.moveBy(1);

    assert.deepEqual(seen, [],
                     "a rating fetch or a media re-render here would be for the wrong list");
  });

  test("moving the wheel still fires them", () => {
    const { vpin } = controller();
    const seen = [];
    vpin.onSelection((i) => seen.push(i));

    vpin.moveBy(1);

    assert.deepEqual(seen, [1]);
  });

  test("coming back out leaves the wheel where the player left it", () => {
    const { vpin } = controller();
    vpin.moveBy(3);
    vpin.pushList(vpin.createList([{ name: "A" }, { name: "B" }], { kind: "collection" }));
    vpin.moveBy(1);

    assert.equal(vpin.popList(), true);
    assert.equal(vpin.atRoot, true);
    assert.equal(vpin.activeList().cursor, 3);
  });

  test("popping at the root reports that there was nothing to pop", () => {
    const { vpin } = controller();

    assert.equal(vpin.popList(), false);
  });
});

describe("what an index message says about its list", () => {
  test("a wheel move names the wheel", () => {
    const { vpin, sent } = controller();
    vpin.collection = "Favorites";

    vpin.moveBy(1);

    assert.equal(sent[0].kind, "table");
    assert.equal(sent[0].list, "Favorites");
  });

  test("a picker move names the picker", () => {
    const { vpin, sent } = controller();
    vpin.pushList(vpin.createList([{ name: "A" }, { name: "B" }],
                                  { id: "collections", kind: "collection" }));
    sent.length = 0;

    vpin.moveBy(1);

    assert.equal(sent[0].kind, "collection");
    assert.equal(sent[0].list, "collections");
  });

  test("a picker move carries no group, because the groups are the wheel's", () => {
    const { vpin, sent } = controller();
    vpin.groupBy = "letter";
    vpin.pushList(vpin.createList([{ name: "A" }, { name: "B" }], { kind: "collection" }));
    sent.length = 0;

    vpin.moveBy(1);

    assert.equal(sent[0].group, "");
    assert.equal(sent[0].groupKind, "");
  });

  test("descending and coming back each announce themselves", () => {
    const { vpin, sent } = controller();

    vpin.pushList(vpin.createList([{ name: "A" }], { kind: "collection" }));
    vpin.popList();

    assert.deepEqual(sent.map((m) => m.reason), ["enter", "leave"]);
  });
});

describe("select and back while core holds a list", () => {
  test("select applies the collection, reloads, and tells the other windows", async () => {
    // All three: the backend swaps the view, this window still holds the previous list,
    // and the other windows have heard nothing. Stopping after the first looked wired.
    const { vpin, sent } = controller();
    const calls = [];
    vpin.call = async (name, ...args) => {
      calls.push([name, ...args]);
      return name === "get_tables" ? '[{"id":"x"}]' : null;
    };
    vpin.pushList(vpin.createList([{ name: "Favorites" }, { name: "Played" }],
                                  { kind: "collection" }));
    vpin.moveBy(1);
    sent.length = 0;

    assert.equal(await vpin.selectCurrent(), true);

    // The first two in order; what getTableData does after is its own business.
    assert.deepEqual(calls.slice(0, 2).map((c) => c[0]),
                     ["set_tables_by_collection", "get_tables"]);
    assert.equal(calls[0][1], "Played");
    assert.deepEqual(vpin.tableData.map((row) => row.id), ["x"],
                     "the wheel holds the new list");
    const change = sent.find((m) => m.type === "TableDataChange");
    assert.ok(change, "the other windows were not told");
    assert.equal(change.collection, "Played");
    assert.equal(vpin.atRoot, true);
  });

  test("select at the root is the theme's, because launching lives there", async () => {
    const { vpin } = controller();

    assert.equal(await vpin.selectCurrent(), false);
  });
});

describe("the picker core opens", () => {
  const rows = (showing) => ["", "Favorites", "Played"].map(
    (name) => ({ name, showing: name === showing }));

  test("it starts on the collection that is already showing", async () => {
    const { vpin } = controller();
    vpin.callInternal = async () => rows("Played");

    const list = await vpin.openCollectionPicker();

    assert.equal(list.cursor, 2);
    assert.equal(list.kind, "collection");
  });

  test("All Games leads, is labeled, and is where it opens on the whole library", async () => {
    const { vpin } = controller();
    vpin.callInternal = async () => rows("");

    const list = await vpin.openCollectionPicker();

    assert.equal(list.cursor, 0);
    assert.deepEqual(list.items.map((item) => item.label), ["All Games", "Favorites", "Played"]);
  });

  test("a picker holding only All Games pushes nothing", async () => {
    const { vpin } = controller();
    vpin.callInternal = async () => ([{ name: "" }]);

    assert.equal(await vpin.openCollectionPicker(), null);
    assert.equal(vpin.atRoot, true, "there would be no way back out of an empty list");
  });
});

describe("choosing All Games", () => {
  test("resets the view and tells the other windows the way the menu does", async () => {
    const { vpin, sent } = controller();
    const calls = [];
    vpin.call = async (name, ...args) => {
      calls.push([name, ...args]);
      return name === "get_tables" ? '[{"id":"x"}]' : null;
    };
    vpin.pushList(vpin.createList([{ name: "" }, { name: "Played" }], { kind: "collection" }));
    sent.length = 0;

    assert.equal(await vpin.selectCurrent(), true);

    assert.deepEqual(calls[0], ["get_tables", true]);
    assert.ok(!calls.some((c) => c[0] === "set_tables_by_collection"));
    const change = sent.find((m) => m.type === "TableDataChange");
    assert.equal(change.collection, "None");
  });
});

// The ordered-sequence-and-a-cursor the wheel and the overlays share.
//
// Seven surfaces hand-rolled this: the wheel, three themes' collection pickers, the main
// menu and its dialog, the collection menu and its dropdown. Three shipped the same
// bail-out bug. The rules are here once so a surface that adopts it cannot get wrap or
// clamping subtly different from its neighbour.

import { test, describe } from "node:test";
import assert from "node:assert/strict";

import { loadCore, newCore } from "./support/load-core.js";

const { context } = loadCore();
const list = (items, options) => new context.NavigableList(items, options);

describe("a step wraps both ways", () => {
  test("forward off the end lands on the first", () => {
    assert.equal(list(["a", "b", "c"], { cursor: 2 }).indexAfter(1), 0);
  });

  test("backward off the start lands on the last", () => {
    assert.equal(list(["a", "b", "c"], { cursor: 0 }).indexAfter(-1), 2);
  });

  test("a single item wraps to itself rather than going nowhere", () => {
    // One collection is still a collection you can sit on, which is the case that made
    // a "nothing to show" bail-out look like a broken picker.
    const one = list(["only"], { cursor: 0 });
    assert.equal(one.indexAfter(1), 0);
    assert.equal(one.indexAfter(-1), 0);
  });

  test("an empty list answers with the cursor it has", () => {
    assert.equal(list([]).indexAfter(1), 0, "no caller should have to count first");
  });
});

describe("a jump clamps rather than wrapping", () => {
  test("past the end stops at the last", () => {
    assert.equal(list(["a", "b", "c"]).moveTo(99), 2);
  });

  test("before the start stops at the first", () => {
    assert.equal(list(["a", "b", "c"], { cursor: 2 }).moveTo(-5), 0);
  });

  test("clamping is a guard, not a refusal to wrap", () => {
    // Paging is circular and letter paging wraps from the last Z to the first A - but
    // it wraps in page_jump_index, so the index arriving here is always in range. This
    // only catches one that is not, which would otherwise land somewhere arbitrary.
    assert.equal(list(["a", "b", "c"]).moveTo(3), 2);
    assert.equal(list(["a", "b", "c"], { cursor: 1 }).moveBy(2), 0,
                 "circular movement is moveBy, and it still wraps");
  });

  test("nonsense lands somewhere valid instead of throwing", () => {
    assert.equal(list(["a", "b"]).moveTo(undefined), 0);
  });
});

describe("the list carries what it is, not what is in it", () => {
  test("it knows its own identity", () => {
    assert.equal(list(["a"], { id: "builtin:all" }).id, "builtin:all");
  });

  test("current is the item under the cursor", () => {
    assert.equal(list(["a", "b", "c"], { cursor: 1 }).current, "b");
  });

  test("items can be anything - it never reads inside one", () => {
    // The whole point: a collection list and a game list are the same object. Nothing
    // here may learn what a game is, or a fourth surface cannot use it.
    const collections = list([{ name: "Bally" }, { name: "Seventies" }], { cursor: 1 });
    assert.equal(collections.current.name, "Seventies");
  });

  test("something that is not a list is an empty one", () => {
    assert.equal(list(null).length, 0);
  });
});

describe("the wheel is one of these", () => {
  test("wheelList reflects the entries and the cursor core holds", () => {
    const { vpin } = newCore();
    vpin.tableData = [{}, {}, {}];
    vpin._currentTableIndex = 2;

    const wheel = vpin.wheelList();

    assert.equal(wheel.length, 3);
    assert.equal(wheel.cursor, 2);
    assert.equal(wheel.indexAfter(1), 0, "the wheel wraps like any other list");
  });

  test("it is built per call, so tableData stays the only copy", () => {
    // Holding a list would give the entries two homes, and disagreeing copies of the
    // wheel is the bug this abstraction exists to retire.
    const { vpin } = newCore();
    vpin.tableData = [{}, {}];
    const first = vpin.wheelList();
    vpin.tableData = [{}, {}, {}, {}];

    assert.equal(first.length, 2, "the old list kept its own snapshot");
    assert.equal(vpin.wheelList().length, 4, "and a new one sees the new entries");
  });

  test("a theme can borrow one for a surface of its own", () => {
    const { vpin } = newCore();

    const menu = vpin.createList(["Settings", "Quit"], { id: "my-menu" });

    assert.equal(menu.indexAfter(-1), 1, "core keeps the arithmetic; nobody rewrites it");
  });
});

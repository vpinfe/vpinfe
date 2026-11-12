// Reading contract 2: an entry list instead of a row array.
//
// Contract 2 wraps the list, an entry is a table with its game attached, and media is a
// set of kind names rather than a filesystem path per kind. Nothing published declares
// it, so this is the only thing exercising the shape at all.

import { test, describe } from "node:test";
import assert from "node:assert/strict";

import { newCore, fixture } from "./support/load-core.js";

const PAYLOAD = fixture("theme_payload.json").contract2;

const BRIDGE_DEFAULTS = {
  get_theme_assets_port: 8000,
  get_initial_table_index: 0,
  get_theme_config: {},
  get_keymapping: {},
  get_joymaping: {},
  get_mainmenu_config: {},
  get_monitors: [],
  get_collections: [],
};

// The payload comes from the fixture Python captures, so this cannot drift from what the
// builder actually produces.
async function coreWithEntries(payload = PAYLOAD) {
  const { vpin, browser } = newCore({ windowName: "table" });
  vpin.call = (method) => {
    if (method === "get_theme_contract") return Promise.resolve(2);
    if (method === "get_tables") return Promise.resolve(JSON.stringify(payload));
    return Promise.resolve(BRIDGE_DEFAULTS[method]);
  };
  vpin.init();
  await browser.WebSocket.instances.at(-1).onopen();
  return vpin;
}

describe("the envelope is unwrapped into a list", () => {
  test("entries is the array a theme iterates", async () => {
    const vpin = await coreWithEntries();

    assert.ok(Array.isArray(vpin.entries));
    assert.equal(vpin.entries.length, PAYLOAD.count);
    assert.equal(vpin.getTableCount(), PAYLOAD.count,
      "the ordinal helpers count entries, not games");
  });

  test("the collection the list belongs to travels with it", async () => {
    const vpin = await coreWithEntries({ ...PAYLOAD, collection: "Friday Night" });

    assert.equal(vpin.collection, "Friday Night");
  });

  test("contract 1's array still loads", async () => {
    const rows = fixture("theme_payload.json").contract1;
    const { vpin, browser } = newCore();
    vpin.call = (method) => {
      if (method === "get_theme_contract") return Promise.resolve(1);
      if (method === "get_tables") return Promise.resolve(JSON.stringify(rows));
      return Promise.resolve(BRIDGE_DEFAULTS[method]);
    };
    vpin.init();
    await browser.WebSocket.instances.at(-1).onopen();

    assert.equal(vpin.tableData.length, rows.length);
  });
});

describe("an entry is a table with its game attached", () => {
  test("it carries both identities", async () => {
    const entry = (await coreWithEntries()).entries[0];

    assert.ok(entry.game.id, "the game it belongs to");
    assert.ok(entry.table.id, "the table it is");
    assert.ok(entry.table.filename);
  });

  test("it carries the play stats a theme would show", async () => {
    const entry = (await coreWithEntries()).entries[0];

    for (const field of ["rating", "favorite", "tags", "last_played",
                         "play_count", "play_time_seconds"]) {
      assert.ok(field in entry.game.user, `game.user.${field} is missing`);
    }
    assert.ok("play_count" in entry.table.user, "the table keeps its own counters");
  });
});

describe("media is named, and the URL follows from the name", () => {
  test("a kind the entry has resolves to the media route", async () => {
    const vpin = await coreWithEntries();
    const entry = vpin.entries[0];
    const kind = entry.media.find((name) => name === "wheel") || entry.media[0];

    const url = vpin.getImageURL(0, kind);

    assert.ok(url.includes(`/media/${entry.table.id}/${kind}`),
      `expected the media route, got ${url}`);
    assert.ok(!url.includes("/tables/"), "no filesystem path reaches a contract-2 theme");
  });

  test("a kind with pages lists them in order, page 1 first", async () => {
    const withPages = {
      ...PAYLOAD,
      entries: PAYLOAD.entries.map((entry, n) => (n === 0
        ? { ...entry, media: [...new Set([...entry.media, "flyer"])], pages: { flyer: 3 } }
        : entry)),
    };
    const vpin = await coreWithEntries(withPages);

    const urls = vpin.getMediaPages(0, "flyer");

    assert.equal(urls.length, 3);
    assert.ok(urls[0].endsWith(`/media/${vpin.entries[0].table.id}/flyer`), urls[0]);
    assert.deepEqual(urls.slice(1), [`${urls[0]}?page=2`, `${urls[0]}?page=3`]);
  });

  test("a kind with one page lists that one, and a kind with none lists nothing", async () => {
    const vpin = await coreWithEntries();
    const kind = vpin.entries[0].media[0];

    assert.equal(vpin.getMediaPages(0, kind).length, 1);
    assert.deepEqual([...vpin.getMediaPages(0, "topper")], []);
    assert.deepEqual([...vpin.getMediaPages(9999, kind)], []);
  });

  test("a kind the entry lacks answers missing, not undefined", async () => {
    const vpin = await coreWithEntries();

    assert.equal(vpin.getMedia(0, "topper").kind, "missing");
  });

  test("a video the entry lacks is the missing-media image, never null", async () => {
    const vpin = await coreWithEntries();
    assert.ok(!vpin.entries[0].media.includes("backglass_video"), "the fixture moved");

    assert.equal(vpin.getVideoURL(0, "bg"), "/core/images/file_missing.png");
  });

  test("a kind that is an image has no video to answer with", async () => {
    const vpin = await coreWithEntries();
    const media = vpin.entries[0].media;
    assert.ok(media.includes("wheel") && media.includes("playfield_video"),
              "the fixture moved");

    assert.equal(vpin.getVideoURL(0, "wheel"), "/core/images/file_missing.png");
    assert.equal(vpin.getVideoURL(0, "audio"), "/core/images/file_missing.png");
    assert.notEqual(vpin.getVideoURL(0, "playfield"), "/core/images/file_missing.png");
    assert.equal(vpin.getVideoURL(0, "playfield"), vpin.getVideoURL(0, "playfield_video"));
  });

  test("every video kind the media spec names is answered when the entry has it", async () => {
    const kinds = ["playfield_video", "backglass_video", "scoreview_video", "topper_video",
                   "loading", "real_dmd_video", "real_dmd_color_video", "full_dmd_video"];
    const everything = {
      ...PAYLOAD,
      entries: PAYLOAD.entries.map((entry, n) => (n === 0
        ? { ...entry, media: [...new Set([...entry.media, ...kinds])] }
        : entry)),
    };
    const vpin = await coreWithEntries(everything);

    for (const kind of kinds) {
      assert.ok(vpin.getVideoURL(0, kind).includes(`/media/${vpin.entries[0].table.id}/${kind}`),
        `${kind} has a video to answer with`);
    }
  });

  test("image versus video is still the user's preference", async () => {
    const vpin = await coreWithEntries();
    const entry = vpin.entries[0];
    if (!entry.media.includes("playfield_video")) return;   // fixture has no video

    assert.equal(vpin.getMedia(0, "playfield").kind, "video", "video by default");

    vpin.mediaPriorities = { ...vpin.mediaPriorities, playfield: "image" };
    assert.equal(vpin.getMedia(0, "playfield").kind, "image");
  });

  test("nothing hands a contract-2 theme a path", async () => {
    const vpin = await coreWithEntries();

    assert.equal(vpin.getMedia(0, "wheel").path, null,
      "the payload names kinds; it does not locate files");
  });

  test("a folder no metadata build has touched still addresses its media", async () => {
    // No tables section yet, so no table id - but it has art and it is in the wheel.
    // The route takes the game id in that case rather than building /media//wheel.
    const vpin = await coreWithEntries();
    const index = vpin.entries.findIndex((e) => !e.table.id && e.media.length);
    if (index < 0) return;                       // fixture has no such game with media

    const url = vpin.getImageURL(index, vpin.entries[index].media[0]);

    assert.ok(!url.includes("/media//"), `an empty id would break the route: ${url}`);
  });

  test("the manufacturer logo comes off the game", async () => {
    const vpin = await coreWithEntries();
    // Null in the fixture - no logo pack is installed - but it must not throw or
    // reach for contract 1's key.
    assert.doesNotThrow(() => vpin.getManufacturerLogoURL(0));
  });
});

describe("selection is something you can follow", () => {
  test("a listener runs when the wheel moves", async () => {
    const vpin = await coreWithEntries();
    const seen = [];
    vpin.onSelection((index) => seen.push(index));

    vpin.sendMessageToAllWindows({ type: "TableIndexUpdate", index: 2 });

    assert.deepEqual(seen, [2], "the listener gets the index it moved to");
  });

  test("unsubscribing stops it", async () => {
    const vpin = await coreWithEntries();
    let calls = 0;
    const off = vpin.onSelection(() => { calls += 1; });

    vpin.sendMessageToAllWindows({ type: "TableIndexUpdate", index: 1 });
    off();
    vpin.sendMessageToAllWindows({ type: "TableIndexUpdate", index: 2 });

    assert.equal(calls, 1);
  });

  test("one listener throwing does not stop the others", async () => {
    const vpin = await coreWithEntries();
    let reached = false;
    vpin.onSelection(() => { throw new Error("bad listener"); });
    vpin.onSelection(() => { reached = true; });

    vpin.sendMessageToAllWindows({ type: "TableIndexUpdate", index: 1 });

    assert.ok(reached, "the rating fetch must not be lost to a theme's bad handler");
  });

  test("a message that is not a selection change does not fire it", async () => {
    const vpin = await coreWithEntries();
    let calls = 0;
    vpin.onSelection(() => { calls += 1; });

    vpin.sendMessageToAllWindows({ type: "SomeThemeMessage", index: 4 });

    assert.equal(calls, 0);
  });
});

describe("what a theme reads before any payload", () => {
  test("collection has its documented type from the start", () => {
    // A theme renders once on boot, before the first TableDataChange. Reading undefined
    // there is how `collection || "All games"` silently becomes the fallback forever.
    const { vpin } = newCore({ windowName: "table" });

    assert.equal(vpin.collection, "");
  });
});

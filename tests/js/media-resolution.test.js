// What a theme gets when it asks for media.
//
// This is the surface a rename broke on the way to the cabinet: MEDIA_PATH_FIELDS maps a
// kind to a payload key, and when the payload key moved the map still pointed at the old
// one. Six gates and 745 Python tests were green. These assertions are the ones that
// would not have been.

import { test, describe } from "node:test";
import assert from "node:assert/strict";

import { newCore, fixture } from "./support/load-core.js";

const PAYLOAD = fixture("theme_payload.json");
const ROWS = PAYLOAD.contract1;

const byFolder = (name) => ROWS.findIndex((row) => row.tableDirName === name);
const AFM = byFolder("Attack from Mars (Bally 1995)");
const CONGO = byFolder("Congo (Williams 1995)");
const MM = byFolder("Medieval Madness (Williams 1997)");
const BARE = byFolder("Bare Table (Gottlieb 1980)");

function coreWithLibrary() {
  const { vpin } = newCore({ windowName: "table" });
  vpin.tableData = ROWS;
  vpin.themeAssetsPort = 8000;
  return vpin;
}

describe("media kinds resolve to URLs", () => {
  test("every kind the payload resolved has a reachable URL", () => {
    const vpin = coreWithLibrary();

    for (const [kind, expected] of [
      ["wheel", "wheel.png"],
      ["bg", "bg.png"],
      ["dmd", "dmd.png"],
    ]) {
      const url = vpin.getImageURL(AFM, kind);
      assert.ok(url.startsWith("http://127.0.0.1:8000/"),
        `${kind} should resolve to a served URL, got ${url}`);
      assert.ok(url.endsWith(expected), `${kind} should end in ${expected}, got ${url}`);
    }
  });

  test("a kind with no file resolves to the missing placeholder, never undefined", () => {
    const vpin = coreWithLibrary();
    const media = vpin.getMedia(BARE, "wheel");

    assert.equal(media.kind, "missing");
    assert.equal(media.path, null);
    assert.ok(media.url.length > 0, "a missing kind still needs something to put in src");
  });

  test("an unknown kind does not throw", () => {
    const vpin = coreWithLibrary();
    // A theme asking for a kind this build does not have is a version skew, not a crash.
    assert.doesNotThrow(() => vpin.getMedia(AFM, "no_such_kind"));
  });
});

describe("the URL builder handles every layout the scan produces", () => {
  test("media under medias/ is served from the game folder", () => {
    const vpin = coreWithLibrary();
    const url = vpin.getImageURL(AFM, "wheel");

    assert.match(url, /\/tables\/Attack%20from%20Mars%20\(Bally%201995\)\/medias\/wheel\.png$/);
  });

  test("media at the folder root is served too", () => {
    const vpin = coreWithLibrary();
    const url = vpin.getImageURL(CONGO, "wheel");

    assert.ok(url.startsWith("http://127.0.0.1:8000/"));
    assert.ok(url.endsWith("wheel.png"));
    assert.ok(!url.includes("/medias/"), `root media should not gain a medias segment: ${url}`);
  });

  test("a wheel set keeps every segment below medias/", () => {
    // The case that needed its own branch: the file sits deeper than medias/, so taking
    // the parent of the filename would address the wrong folder.
    const vpin = coreWithLibrary();
    const url = vpin.getImageURL(MM, "wheel");

    assert.match(url, /\/medias\/wheels\/monochrome\/wheel\.png$/);
  });

  test("folder names with spaces and brackets are encoded", () => {
    const vpin = coreWithLibrary();
    const url = vpin.getImageURL(AFM, "wheel");

    assert.ok(!url.includes(" "), `a raw space would break the request: ${url}`);
  });
});

describe("an index with no row still gets an image", () => {
  test("outside the list, the answer is the missing-media image and never null", () => {
    const vpin = coreWithLibrary();

    for (const index of [ROWS.length, ROWS.length + 3, -1]) {
      assert.equal(vpin.getImageURL(index, "bg"), "/core/images/file_missing.png",
        `index ${index} of ${ROWS.length}`);
    }
  });

  test("an empty list answers the same way", () => {
    const vpin = coreWithLibrary();
    vpin.tableData = [];

    assert.equal(vpin.getImageURL(0, "wheel"), "/core/images/file_missing.png");
  });

  test("a video asked for outside the list is the missing-media image too", () => {
    const vpin = coreWithLibrary();

    for (const index of [ROWS.length, -1]) {
      assert.equal(vpin.getVideoURL(index, "bg"), "/core/images/file_missing.png",
        `index ${index} of ${ROWS.length}`);
    }
  });

  test("a media lookup outside the list answers missing, with the image to show", () => {
    const vpin = coreWithLibrary();

    for (const kind of ["bg", "real_dmd", "wheel"]) {
      const media = vpin.getMedia(ROWS.length, kind);
      assert.equal(media.kind, "missing", kind);
      assert.equal(media.url, "/core/images/file_missing.png", kind);
      assert.equal(vpin.getMediaURL(ROWS.length, kind), "/core/images/file_missing.png");
    }
  });
});

describe("image versus video is the user's preference, and it is honoured", () => {
  test("video wins by default when both exist", () => {
    const vpin = coreWithLibrary();
    const media = vpin.getMedia(AFM, "playfield");

    assert.equal(media.kind, "video");
    assert.ok(media.url.endsWith(".mp4"));
  });

  test("image wins when the priority says so", () => {
    const vpin = coreWithLibrary();
    vpin.mediaPriorities = { ...vpin.mediaPriorities, playfield: "image" };
    const media = vpin.getMedia(AFM, "playfield");

    assert.equal(media.kind, "image");
    assert.ok(media.url.endsWith(".png"));
  });

  test("the preference falls back rather than showing nothing", () => {
    // Congo has neither playfield image nor video; bg exists only as an image.
    const vpin = coreWithLibrary();
    vpin.mediaPriorities = { ...vpin.mediaPriorities, backglass: "video" };
    const media = vpin.getMedia(MM, "bg");

    assert.equal(media.kind, "image", "a missing video must fall back to the image");
  });
});

describe("the priority the settings send reaches every window's media", () => {
  // What the bridge answers with every priority set to image.
  const IMAGE_FIRST = {
    playfield: "image", backglass: "image", scoreview: "image", real_dmd: "color",
    bg: "image", dmd: "image",
  };
  const BRIDGE = {
    get_theme_assets_port: 8000, get_initial_table_index: 0, get_theme_config: {},
    get_keymapping: {}, get_joymaping: {}, get_mainmenu_config: {}, get_monitors: [],
    get_collections: [], get_media_priorities: IMAGE_FIRST,
  };

  // The first game, with a video for every window so image first has something to beat.
  function withEveryVideo(contract) {
    if (contract === 1) {
      const rows = ROWS.map((row) => ({ ...row }));
      const medias = rows[AFM].BGImagePath.replace(/[^/]+$/, "");
      rows[AFM].BGVideoPath = `${medias}bg.mp4`;
      rows[AFM].DMDVideoPath = `${medias}dmd.mp4`;
      return { payload: rows, index: AFM };
    }
    const entries = PAYLOAD.contract2.entries.map((entry) => ({ ...entry }));
    entries[0].media = [...entries[0].media, "backglass_video", "scoreview_video"];
    return { payload: { ...PAYLOAD.contract2, entries }, index: 0 };
  }

  async function coreThroughInit(contract, payload) {
    const { vpin, browser } = newCore({ windowName: contract === 1 ? "table" : "playfield" });
    vpin.call = (method) => {
      if (method === "get_theme_contract") return Promise.resolve(contract);
      if (method === "get_tables") return Promise.resolve(JSON.stringify(payload));
      return Promise.resolve(BRIDGE[method]);
    };
    vpin.init();
    await browser.WebSocket.instances.at(-1).onopen();
    return vpin;
  }

  test("a theme reading 2.x's bg and dmd off mediaPriorities still finds them", async () => {
    const vpin = await coreThroughInit(1, ROWS);

    assert.equal(vpin.mediaPriorities.bg, "image");
    assert.equal(vpin.mediaPriorities.dmd, "image");
  });

  for (const contract of [1, 2]) {
    test(`image first is honoured for all three windows at contract ${contract}`, async () => {
      const { payload, index } = withEveryVideo(contract);
      const vpin = await coreThroughInit(contract, payload);

      for (const kind of ["playfield", "backglass", "scoreview"]) {
        assert.ok(!vpin.getVideoURL(index, kind).includes("file_missing"),
          `${kind} needs a video for image first to mean anything`);
        const media = vpin.getMedia(index, kind);
        assert.equal(media.kind, "image", `${kind} came back ${media.url}`);
      }
    });
  }
});

describe("realdmd is one kind with two frames", () => {
  test("color is preferred by default", () => {
    const vpin = coreWithLibrary();
    const media = vpin.getMedia(AFM, "realdmd");

    assert.ok(media.url.endsWith("realdmd-color.png"), `got ${media.url}`);
  });

  test("both spellings of the color kind reach the same place", () => {
    const vpin = coreWithLibrary();

    assert.equal(vpin.getMedia(AFM, "realdmd-color").url,
                 vpin.getMedia(AFM, "realdmd_color").url);
  });
});

describe("the kind names earlier builds used still answer", () => {
  test("table, table_video and fss reach their renamed kinds", () => {
    const vpin = coreWithLibrary();

    // Assert a real file, not just that two calls agree - both would agree on the
    // missing-media placeholder, which is how this passed while getImageURL ignored
    // the alias table entirely.
    const viaOldName = vpin.getImageURL(AFM, "table");
    assert.ok(viaOldName.endsWith("table.png"), `expected the playfield, got ${viaOldName}`);
    assert.equal(viaOldName, vpin.getImageURL(AFM, "playfield"));

    const video = vpin.getVideoURL(AFM, "table");
    assert.ok(video.endsWith("table.mp4"), `expected the playfield video, got ${video}`);
  });

  test("a theme naming the playfield by its old key still gets the playfield", () => {
    // The single most common call in any theme, and the one a rename would break
    // most visibly.
    const vpin = coreWithLibrary();

    assert.notEqual(vpin.getMedia(AFM, "table").kind, "missing");
    assert.ok(vpin.getImageURL(AFM, "table").includes("/tables/"),
      "getImageURL is the call every published theme uses, so it has to alias too");
  });
});

describe("a window name is also a media kind", () => {
  // cab, Basic Cab and Trinidad all call getImageURL(index, windowName). Nothing in
  // either vocabulary says the two have to line up, but three published themes rely on
  // it, so renaming a window or a kind has to keep every window name resolvable.
  for (const windowName of ["table", "bg", "dmd"]) {
    test(`getImageURL(i, "${windowName}") resolves`, () => {
      const vpin = coreWithLibrary();

      const url = vpin.getImageURL(AFM, windowName);
      assert.ok(url.includes("/tables/"),
        `${windowName} is a window name three themes pass as a media kind; got ${url}`);
    });
  }
});

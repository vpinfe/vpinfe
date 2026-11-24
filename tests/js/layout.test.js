// vpin.layout - the three layout answers, resolved once.
//
// Four published themes each worked these out by hand and no two agreed: six of the eight
// display configurations produced different geometry depending on which theme was
// installed, so the correct value in a user's ini depended on their theme. These
// assertions are the single answer that replaces that.

import { test, describe } from "node:test";
import assert from "node:assert/strict";

import { newCore } from "./support/load-core.js";

const BRIDGE = {
  get_tables: "[]",
  get_theme_assets_port: 8000,
  get_initial_table_index: 0,
  get_theme_config: {},
  get_keymapping: {},
  get_joymaping: {},
  get_mainmenu_config: {},
  get_monitors: [],
  get_collections: [],
  get_audio_muted: false,
};

/** A booted core with the display config the ini would have supplied. */
async function coreWith({ orientation, rotation, cabMode = false, windowName = "table",
                          innerWidth, innerHeight }) {
  const { vpin, browser } = newCore({ windowName, innerWidth, innerHeight });
  vpin.call = (method) => Promise.resolve({
    ...BRIDGE,
    get_playfield_orientation: orientation,
    get_playfield_rotation: rotation,
    get_cab_mode: cabMode,
  }[method]);
  vpin.init();
  await browser.WebSocket.instances.at(-1).onopen();
  return { vpin, browser };
}

describe("the shape a theme designs for", () => {
  // The cabinet cases that matter: the OS turned the screen, or VPinFE has to.
  const CASES = [
    { name: "OS-rotated portrait cabinet", orientation: "portrait", rotation: 0,
      surface: "portrait", uprightRotation: 0 },
    { name: "portrait cabinet the OS did not turn", orientation: "portrait", rotation: 90,
      surface: "portrait", uprightRotation: 90 },
    { name: "portrait cabinet turned the other way", orientation: "portrait", rotation: 270,
      surface: "portrait", uprightRotation: 270 },
    { name: "desktop", orientation: "landscape", rotation: 0,
      surface: "landscape", uprightRotation: 0 },
    { name: "landscape playfield, upside down", orientation: "landscape", rotation: 180,
      surface: "landscape", uprightRotation: 180 },
  ];

  for (const c of CASES) {
    test(`${c.name}: ${c.orientation} + ${c.rotation}`, async () => {
      const { vpin } = await coreWith(c);

      assert.equal(vpin.layout.surface, c.surface);
      assert.equal(vpin.layout.uprightRotation, c.uprightRotation);
    });
  }

  test("a portrait cabinet reads portrait however it got there", async () => {
    // The whole point of `surface`: these two setups look identical to a theme, so one
    // layout serves both.
    const osTurnedIt = await coreWith({ orientation: "portrait", rotation: 0 });
    const weTurnIt = await coreWith({ orientation: "portrait", rotation: 90 });

    assert.equal(osTurnedIt.vpin.layout.surface, weTurnIt.vpin.layout.surface);
    assert.notEqual(osTurnedIt.vpin.layout.uprightRotation,
      weTurnIt.vpin.layout.uprightRotation, "but they do not turn the UI the same way");
  });
});

describe("every combination resolves to a quarter turn", () => {
  for (const orientation of ["landscape", "portrait"]) {
    for (const rotation of [0, 90, 180, 270]) {
      test(`${orientation} + ${rotation}`, async () => {
        const { vpin } = await coreWith({ orientation, rotation });

        assert.equal(vpin.layout.surface, orientation,
          "surface is the mounting; the rotation only says who did the turning");
        assert.equal(vpin.layout.uprightRotation, rotation);
      });
    }
  }
});

describe("values the ini is free to contain but nothing validates", () => {
  test("orientation is matched without regard to case", async () => {
    // [Displays] is a free-text field and every theme compares it with ===, so `Portrait`
    // silently meant landscape.
    const { vpin } = await coreWith({ orientation: "Portrait", rotation: 0 });

    assert.equal(vpin.layout.surface, "portrait");
  });

  test("surrounding whitespace does not change the answer", async () => {
    const { vpin } = await coreWith({ orientation: "  portrait  ", rotation: 0 });

    assert.equal(vpin.layout.surface, "portrait");
  });

  test("a word that is neither falls back to landscape", async () => {
    const { vpin } = await coreWith({ orientation: "portait", rotation: 0 });

    assert.equal(vpin.layout.surface, "landscape");
  });

  test("a rotation that is not a quarter turn falls back to none", async () => {
    // 45 reached themes that test for 90 or 270, skipped the axis swap, and rendered a
    // 45-degree image in an unswapped box.
    const { vpin } = await coreWith({ orientation: "portrait", rotation: 45 });

    assert.equal(vpin.layout.uprightRotation, 0);
  });

  test("rotation given as a string still resolves", async () => {
    const { vpin } = await coreWith({ orientation: "portrait", rotation: "90" });

    assert.equal(vpin.layout.uprightRotation, 90);
  });

  test("a negative rotation resolves to its positive equivalent", async () => {
    const { vpin } = await coreWith({ orientation: "portrait", rotation: -90 });

    assert.equal(vpin.layout.uprightRotation, 270);
  });

  test("nonsense does not throw", async () => {
    await assert.doesNotReject(() =>
      coreWith({ orientation: null, rotation: "sideways" }));
  });
});

describe("windows other than the controller", () => {
  test("a backglass measures its own shape and is never turned", async () => {
    // playfieldorientation describes the playfield monitor. Nothing describes the others,
    // and nothing rotates them.
    const { vpin } = await coreWith({
      orientation: "portrait", rotation: 90, windowName: "bg",
      innerWidth: 1920, innerHeight: 1080,
    });

    assert.equal(vpin.layout.uprightRotation, 0, "the playfield's rotation is not theirs");
    assert.equal(vpin.layout.surface, "landscape");
  });

  test("a tall secondary window measures portrait", async () => {
    // Proves the measurement is read rather than defaulted: same config, taller window.
    const { vpin } = await coreWith({
      orientation: "landscape", rotation: 0, windowName: "dmd",
      innerWidth: 1080, innerHeight: 1920,
    });

    assert.equal(vpin.layout.surface, "portrait");
  });
});

describe("the cabinet flag", () => {
  test("it carries through", async () => {
    const { vpin } = await coreWith({ orientation: "portrait", rotation: 0, cabMode: true });

    assert.equal(vpin.layout.cabinet, true);
  });

  test("it does not touch geometry", async () => {
    const off = await coreWith({ orientation: "landscape", rotation: 0, cabMode: false });
    const on = await coreWith({ orientation: "landscape", rotation: 0, cabMode: true });

    assert.equal(off.vpin.layout.surface, on.vpin.layout.surface);
    assert.equal(off.vpin.layout.uprightRotation, on.vpin.layout.uprightRotation);
  });
});

describe("before the bridge answers", () => {
  test("layout already has its documented types", () => {
    // A theme lays out once on boot. Reading undefined there is how a fallback becomes
    // permanent.
    const { vpin } = newCore({ windowName: "table" });

    assert.equal(vpin.layout.cabinet, false);
    assert.equal(vpin.layout.uprightRotation, 0);
    assert.equal(vpin.layout.surface, "landscape");
  });
});

describe("core_layout publishes the layout to CSS", () => {
  /** Boot with the capability on, as a theme's config.json would ask for. */
  async function coreWithLayout(display, mediaRotation = "auto") {
    const { vpin, browser } = newCore({ windowName: "table" });
    vpin.call = (method) => Promise.resolve({
      ...BRIDGE,
      get_theme_config: { layout: { enabled: true } },
      get_playfield_orientation: display.orientation,
      get_playfield_rotation: display.rotation,
      get_cab_mode: display.cabMode ?? false,
      get_playfield_media_rotation: mediaRotation,
    }[method]);
    vpin.init();
    await browser.WebSocket.instances.at(-1).onopen();
    return { vpin, root: browser.document.documentElement };
  }

  test("it sets nothing unless the theme opted in", async () => {
    const { vpin, browser } = await coreWith({ orientation: "portrait", rotation: 90 });

    assert.equal(vpin.enabled("core_layout"), false);
    assert.equal(browser.document.documentElement.dataset.vpinfeSurface, undefined,
      "a theme that lays itself out must not find core moving its root element");
  });

  test("surface and rotation reach the stylesheet", async () => {
    const { root } = await coreWithLayout({ orientation: "portrait", rotation: 90 });

    assert.equal(root.dataset.vpinfeSurface, "portrait");
    assert.equal(root.dataset.vpinfeUpright, "90");
    assert.equal(root.style._props["--vpinfe-upright-rotation"], "90deg");
  });

  test("the cabinet flag is only present on a cabinet", async () => {
    const off = await coreWithLayout({ orientation: "landscape", rotation: 0 });
    const on = await coreWithLayout({ orientation: "landscape", rotation: 0, cabMode: true });

    assert.equal(off.root.dataset.vpinfeCabinet, undefined);
    assert.equal(on.root.dataset.vpinfeCabinet, "true");
  });
});

describe("playfield art is measured, not assumed", () => {
  /** An element that already knows its size, the way a cached image does. */
  const media = (w, h) => ({
    naturalWidth: w, naturalHeight: h, dataset: {},
    style: { _props: {}, setProperty(n, v) { media.last = v; this._props[n] = v; } },
    addEventListener() {},
  });

  async function applied(display, art, mediaRotation = "auto") {
    const { vpin, browser } = newCore({ windowName: "table" });
    vpin.call = (m) => Promise.resolve({
      ...BRIDGE,
      get_theme_config: { layout: { enabled: true } },
      get_playfield_orientation: display.orientation,
      get_playfield_rotation: display.rotation,
      get_cab_mode: false,
      get_playfield_media_rotation: mediaRotation,
    }[m]);
    vpin.init();
    await browser.WebSocket.instances.at(-1).onopen();
    vpin.applyPlayfieldMediaRotation(art);
    return art;
  }

  test("landscape art on a portrait cabinet is turned a quarter", async () => {
    const art = await applied({ orientation: "portrait", rotation: 0 }, media(1920, 1080));

    assert.equal(art.style._props["--vpinfe-playfield-media-rotation"], "90deg");
    assert.equal(art.dataset.vpinfeTurned, "true");
  });

  test("portrait art on a portrait cabinet is left alone", async () => {
    // The FSS case. A fixed "always turn on portrait" rule would break exactly the
    // cabinets most likely to use it.
    const art = await applied({ orientation: "portrait", rotation: 0 }, media(1080, 1920));

    assert.equal(art.style._props["--vpinfe-playfield-media-rotation"], "0deg");
    assert.equal(art.dataset.vpinfeTurned, "false");
  });

  test("landscape art on a desktop is left alone", async () => {
    const art = await applied({ orientation: "landscape", rotation: 0 }, media(1920, 1080));

    assert.equal(art.style._props["--vpinfe-playfield-media-rotation"], "0deg");
  });

  test("portrait art on a desktop is turned", async () => {
    const art = await applied({ orientation: "landscape", rotation: 0 }, media(1080, 1920));

    assert.equal(art.style._props["--vpinfe-playfield-media-rotation"], "90deg");
  });

  test("a stated rotation overrides the measurement", async () => {
    // What measuring cannot see: art that is upside down. Arrives as a string.
    const art = await applied({ orientation: "portrait", rotation: 0 }, media(1920, 1080), "180");

    assert.equal(art.style._props["--vpinfe-playfield-media-rotation"], "180deg");
    assert.equal(art.dataset.vpinfeTurned, "false", "a half turn does not swap the axes");
  });

  test("a stated zero means never turn, even when the aspect disagrees", async () => {
    // Letterbox by choice rather than turn.
    const art = await applied({ orientation: "portrait", rotation: 0 }, media(1920, 1080), "0");

    assert.equal(art.style._props["--vpinfe-playfield-media-rotation"], "0deg");
  });

  test("it does nothing when the theme did not opt in", async () => {
    const { vpin } = await coreWith({ orientation: "portrait", rotation: 0 });
    const art = media(1920, 1080);

    vpin.applyPlayfieldMediaRotation(art);

    assert.deepEqual({ ...art.dataset }, {});
  });
});

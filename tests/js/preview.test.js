// A recording kept for a decision, shown in place: which window draws it, what it
// plays, what the controller says, and what the buttons answer.

import { test, describe } from "node:test";
import assert from "node:assert/strict";

import { loadCore } from "./support/load-core.js";

const settle = () => new Promise(resolve => setTimeout(resolve, 0));

const PLAYFIELD = { proposal: "3f9c0a1b2c4d", showing: "after", kind: "playfield_video",
                    label: "Playfield Video", game_id: "6f1c9a4e", table_id: "",
                    before: true };
const BACKGLASS = { ...PLAYFIELD, kind: "backglass_video", label: "Backglass Video" };

// One of the three windows a theme that declares nothing opens, with core's own calls
// noted and a theme handler that notes what reaches it.
function window_(name) {
  const { VPinFECore, browser } = loadCore({ windowName: name });
  const vpin = new VPinFECore();
  const internal = [];
  vpin.callInternal = async (...call) => { internal.push(call); return {}; };
  vpin.call = async () => null;
  const theme = [];
  vpin.inputHandlers.push((action) => theme.push(action));
  vpin.registerEventHandler("ProposalPreview", () => theme.push("ProposalPreview"));
  const show = (preview) => vpin.handleEvent({ type: "ProposalPreview", preview });
  const layer = () =>
    browser.document.body.children.find(el => el.className === "vpinfe-preview");
  const find = (name) => {
    const walk = (el) => el.className === name ? el
      : (el.children || []).map(walk).find(Boolean);
    return layer() && walk(layer());
  };
  const press = async (action) => {
    await vpin.handleEvent({ type: "InputAction", action, phase: "press" });
    await vpin.handleEvent({ type: "InputAction", action, phase: "release" });
    await settle();
  };
  return { vpin, show, layer, find, press, internal, theme };
}

describe("the window whose screen it is plays it", () => {
  test("a playfield video plays on the controller, under what it is and the buttons", async () => {
    const { show, layer, find } = window_("table");

    await show(PLAYFIELD);

    assert.equal(layer().attributes["data-shows"], "media");
    assert.equal(find("vpinfe-preview-media").src,
                 "http://127.0.0.1:8001/api/v1/capture/proposals/3f9c0a1b2c4d/file");
    assert.equal(find("vpinfe-preview-media").loop, true);
    assert.equal(find("vpinfe-preview-title").textContent, "Playfield Video - After");
    assert.equal(find("vpinfe-preview-hint").textContent,
                 "Left and Right to compare, Select to keep, Back to discard");
  });

  test("a choice that was refused says why where the hint was", async () => {
    const { show, find } = window_("table");

    await show({ ...PLAYFIELD, refused: "The backups folder cannot be written to" });

    assert.equal(find("vpinfe-preview-hint").textContent,
                 "The backups folder cannot be written to");
  });

  test("a backglass video plays on the backglass window, saying which side it is", async () => {
    const { show, find } = window_("bg");

    await show(BACKGLASS);

    assert.match(find("vpinfe-preview-media").src, /proposals\/3f9c0a1b2c4d\/file$/);
    assert.equal(find("vpinfe-preview-tag").children[0].textContent, "After");
    assert.equal(find("vpinfe-preview-bar"), undefined);
  });

  test("the controller then says what it is over its own screen, playing nothing", async () => {
    const { show, layer, find } = window_("table");

    await show(BACKGLASS);

    assert.equal(layer().attributes["data-shows"], "words");
    assert.equal(find("vpinfe-preview-media"), undefined);
    assert.equal(find("vpinfe-preview-title").textContent, "Backglass Video - After");
  });

  test("a window the kind is not for draws nothing", async () => {
    const { show, layer } = window_("dmd");

    await show(BACKGLASS);

    assert.equal(layer(), undefined);
  });

  test("Before plays the file serving the slot now, the table's where it is a table's",
       async () => {
    const { show, find } = window_("bg");

    await show({ ...BACKGLASS, showing: "before", table_id: "tbl0000001" });

    assert.equal(find("vpinfe-preview-media").src,
                 "http://127.0.0.1:8001/api/v1/games/6f1c9a4e/tables/tbl0000001/media/"
                 + "backglass_video");
    assert.equal(find("vpinfe-preview-tag").children[0].textContent, "Before");
  });

  test("Before on an empty slot says it is missing", async () => {
    const { show, find } = window_("bg");

    await show({ ...BACKGLASS, showing: "before", before: false });

    assert.equal(find("vpinfe-preview-missing").textContent, "Missing");
  });

  test("a playfield file is turned to fill the surface, as core turns playfield art",
       async () => {
    const { vpin, show, find } = window_("table");
    vpin.layout = { cabinet: true, uprightRotation: 0, surface: "portrait" };
    await show(PLAYFIELD);
    const media = find("vpinfe-preview-media");

    Object.assign(media, { videoWidth: 1920, videoHeight: 1080 });
    media.listeners.loadedmetadata[0]();

    assert.equal(media.attributes["data-turned"], "true");
    assert.match(media.style.transform, /rotate\(90deg\)/);
  });

  test("it faces the player where the OS does not turn the screen", async () => {
    const { vpin, show, find } = window_("table");
    vpin.layout = { cabinet: true, uprightRotation: 270, surface: "portrait" };

    await show(PLAYFIELD);

    assert.equal(find("vpinfe-preview-surface").attributes["data-upright"], "270");
    assert.match(find("vpinfe-preview-surface").style.transform, /rotate\(270deg\)/);
  });

  test("it goes when core says nothing is on show", async () => {
    const { show, layer } = window_("table");
    await show(PLAYFIELD);

    await show(null);

    assert.equal(layer(), undefined);
  });

  test("the theme's handlers never see it", async () => {
    const { show, theme } = window_("table");

    await show(PLAYFIELD);

    assert.deepEqual(theme, []);
  });
});

describe("the buttons answer it, and nothing else", () => {
  for (const [action, call] of [["previous", ["switch_preview", "before"]],
                                ["next", ["switch_preview", "after"]],
                                ["select", ["decide_preview", true]],
                                ["back", ["decide_preview", false]],
                                ["exit", ["end_preview"]]]) {
    test(`${action} asks core ${call.join(" ")}`, async () => {
      const { show, press, internal, theme } = window_("table");
      await show(PLAYFIELD);

      await press(action);

      assert.deepEqual(internal.map(one => [...one]), [call]);
      assert.deepEqual(theme, [], "the wheel behind it must not move");
    });
  }

  test("the menu does not open over it", async () => {
    const { vpin, show, press, internal } = window_("table");
    await show(PLAYFIELD);

    await press("menu");

    assert.equal(vpin.overlay, null);
    assert.deepEqual(internal, []);
  });

  test("an open menu closes when one is shown", async () => {
    const { vpin, show } = window_("table");
    await vpin.toggleOverlay("menu");

    await show(PLAYFIELD);

    assert.equal(vpin.overlay, null);
  });

  test("once it is gone the buttons are the theme's again", async () => {
    const { show, press, internal, theme } = window_("table");
    await show(PLAYFIELD);
    await show(null);

    await press("next");

    assert.deepEqual(internal, []);
    assert.deepEqual(theme, ["joyright"]);
  });
});

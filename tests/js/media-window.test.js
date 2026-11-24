// A display window shows the media named for it, without the theme saying so.
//
// A media kind is named after the display it captures, so a window that is a display has
// exactly one obvious thing to show. Every theme wrote the same handful of lines to show
// it, and the Reference theme - written to demonstrate best practice - could not avoid
// them either. That is the signal a behavior belongs in core.
//
// The mapping is the part worth testing. A contract 1 window is called `bg` and its media
// is `backglass`; a window a theme invented is neither, and must resolve to nothing
// rather than to a lookup that quietly finds nothing.

import { test, describe } from "node:test";
import assert from "node:assert/strict";

import { newCore, fixture } from "./support/load-core.js";

const ROWS = fixture("theme_payload.json").contract1;

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

/** A core on a named window, with a page that may or may not offer a render target. */
async function coreOn(windowName, { target = true, themeConfig = {} } = {}) {
  const { vpin, browser } = newCore({ windowName });
  vpin.mediaSettleMs = 0;
  vpin.call = (method) => Promise.resolve(
    method === "get_theme_config" ? themeConfig : BRIDGE_DEFAULTS[method]);
  vpin.init();
  await browser.WebSocket.instances.at(-1).onopen();

  const stage = { children: [], getAttribute: () => "", replaceChildren(...kids) { this.children = kids; } };
  if (target) browser.document._queryAll["[data-vpin-media]"] = [stage];

  vpin.tableData = ROWS;
  vpin.themeAssetsPort = 8000;
  return { vpin, stage, browser };
}

/**
 * A core brought through init the way a page does it, with the calls it made on the bridge
 * recorded. The controller is asked for the selection by `controllerAsked` before its
 * restore has had a chance to run.
 */
async function started(windowName, { target = true, index = 0, quick, ask, slow,
                                     controllerAsked = null } = {}) {
  const { vpin, browser } = newCore({ windowName });
  vpin.mediaSettleMs = 0;
  const sent = [];
  // The list this window is given, which a test can change before the next reload.
  const list = { rows: ROWS };
  vpin.call = (method, ...args) => {
    if (method === "send_event") sent.push({ method, window: args[0], message: args[1] });
    else if (method.startsWith("send_event")) sent.push({ method, message: args[0] });
    if (method === "get_tables") return Promise.resolve(JSON.stringify(list.rows));
    if (method === "get_initial_table_index") return Promise.resolve(index);
    return Promise.resolve(BRIDGE_DEFAULTS[method]);
  };
  // A theme's page has registered its handler by the time core asks; without one core
  // waits for it.
  browser.window.receiveEvent = () => {};
  if (quick) vpin.constructor.SELECTION_QUICK_MS = quick;
  if (ask) vpin.constructor.SELECTION_ASK_MS = ask;
  if (slow) vpin.constructor.SELECTION_SLOW_MS = slow;

  const stage = { children: [], getAttribute: () => "",
    replaceChildren(...kids) { this.children = kids; } };
  if (target) browser.document._queryAll["[data-vpin-media]"] = [stage];

  vpin.init();
  if (controllerAsked) vpin.handleEvent({ type: "SelectionRequest", window: controllerAsked });
  await browser.WebSocket.instances.at(-1).onopen();
  await new Promise((resolve) => setTimeout(resolve, 20));
  return { vpin, browser, stage, sent, list };
}

// What a contract 1 row is named by: the table's id, the folder where it has none.
const idOf = (row) => row.meta?.VPXFile?.id || row.meta?.VPinFE?.game_id || row.tableDirName;

// The controller's message for one game: its index in the controller's list, and which
// game that is.
const named = (index, rows = ROWS) => ({
  type: "TableIndexUpdate", index, reason: "restore", id: idOf(rows[index]) });

// The receive path, which is how a secondary window learns the wheel moved. It is not
// the controller, so the send path's selection hook never runs there.
const step = (vpin, index = 0) =>
  vpin.handleEvent({ type: "TableIndexUpdate", index });

describe("the media kind a window shows", () => {
  test("a contract 1 window resolves through its own spelling", async () => {
    const { vpin } = await coreOn("bg");
    assert.equal(vpin.windowMediaKind, "backglass");
  });

  test("a contract 2 window is already the kind", async () => {
    const { vpin } = await coreOn("backglass");
    assert.equal(vpin.windowMediaKind, "backglass");
  });

  test("a window a theme invented is not a media kind", async () => {
    const { vpin } = await coreOn("marquee");
    assert.equal(vpin.windowMediaKind, null);
  });

  test("the controller is not a display, so it renders nothing here", async () => {
    // `table` is contract 1's playfield, which IS a kind - so the guard that keeps core
    // off the controller cannot be the kind lookup. A controller opts out by having no
    // target element, which is what every controller page looks like.
    const { vpin, stage } = await coreOn("table", { target: false });
    step(vpin);
    assert.deepEqual(stage.children, []);
  });
});

describe("core rendering the window's media", () => {
  test("it draws into the element the page offers", async () => {
    const { vpin, stage } = await coreOn("backglass");
    step(vpin);
    assert.equal(stage.children.length, 1);
  });

  test("a value on the element names the kind, in any window", async () => {
    const { vpin, browser } = await coreOn("table", { target: false });
    const named = { children: [], getAttribute: () => "playfield",
      replaceChildren(...kids) { this.children = kids; } };
    browser.document._queryAll["[data-vpin-media]"] = [named];
    step(vpin);
    assert.equal(named.children.length, 1);
  });

  test("every marked element is drawn into, each with its own kind", async () => {
    const { vpin, stage, browser } = await coreOn("backglass");
    const other = { children: [], getAttribute: () => "playfield",
      replaceChildren(...kids) { this.children = kids; } };
    browser.document._queryAll["[data-vpin-media]"] = [stage, other];
    step(vpin);
    assert.equal(stage.children.length, 1);
    assert.equal(other.children.length, 1);
  });

  test("a value that is not a kind draws nothing", async () => {
    const { vpin, browser } = await coreOn("backglass", { target: false });
    const odd = { children: [], getAttribute: () => "true",
      replaceChildren(...kids) { this.children = kids; } };
    browser.document._queryAll["[data-vpin-media]"] = [odd];
    step(vpin);
    assert.deepEqual(odd.children, []);
  });

  test("a page with no target is left alone", async () => {
    const { vpin, stage } = await coreOn("backglass", { target: false });
    step(vpin);
    assert.deepEqual(stage.children, []);
  });

  test("a theme can turn it off and keep the element", async () => {
    const { vpin, stage } = await coreOn("backglass",
      { themeConfig: { media_window: { enabled: false } } });
    step(vpin);
    assert.deepEqual(stage.children, []);
  });

  test("it is on without the theme asking", async () => {
    const { vpin } = await coreOn("backglass");
    assert.equal(vpin.enabled("core_media_window"), true);
  });

  test("a window that is not the controller draws once the controller names the game", async () => {
    const { vpin, stage, sent } = await started("backglass");

    assert.equal(stage.children.length, 0, "nothing drawn while the selection is unknown");
    assert.ok(sent.some((call) => call.message?.type === "SelectionRequest"));

    await vpin.handleEvent(named(0));

    assert.equal(stage.children.length, 1, "the answer names the first game and it drew");
  });

  test("it draws the game the controller names, never the first one first", async () => {
    const { vpin, stage } = await started("backglass");
    const drawn = [];
    stage.replaceChildren = (...kids) => { stage.children = kids; drawn.push(kids[0]?.src); };

    await vpin.handleEvent(named(3));

    assert.equal(drawn.length, 1);
    assert.match(drawn[0], /Medieval/);
  });

  test("a window whose list is ordered differently draws the game named, not the index", async () => {
    const { vpin, stage } = await started("backglass");
    vpin.tableData = [...ROWS].reverse();
    const seen = [];
    vpin.registerEventHandler("TableIndexUpdate", (message) => seen.push(message.index));

    // Medieval Madness is the controller's index 3; here it is first.
    await vpin.handleEvent(named(3));

    assert.match(stage.children[0].src, /Medieval/);
    assert.equal(vpin._currentTableIndex, 0);
    assert.deepEqual(seen, [0], "the theme reads an index into this window's list");
  });

  test("a game this window's list does not hold draws nothing, and clears what it drew", async () => {
    const { vpin, stage } = await started("backglass");
    const seen = [];
    vpin.registerEventHandler("TableIndexUpdate", (message) => seen.push(message.index));
    await vpin.handleEvent(named(0));
    assert.equal(stage.children.length, 1);

    vpin.tableData = ROWS.slice(0, 3);
    await vpin.handleEvent(named(3));

    assert.equal(stage.children.length, 0, "the game before is not left up");
    assert.deepEqual(seen, [0], "the theme is not told an index that is another game here");
  });

  test("a held window draws the game once a reload brings it", async () => {
    const { vpin, stage, list } = await started("backglass");
    list.rows = ROWS.slice(0, 3);
    await vpin.getTableData();
    await vpin.handleEvent(named(3));
    assert.equal(stage.children.length, 0);

    list.rows = ROWS;
    await vpin.getTableData();

    assert.match(stage.children[0].src, /Medieval/);
  });

  test("a reload that reorders the list keeps the game that was named", async () => {
    const { vpin, stage, list } = await started("backglass");
    await vpin.handleEvent(named(3));

    list.rows = [...ROWS].reverse();
    await vpin.getTableData();

    assert.equal(vpin._currentTableIndex, 0);
    assert.match(stage.children[0].src, /Medieval/);
  });

  test("a data change that names an index moves the window there", async () => {
    const { vpin, stage } = await started("backglass");
    await vpin.handleEvent(named(3));

    await vpin.handleEvent({ type: "TableDataChange", index: 0, collection: "None" });
    // A later reload must keep following the game now selected.
    await vpin.getTableData();

    assert.match(stage.children[0].src, /Attack/);
  });

  test("a new collection in another order lands on the game the controller opens on", async () => {
    const { vpin, stage, list } = await started("backglass");
    await vpin.handleEvent(named(3));
    const seen = [];
    vpin.registerEventHandler("TableDataChange", (message) => seen.push(message.index));

    // The controller's list opens on Attack from Mars; this window orders the same
    // collection the other way round, so that game is its last row.
    list.rows = [...ROWS].reverse();
    await vpin.handleEvent({ type: "TableDataChange", index: 0, collection: "None",
      id: idOf(ROWS[0]) });

    assert.match(stage.children[0].src, /Attack/);
    assert.equal(vpin._currentTableIndex, 3);
    assert.deepEqual(seen, [3], "the theme reads the index into this window's list");
  });

  test("a data change that names no game opens on this window's own index", async () => {
    const { vpin, stage, list } = await started("backglass");
    await vpin.handleEvent(named(3));

    list.rows = [...ROWS].reverse();
    await vpin.handleEvent({ type: "TableDataChange", index: 0, collection: "None" });
    await vpin.getTableData();

    assert.match(stage.children[0].src, /Medieval/, "its first row, which a reload keeps");
  });

  test("a data change from the backend does not draw the game before on a held window", async () => {
    const { vpin, stage, list } = await started("backglass");
    await vpin.handleEvent(named(0));
    list.rows = ROWS.slice(0, 3);
    await vpin.getTableData();
    await vpin.handleEvent(named(3));
    assert.equal(stage.children.length, 0);

    await vpin.handleEvent({ type: "TableDataChange" });

    assert.equal(stage.children.length, 0, "the controller's game is still not in this list");
  });

  test("a list a theme pushes is not the wheel moving", async () => {
    const { vpin, stage } = await started("backglass");
    await vpin.handleEvent(named(0));

    await vpin.handleEvent({ type: "TableIndexUpdate", index: 3, list: "my-menu", kind: "table" });
    await vpin.getTableData();

    assert.match(stage.children[0].src, /Attack/);
    assert.equal(vpin._currentTableIndex, 0);
  });

  test("an answer that beats the game list still draws once the list is here", async () => {
    const { vpin, browser } = newCore({ windowName: "backglass" });
    vpin.mediaSettleMs = 0;
    let releaseTables;
    const tablesGate = new Promise((resolve) => { releaseTables = resolve; });
    vpin.call = (method) => Promise.resolve(
      method === "get_tables" ? tablesGate.then(() => JSON.stringify(ROWS)) : BRIDGE_DEFAULTS[method]);
    browser.window.receiveEvent = () => {};
    const stage = { children: [], getAttribute: () => "",
      replaceChildren(...kids) { this.children = kids; } };
    browser.document._queryAll["[data-vpin-media]"] = [stage];

    vpin.init();
    const opened = browser.WebSocket.instances.at(-1).onopen();
    await new Promise((resolve) => setTimeout(resolve, 20));
    await vpin.handleEvent(named(3));
    assert.equal(stage.children.length, 0, "no games to draw from yet");

    releaseTables();
    await opened;

    assert.match(stage.children[0].src, /Medieval/);
  });

  test("with no controller answering it draws nothing, and keeps asking more slowly", async () => {
    const { vpin, stage, sent } = await started("backglass", { quick: 60, ask: 10, slow: 40 });
    const asks = () => sent.filter((call) => call.message?.type === "SelectionRequest").length;

    await new Promise((resolve) => setTimeout(resolve, 250));
    assert.equal(stage.children.length, 0, "it never draws a game it was not told about");
    const early = asks();
    assert.ok(early >= 5, "it asked often at first");

    await new Promise((resolve) => setTimeout(resolve, 200));
    assert.ok(asks() > early, "and it has not given up");
    assert.ok(asks() - early <= 8, "but it asks less often now");

    await vpin.handleEvent(named(0));
    await new Promise((resolve) => setTimeout(resolve, 100));
    const settled = asks();
    await new Promise((resolve) => setTimeout(resolve, 100));
    assert.equal(asks(), settled, "once told, it stops");
  });

  test("an index message that names no game is taken at its index", async () => {
    const { vpin, stage } = await started("backglass");
    await vpin.handleEvent({ type: "TableIndexUpdate", index: 3 });
    assert.match(stage.children[0].src, /Medieval/);
  });

  test("the controller does not answer before its own restore has settled", async () => {
    const { sent } = await started("table", {
      target: false, index: 2, controllerAsked: "backglass" });

    const answers = sent.filter((call) => call.method === "send_event"
      && call.window === "backglass" && call.message?.type === "TableIndexUpdate");
    assert.equal(answers.length, 1, "asked once before the restore, answered once after");
    assert.equal(answers[0].message.index, 2, "with the restored game, not the first");
    assert.equal(answers[0].message.id, idOf(ROWS[2]), "and says which game that is");
  });

  test("a controller asked after its restore answers at once", async () => {
    const { vpin, sent } = await started("table", { target: false, index: 3 });

    await vpin.handleEvent({ type: "SelectionRequest", window: "backglass" });

    const answers = sent.filter((call) => call.method === "send_event"
      && call.window === "backglass" && call.message?.type === "TableIndexUpdate");
    assert.equal(answers.length, 1);
    assert.equal(answers[0].message.index, 3);
    assert.equal(answers[0].message.id, idOf(ROWS[3]));
  });

  test("a reload that drops the controller's game tells the other windows where it landed", async () => {
    const { vpin, sent, list } = await started("table", { target: false, index: 3 });
    const before = sent.length;

    list.rows = ROWS.slice(0, 3);
    await vpin.handleEvent({ type: "TableDataChange" });

    const told = sent.slice(before).filter((call) =>
      call.method === "send_event_all_windows" && call.message?.type === "TableIndexUpdate");
    assert.equal(told.length, 1);
    assert.equal(told[0].message.index, vpin._currentTableIndex);
    assert.equal(told[0].message.id, idOf(ROWS[vpin._currentTableIndex]));
    assert.equal(told[0].message.reason, "restore");
  });

  test("a reload that keeps the controller's game says nothing more", async () => {
    const { vpin, sent, list } = await started("table", { target: false, index: 3 });
    const before = sent.length;

    list.rows = [...ROWS].reverse();
    await vpin.handleEvent({ type: "TableDataChange" });

    const told = sent.slice(before).filter((call) => call.message?.type === "TableIndexUpdate");
    assert.deepEqual(told, []);
  });

  test("a game the controller names while a reload is waiting is the one the window lands on", async () => {
    const { vpin, stage } = await started("backglass");
    await vpin.handleEvent(named(3));
    const call = vpin.call;
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    vpin.call = (method, ...args) => method === "get_tables"
      ? gate.then(() => call(method, ...args)) : call(method, ...args);

    const reloading = vpin.handleEvent({ type: "TableDataChange" });
    await vpin.handleEvent(named(0));
    release();
    await reloading;

    assert.equal(vpin._currentTableIndex, 0);
    assert.match(stage.children[0].src, /Attack/);
  });

  test("a held window is marked on the document, and the mark goes when it follows a game", async () => {
    const { vpin, browser, list } = await started("backglass");
    const root = browser.document.documentElement;
    assert.equal(root.dataset.vpinfeHeld, "", "not told yet");

    await vpin.handleEvent(named(3));
    assert.equal(root.dataset.vpinfeHeld, undefined);

    list.rows = ROWS.slice(0, 3);
    await vpin.getTableData();
    await vpin.handleEvent(named(3));
    assert.equal(root.dataset.vpinfeHeld, "", "the game is not in this list");

    list.rows = ROWS;
    await vpin.getTableData();
    assert.equal(root.dataset.vpinfeHeld, undefined);
  });

  test("the mark is set whether or not the theme lets core draw the media", async () => {
    const { vpin, browser } = await started("backglass", { target: false });
    vpin.enabled = (name) => name !== "core_media_window";
    vpin.tableData = ROWS.slice(0, 3);

    await vpin.handleEvent(named(3));

    assert.equal(browser.document.documentElement.dataset.vpinfeHeld, "");
  });

  test("the controller and an empty list are never marked", async () => {
    const controller = await started("table", { target: false });
    assert.equal(controller.browser.document.documentElement.dataset.vpinfeHeld, undefined);

    const empty = await started("backglass");
    empty.vpin.tableData = [];
    await empty.vpin.handleEvent({ type: "TableIndexUpdate", index: 0, id: "x" });
    assert.equal(empty.browser.document.documentElement.dataset.vpinfeHeld, undefined);
  });

  test("an index message a theme sends names its game from the sender's list", async () => {
    const { vpin, sent } = await started("table", { target: false });

    vpin.sendMessageToAllWindows({ type: "TableIndexUpdate", index: 2 });

    const out = sent.find((call) => call.message?.type === "TableIndexUpdate");
    assert.equal(out.message.id, idOf(ROWS[2]));
  });

  test("a page with no theme script still receives events", async () => {
    // The default is only usable if a window core draws needs no script of its own, and
    // incoming events reach handleEvent through window.receiveEvent - which a page with
    // no theme.js does not define. Core pumps its own in that case.
    const { vpin, stage, browser } = await coreOn("backglass");
    const socket = vpin._ws;
    delete browser.window.receiveEvent;

    socket.onmessage({ data: JSON.stringify(
      { type: "event", message: { type: "TableIndexUpdate", index: 0 } }) });
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.equal(stage.children.length, 1, "no script on the page, and it still drew");
  });
});

describe("playfield art drawn by core is turned to fit the surface", () => {
  const AFM = ROWS.findIndex((row) => row.tableDirName === "Attack from Mars (Bally 1995)");
  const MM = ROWS.findIndex((row) => row.tableDirName === "Medieval Madness (Williams 1997)");

  /** A marked element that answers attribute reads and holds what core draws into it. */
  async function drawn(kind, attributes = {}, { layout = true, priority } = {}) {
    const themeConfig = layout ? { layout: { enabled: true } } : {};
    const { vpin, browser } = await coreOn("table", { target: false, themeConfig });
    if (priority) vpin.mediaPriorities = { ...vpin.mediaPriorities, [kind]: priority };
    const target = {
      children: [],
      getAttribute: (name) => (name === "data-vpin-media" ? kind : attributes[name] ?? null),
      replaceChildren(...kids) { this.children = kids; },
    };
    browser.document._queryAll["[data-vpin-media]"] = [target];
    return { vpin, target, show: (index) => step(vpin, index) };
  }

  test("a playfield picture is marked and set up for rotation", async () => {
    const { target, show } = await drawn("playfield", {}, { priority: "image" });
    show(AFM);

    const [node] = target.children;
    assert.ok(node.classes.has("vpinfe-playfield-media"));
    assert.ok(node.listeners.load, "it is measured once the picture knows its size");
  });

  test("a playfield video is measured when its metadata arrives", async () => {
    const { target, show } = await drawn("playfield");
    show(AFM);

    const [node] = target.children;
    assert.ok(node.classes.has("vpinfe-playfield-media"));
    assert.ok(node.listeners.loadedmetadata);
  });

  test("a theme that did not opt into layout gets an unmarked node", async () => {
    const { target, show } = await drawn("playfield", {}, { layout: false });
    show(AFM);

    assert.equal(target.children[0].classes.size, 0);
  });

  test("a backglass is never turned", async () => {
    const { target, show } = await drawn("backglass");
    show(AFM);

    assert.equal(target.children[0].classes.size, 0);
  });

  test("the user's priority decides between the still and the video", async () => {
    const image = await drawn("playfield", {}, { priority: "image" });
    image.show(AFM);
    const video = await drawn("playfield", {}, { priority: "video" });
    video.show(AFM);

    assert.ok(image.target.children[0].src.endsWith(".png"));
    assert.ok(video.target.children[0].src.endsWith(".mp4"));
  });

  test("an element can ask for the still whatever the priority is", async () => {
    const { target, show } = await drawn("playfield",
      { "data-vpin-media-prefer": "image" }, { priority: "video" });
    show(AFM);

    assert.ok(target.children[0].src.endsWith(".png"));
  });

  test("an element can ask for the video whatever the priority is", async () => {
    const { target, show } = await drawn("playfield",
      { "data-vpin-media-prefer": "video" }, { priority: "image" });
    show(AFM);

    assert.ok(target.children[0].src.endsWith(".mp4"));
  });

  test("a preference for what a game lacks falls back to what it has", async () => {
    const { target, show } = await drawn("backglass", { "data-vpin-media-prefer": "video" });
    show(MM);

    assert.ok(target.children[0]?.src.endsWith(".png"), "no backglass video, so the still");
  });

  test("the same game again keeps its node", async () => {
    const { target, show } = await drawn("playfield");
    show(AFM);
    const [first] = target.children;
    show(AFM);

    assert.equal(target.children[0], first, "a playing video is not restarted");
  });

  test("a reloaded library draws it again", async () => {
    const { vpin, target, show } = await drawn("playfield");
    show(AFM);
    const [first] = target.children;
    vpin.tableData = ROWS.map((row) => ({ ...row }));
    show(AFM);

    assert.notEqual(target.children[0], first, "a picture replaced under its URL is fetched anew");
  });

  test("another game replaces it", async () => {
    const { target, show } = await drawn("playfield", {}, { priority: "image" });
    show(AFM);
    const [first] = target.children;
    show(ROWS.findIndex((row) => row.tableDirName !== ROWS[AFM].tableDirName
      && row.playfieldImagePath));

    assert.notEqual(target.children[0], first);
  });
});

describe("dimming every window while VPX launches", () => {
  test("it is set on the document, not left to the theme", async () => {
    // A theme's script runs in the controller only, so a theme doing this for itself
    // dims one screen and leaves the others lit - which is what the cabinet showed.
    const { vpin, browser } = await coreOn("backglass");
    await vpin.handleEvent({ type: "TableLaunching" });
    assert.equal(browser.document.documentElement.dataset.vpinfeLaunching, "true");

    await vpin.handleEvent({ type: "TableLaunchComplete" });
    assert.equal(browser.document.documentElement.dataset.vpinfeLaunching, undefined);
  });

  test("a theme can turn it off", async () => {
    const { vpin, browser } = await coreOn("backglass",
      { themeConfig: { launch_dim: { enabled: false } } });
    await vpin.handleEvent({ type: "TableLaunching" });
    assert.equal(browser.document.documentElement.dataset.vpinfeLaunching, undefined);
  });
});

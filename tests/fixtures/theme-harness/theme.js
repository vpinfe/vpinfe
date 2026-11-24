// The theme the render smoke test drives.
//
// Deliberately not a published theme: those move, and a test that moves with them stops
// being a test. It uses the surface a real theme uses - init, ready, entries, media by
// kind, the input handler, receiveEvent - because the point is to exercise that surface
// in a real browser rather than to be minimal for its own sake.
//
// Everything the test reads is a data-* attribute. Nothing asserts on a class, a
// position or the text of an element, so restyling this cannot quietly break the suite.

const vpin = new VPinFECore();
window.vpin = vpin;
window.receiveEvent = receiveEvent;

let selected = 0;
let failures = [];

start();

async function start() {
  try {
    vpin.init();
    await vpin.ready;

    document.body.dataset.window = vpin.windowName;
    if (vpin.isController()) {
      vpin.registerInputHandler(handleInput);
      selected = vpin.getCurrentTableIndex();
    }
    render();
    document.body.dataset.ready = "true";
  } catch (error) {
    // A theme that throws during startup renders nothing, which is indistinguishable
    // from a blank screen. Say which it was.
    fail(`start: ${error && error.message}`);
  }
}

async function receiveEvent(message) {
  await vpin.handleEvent(message);
  if (message.type === "TableIndexUpdate" && typeof message.index === "number") {
    selected = message.index;
  }
  render();
}

function handleInput(action) {
  const count = vpin.getTableCount();
  if (!count) return;
  if (action === "next") selected = (selected + 1) % count;
  else if (action === "previous") selected = (selected - 1 + count) % count;
  else return;
  vpin.selectGame(selected);
  render();
}

function fail(what) {
  failures.push(what);
  document.body.dataset.failures = failures.join(" | ");
}

function render() {
  const count = vpin.getTableCount();
  const root = document.getElementById("wheel");
  root.replaceChildren();

  for (let i = 0; i < count; i++) {
    const entry = vpin.getTableMeta(i);
    const el = document.createElement("div");
    el.dataset.entry = String(i);
    el.dataset.title = (entry && entry.game && entry.game.title) || "";
    if (i === selected) el.dataset.selected = "true";

    // Media by kind, which is what contract 2 serves. An entry that declares a kind
    // must produce a URL for it - a null here is the resolution break that reached the
    // cabinet twice, and it is invisible without a browser.
    const kinds = (entry && entry.media) || [];
    for (const kind of ["wheel", "playfield"]) {
      if (!kinds.includes(kind)) continue;
      const url = vpin.getMediaURL(i, kind);
      if (!url) {
        fail(`entry ${i} declares ${kind} and resolved no url`);
        continue;
      }
      const img = document.createElement("img");
      img.dataset.kind = kind;
      img.addEventListener("error", () => fail(`entry ${i} ${kind} failed to load`));
      img.src = url;
      el.appendChild(img);
    }
    root.appendChild(el);
  }

  document.body.dataset.rendered = String(count);
  document.body.dataset.selected = String(selected);
  document.body.dataset.collection = vpin.collection || "";
}

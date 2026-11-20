import { readFileSync } from "node:fs";
import { after } from "node:test";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

// The browser surface vpinfe-core.js needs, and nothing more.
//
// The file touches five globals - window, document, WebSocket, fetch, navigator - so a
// stub is small enough to read in one sitting. Anything a test does not exercise throws
// rather than returning undefined, because a silent undefined is how a test passes while
// the real thing is broken.

// What `_serve_core_words` sends a page: the namespaces in `CORE_WORDS`. Read from the
// real catalog so a test asserting on a word is asserting on the word that ships, not on
// one written twice.
// Every timer the stubbed browser hands out, so the file can end even if core left one
// re-arming. Registered once: `after` at module scope runs when the test file is done.
const timers = new Set();
const intervals = new Set();

after(() => {
  for (const timer of timers) clearTimeout(timer);
  for (const timer of intervals) clearInterval(timer);
  timers.clear();
  intervals.clear();
});


function coreWords() {
  const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
  const all = JSON.parse(readFileSync(join(root, "common/i18n/catalogs/en.json"), "utf8"));
  return Object.fromEntries(Object.entries(all).filter(
    ([key]) => ["frontend.", "word.", "input."].some((space) => key.startsWith(space))));
}

function unimplemented(name) {
  return () => {
    throw new Error(`${name} is not stubbed; add it to tests/js/support/browser.js`);
  };
}

// Records what was sent so a test can assert on it, and answers calls with whatever the
// test queued up. No timers, no reconnect: the socket is a spy, not a simulation.
class FakeWebSocket {
  constructor(url) {
    this.url = url;
    this.sent = [];
    this.readyState = FakeWebSocket.OPEN;
    FakeWebSocket.instances.push(this);
  }

  send(raw) {
    this.sent.push(JSON.parse(raw));
  }

  close() {
    this.readyState = FakeWebSocket.CLOSED;
  }

  // Deliver a message as the bridge would.
  receive(message) {
    if (this.onmessage) this.onmessage({ data: JSON.stringify(message) });
  }
}
FakeWebSocket.OPEN = 1;
FakeWebSocket.CLOSED = 3;
FakeWebSocket.instances = [];


function FakeEventSource(url) {
  const listeners = {};
  const source = {
    url,
    addEventListener(name, fn) { (listeners[name] || (listeners[name] = [])).push(fn); },
    removeEventListener() {},
    close() {},
    /** Deliver one event, the way the server would. */
    emit(name, payload) {
      for (const fn of listeners[name] || []) fn({ data: JSON.stringify(payload) });
    },
  };
  FakeEventSource.instances.push(source);
  return source;
}

FakeEventSource.instances = [];

// Records every src it was given. Preloading is judged by how many requests it makes,
// so the count is the assertion.
class FakeImage {
  constructor() {
    this.decoding = "";
    this._src = "";
  }
  set src(value) { this._src = value; FakeImage.requested.push(value); }
  get src() { return this._src; }
}
FakeImage.requested = [];

class FakeAudio {
  constructor(src = "") {
    this.loop = false;
    this.muted = false;
    this.volume = 1;
    this.src = src;
    this.paused = true;
  }
  play() { this.paused = false; return Promise.resolve(); }
  pause() { this.paused = true; }
  addEventListener() {}
  removeEventListener() {}
}

// `search` is what #detectWindowName reads first, so a test picks its window by URL the
// same way a real window does.
export function makeBrowser({ windowName = "table", search = null, pathname = "/",
                              innerWidth = 1920, innerHeight = 1080 } = {}) {
  const query = search === null ? `?window=${windowName}` : search;

  // Just enough DOM for the overlays: an element that can hold a class and children,
  // and iframes that record what they were posted. Not a browser - a place for the
  // overlay logic to leave evidence.
  function element(id = "") {
    const el = {
      id,
      src: "",
      style: {},
      children: [],
      classes: new Set(),
      posted: [],
      attributes: {},
      classList: {
        add: (name) => el.classes.add(name),
        remove: (name) => el.classes.delete(name),
        contains: (name) => el.classes.has(name),
      },
      className: "",
      textContent: "",
      setAttribute(name, value) { el.attributes[name] = value; },
      canPlayType() { return ""; },
      appendChild(child) { el.children.push(child); documentStub._byId[child.id] = child; },
      remove() {
        const parent = documentStub.body.children.includes(el)
          ? documentStub.body : null;
        if (parent) parent.children.splice(parent.children.indexOf(el), 1);
      },
      // An overlay is an iframe, and core fills its words when the frame loads. Nothing
      // here fires a load event, so the listener is recorded and never called - which is
      // the same path a frame served from cache takes.
      listeners: {},
      addEventListener(name, handler) { (el.listeners[name] ||= []).push(handler); },
      removeEventListener(name, handler) {
        el.listeners[name] = (el.listeners[name] || []).filter((one) => one !== handler);
      },
      contentDocument: null,
      contentWindow: { postMessage: (message) => el.posted.push(message) },
    };
    return el;
  }

  const rootStub = {
    dataset: {},
    style: { _props: {}, setProperty(name, value) { rootStub.style._props[name] = value; } },
    // Core mounts what must outlive the body's fade here. The stub keeps one list of
    // everything added to the page, so remove() and a test's count both find it.
    appendChild(child) { documentStub.body.children.push(child); },
  };

  const documentStub = {
    documentElement: rootStub,
    title: "",
    addEventListener() {},
    removeEventListener() {},
    // Core looks for the element it renders a display window's media into. A test that
    // wants that path puts one in `_query`; everything else has no target and core does
    // nothing, which is the real behavior for a page that never opted in.
    _query: {},
    querySelector(selector) { return documentStub._query[selector] || null; },
    // Core fills every [data-i18n] element from the catalog on init. A page with none
    // is the ordinary case here, and a test that wants them puts them in `_queryAll`.
    _queryAll: {},
    querySelectorAll(selector) { return documentStub._queryAll[selector] || []; },
    readyState: "complete",
    _byId: { "overlay-root": element("overlay-root") },
    getElementById(id) { return documentStub._byId[id] || null; },
    createElement() { return element(); },
    createElementNS() { return element(); },
    // Real enough to see what core put on the page. Core's confirm dialog is not
    // reachable any other way - it is built and removed inside one call - so a stub
    // that threw would leave it untestable.
    body: { children: [], appendChild(child) { documentStub.body.children.push(child); } },
  };

  const windowStub = {
    name: "",
    // A real window has a size, and #resolveLayout measures it for any window the ini
    // does not describe. Left undefined, `h > w` is false and every test would read
    // "landscape" whether or not the code worked.
    innerWidth,
    innerHeight,
    // `protocol` is read: the remote-launch stream is skipped on file://, where CORS
    // blocks it. Without it here the guard compares against undefined and passes by luck.
    location: { search: query, pathname, protocol: "http:",
                href: `http://127.0.0.1:8000${pathname}${query}` },
    addEventListener() {},
    removeEventListener() {},
    postMessage() {},
    setTimeout: (fn, ms) => setTimeout(fn, ms),
    clearTimeout: (id) => clearTimeout(id),
    requestAnimationFrame: (fn) => setTimeout(() => fn(Date.now()), 0),
  };

  FakeWebSocket.instances = [];
  FakeEventSource.instances = [];
  FakeImage.requested = [];

  // The next animation frame the code under test asked for, and a way to run it. One
  // slot, because the only caller that re-arms is the gamepad poll.
  const frames = {
    pending: null,
    step() {
      const fn = frames.pending;
      frames.pending = null;
      if (fn) fn();
      return !!fn;
    },
  };

  return {
    frames,
    window: windowStub,
    document: documentStub,
    navigator: { getGamepads: () => [] },
    WebSocket: FakeWebSocket,
    Audio: FakeAudio,
    Image: FakeImage,
    // Core asks for /core/i18n.json on init. An empty catalog is the honest answer
    // here: every [data-i18n] element keeps the English between its tags and every
    // runtime call passes its own, so a page with no catalog reads correctly. A test
    // that wants words can replace this.
    fetch: async (url) => (String(url).endsWith("/core/i18n.json")
      ? { ok: true, json: async () => coreWords() }
      : unimplemented(`fetch(${url})`)()),
    // The remote-launch stream. This used to swallow listeners, which is why nothing
    // could reach the handler behind it - and the handler was reading a field the
    // payload does not have. Keep it drivable.
    EventSource: FakeEventSource,
    URLSearchParams,
    // The catalog fetch carries a timeout signal.
    AbortSignal,
    Promise,
    Map,
    Set,
    Date,
    Math,
    JSON,
    Object,
    Array,
    String,
    Number,
    Boolean,
    Error,
    // Tracked and cleared when the file ends, never unreferenced: an unreferenced timer
    // cannot resolve a promise that is awaiting it, and core awaits one.
    setTimeout: (fn, ms, ...rest) => {
      const timer = setTimeout(fn, ms, ...rest);
      timers.add(timer);
      return timer;
    },
    clearTimeout: (timer) => { timers.delete(timer); return clearTimeout(timer); },
    // Held rather than scheduled: the gamepad poll re-arms itself every frame, so a real
    // frame would spin a test forever. Keeping the callback lets a test that cares about
    // a held button step the poll itself - `frames.step()` - which is the only way to
    // produce a press edge and then a release edge from here.
    requestAnimationFrame: (fn) => { frames.pending = fn; return frames.pending ? 1 : 0; },
    cancelAnimationFrame: () => {},
    setInterval: (fn, ms, ...rest) => {
      const timer = setInterval(fn, ms, ...rest);
      intervals.add(timer);
      return timer;
    },
    clearInterval: (timer) => { intervals.delete(timer); return clearInterval(timer); },
    console,
  };
}

export { FakeWebSocket, FakeAudio, FakeImage };

/**
 * What a browser sends as `event.code` for a single character.
 *
 * A keyboard event carries both: `key` is what the key produced ("q"), `code` is which
 * key it is ("KeyQ"). A test that set the code to the key sent something no keyboard
 * sends, which went unnoticed while the shipped bindings were spelled as keys - and
 * became a test that waits forever the moment they were spelled as codes.
 *
 * Anything longer than one character is already a code - "Escape", "ArrowLeft".
 */
export function codeFor(key) {
  if (typeof key !== "string" || key.length !== 1) return key;
  if (/[a-z]/i.test(key)) return "Key" + key.toUpperCase();
  if (/[0-9]/.test(key)) return "Digit" + key;
  return key;
}

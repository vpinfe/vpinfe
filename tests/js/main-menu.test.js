// What the main menu's metadata build says when it stops.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import vm from "node:vm";

import { REPO_ROOT } from "./support/load-core.js";

const MENU = path.join(REPO_ROOT, "frontend", "static", "mainmenu", "mainmenu.js");
const WORDS = JSON.parse(readFileSync(
  path.join(REPO_ROOT, "common", "i18n", "catalogs", "en.json"), "utf8"));

function menuPage() {
  const elements = {};
  const element = (id) => (elements[id] ||= { id, textContent: "", style: {} });
  const window = {
    __vpinWords: WORDS,
    parent: { vpin: { registerOverlayHandler() {} } },
    addEventListener() {},
  };
  const context = vm.createContext({ window, document: { getElementById: element } });
  vm.runInContext(readFileSync(MENU, "utf8"), context, { filename: MENU });
  return { window, element };
}

test("a build that stops says the reason it was given, and nothing before it", () => {
  const { window, element } = menuPage();

  window.receiveEvent({ type: "buildmeta_error",
                        error: WORDS["frontend.buildmeta.library_busy"] });

  assert.equal(element("progress-text").textContent,
               "The library is busy - try again when it finishes");
});

function managerPanelPage(link) {
  const elements = {};
  const element = (id) => (elements[id] ||= {
    id, textContent: "", innerHTML: "", hidden: true, style: {},
    replaceChildren(...children) { this.children = children; },
  });
  const window = {
    __vpinWords: WORDS,
    parent: { vpin: { registerOverlayHandler() {}, call: async () => link } },
    addEventListener() {},
  };
  const document = {
    getElementById: element,
    createElement: () => ({ textContent: "" }),
  };
  const context = vm.createContext({ window, document });
  vm.runInContext(readFileSync(MENU, "utf8"), context, { filename: MENU });
  return { context, element };
}

test("the Manager panel shows its QR and the addresses with their scheme and without loopback", async () => {
  const { context, element } = managerPanelPage({
    url: "http://cab:8001/manager",
    urls: ["http://cab:8001/manager", "https://192.168.1.20:8001/manager",
           "http://localhost:8001/manager"],
    qr_svg: "<svg></svg>",
  });

  await context.loadManagerQrPanel();

  assert.equal(element("manager-qr-code").innerHTML, "<svg></svg>");
  assert.deepEqual(element("manager-address").children.map((line) => line.textContent),
                   ["http://cab:8001/manager", "https://192.168.1.20:8001/manager"]);
  assert.equal(element("manager-qr-panel").hidden, false);
  assert.equal(element("menu-qr-panels").hidden, false);
});

test("an install that answers only itself leaves the Manager panel hidden", async () => {
  const { context, element } = managerPanelPage({ url: "", urls: [], qr_svg: "" });

  await context.loadManagerQrPanel();

  assert.equal(element("manager-qr-panel").hidden, true);
  assert.equal(element("menu-qr-panels").hidden, true);
});

function resolvingPage(answers) {
  const elements = {};
  const element = (id) => (elements[id] ||= {
    id, textContent: "", innerHTML: "", hidden: true, style: {},
    replaceChildren(...children) { this.children = children; },
  });
  const later = [];
  const window = {
    __vpinWords: WORDS,
    parent: { vpin: { registerOverlayHandler() {}, call: async () => answers.shift() } },
    addEventListener() {},
  };
  const document = { getElementById: element, createElement: () => ({ textContent: "" }) };
  const context = vm.createContext({
    window, document, setTimeout: (callback) => later.push(callback),
  });
  vm.runInContext(readFileSync(MENU, "utf8"), context, { filename: MENU });
  return { context, element, later };
}

const BEFORE = { urls: ["http://192.168.1.20:8001/manager"], qr_svg: "<svg>ip</svg>",
                 resolving: true };
const AFTER = { urls: ["http://cab.local:8001/manager", "http://192.168.1.20:8001/manager"],
                qr_svg: "<svg>ip</svg>", resolving: false };

test("the Manager panel opens on the address and swaps the name in when it answers", async () => {
  const { context, element, later } = resolvingPage([BEFORE, AFTER]);

  await context.loadManagerQrPanel();
  assert.deepEqual(element("manager-address").children.map((line) => line.textContent),
                   ["http://192.168.1.20:8001/manager"]);
  assert.equal(later.length, 1);

  await later.shift()();

  assert.deepEqual(element("manager-address").children.map((line) => line.textContent),
                   ["http://cab.local:8001/manager", "http://192.168.1.20:8001/manager"]);
  assert.equal(element("manager-qr-code").innerHTML, "<svg>ip</svg>");
  assert.equal(later.length, 0);
});

test("a panel whose name already answered is not asked again", async () => {
  const { context, later } = resolvingPage([AFTER]);

  await context.loadManagerQrPanel();

  assert.equal(later.length, 0);
});

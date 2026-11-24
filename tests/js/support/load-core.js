// Loads frontend/static/common/vpinfe-core.js into a fresh context and hands back the class.
//
// The file is a plain script that declares VPinFECore and instantiates nothing - the
// theme does `new VPinFECore()`. That is what makes it loadable here at all, so it is
// worth not breaking: if the file ever instantiates itself, this harness stops working.
//
// A class declaration at script top level is a lexical binding, not a property of the
// global, so the source gets one appended line to hand it out.

import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { readFileSync } from "node:fs";
import path from "node:path";
import vm from "node:vm";

import { makeBrowser } from "./browser.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const REPO_ROOT = path.resolve(HERE, "..", "..", "..");
const CORE_PATH = path.join(REPO_ROOT, "frontend", "static", "common", "vpinfe-core.js");

// Module-private declarations are lexical bindings, not properties of the global, so the
// source gets an appended line handing out the ones the tests need. Kept to a short list
// on purpose: a test reaching for an internal is usually a test asserting the wrong
// thing, and this is the list of exceptions.
const EXPOSE = [
  "VPinFECore",
  "NavigableList",
  "canonicalMessageType",
  "parseChordBinding",
  "buildBindingMaps",
  "downToken",
  "MESSAGE_TYPE_ALIASES",
  "MEDIA_PATH_FIELDS",
  "MEDIA_VIDEO_PATH_FIELDS",
  "VPINFE_RENAMED_MEMBERS",
  "INTERNAL_METHODS",
];

const SOURCE = readFileSync(CORE_PATH, "utf8")
  + "\n" + EXPOSE.map((name) => `globalThis.${name} = ${name};`).join("\n") + "\n";

/**
 * A core instance in its own context, plus the stubs it was built against.
 *
 * Anything the core creates - arrays, objects, messages - is built with the vm's own
 * Object.prototype, so `assert.deepEqual` from node:assert/strict reports "same
 * structure but not reference-equal". Copy it first (`[...value]`) or assert on fields.
 */
export function loadCore(options = {}) {
  const browser = makeBrowser(options);
  const context = vm.createContext({ ...browser, globalThis: undefined });
  // vm needs globalThis to be the context itself, which createContext arranges once the
  // object is contextified - assigning it beforehand would shadow it.
  delete context.globalThis;
  vm.runInContext(SOURCE, context, { filename: CORE_PATH });
  return { VPinFECore: context.VPinFECore, context, browser };
}

/** A value the core built, as plain objects of this realm, ready for `assert.deepEqual`. */
export function plain(value) {
  return value === undefined ? value : JSON.parse(JSON.stringify(value));
}

/** The common case: a constructed core for one window. */
export function newCore(options = {}) {
  const { VPinFECore, context, browser } = loadCore(options);
  return { vpin: new VPinFECore(), context, browser };
}

export function fixture(name) {
  const require = createRequire(import.meta.url);
  return require(path.join(REPO_ROOT, "tests", "fixtures", name));
}

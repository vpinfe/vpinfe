// Core's words as a cabinet page reads them, off the real catalog.

import assert from "node:assert/strict";
import { test } from "node:test";

import { newCore } from "./support/load-core.js";

async function withWords() {
  const { vpin } = newCore();
  vpin.init();
  await new Promise((resolve) => setTimeout(resolve, 0));
  return vpin;
}

test("a count picks its form", async () => {
  const vpin = await withWords();

  assert.equal(vpin.t("frontend.collectionmenu.games", "", { count: 1 }), "1 game");
  assert.equal(vpin.t("frontend.collectionmenu.games", "", { count: 0 }), "0 games");
  assert.equal(vpin.t("frontend.collectionmenu.games", "", { count: 12 }), "12 games");
});

test("a plain entry is unchanged by a count", async () => {
  const vpin = await withWords();

  assert.equal(vpin.t("frontend.collectionmenu.pages_time", "", { size: 5, count: 1 }),
               "Pages 5 at a time");
});

test("an English fallback picks its form by count too", async () => {
  const vpin = await withWords();
  const english = { one: "{count} wheel", other: "{count} wheels" };

  assert.equal(vpin.t("theme.not.in.the.catalog", english, { count: 1 }), "1 wheel");
  assert.equal(vpin.t("theme.not.in.the.catalog", english, { count: 3 }), "3 wheels");
});

test("an entry marks which values are numbers", async () => {
  const vpin = await withWords();

  assert.equal(vpin.t("theme.not.in.the.catalog", "{count:n} files", { count: 1234567 }),
               "1,234,567 files");
  assert.equal(vpin.t("theme.not.in.the.catalog", "{size:n} MB", { size: 1.5 }), "1.5 MB");
  assert.equal(vpin.t("theme.not.in.the.catalog", "{count:n} files", { count: "many" }),
               "many files");
  assert.equal(vpin.t("theme.not.in.the.catalog", "{count} files", { count: 1234567 }),
               "1234567 files");
});

test("a count in the catalog is grouped", async () => {
  const vpin = await withWords();

  assert.equal(vpin.t("frontend.collectionmenu.games", "", { count: 1234 }), "1,234 games");
});

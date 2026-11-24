// Where the page thinks the services are.
//
// Six places hardcoded 127.0.0.1 and read a port off the core, so "which machine answers
// this" was asserted six times and a seventh was easy to add. The block answers it once,
// keyed by what answers - the library and this machine, not by transport, because the
// transport serving both and a transport-keyed block cannot express the split.
//
// Ports arrive in the url because the page cannot ask for them: asking needs the bridge,
// and finding the bridge needs a port.

import { test, describe } from "node:test";
import assert from "node:assert/strict";

import { newCore, fixture } from "./support/load-core.js";

const ROWS = fixture("theme_payload.json").contract1;

const withPorts = (search) => newCore({ windowName: "table", search }).vpin;

describe("the endpoint block", () => {
  test("is resolved from the url before any theme code runs", () => {
    const vpin = withPorts("?window=table&wsPort=9002&themeAssetsPort=9000&libraryPort=9001");

    assert.equal(vpin.endpoints.files, "http://127.0.0.1:9000");
    assert.equal(vpin.endpoints.device, "http://127.0.0.1:9001");
    assert.equal(vpin.endpoints.library, "http://127.0.0.1:9001", "one api answers for both today");
    assert.equal(vpin.endpoints.frontend_channel, "ws://127.0.0.1:9002");
  });

  test("falls back to what the frontend has always assumed", () => {
    // A page opened by hand, or an older launcher that sends no ports.
    const vpin = withPorts("?window=table");

    assert.equal(vpin.endpoints.files, "http://127.0.0.1:8001");
    assert.equal(vpin.endpoints.device, "http://127.0.0.1:8001");
    assert.equal(vpin.endpoints.frontend_channel, "ws://127.0.0.1:8002");
  });

  test("a port given in the url wins over the assumed one", () => {
    const vpin = withPorts("?window=table&themeAssetsPort=9000");

    assert.equal(vpin.endpoints.files, "http://127.0.0.1:9000");
    assert.equal(vpin.endpoints.device, "http://127.0.0.1:8001", "the rest are untouched");
  });

  test("each key points where its own setting says", () => {
    // The keys are not interchangeable: media comes off the file server and the api
    // off the manager ui port. Pointing the library at the file server was the original bug.
    const vpin = withPorts("?window=table&wsPort=9002&themeAssetsPort=9000&libraryPort=9001");

    assert.equal(vpin.endpoints.files, `http://127.0.0.1:${vpin.themeAssetsPort}`);
    assert.equal(vpin.endpoints.library, `http://127.0.0.1:${vpin.libraryPort}`);
    assert.equal(vpin.endpoints.device, `http://127.0.0.1:${vpin.devicePort}`);
    assert.equal(vpin.endpoints.frontend_channel, `ws://127.0.0.1:${vpin.wsPort}`);
  });

  test("a library on another machine moves its services and nothing else", () => {
    // `libraryPort` is the other install's, so `device` cannot read it: this machine's
    // own api is on devicePort, at loopback, whoever holds the library.
    const vpin = withPorts(
      "?window=table&wsPort=8002&themeAssetsPort=8001&libraryPort=9000" +
      "&libraryHost=library.example&devicePort=8001");

    assert.equal(vpin.endpoints.library, "http://library.example:9000");
    assert.equal(vpin.endpoints.files, "http://127.0.0.1:8001/library",
      "art comes through this device, which is signed in to the library and a page is not");
    assert.equal(vpin.endpoints.device, "http://127.0.0.1:8001", "this machine, always");
    assert.equal(vpin.endpoints.frontend_channel, "ws://127.0.0.1:8002", "this machine, always");
  });

  test("no libraryHost is every single-machine install, unchanged", () => {
    const vpin = withPorts("?window=table&wsPort=8002&themeAssetsPort=8001&libraryPort=8001");

    assert.equal(vpin.endpoints.library, "http://127.0.0.1:8001");
    assert.equal(vpin.endpoints.files, "http://127.0.0.1:8001");
    assert.equal(vpin.endpoints.device, "http://127.0.0.1:8001");
  });

  test("the frontend channel is a line to hold open, not an address to append to", () => {
    const vpin = withPorts("?window=table&wsPort=9002");

    assert.ok(vpin.endpoints.frontend_channel.startsWith("ws://"), "not http");
    assert.ok(!vpin.endpoints.frontend_channel.endsWith("/"), "no path is appended to it");
  });

  test("correcting a port after startup corrects the urls built from it", () => {
    // The bridge answers get_theme_assets_port during init and may correct a page that
    // opened without one. Resolved once at construction, that correction would land on
    // the port and never reach the urls, which fails silently on the default port.
    const vpin = withPorts("?window=table");

    vpin.themeAssetsPort = 9000;

    assert.equal(vpin.endpoints.files, "http://127.0.0.1:9000");
  });
});

describe("every url the page builds comes from the block", () => {
  test("game media resolves against the file server", () => {
    const vpin = withPorts("?window=table&themeAssetsPort=9000");
    vpin.tableData = ROWS;
    const index = ROWS.findIndex((row) => row.tableDirName === "Attack from Mars (Bally 1995)");

    const url = vpin.getImageURL(index, "wheel");

    assert.ok(url.startsWith("http://127.0.0.1:9000/"), url);
  });

  test("the manufacturer logo resolves against the file server", () => {
    const vpin = withPorts("?window=table&themeAssetsPort=9000");
    vpin.tableData = [{}];
    vpin._reader = { logo: () => "/manufacturers/bally/logo" };

    assert.equal(vpin.getManufacturerLogoURL(0),
                 "http://127.0.0.1:9000/manufacturers/bally/logo");
  });

  test("game media of a shared library resolves against this device", () => {
    const vpin = withPorts(
      "?window=table&themeAssetsPort=8001&libraryPort=9000" +
      "&libraryHost=library.example&devicePort=8001");
    vpin.tableData = [{}];
    vpin._reader = { logo: () => "/manufacturers/bally/logo" };

    assert.equal(vpin.getManufacturerLogoURL(0),
                 "http://127.0.0.1:8001/library/manufacturers/bally/logo");
  });

  test("the frontend channel is dialled at its own endpoint", () => {
    const { vpin, browser } = newCore({
      windowName: "table", search: "?window=table&wsPort=9002",
    });

    vpin.init();

    assert.equal(browser.WebSocket.instances.at(-1).url,
                 "ws://127.0.0.1:9002?window=table");
  });
});

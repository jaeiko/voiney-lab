// Lane APP1, decision 5: the bundled screen's server address, written at
// build time into www/app-config.js (mobile/scripts/app-config.mjs).
// Run with `node --test tests/native_bridge/*.test.js`.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const load = () => import(pathToFileURL(path.join(__dirname, "..", "..", "mobile", "scripts", "app-config.mjs")).href);

test("the server address is https (http only on this computer), without a path or credentials", async () => {
  const { appServerUrl } = await load();
  assert.equal(appServerUrl("https://pilot.example/"), "https://pilot.example");
  assert.equal(appServerUrl(" https://Pilot.Example:8443/x "), "https://pilot.example:8443");
  assert.equal(appServerUrl("http://127.0.0.1:8000"), "http://127.0.0.1:8000");
  assert.equal(appServerUrl(""), null);
  assert.equal(appServerUrl(undefined), null);
  for (const bad of ["http://pilot.example", "pilot.example", "https://u:p@pilot.example", "ftp://pilot.example"]) {
    assert.throws(() => appServerUrl(bad), /VOINEY_APP_API_URL/);
  }
});

test("the written file sets only the public address", async () => {
  const { appConfigSource } = await load();
  const source = appConfigSource({ VOINEY_APP_API_URL: "https://pilot.example", SECRET: "x" });
  const window = {};
  new Function("window", source)(window);
  assert.deepEqual(window.VOINEY_APP_CONFIG, { serverUrl: "https://pilot.example" });
  assert.ok(Object.isFrozen(window.VOINEY_APP_CONFIG));
  const empty = {};
  new Function("window", appConfigSource({}))(empty);
  assert.deepEqual(empty.VOINEY_APP_CONFIG, { serverUrl: null });
});

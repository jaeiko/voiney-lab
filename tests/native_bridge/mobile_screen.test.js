"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { readFileSync } = require("node:fs");
const { runInNewContext } = require("node:vm");
const ROOT = path.resolve(__dirname, "../..");
const configSource = readFileSync(path.join(ROOT, "mobile/capacitor.config.js"), "utf8");
function config(env = {}) {
  const module = { exports: {} };
  runInNewContext(configSource, { URL, process: { env }, module });
  return module.exports;
}
test("both app loading modes open the new screen, and a device-check override stays explicit", () => {
  assert.equal(config().server.appStartPath, "app/index.html");
  assert.equal(config({ VOINEY_APP_SERVER_URL: "https://pilot.example/old?x=1" }).server.url, "https://pilot.example/app/");
  assert.equal(config({ VOINEY_APP_START_PATH: "device-check.html" }).server.appStartPath, "device-check.html");
  assert.equal(config().appName, "Voiney Lab");
  assert.equal(config().plugins.LocalNotifications.smallIcon, "ic_stat_voiney");
});
test("the actual packager includes /app and the app bridge before the screen runs", async () => {
  const { prepareWeb } = await import("../../mobile/scripts/prepare-web.mjs");
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "voiney-app-"));
  try {
    const source = path.join(dir, "static");
    const www = path.join(dir, "www");
    await fs.mkdir(path.join(source, "app"), { recursive: true });
    await fs.writeFile(path.join(source, "index.html"), "<head></head>old screen");
    await fs.writeFile(path.join(source, "app/index.html"), '<head><script type="module" src="/app/assets/screen.js"></script></head><div id="root"></div>');
    await prepareWeb({ staticDir: source, www, env: { VOINEY_APP_API_URL: "https://pilot.example" } });
    const html = await fs.readFile(path.join(www, "app/index.html"), "utf8");
    for (const script of ["/capacitor.js", "/app-config.js", "/native-bridge.js", "/app-bridge.js"]) assert.ok(html.includes(`src="${script}"`), script);
    assert.ok(await fs.readFile(path.join(www, "app-bridge.js")));
    assert.match(await fs.readFile(path.join(www, "app-config.js"), "utf8"), /https:\/\/pilot.example/);
    await fs.rm(path.join(source, "app"), { recursive: true });
    await assert.rejects(() => prepareWeb({ staticDir: source, www }), /new screen/);
  } finally { await fs.rm(dir, { recursive: true, force: true }); }
});

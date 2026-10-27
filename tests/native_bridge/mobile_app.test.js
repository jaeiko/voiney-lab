"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { webcrypto } = require("node:crypto");
const { install, createAppSession } = require("../../mobile/www-src/app-bridge.js");
const core = require("../../src/voiney_lab/static/native-bridge.js");
const BASE = "https://pilot.example";
const TOKEN = "t".repeat(43);
function storage() {
  const values = new Map();
  return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
}
function capacitor() {
  let token = null;
  return {
    Plugins: { VoineySessionStore: {} }, isNativePlatform: () => true,
    isPluginAvailable: name => name === "VoineySessionStore",
    nativePromise: async (plugin, method, opts) => {
      assert.equal(plugin, "VoineySessionStore");
      if (method === "set") token = opts.token;
      if (method === "clear") token = null;
      return { token };
    },
  };
}
class Socket extends EventTarget {
  static OPEN = 1;
  constructor(url) { super(); this.url = url; this.sent = []; }
  send(value) { this.sent.push(value); }
}
function windowFor({ saved = storage(), cap = capacitor(), fetch } = {}) {
  return { Capacitor: cap, VoineyNativeBridge: core, VOINEY_APP_CONFIG: { serverUrl: BASE },
    localStorage: saved, crypto: webcrypto, location: { href: "https://localhost/app/", origin: "https://localhost" },
    fetch, WebSocket: Socket };
}
test("the installed app sends one persistent random device on login, password and erasure", async () => {
  const calls = [];
  const fetch = async (url, init) => {
    calls.push({ url, init });
    return new Response(JSON.stringify(url.endsWith("/login") ? { app_session: { token: TOKEN } } : {}));
  };
  const saved = storage();
  const cap = capacitor();
  const root = windowFor({ saved, cap, fetch });
  await install(root);
  await root.fetch("/api/auth/login", { method: "POST", body: JSON.stringify({ name: "synthetic", password: "test-password" }) });
  // A fresh page/session on this installation keeps the same marker and token.
  const again = windowFor({ saved, cap, fetch });
  await install(again);
  await again.fetch("/api/auth/password", { method: "POST", body: JSON.stringify({ current: "test-password", new: "next-password", device: "untrusted-override" }) });
  await again.fetch("/api/auth/delete-account", { method: "POST", body: JSON.stringify({ password: "next-password" }) });
  const markers = calls.map(call => JSON.parse(call.init.body).device);
  assert.match(markers[0], /^[A-Za-z0-9_-]{20,64}$/);
  assert.equal(new Set(markers).size, 1);
  assert.equal(calls[0].url, `${BASE}/api/auth/login`);
  assert.equal(calls[1].init.headers.Authorization, `Bearer ${TOKEN}`);
  assert.equal(calls[2].init.headers.Authorization, `Bearer ${TOKEN}`);
  assert.equal(calls[0].init.credentials, "omit");
  const socket = new again.WebSocket("wss://localhost/ws");
  socket.dispatchEvent(new Event("open"));
  assert.deepEqual(socket.sent, [], "account erasure removes the bearer");
  assert.ok(calls.every(call => !call.url.includes(markers[0])));
});
test("the device-check session uses the same device policy; logout keeps the marker", async () => {
  const saved = storage();
  const calls = [];
  const fetch = async (url, init) => {
    calls.push({ url, init });
    return new Response(JSON.stringify({ account: {}, app_session: { token: TOKEN } }));
  };
  const cap = capacitor();
  const opts = { serverUrl: BASE, storage: saved, crypto: webcrypto, fetch };
  const session = createAppSession(core, cap, opts);
  await session.login("synthetic", "test-password");
  await session.logout();
  await createAppSession(core, cap, opts).login("synthetic", "test-password");
  const logins = calls.filter(call => call.url.endsWith("/login"));
  assert.equal(JSON.parse(logins[0].init.body).device, JSON.parse(logins[1].init.body).device);
});
test("different installs differ; storage failure sends no unmarked login", async () => {
  const markers = [];
  for (let i = 0; i < 2; i++) {
    const root = windowFor({ fetch: async (_url, init) => {
      markers.push(JSON.parse(init.body).device);
      return new Response("{}", { status: 401 });
    } });
    await install(root);
    await root.fetch("/api/auth/login", { method: "POST", body: "{}" });
  }
  assert.notEqual(markers[0], markers[1]);
  const root = windowFor({ saved: { getItem: () => { throw new Error("blocked"); } }, fetch: () => assert.fail("must not send") });
  await assert.rejects(() => install(root), /device_storage_unavailable/);
});
test("socket authenticates before screen handlers; wrong current password preserves the login", async () => {
  const root = windowFor({ fetch: async url => url.endsWith("/login")
    ? new Response(JSON.stringify({ app_session: { token: TOKEN } }))
    : new Response('{"detail":"password_refused"}', { status: 401, headers: { "Retry-After": "60" } }) });
  await install(root);
  await root.fetch("/api/auth/login", { method: "POST", body: "{}" });
  const refused = await root.fetch("/api/auth/password", { method: "POST", body: "{}" });
  assert.equal(refused.status, 401);
  assert.equal(refused.headers.get("Retry-After"), "60");
  const socket = new root.WebSocket("wss://localhost/ws");
  socket.addEventListener("open", () => socket.send("screen-first-control"));
  socket.dispatchEvent(new Event("open"));
  assert.equal(socket.url, "wss://pilot.example/ws");
  assert.deepEqual(socket.sent.map(value => value === "screen-first-control" ? value : JSON.parse(value)), [{ type: "client.auth", token: TOKEN }, "screen-first-control"]);
});
test("web, bundled assets and other origins are untouched and see no device or bearer", async () => {
  let call;
  const root = windowFor({ fetch: async (...args) => { call = args; return new Response("{}"); } });
  await install(root);
  for (const url of ["/app/brand/logo.svg", "https://elsewhere.example/api/auth/login"]) {
    await root.fetch(url, { method: "POST", body: "{}" });
    assert.deepEqual(call, [url, { method: "POST", body: "{}" }]);
  }
  const browser = windowFor({ cap: { isNativePlatform: () => false } });
  const old = browser.fetch;
  await install(browser);
  assert.equal(browser.fetch, old);
});

test("protocol reads stay unchanged across chemistry, biology and materials", async () => {
  const calls = [];
  const root = windowFor({ fetch: async (url, init) => {
    calls.push({ url, init });
    return new Response(JSON.stringify({ protocol_id: url.split("/").pop(), state: "read-only-synthetic" }));
  } });
  await install(root);
  for (const id of ["chemistry", "biology", "battery-materials"]) {
    const result = await root.fetch(`/api/protocols/${id}`);
    assert.deepEqual(await result.json(), { protocol_id: id, state: "read-only-synthetic" });
  }
  assert.ok(calls.every(call => !call.init.body && !call.init.method));
});

test("a development server does not receive a bearer socket greeting", async () => {
  const cap = capacitor();
  await cap.nativePromise("VoineySessionStore", "set", { token: TOKEN });
  const root = windowFor({ cap, fetch: async () => new Response('{"mode":"dev","signed_in":true}') });
  await install(root);
  await root.fetch("/api/auth/session");
  const socket = new root.WebSocket("wss://localhost/ws");
  socket.dispatchEvent(new Event("open"));
  assert.deepEqual(socket.sent, []);
});

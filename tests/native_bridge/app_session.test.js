// Lane APP1, decision 3: the app's login. The bundled screen gets its session
// token in the login answer (only from an origin the server lists), keeps it
// in the phone's secure store (the VoineySessionStore plugin, Android
// Keystore), and sends it as `Authorization: Bearer` on /api and as the
// first /ws message -- never in a URL.
// Run with `node --test tests/native_bridge/*.test.js`; tests/test_native_bridge.py
// runs the same from pytest.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const bridge = require(path.join(__dirname, "..", "..", "src", "voiney_lab", "static", "native-bridge.js"));

const TOKEN = "t".repeat(43);
const BASE = "https://pilot.example";

function fakeCapacitor({ plugin = true, stored = null } = {}) {
  const sent = [];
  const plugins = plugin ? { VoineySessionStore: {} } : {};
  const cap = {
    sent,
    stored,
    Plugins: plugins,
    isPluginAvailable: (name) => Object.prototype.hasOwnProperty.call(plugins, name),
    nativePromise: async (name, method, options) => {
      sent.push([name, method, options]);
      if (!cap.isPluginAvailable(name)) throw new Error("not implemented");
      if (method === "set") cap.stored = options.token;
      if (method === "get") return { token: cap.stored };
      if (method === "clear") cap.stored = null;
      return {};
    },
  };
  return cap;
}

function fakeFetch(routes) {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url, init: init || {} });
    const route = routes[`${(init && init.method) || "GET"} ${url}`];
    const answer = typeof route === "function" ? route(init || {}) : route || { status: 404, body: { detail: "not_found" } };
    return {
      status: answer.status,
      ok: answer.status >= 200 && answer.status < 300,
      json: async () => answer.body,
    };
  };
  fn.calls = calls;
  return fn;
}

test("the server address is https (or http on this computer) and the socket follows it", () => {
  assert.equal(bridge.serverBase("https://pilot.example/"), BASE);
  assert.equal(bridge.serverBase("https://Pilot.Example:8443/anything?x=1"), "https://pilot.example:8443");
  assert.equal(bridge.serverBase("http://127.0.0.1:8000"), "http://127.0.0.1:8000");
  assert.equal(bridge.serverBase("http://localhost:8000"), "http://localhost:8000");
  for (const bad of ["http://pilot.example", "ftp://pilot.example", "pilot.example", "", null, "https://user:pw@pilot.example"]) {
    assert.equal(bridge.serverBase(bad), null, String(bad));
  }
  assert.equal(bridge.socketUrl(BASE), "wss://pilot.example/ws");
  assert.equal(bridge.socketUrl("http://127.0.0.1:8000"), "ws://127.0.0.1:8000/ws");
  assert.equal(bridge.apiUrl(BASE, "/api/protocols"), "https://pilot.example/api/protocols");
  assert.throws(() => bridge.apiUrl(BASE, "https://elsewhere.example/api/x"));
  assert.throws(() => bridge.apiUrl(BASE, "/static/thing.js"));
});

test("headers and the socket's first message carry the token; nothing else does", () => {
  assert.deepEqual(bridge.authHeaders(TOKEN, { "Content-Type": "application/json" }), {
    "Content-Type": "application/json",
    Authorization: `Bearer ${TOKEN}`,
  });
  assert.deepEqual(bridge.authHeaders(null, { A: "1" }), { A: "1" });
  assert.deepEqual(JSON.parse(bridge.socketAuthMessage(TOKEN)), { type: "client.auth", token: TOKEN });
  assert.equal(bridge.socketAuthMessage(null), null);
});

test("login keeps the token in the secure store, and every request and the socket use it", async () => {
  const cap = fakeCapacitor();
  const fetch = fakeFetch({
    [`POST ${BASE}/api/auth/login`]: (init) => {
      assert.deepEqual(JSON.parse(init.body), { name: "김재준", password: "correct horse battery" });
      return { status: 200, body: { account: { name: "김재준" }, app_session: { token: TOKEN, expires_in_seconds: 2592000 } } };
    },
    [`GET ${BASE}/api/auth/session`]: { status: 200, body: { mode: "accounts", signed_in: true, account: { name: "김재준" } } },
    [`GET ${BASE}/api/protocols`]: { status: 200, body: { protocols: [] } },
  });
  const session = bridge.createAppSession(cap, { serverUrl: `${BASE}/`, fetch });
  const result = await session.login("김재준", "correct horse battery");
  assert.deepEqual(result, { signedIn: true, account: { name: "김재준" } });
  assert.equal(cap.stored, TOKEN);
  const response = await session.fetch("/api/protocols");
  assert.equal(response.status, 200);
  const last = fetch.calls[fetch.calls.length - 1];
  assert.equal(last.url, `${BASE}/api/protocols`);
  assert.equal(last.init.headers.Authorization, `Bearer ${TOKEN}`);
  assert.equal(last.init.credentials, "omit", "the app never sends cookies");
  assert.deepEqual(JSON.parse(await session.socketHello()), { type: "client.auth", token: TOKEN });
  assert.equal(session.socketUrl(), "wss://pilot.example/ws");
  for (const call of fetch.calls) assert.ok(!call.url.includes(TOKEN), "never in a URL");
  // The returned value never shows the token.
  assert.ok(!JSON.stringify(result).includes(TOKEN));
});

test("restore reads the stored token after the app restarts; dev mode needs no token on the socket", async () => {
  const cap = fakeCapacitor({ stored: TOKEN });
  const fetch = fakeFetch({
    [`GET ${BASE}/api/auth/session`]: (init) => ({
      status: 200,
      body: init.headers && init.headers.Authorization === `Bearer ${TOKEN}`
        ? { mode: "accounts", signed_in: true, account: { name: "김재준" } }
        : { mode: "accounts", signed_in: false, account: null },
    }),
  });
  const session = bridge.createAppSession(cap, { serverUrl: BASE, fetch });
  assert.deepEqual(await session.restore(), { signedIn: true, mode: "accounts", account: { name: "김재준" } });
  const dev = bridge.createAppSession(fakeCapacitor(), {
    serverUrl: BASE,
    fetch: fakeFetch({ [`GET ${BASE}/api/auth/session`]: { status: 200, body: { mode: "dev", signed_in: true, account: { name: "개발" } } } }),
  });
  assert.equal((await dev.restore()).mode, "dev");
  assert.equal(await dev.socketHello(), null, "no client.auth for a server in dev mode");
});

test("a session the server ended is forgotten on the phone", async () => {
  const cap = fakeCapacitor({ stored: TOKEN });
  const fetch = fakeFetch({
    [`GET ${BASE}/api/auth/session`]: { status: 200, body: { mode: "accounts", signed_in: false, account: null } },
    [`GET ${BASE}/api/protocols`]: { status: 401, body: { detail: "authentication_required" } },
  });
  const session = bridge.createAppSession(cap, { serverUrl: BASE, fetch });
  assert.equal((await session.restore()).signedIn, false);
  assert.equal(cap.stored, null);
  const again = bridge.createAppSession(fakeCapacitor({ stored: TOKEN }), { serverUrl: BASE, fetch });
  await again.restore();
  const stale = fakeCapacitor({ stored: TOKEN });
  const third = bridge.createAppSession(stale, { serverUrl: BASE, fetch: fakeFetch({
    [`GET ${BASE}/api/protocols`]: { status: 401, body: { detail: "authentication_required" } },
  }) });
  await third.load();
  const refused = await third.fetch("/api/protocols");
  assert.equal(refused.status, 401);
  assert.equal(stale.stored, null);
});

test("a refused login, or a server that does not list the app, stores nothing", async () => {
  const cap = fakeCapacitor();
  const wrong = bridge.createAppSession(cap, { serverUrl: BASE, fetch: fakeFetch({
    [`POST ${BASE}/api/auth/login`]: { status: 401, body: { detail: "login_refused" } },
  }) });
  assert.deepEqual(await wrong.login("김재준", "x".repeat(8)), { signedIn: false, status: 401, reason: "login_refused" });
  assert.equal(cap.stored, null);
  const unlisted = bridge.createAppSession(cap, { serverUrl: BASE, fetch: fakeFetch({
    // An origin the server does not list gets the web's answer: a cookie, no token.
    [`POST ${BASE}/api/auth/login`]: { status: 200, body: { account: { name: "김재준" } } },
  }) });
  assert.deepEqual(await unlisted.login("김재준", "x".repeat(8)), { signedIn: false, status: 200, reason: "app_origin_not_listed" });
  assert.equal(cap.stored, null);
});

test("logout ends the server session and clears the phone", async () => {
  const cap = fakeCapacitor({ stored: TOKEN });
  const fetch = fakeFetch({ [`POST ${BASE}/api/auth/logout`]: { status: 200, body: { signed_out: true } } });
  const session = bridge.createAppSession(cap, { serverUrl: BASE, fetch });
  await session.load();
  await session.logout();
  assert.equal(fetch.calls[0].init.headers.Authorization, `Bearer ${TOKEN}`);
  assert.equal(cap.stored, null);
  assert.equal(await session.socketHello(), null);
});

test("without the secure store the token is never kept anywhere else", async () => {
  const cap = fakeCapacitor({ plugin: false });
  const fetch = fakeFetch({
    [`POST ${BASE}/api/auth/login`]: { status: 200, body: { account: {}, app_session: { token: TOKEN } } },
  });
  const session = bridge.createAppSession(cap, { serverUrl: BASE, fetch });
  assert.deepEqual(await session.login("김재준", "x".repeat(8)), { signedIn: false, status: 200, reason: "secure_store_unavailable" });
  assert.equal(await session.socketHello(), null);
  assert.throws(() => bridge.createAppSession(cap, { serverUrl: "http://pilot.example", fetch }));
});

test("the page's own fetch, in the app: server paths go to the server with the bearer, the rest stays local", async () => {
  const cap = fakeCapacitor({ stored: TOKEN });
  const fetch = fakeFetch({
    [`GET ${BASE}/api/protocols?filename=a.pdf`]: { status: 200, body: {} },
    [`GET ${BASE}/readyz`]: { status: 200, body: { status: "ok" } },
    [`PUT ${BASE}/api/experimenter/settings`]: { status: 200, body: {} },
    "GET /mic-capture-worklet.js": { status: 200, body: {} },
  });
  const session = bridge.createAppSession(cap, { serverUrl: BASE, fetch });
  await session.load();
  const appFetch = bridge.createAppFetch(session, fetch);
  await appFetch("/api/protocols?filename=a.pdf", { headers: { Accept: "application/json" }, redirect: "error" });
  await appFetch("/readyz", { cache: "no-store" });
  await appFetch("/api/experimenter/settings", { method: "PUT", headers: new Map([["Content-Type", "application/json"]]) });
  await appFetch("/mic-capture-worklet.js");
  const [list, ready, put, local] = fetch.calls;
  assert.equal(list.url, `${BASE}/api/protocols?filename=a.pdf`);
  assert.equal(list.init.headers.Authorization, `Bearer ${TOKEN}`);
  assert.equal(list.init.headers.Accept, "application/json");
  assert.equal(list.init.redirect, "error");
  assert.equal(ready.url, `${BASE}/readyz`);
  assert.equal(put.init.headers["Content-Type"], "application/json", "a Headers-like object keeps its entries");
  assert.equal(local.url, "/mic-capture-worklet.js");
  assert.equal(local.init.headers, undefined, "the bundled files never see the token");
});

test("an image or a download from /api becomes a blob address, fetched with the bearer", async () => {
  const cap = fakeCapacitor({ stored: TOKEN });
  const blob = { size: 3 };
  const made = [];
  const fetch = async (url, init) => ({ status: 200, ok: true, url, init, blob: async () => blob });
  const session = bridge.createAppSession(cap, { serverUrl: BASE, fetch: async (url, init) => { made.push([url, init]); return fetch(url, init); } });
  await session.load();
  const address = await bridge.resourceObjectUrl(session, "/api/protocols/p1/figures/2", { createObjectURL: (value) => (value === blob ? "blob:app/1" : null) });
  assert.equal(address, "blob:app/1");
  assert.equal(made[0][0], `${BASE}/api/protocols/p1/figures/2`);
  assert.equal(made[0][1].headers.Authorization, `Bearer ${TOKEN}`);
  const refused = bridge.createAppSession(fakeCapacitor({ stored: TOKEN }), { serverUrl: BASE, fetch: async () => ({ status: 404, ok: false }) });
  await refused.load();
  assert.equal(await bridge.resourceObjectUrl(refused, "/api/x", { createObjectURL: () => "blob:never" }), null);
});

test("joining through an invite link from the app signs in the same way", async () => {
  const cap = fakeCapacitor();
  const fetch = fakeFetch({
    [`POST ${BASE}/api/auth/signup`]: (init) => {
      assert.deepEqual(JSON.parse(init.body), { invite: "inv-1", name: "김재준", password: "correct horse battery" });
      return { status: 201, body: { account: { name: "김재준" }, app_session: { token: TOKEN } } };
    },
  });
  const session = bridge.createAppSession(cap, { serverUrl: BASE, fetch });
  assert.deepEqual(await session.signup("inv-1", "김재준", "correct horse battery"), { signedIn: true, account: { name: "김재준" } });
  assert.equal(cap.stored, TOKEN);
});

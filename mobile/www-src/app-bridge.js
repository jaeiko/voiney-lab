/* App-only transport. The server owns every workflow decision. */
(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else {
    root.VoineyAppBridge = api;
    api.install(root).catch(() => {
      // Fail closed: no unmarked auth request and no fallback token storage.
      root.fetch = () => Promise.reject(new Error("app_bridge_unavailable"));
      root.location.replace("/offline.html");
    });
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const DEVICE_KEY = "voiney.app.device";
  const DEVICE_PATHS = new Set(["/api/auth/login", "/api/auth/password", "/api/auth/delete-account"]);
  const SIGN_IN = new Set(["/api/auth/login", "/api/auth/signup"]);
  const CURRENT_PASSWORD = new Set(["/api/auth/password", "/api/auth/delete-account"]);
  const STORE = "VoineySessionStore";

  function deviceMarker(storage, crypto) {
    try {
      const previous = storage.getItem(DEVICE_KEY);
      if (/^[A-Za-z0-9_-]{20,64}$/.test(previous || "")) return previous;
      const bytes = new Uint8Array(24);
      crypto.getRandomValues(bytes);
      const marker = Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("");
      storage.setItem(DEVICE_KEY, marker);
      if (storage.getItem(DEVICE_KEY) !== marker) throw new Error("not saved");
      return marker;
    } catch (_error) { throw new Error("device_storage_unavailable"); }
  }

  function createDeviceFetch(options) {
    const marker = deviceMarker(options.storage, options.crypto);
    const base = new URL(options.serverUrl).origin;
    return (input, init) => {
      const url = typeof input === "string" ? new URL(input, base) : null;
      const given = init || {};
      if (url && url.origin === base && DEVICE_PATHS.has(url.pathname) && (given.method || "GET").toUpperCase() === "POST") {
        const body = JSON.parse(given.body);
        if (!body || Array.isArray(body) || typeof body !== "object") throw new Error("invalid_auth_body");
        return options.fetch(input, { ...given, body: JSON.stringify({ ...body, device: marker }) });
      }
      return options.fetch(input, init);
    };
  }

  // The device-check page keeps the established secure session bridge.
  function createAppSession(core, cap, options) {
    return core.createAppSession(cap, { ...options, fetch: createDeviceFetch(options) });
  }

  async function install(root) {
    const cap = root.Capacitor;
    if (!cap || !cap.isNativePlatform || !cap.isNativePlatform()) return false;
    const core = root.VoineyNativeBridge;
    const base = core.serverBase(root.VOINEY_APP_CONFIG && root.VOINEY_APP_CONFIG.serverUrl);
    // A build without an API address can show the screen, but cannot connect.
    if (!base) return false;
    if (!core.pluginAvailable(cap, STORE)) throw new Error("secure_store_unavailable");
    const localOrigin = new URL(root.location.href).origin;
    const originalFetch = root.fetch.bind(root);
    const markedFetch = createDeviceFetch({ serverUrl: base, storage: root.localStorage, crypto: root.crypto, fetch: originalFetch });
    let token = null;
    let mode = null;
    let loaded = false;
    const ready = cap.nativePromise(STORE, "get", {}).then(result => {
      token = result && typeof result.token === "string" && result.token ? result.token : null;
      loaded = true;
    });
    const forget = async () => {
      token = null;
      await cap.nativePromise(STORE, "clear", {});
    };
    root.fetch = async (input, init) => {
      const url = typeof input === "string" ? new URL(input, root.location.href) : null;
      if (!url || ![localOrigin, base].includes(url.origin) || !/^\/(api\/|readyz$|healthz$)/.test(url.pathname)) return originalFetch(input, init);
      await ready;
      const requestPath = url.pathname + url.search;
      const response = await markedFetch(base + requestPath, { ...init, credentials: "omit", headers: core.authHeaders(token, init && init.headers) });
      const post = (init && init.method || "GET").toUpperCase() === "POST";
      if (post && SIGN_IN.has(url.pathname) && response.ok) {
        const body = await response.clone().json();
        const given = body.app_session && body.app_session.token;
        if (typeof given !== "string" || !given) throw new Error("app_origin_not_listed");
        await cap.nativePromise(STORE, "set", { token: given });
        token = given;
        mode = "accounts";
      } else if (post && response.ok && ["/api/auth/logout", "/api/auth/delete-account"].includes(url.pathname)) {
        await forget();
      } else if (response.status === 401 && !SIGN_IN.has(url.pathname) && !CURRENT_PASSWORD.has(url.pathname)) {
        await forget();
      } else if (url.pathname === "/api/auth/session" && response.ok) {
        const body = await response.clone().json();
        mode = body.mode || null;
        if (body.signed_in !== true && token) await forget();
      }
      return response;
    };
    const NativeSocket = root.WebSocket;
    root.WebSocket = class extends NativeSocket {
      constructor(input, protocols) {
        const url = new URL(input, root.location.href);
        const httpOrigin = url.origin.replace(/^ws/, "http");
        const serverSocket = [localOrigin, base].includes(httpOrigin) && url.pathname === "/ws";
        if (serverSocket && !loaded) throw new Error("app_session_loading");
        super(serverSocket ? core.socketUrl(base) : input, protocols);
        if (serverSocket) this.addEventListener("open", () => {
          // Registered before the screen can send a control or an audio frame.
          const hello = mode === "dev" ? null : core.socketAuthMessage(token);
          if (hello) this.send(hello);
        });
      }
    };
    await ready;
    return true;
  }
  return { createDeviceFetch, createAppSession, install };
});

// The bundled screen's own settings (lane APP1, decision 5): which server it
// talks to. Written by prepare-web.mjs into www/app-config.js, read by the
// page as window.VOINEY_APP_CONFIG before native-bridge.js uses it
// (createAppSession({ serverUrl })). Only a public address goes here -- no
// token, no key: the login token lives in the phone's secure store.
//
// VOINEY_APP_API_URL: the server's https:// address (http:// only for
// localhost or 127.0.0.1, for development). Empty: the bundled screen has no
// server yet (the device-check page asks for one).

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

export function appServerUrl(value) {
  const text = typeof value === "string" ? value.trim() : "";
  if (!text) return null;
  let url;
  try {
    url = new URL(text);
  } catch (_error) {
    throw new Error("VOINEY_APP_API_URL is not an address");
  }
  if (url.username || url.password) throw new Error("VOINEY_APP_API_URL must not carry a user or password");
  if (url.protocol !== "https:" && !(url.protocol === "http:" && LOCAL_HOSTS.has(url.hostname))) {
    throw new Error("VOINEY_APP_API_URL must be https:// (the microphone needs a secure page)");
  }
  return `${url.protocol}//${url.host}`;
}

export function appConfigSource(env) {
  const config = { serverUrl: appServerUrl((env || {}).VOINEY_APP_API_URL) };
  return `// Written by mobile/scripts/prepare-web.mjs; not tracked.\nwindow.VOINEY_APP_CONFIG = Object.freeze(${JSON.stringify(config)});\n`;
}

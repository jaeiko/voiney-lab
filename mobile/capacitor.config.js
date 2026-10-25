// Voiney Lab store app shell (lane APP0). See docs/APP_PLAN.md.
//
// Two ways to load the screen, chosen when the app is built:
//
// * bundled (default, the store build): the server's static screen
//   (src/voiney_lab/static) is copied into www/ by scripts/prepare-web.mjs
//   and shipped inside the app. The screen still talks to the server for
//   everything; until index.html takes its server address from the app
//   (connection points in docs/APP_PLAN.md) this build cannot reach one.
// * pilot (VOINEY_APP_SERVER_URL=https://...): the WebView opens the server's
//   own page. Capacitor documents `server.url` as "not intended for use in
//   production", so this is for internal device tests only.
//
// VOINEY_APP_START_PATH picks another bundled page to open first, e.g.
// device-check.html (the device test page built into the debug APK).
//
// No secret goes in this file or in the app: the server URL is public and
// the signing keys stay outside the repository.
const serverUrl = (process.env.VOINEY_APP_SERVER_URL || "").trim();
const startPath = (process.env.VOINEY_APP_START_PATH || "").trim();

if (serverUrl && !serverUrl.startsWith("https://")) {
  throw new Error("VOINEY_APP_SERVER_URL must be https:// (the microphone needs a secure page)");
}

const server = { androidScheme: "https", errorPath: "offline.html" };
if (serverUrl) server.url = serverUrl;
if (startPath) server.appStartPath = startPath;

module.exports = {
  // Placeholder until a person fixes it; a store listing cannot change it
  // after the first upload (docs/APP_PLAN.md, 사람이 할 일).
  appId: "com.voiney.lab",
  appName: "Voiney Lab",
  webDir: "www",
  server,
  // Lane APP1: Capacitor's default ("debug") logs every plugin call's
  // options and answers to the device log in debug builds -- that would put
  // the login token (VoineySessionStore) and the microphone's sound
  // (VoineyMicrophone) in logcat. Off in every build.
  loggingBehavior: "none",
  plugins: {
    LocalNotifications: {
      iconColor: "#1f6f5c",
    },
  },
};

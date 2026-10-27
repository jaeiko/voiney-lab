// Builds mobile/www (not tracked) from the server's static screen and the
// app's own local pages. Run before `cap sync`.
import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { appConfigSource } from "./app-config.mjs";

const mobile = join(dirname(fileURLToPath(import.meta.url)), "..");
const localPages = join(mobile, "www-src");

export async function prepareWeb(options = {}) {
  const staticDir = options.staticDir || join(mobile, "..", "src", "voiney_lab", "static");
  const www = options.www || join(mobile, "www");
  if (!existsSync(join(staticDir, "app", "index.html"))) {
    throw new Error("new screen missing: build web/ before preparing the app");
  }
  // Validate before replacing an existing bundle.
  const config = appConfigSource(options.env || process.env);
  rmSync(www, { recursive: true, force: true });
  mkdirSync(www, { recursive: true });
  cpSync(staticDir, www, { recursive: true });
  cpSync(localPages, www, { recursive: true });
  writeFileSync(join(www, "app-config.js"), config);
  // Classic scripts run before Vite's deferred module and its first auth fetch.
  const bootstrap = ["capacitor.js", "app-config.js", "native-bridge.js", "app-bridge.js"]
    .map(name => `<script src="/${name}"></script>`).join("\n");
  for (const page of ["app/index.html", "index.html"]) {
    const file = join(www, page);
    if (existsSync(file)) writeFileSync(file, readFileSync(file, "utf8").replace("</head>", `${bootstrap}\n</head>`));
  }
  return www;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  console.log(`prepared ${await prepareWeb()}`);
}

// Builds mobile/www (not tracked) from the server's static screen and the
// app's own local pages. Run before `cap sync`.
import { cpSync, existsSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { appConfigSource } from "./app-config.mjs";

const mobile = join(dirname(fileURLToPath(import.meta.url)), "..");
const staticDir = join(mobile, "..", "src", "voiney_lab", "static");
const localPages = join(mobile, "www-src");
const www = join(mobile, "www");

if (!existsSync(join(staticDir, "index.html"))) {
  throw new Error(`server static screen not found at ${staticDir}`);
}
rmSync(www, { recursive: true, force: true });
mkdirSync(www, { recursive: true });
cpSync(staticDir, www, { recursive: true });
cpSync(localPages, www, { recursive: true });
// Lane APP1: the bundled screen's server address (VOINEY_APP_API_URL).
writeFileSync(join(www, "app-config.js"), appConfigSource(process.env));
console.log(`prepared ${www} from ${staticDir} and ${localPages}`);

// Where the new screen sends the person for what it has not taken over yet
// (lane RX0): the old screen at /index.html keeps its hash routes, so a
// hash the new screen does not know is opened there unchanged.

export const OLD_SCREEN = "/index.html";

export function oldScreenUrl(hash: string): string {
  const clean = hash.startsWith("#") ? hash : `#${hash}`;
  return `${OLD_SCREEN}${clean}`;
}

export const oldRoutes = {
  upload: oldScreenUrl("#/upload"),
  experiment: oldScreenUrl("#/experiment"),
  records: oldScreenUrl("#/records"),
  settings: oldScreenUrl("#/settings"),
  protocol: (id: string) => oldScreenUrl(`#/protocols/${encodeURIComponent(id)}`),
  record: (id: string) => oldScreenUrl(`#/records/${encodeURIComponent(id)}`),
} as const;

export function goToOldScreen(hash: string): void {
  window.location.replace(oldScreenUrl(hash));
}

export interface Route {
  name: "home" | "protocols" | "start" | "run" | "settings" | "welcome" | "handoff";
  hash: string;
  /** The protocol a start names (#/start/<id>), or the saved experiment it continues (#/start/<id>?resume=<session>). */
  protocolId?: string | null;
  resumeId?: string | null;
  /** The start flow's step to open at (1-5), after the server refused a start for that step. */
  step?: number | null;
}

function decoded(value: string): string | null {
  try {
    const text = decodeURIComponent(value);
    return text.trim() ? text : null;
  } catch {
    return null;
  }
}

/** The new screen's own routes; everything else is the old screen's. */
export function parseRoute(hash: string): Route {
  const clean = (hash || "").replace(/^#/, "");
  if (clean === "" || clean === "/" || clean === "/home") return { name: "home", hash: "#/home" };
  if (clean === "/protocols") return { name: "protocols", hash: "#/protocols" };
  // Lane RX2: the start flow, the experiment screen, the settings and the first guide.
  const start = /^\/start(?:\/([^/?#]+))?\/?(?:\?(.*))?$/.exec(clean);
  if (start) {
    const protocolId = start[1] ? decoded(start[1]) : null;
    const query = new URLSearchParams(start[2] || "");
    const resumeId = query.get("resume");
    const step = Number.parseInt(query.get("step") || "", 10);
    return {
      name: "start", hash: `#${clean}`, protocolId,
      resumeId: resumeId && resumeId.trim() ? resumeId : null,
      step: Number.isInteger(step) && step >= 1 && step <= 5 ? step : null,
    };
  }
  if (clean === "/experiment" || clean === "/run") return { name: "run", hash: "#/experiment" };
  if (clean === "/settings") return { name: "settings", hash: "#/settings" };
  if (clean === "/welcome") return { name: "welcome", hash: "#/welcome" };
  if (clean.startsWith("/")) return { name: "handoff", hash: `#${clean}` };
  return { name: "home", hash: "#/home" };
}

export const routes = {
  home: "#/home",
  protocols: "#/protocols",
  start: (protocolId?: string | null, options: { resume?: string | null; step?: number | null } = {}) => {
    const query = new URLSearchParams();
    if (options.resume) query.set("resume", options.resume);
    if (options.step) query.set("step", String(options.step));
    const tail = query.toString();
    return `#/start${protocolId ? `/${encodeURIComponent(protocolId)}` : ""}${tail ? `?${tail}` : ""}`;
  },
  run: "#/experiment",
  settings: "#/settings",
  welcome: "#/welcome",
} as const;

export function go(hash: string, replace = false): void {
  if (replace) window.location.replace(hash);
  else window.location.hash = hash;
}

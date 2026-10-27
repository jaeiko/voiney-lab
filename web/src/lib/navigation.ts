// Where the new screen sends the person for what it has not taken over yet
// (lane RX0): the old screen at /index.html keeps its hash routes, so a
// hash the new screen does not know is opened there unchanged. Lane RX1
// took over the upload, the analysis progress, the confirmation screen and
// today's values; lane RX2 the start flow, the experiment, the settings and
// the first guide. Lane RX3 owns records and notebooks; protocol detail still has a legacy view.

export const OLD_SCREEN = "/index.html";

export function oldScreenUrl(hash: string): string {
  const clean = hash.startsWith("#") ? hash : `#${hash}`;
  return `${OLD_SCREEN}?legacy=1${clean}`;
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

export type RouteName =
  | "home" | "protocols" | "upload" | "analysis" | "review" | "changes"
  | "start" | "run" | "settings" | "welcome" | "records" | "record" | "notebook" | "handoff";

export interface Route {
  name: RouteName;
  hash: string;
  /** The protocol a screen is about (#/protocols/<id>/…, #/start/<id>). */
  protocolId?: string | null;
  sessionId?: string;
  reportId?: string;
  /** The saved experiment a start continues (#/start/<id>?resume=<session>). */
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

const PROTOCOL_SCREEN = /^\/protocols\/([^/]+)\/(analysis|review|changes)$/;

/** The new screen's own routes; everything else is the old screen's. */
export function parseRoute(hash: string): Route {
  const clean = (hash || "").replace(/^#/, "").replace(/\/(?=\?|$)/, "");
  if (clean === "" || clean === "/" || clean === "/home") return { name: "home", hash: "#/home" };
  const record = /^\/records(?:\/([^/?#]+))?(?:\/notebook\/([^/?#]+))?\/?(?:\?[^#]*)?$/.exec(clean);
  if (record) {
    const sessionId = record[1] ? decoded(record[1]) : null;
    const reportId = record[2] ? decoded(record[2]) : null;
    if (!record[1]) return { name: "records", hash: "#/records" };
    if (sessionId && (!record[2] || reportId)) return { name: reportId ? "notebook" : "record", hash: `#${clean}`, sessionId, ...(reportId ? { reportId } : {}) };
  }
  if (clean === "/protocols") return { name: "protocols", hash: "#/protocols" };
  if (clean === "/upload") return { name: "upload", hash: "#/upload" };
  const screen = PROTOCOL_SCREEN.exec(clean);
  if (screen) {
    let protocolId = screen[1];
    try {
      protocolId = decodeURIComponent(screen[1]);
    } catch {
      // A hash someone typed by hand: kept as written.
    }
    return { name: screen[2] as "analysis" | "review" | "changes", hash: `#${clean}`, protocolId };
  }
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
  records: "#/records",
  record: (id: string) => `#/records/${encodeURIComponent(id)}`,
  notebook: (sessionId: string, reportId: string) => `#/records/${encodeURIComponent(sessionId)}/notebook/${encodeURIComponent(reportId)}`,
  protocols: "#/protocols",
  upload: "#/upload",
  analysis: (id: string) => `#/protocols/${encodeURIComponent(id)}/analysis`,
  review: (id: string) => `#/protocols/${encodeURIComponent(id)}/review`,
  changes: (id: string) => `#/protocols/${encodeURIComponent(id)}/changes`,
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

/** Where "실험 시작" goes for a protocol: lane RX2's start flow for it. */
export function startHref(protocolId: string): string {
  return routes.start(protocolId);
}

export function goTo(hash: string): void {
  window.location.hash = hash;
}

export function go(hash: string, replace = false): void {
  if (replace) window.location.replace(hash);
  else window.location.hash = hash;
}

// Where the new screen sends the person for what it has not taken over yet
// (lane RX0): the old screen at /index.html keeps its hash routes, so a
// hash the new screen does not know is opened there unchanged. Lane RX1
// took over the upload, the analysis progress, the confirmation screen and
// today's values; the start (and the experiment, records, settings) are
// still the old screen's -- lane RX2 repoints `startHref`.

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

export type RouteName = "home" | "protocols" | "upload" | "analysis" | "review" | "changes" | "handoff";

export interface Route {
  name: RouteName;
  hash: string;
  protocolId?: string;
}

const PROTOCOL_SCREEN = /^\/protocols\/([^/]+)\/(analysis|review|changes)$/;

/** The new screen's own routes; everything else is the old screen's. */
export function parseRoute(hash: string): Route {
  const clean = (hash || "").replace(/^#/, "");
  if (clean === "" || clean === "/" || clean === "/home") return { name: "home", hash: "#/home" };
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
  if (clean.startsWith("/")) return { name: "handoff", hash: `#${clean}` };
  return { name: "home", hash: "#/home" };
}

export const routes = {
  home: "#/home",
  protocols: "#/protocols",
  upload: "#/upload",
  analysis: (id: string) => `#/protocols/${encodeURIComponent(id)}/analysis`,
  review: (id: string) => `#/protocols/${encodeURIComponent(id)}/review`,
  changes: (id: string) => `#/protocols/${encodeURIComponent(id)}/changes`,
} as const;

/** Where "실험 시작" goes for a protocol: the old screen's detail (its start button) until lane RX2 builds the start flow. */
export function startHref(protocolId: string): string {
  return oldRoutes.protocol(protocolId);
}

export function goTo(hash: string): void {
  window.location.hash = hash;
}

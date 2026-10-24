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
  name: "home" | "protocols" | "handoff";
  hash: string;
}

/** The new screen's own routes; everything else is the old screen's. */
export function parseRoute(hash: string): Route {
  const clean = (hash || "").replace(/^#/, "");
  if (clean === "" || clean === "/" || clean === "/home") return { name: "home", hash: "#/home" };
  if (clean === "/protocols") return { name: "protocols", hash: "#/protocols" };
  if (clean.startsWith("/")) return { name: "handoff", hash: `#${clean}` };
  return { name: "home", hash: "#/home" };
}

export const routes = {
  home: "#/home",
  protocols: "#/protocols",
} as const;

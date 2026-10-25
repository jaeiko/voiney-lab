// One fetch for every server call (lane RX0). Same origin, the session
// cookie travels by itself (credentials: same-origin), redirects are
// refused, and an answer that is not JSON or not 2xx becomes an ApiError
// that carries the server's detail code -- never its prose -- so the copy
// table (copy/ko.ts) chooses the sentence.

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryAfterSeconds: number | null;
  /** The refusal's body as the server sent it (lane RX2: the start flow reads its message_ko and detail fields). */
  readonly payload: unknown;

  constructor(status: number, code: string, retryAfterSeconds: number | null = null, payload: unknown = null) {
    super(`${status} ${code}`);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.retryAfterSeconds = retryAfterSeconds;
    this.payload = payload;
  }
}

export const NETWORK_ERROR = "network";

function detailOf(payload: unknown): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail) return detail;
    if (detail && typeof detail === "object" && typeof (detail as { code?: unknown }).code === "string") {
      return (detail as { code: string }).code;
    }
  }
  return "unexpected_server_error";
}

function retryAfterOf(response: Response): number | null {
  const raw = response.headers.get("Retry-After");
  if (!raw) return null;
  const seconds = Number.parseInt(raw, 10);
  return Number.isFinite(seconds) && seconds > 0 ? seconds : null;
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      credentials: "same-origin",
      redirect: "error",
      cache: "no-store",
      ...init,
      headers: { Accept: "application/json", ...(init.headers || {}) },
    });
  } catch {
    throw new ApiError(0, NETWORK_ERROR);
  }
  let payload: unknown = null;
  const text = await response.text();
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }
  if (!response.ok) {
    throw new ApiError(response.status, detailOf(payload), retryAfterOf(response), payload);
  }
  return payload as T;
}

export function postJson<T>(path: string, body: unknown): Promise<T> {
  return apiFetch<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// One fetch for every server call (lane RX0). Same origin, the session
// cookie travels by itself (credentials: same-origin), redirects are
// refused, and an answer that is not JSON or not 2xx becomes an ApiError
// that carries the server's detail code -- never its prose -- so the copy
// table (copy/ko.ts) chooses the sentence. Lane RX1: a detail that is an
// object with a Korean sentence ({code, message_ko}, the link import and
// today's values) keeps that sentence beside the code, and the 422 that asks
// to confirm a large value keeps its payload (`large`).

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryAfterSeconds: number | null;
  /** The server's own sentence when its detail carried one (shown as sent). */
  readonly messageKo: string | null;
  /** The detail object itself when it was one (a 422's extra fields). */
  readonly detail: Record<string, unknown> | null;
  /** The refusal's whole body as the server sent it (lane RX2: a start refusal's message_ko at the top level). */
  readonly payload: unknown;

  constructor(
    status: number,
    code: string,
    retryAfterSeconds: number | null = null,
    messageKo: string | null = null,
    detail: Record<string, unknown> | null = null,
    payload: unknown = null,
  ) {
    super(`${status} ${code}`);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.retryAfterSeconds = retryAfterSeconds;
    this.messageKo = messageKo;
    this.detail = detail;
    this.payload = payload;
  }
}

export const NETWORK_ERROR = "network";

function detailOf(payload: unknown): { code: string; messageKo: string | null; detail: Record<string, unknown> | null } {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail) return { code: detail, messageKo: null, detail: null };
    if (detail && typeof detail === "object") {
      const object = detail as Record<string, unknown>;
      const code = typeof object.code === "string" && object.code ? object.code : "unexpected_server_error";
      const messageKo = typeof object.message_ko === "string" && object.message_ko.trim() ? object.message_ko : null;
      return { code, messageKo, detail: object };
    }
  }
  return { code: "unexpected_server_error", messageKo: null, detail: null };
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
    const { code, messageKo, detail } = detailOf(payload);
    throw new ApiError(response.status, code, retryAfterOf(response), messageKo, detail, payload);
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

/** A file's bytes as the body, typed by its media type (the upload route reads the type, not the name). */
export function postFile<T>(path: string, file: Blob, mediaType: string): Promise<T> {
  return apiFetch<T>(path, {
    method: "POST",
    headers: { "Content-Type": mediaType },
    body: file,
  });
}

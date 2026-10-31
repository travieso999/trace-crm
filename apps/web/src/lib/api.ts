// Thin fetch wrapper: same-origin cookies, CSRF header on unsafe methods, typed errors.

export class ApiError extends Error {
  status: number;
  detail: unknown;
  errors?: { loc: string[]; msg: string }[];

  constructor(status: number, detail: unknown, errors?: { loc: string[]; msg: string }[]) {
    super(
      typeof detail === "string"
        ? detail
        : ((detail as { message?: string } | null)?.message ?? `Request failed (${status})`),
    );
    this.status = status;
    this.detail = detail;
    this.errors = errors;
  }

  /** Detail plus any per-field validation messages, for toasts. */
  get description(): string {
    const problems = (this.detail as { problems?: string[] } | null)?.problems;
    if (problems?.length) return problems.join(" • ");
    if (this.errors?.length) return this.errors.map((e) => `${e.loc.slice(1).join(".")}: ${e.msg}`).join(" • ");
    return this.message;
  }
}

function readCookie(name: string): string | undefined {
  if (typeof document === "undefined") return undefined;
  return document.cookie
    .split("; ")
    .find((c) => c.startsWith(name + "="))
    ?.split("=")[1];
}

type Query = Record<string, string | number | boolean | null | undefined>;

export function withQuery(path: string, query?: Query): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

export async function api<T = unknown>(
  path: string,
  opts: { method?: string; body?: unknown; form?: FormData; raw?: Blob; query?: Query; signal?: AbortSignal } = {},
): Promise<T> {
  const method = opts.method ?? (opts.body !== undefined || opts.form || opts.raw ? "POST" : "GET");
  const headers: Record<string, string> = { Accept: "application/json" };
  if (method !== "GET" && method !== "HEAD") {
    const token = readCookie("ts_csrf");
    if (token) headers["X-CSRF-Token"] = decodeURIComponent(token);
  }
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.raw) {
    headers["Content-Type"] = "application/octet-stream";
    body = opts.raw;
  } else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  const res = await fetch(withQuery(path, opts.query), {
    method,
    headers,
    body,
    credentials: "same-origin",
    signal: opts.signal,
  });
  if (res.status === 401 && typeof window !== "undefined" && !path.startsWith("/api/auth/")) {
    // Outside React (no router here); a full load also drops the stale query cache.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
  }
  if (!res.ok) {
    let payload: { detail?: unknown; errors?: { loc: string[]; msg: string }[] } = {};
    try {
      payload = await res.json();
    } catch {
      /* non-JSON error */
    }
    throw new ApiError(res.status, payload.detail ?? res.statusText, payload.errors);
  }
  if (res.status === 204) return undefined as T;
  const type = res.headers.get("content-type") ?? "";
  return (type.includes("application/json") ? res.json() : res.text()) as Promise<T>;
}

/** Download a file from an authenticated GET endpoint. */
export async function download(path: string, query?: Query, fallbackName = "export.csv") {
  const res = await fetch(withQuery(path, query), { credentials: "same-origin" });
  if (!res.ok) throw new ApiError(res.status, "Export failed");
  const blob = await res.blob();
  const name = /filename="([^"]+)"/.exec(res.headers.get("content-disposition") ?? "")?.[1] ?? fallbackName;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.description;
  if (e instanceof Error) return e.message;
  return "Something went wrong";
}

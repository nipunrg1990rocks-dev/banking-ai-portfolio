// Thin fetch wrapper: bearer token, error envelope, query strings, file downloads.

export type ApiErrorBody = { code: string; message: string; details?: unknown; correlation_id?: string };

export class ApiError extends Error {
  status: number;
  body: ApiErrorBody;
  constructor(status: number, body: ApiErrorBody) {
    super(body.message);
    this.status = status;
    this.body = body;
  }
}

const TOKEN_KEY = "cf360.token";
let token: string | null = (() => {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
})();

export function setToken(t: string | null) {
  token = t;
  try {
    if (t) sessionStorage.setItem(TOKEN_KEY, t);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage may be unavailable */
  }
}

export function getToken() {
  return token;
}

type Params = Record<string, string | number | boolean | null | undefined | string[]>;

export function qs(params?: Params): string {
  if (!params) return "";
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => u.append(k, x));
    else u.append(k, String(v));
  }
  const s = u.toString();
  return s ? `?${s}` : "";
}

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown; params?: Params; headers?: Record<string, string> } = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json", ...(opts.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(`/api${path}${qs(opts.params)}`, {
    method: opts.method || "GET",
    headers,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const body: ApiErrorBody = data?.error || { code: `E_HTTP_${res.status}`, message: res.statusText };
    if (res.status === 401 && path !== "/auth/login") {
      setToken(null);
      window.dispatchEvent(new Event("cf360:logout"));
    }
    throw new ApiError(res.status, body);
  }
  return data as T;
}

export async function download(path: string, params: Params, filename: string) {
  const res = await fetch(`/api${path}${qs(params)}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!res.ok) throw new ApiError(res.status, { code: "E_DOWNLOAD", message: "Download failed" });
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

export function newIdempotencyKey() {
  return `ui-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export type Page<T> = { items: T[]; total: number; page: number; page_size: number; pages: number; [k: string]: any };

/**
 * SWR-based API client.
 * Handles JWT auth, token refresh, and standardized error handling.
 */
const API_BASE = ""; // Proxied through Next.js rewrites

interface FetchOptions extends RequestInit {
  params?: Record<string, string | number | undefined>;
  timeoutMs?: number;
}

class ApiError extends Error {
  status: number;
  code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
    this.name = "ApiError";
  }
}

let accessToken: string | null = null;
let refreshPromise: Promise<string | null> | null = null;

export function setAccessToken(token: string | null) {
  accessToken = token;
}

export function getAccessToken(): string | null {
  return accessToken;
}
export function clearAccessToken() {
  accessToken = null;
}

async function refreshAccessToken(): Promise<string | null> {
  if (refreshPromise) return refreshPromise;
  refreshPromise = (async () => {
    try {
      const resp = await fetch("/auth/refresh", {
        method: "POST",
        credentials: "include",
      });
      if (!resp.ok) return null;
      const json = await resp.json();
      const token = json.data?.access_token;
      if (token) setAccessToken(token);
      return token;
    } catch {
      return null;
    } finally {
      refreshPromise = null;
    }
  })();
  return refreshPromise;
}

/** Decode JWT payload without validating signature (for expiry check only). */
function decodeTokenPayload(token: string): Record<string, any> | null {
  try {
    const payload = JSON.parse(atob(token.split('.')[1]));
    return payload;
  } catch {
    return null;
  }
}

/** Check if token is expired or will expire within the next 30 seconds. */
function isTokenExpired(token: string): boolean {
  const payload = decodeTokenPayload(token);
  if (!payload || !payload.exp) return true;
  // exp is in seconds; add 30s buffer to refresh before it actually expires
  return (payload.exp * 1000) < Date.now() + 30_000;
}

/** Promise that resolves once the initial auth boot check completes. */
let _authBootResolve: (() => void) | null = null;
export const authBootDone: Promise<void> = new Promise((resolve) => {
  _authBootResolve = resolve;
});

/** Mark auth boot as complete. Called once after the first ensureValidToken / refresh attempt. */
export function signalAuthBoot(): void {
  if (_authBootResolve) {
    _authBootResolve();
    _authBootResolve = null;
  }
}

/**
 * Ensure a valid access token is available.
 * If current token is expired, attempts to refresh via /auth/refresh.
 * Called before API calls to prevent 401 errors in browser console.
 */
export async function ensureValidToken(): Promise<string | null> {
  const token = getAccessToken();
  if (!token) {
    // No token at all — redirect to login
    if (typeof window !== "undefined") {
      window.location.href = "/login?expired=1";
    }
    return null;
  }
  if (!isTokenExpired(token)) return token;
  // Token expired or about to expire — refresh now
  const newToken = await refreshAccessToken();
  if (!newToken) {
    // Refresh failed — session is dead
    clearAccessToken();
    if (typeof window !== "undefined") {
      window.location.href = "/login?expired=1";
    }
  }
  return newToken;
}
/**
 * Attempt an initial token refresh from the httpOnly refresh cookie.
 * Should be called once at app boot (layout) to restore a session.
 * Returns the refreshed token or null.
 */
export async function bootAuthFromCookie(): Promise<string | null> {
  const existing = getAccessToken();
  if (existing && !isTokenExpired(existing)) {
    signalAuthBoot();
    return existing;
  }
  const token = await refreshAccessToken();
  signalAuthBoot();
  return token;
}

export const DEFAULT_TIMEOUT_MS = 30000; // 30s for OpenSearch queries

export async function apiFetch<T = unknown>(
  path: string,
  options: FetchOptions = {},
): Promise<T> {
  const { params, timeoutMs = DEFAULT_TIMEOUT_MS, ...fetchOpts } = options;

  const controller = new AbortController();
  const timeoutId = typeof window !== "undefined" ? setTimeout(() => controller.abort(), timeoutMs) : null;

  // Build URL with query params
  let url = `${API_BASE}${path}`;
  if (params) {
    const searchParams = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null) {
        searchParams.set(key, String(value));
      }
    });
    const qs = searchParams.toString();
    if (qs) url += `?${qs}`;
  }

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(fetchOpts.headers as Record<string, string>),
  };

  const token = getAccessToken();
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  let resp: Response;
  try {
    resp = await fetch(url, { ...fetchOpts, headers, credentials: "include", signal: controller.signal });
    if (timeoutId) clearTimeout(timeoutId);
  } catch (e) {
    if (timeoutId) clearTimeout(timeoutId);
    const err = e instanceof Error ? e : new Error(String(e));
    if (err.name === "AbortError") {
      throw new ApiError(408, "TIMEOUT", `Request timed out after ${timeoutMs}ms`);
    }
    throw err;
  }

  // Auto-refresh on 401
  if (resp.status === 401) {
    const newToken = await refreshAccessToken();
    if (newToken) {
      headers["Authorization"] = `Bearer ${newToken}`;
      resp = await fetch(url, { ...fetchOpts, headers, credentials: "include" });
    } else {
      // Refresh failed — session is dead, redirect to login
      clearAccessToken();
      if (typeof window !== "undefined") {
        window.location.href = "/login?expired=1";
      }
      throw new ApiError(401, "SESSION_EXPIRED", "Session expired. Please log in again.");
    }
  }

  // Intercept MUST_CHANGE_PASSWORD 403
  if (resp.status === 403) {
    try {
      const body = await resp.clone().json();
      if (body?.error?.code === "MUST_CHANGE_PASSWORD") {
        if (typeof window !== "undefined") {
          window.location.href = "/dashboard/settings?tab=password&required=true";
        }
        throw new ApiError(403, "MUST_CHANGE_PASSWORD", "Password change required");
      }
    } catch (e) {
      if (e instanceof ApiError) throw e;
      // JSON parse failed, fall through to normal error handling
    }
  }

  // Safely parse JSON — non-JSON 5xx responses (e.g. plain text from
  // uvicorn's default error handler) must not crash the client with
  // "Unexpected token" SyntaxErrors. Treat as a generic error.
  let json: any;
  try {
    json = await resp.json();
  } catch {
    throw new ApiError(
      resp.status,
      "INVALID_RESPONSE",
      `Server returned non-JSON response (status ${resp.status})`
    );
  }

  if (!resp.ok || !json.success) {
    // FastAPI 422 validation errors come back as { detail: [{loc, msg, type, ...}] }
    // instead of the project's { error: { code, message } } shape. Surface the
    // first validation message so the user sees *why* it was rejected, not a
    // generic "An error occurred".
    let code = json.error?.code || "UNKNOWN_ERROR";
    let message = json.error?.message || "An error occurred";
    if (Array.isArray(json.detail) && json.detail.length > 0) {
      const first = json.detail[0];
      const loc = Array.isArray(first.loc) ? first.loc.filter((p: any) => p !== "body").join(".") : "";
      message = loc ? `${loc}: ${first.msg}` : first.msg || message;
      code = first.type?.toUpperCase() || code;
    }
    throw new ApiError(resp.status, code, message);
  }

  return json as T;
}

// SWR fetcher
export const swrFetcher = <T = unknown>(url: string) => apiFetch<T>(url);

export { ApiError };
export default apiFetch;

/** Safely extract a human-readable message from an unknown catch value. */
export function getErrorMessage(err: unknown, fallback = "Unknown error"): string {
  if (err instanceof Error) return err.message;
  if (typeof err === "string") return err;
  return fallback;
}

/** Decode JWT payload to extract user role (without verifying signature — for UI gating only). */
export function getUserRole(): string | null {
  const token = getAccessToken();
  if (!token) return null;
  try {
    const payload = JSON.parse(atob(token.split('.')[1]));
    return payload.role || null;
  } catch {
    return null;
  }
}

/** Check if current user has at least the given role level. */
export function hasMinRole(minRole: string): boolean {
  const role = getUserRole();
  if (!role) return false;
  const levels: Record<string, number> = { viewer: 0, operator: 1, admin: 2, superadmin: 3 };
  return (levels[role] || 0) >= (levels[minRole] || 0);
}

/**
 * SWR-based API client.
 * Handles JWT auth, token refresh, and standardized error handling.
 */
const API_BASE = ""; // Proxied through Next.js rewrites

interface FetchOptions extends RequestInit {
  params?: Record<string, string | number | undefined>;
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

export function setAccessToken(token: string | null) {
  accessToken = token;
}

export function getAccessToken(): string | null {
  return accessToken;
}

async function refreshAccessToken(): Promise<string | null> {
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
  }
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
  if (!token) return null;
  if (!isTokenExpired(token)) return token;
  // Token expired or about to expire — refresh now
  const newToken = await refreshAccessToken();
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

export async function apiFetch<T = unknown>(
  path: string,
  options: FetchOptions = {}
): Promise<T> {
  const { params, ...fetchOpts } = options;

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

  let resp = await fetch(url, { ...fetchOpts, headers, credentials: "include" });

  // Auto-refresh on 401
  if (resp.status === 401) {
    const newToken = await refreshAccessToken();
    if (newToken) {
      headers["Authorization"] = `Bearer ${newToken}`;
      resp = await fetch(url, { ...fetchOpts, headers, credentials: "include" });
    }
  }

  const json = await resp.json();

  if (!resp.ok || !json.success) {
    throw new ApiError(
      resp.status,
      json.error?.code || "UNKNOWN_ERROR",
      json.error?.message || "An error occurred"
    );
  }

  return json as T;
}

// SWR fetcher
export const swrFetcher = <T = unknown>(url: string) => apiFetch<T>(url);

export { ApiError };
export default apiFetch;

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

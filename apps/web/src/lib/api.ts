import type { ApiErrorBody } from '@ticket/shared';

/** Empty in dev — vite proxies /api to the API. Must be set on Vercel. */
const BASE = import.meta.env.VITE_API_URL ?? '';

// In development an empty BASE is correct: vite proxies to localhost:4000. In
// production it means every request goes to the static host, which answers 404
// for /api/* — an app that looks broken for a reason nobody can see. Say so.
if (import.meta.env.PROD && !BASE) {
  console.error(
    'VITE_API_URL is not set. This build will call its own origin for /api/* ' +
      'and every request will 404. Set it to the API URL in the Vercel project ' +
      'settings and redeploy — it is baked in at build time, so a restart is not enough.',
  );
}

export class ApiClientError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details?: unknown,
  ) {
    super(message);
    this.name = 'ApiClientError';
  }
}

let accessToken: string | null = localStorage.getItem('accessToken');

export const getAccessToken = () => accessToken;

export function setAccessToken(token: string | null) {
  accessToken = token;
  if (token) localStorage.setItem('accessToken', token);
  else localStorage.removeItem('accessToken');
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body) headers.set('Content-Type', 'application/json');
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`);

  const res = await fetch(`${BASE}${path}`, { ...init, headers });

  if (!res.ok) {
    // Every API failure has the same shape — but a proxy or a cold start can
    // still return HTML, so never assume the body parses.
    const body = (await res.json().catch(() => null)) as ApiErrorBody | null;
    throw new ApiClientError(
      res.status,
      body?.error.code ?? 'UNKNOWN',
      body?.error.message ?? `Request failed with ${res.status}.`,
      body?.error.details,
    );
  }

  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(
      path,
      body === undefined ? { method: 'POST' } : { method: 'POST', body: JSON.stringify(body) },
    ),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: 'PATCH', body: JSON.stringify(body) }),
  del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
};

// API client: the single place the frontend talks to the backend.
// All calls go to /api on the same origin (Vite proxies it to the API in development), so the
// HttpOnly session cookie stays first-party. Requests time out; errors become ApiError.

/**
 * Purpose: compute base API path.
 * In production or public mode (or whenever configured with localhost),
 * strictly use same-origin relative '/api' so mobile clients on tunnel URLs never call localhost.
 * Inputs: none.
 * Outputs: string base URL ('/api' or custom non-localhost origin + '/api').
 * Side effects: reads import.meta.env.
 */
function resolveApiBase(): string {
  const envUrl = import.meta.env.VITE_FACESCAN_API_URL;
  if (
    import.meta.env.PROD ||
    !envUrl ||
    envUrl.includes('localhost') ||
    envUrl.includes('127.0.0.1')
  ) {
    return '/api';
  }
  return `${envUrl.replace(/\/$/, '')}/api`;
}

export const API_BASE = resolveApiBase();
const DEFAULT_TIMEOUT_MS = 30_000;

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
  ) {
    super(`API ${status}: ${detail}`);
  }
}

interface RequestOptions {
  method?: 'GET' | 'POST';
  json?: unknown;
  body?: BodyInit;
  headers?: Record<string, string>;
  timeoutMs?: number;
}

function detailOf(payload: unknown): string {
  if (payload && typeof payload === 'object') {
    const record = payload as Record<string, unknown>;
    if (typeof record.detail === 'string') return record.detail;
    if (typeof record.error === 'string') return record.error;
  }
  return 'request_failed';
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), options.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  const headers: Record<string, string> = { ...options.headers };
  let body = options.body;
  if (options.json !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(options.json);
  }
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      method: options.method ?? (body === undefined ? 'GET' : 'POST'),
      headers,
      body,
      credentials: 'same-origin',
      signal: controller.signal,
    });
    const text = await response.text();
    const payload: unknown = text ? JSON.parse(text) : null;
    if (!response.ok) throw new ApiError(response.status, detailOf(payload));
    return payload as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(0, 'network_error');
  } finally {
    clearTimeout(timer);
  }
}

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

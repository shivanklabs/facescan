// Typed access to public build-time env vars. Never put secrets in VITE_* variables.
// The API is reached through /api on the same origin (see lib/apiClient.ts and the Vite proxy).
export const env = {
  apiBase: '/api',
} as const;

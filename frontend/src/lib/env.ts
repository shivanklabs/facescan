// Typed access to public build-time env vars. Never put secrets in VITE_* variables.
const apiUrl: unknown = import.meta.env.VITE_FACESCAN_API_URL;

export const env = {
  apiUrl: typeof apiUrl === 'string' && apiUrl.length > 0 ? apiUrl : 'http://localhost:8000',
} as const;

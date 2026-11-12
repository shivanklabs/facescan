// Entry point: mounts the app shell into #root.
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';

/**
 * Purpose: in development, move 127.0.0.1 to localhost. The API only accepts requests from
 *   the configured origin (http://localhost:5173, also needed for passkeys), so pages opened
 *   on 127.0.0.1 would get 403 cross_origin on every sign-in.
 * Inputs: none. Outputs: true when the page is being redirected.
 * Side effects: may replace the current URL (development builds only).
 */
function redirectLoopbackToLocalhost(): boolean {
  if (!import.meta.env.DEV || window.location.hostname !== '127.0.0.1') return false;
  const url = new URL(window.location.href);
  url.hostname = 'localhost';
  window.location.replace(url.toString());
  return true;
}

if (!redirectLoopbackToLocalhost()) {
  const container = document.getElementById('root');
  if (!container) {
    throw new Error('Root element #root not found');
  }
  createRoot(container).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}

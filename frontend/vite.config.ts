import react from '@vitejs/plugin-react';
import { loadEnv } from 'vite';
import { VitePWA } from 'vite-plugin-pwa';
import { defineConfig } from 'vitest/config';

/**
 * Purpose: Vite + Vitest config. The dev port and API target can be overridden with
 *   FACESCAN_WEB_PORT / FACESCAN_WEB_API_TARGET (e.g. a second dev server beside the first).
 * Inputs: Vite mode. Outputs: config. Side effects: none.
 */
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', 'FACESCAN_WEB_');
  return {
    plugins: [
      react(),
      VitePWA({
        registerType: 'autoUpdate',
        manifest: false, // served from public/manifest.webmanifest
        workbox: {
          // Never cache API responses: photos are served via short-lived signed URLs.
          navigateFallbackDenylist: [/^\/api\//],
          // The on-device blink model (MediaPipe wasm ~13 MB, model ~4 MB) is loaded only when
          // the live-blink scan is used; it is not precached for offline use.
          // three.js (3D gallery) and the blink model are left to the browser cache, not installed.
          globIgnores: [
            '**/*.wasm',
            '**/*.task',
            '**/liveBlink-*.js',
            '**/vision_wasm_*.js',
            '**/three.module-*.js',
          ],
        },
      }),
    ],
    resolve: {
      // '/src/' resolves from the project root in both Vite and Vitest.
      alias: [{ find: /^@\//, replacement: '/src/' }],
    },
    // Dev server listens on this machine only (not the local network).
    server: {
      host: '127.0.0.1',
      port: Number(env.FACESCAN_WEB_PORT ?? '5173'),
      strictPort: true,
      // Same-origin API: /api/* goes to the FastAPI process, so the session cookie is first-party.
      proxy: {
        '/api': {
          target: env.FACESCAN_WEB_API_TARGET ?? 'http://127.0.0.1:8000',
          rewrite: (path: string) => path.replace(/^\/api/, ''),
        },
      },
    },
    test: {
      environment: 'jsdom',
      setupFiles: ['./tests/setup.ts'],
      include: ['tests/**/*.test.{ts,tsx}', 'src/**/*.test.{ts,tsx}'],
    },
  };
});

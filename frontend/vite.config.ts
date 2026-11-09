import react from '@vitejs/plugin-react';
import { VitePWA } from 'vite-plugin-pwa';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      manifest: false, // served from public/manifest.webmanifest
      workbox: {
        // Never cache API responses: photos are served via short-lived signed URLs.
        navigateFallbackDenylist: [/^\/api\//],
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
    port: 5173,
    strictPort: true,
    // Same-origin API: /api/* goes to the FastAPI process, so the session cookie is first-party.
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./tests/setup.ts'],
    include: ['tests/**/*.test.{ts,tsx}', 'src/**/*.test.{ts,tsx}'],
  },
});

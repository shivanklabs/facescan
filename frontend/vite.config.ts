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
  server: { host: true, port: 5173, strictPort: true },
  test: {
    environment: 'jsdom',
    setupFiles: ['./tests/setup.ts'],
    include: ['tests/**/*.test.{ts,tsx}', 'src/**/*.test.{ts,tsx}'],
  },
});

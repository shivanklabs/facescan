import { readFileSync, writeFileSync } from 'node:fs';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { resolve } from 'node:path';
import react from '@vitejs/plugin-react';
import { loadEnv, type Plugin } from 'vite';
import { VitePWA } from 'vite-plugin-pwa';
import { defineConfig } from 'vitest/config';

// ---------------------------------------------------------------- Edit mode (dev server only)
// The browser's Edit mode (src/components/site/EditMode.tsx) saves text into the language
// files and sizes into src/styles/site-edits.css through two local-only routes. They exist
// only on `vite` dev (apply: 'serve'); the built website has neither the routes nor the panel.

const LANGUAGES = ['en', 'hi'] as const;
const STYLE_PROPS = [
  'height',
  'min-height',
  'width',
  'max-width',
  'font-size',
  'border-radius',
  'padding',
  'gap',
] as const;
const SELECTOR = /^(\.[a-z][a-z0-9-]{0,40}){1,3}$/;
const LENGTH = /^\d{1,4}(\.\d{1,2})?(px|rem|%)$/;
const KEY = /^[A-Za-z0-9_]{1,40}(\.[A-Za-z0-9_]{1,40}){0,5}$/;
const MAX_BODY = 10_000;

type Json = string | number | boolean | null | Json[] | { [key: string]: Json };

/**
 * Purpose: replace one existing text in a language file (never adds keys, never other types).
 * Inputs: the file's JSON text, a dotted key, the new text (1-500 characters).
 * Outputs: the new JSON text (2-space indent, trailing newline). Throws Error when the key is
 *   unknown, not a text, or the value is empty / too long.
 * Side effects: none.
 */
export function applyTextEdit(fileText: string, key: string, value: string): string {
  if (!KEY.test(key)) throw new Error('bad key');
  const text = value.trim();
  if (text.length < 1 || text.length > 500) throw new Error('text must be 1-500 characters');
  const data = JSON.parse(fileText) as Json;
  const parts = key.split('.');
  const last = parts.pop() ?? '';
  let node: Json = data;
  for (const part of parts) {
    if (typeof node !== 'object' || node === null || Array.isArray(node) || !(part in node)) {
      throw new Error('unknown key');
    }
    node = node[part] as Json;
  }
  if (typeof node !== 'object' || node === null || Array.isArray(node))
    throw new Error('unknown key');
  if (typeof node[last] !== 'string') throw new Error('unknown key');
  node[last] = text;
  return JSON.stringify(data, null, 2) + '\n';
}

/**
 * Purpose: set one CSS property for one class selector in site-edits.css (adds or updates
 *   the rule; other rules untouched). Only simple class selectors, a short list of size
 *   properties and plain lengths (px / rem / %) are accepted.
 * Inputs: the CSS file text, selector, property, value. Outputs: the new CSS text.
 *   Throws Error for anything outside those rules. Side effects: none.
 */
export function applyStyleEdit(css: string, selector: string, prop: string, value: string): string {
  if (!SELECTOR.test(selector)) throw new Error('only class selectors like .st-card');
  if (!(STYLE_PROPS as readonly string[]).includes(prop)) throw new Error('property not allowed');
  if (!LENGTH.test(value)) throw new Error('use a length like 240px, 1.2rem or 50%');
  const escaped = selector.replace(/[.]/g, '\\.');
  const rule = new RegExp(`^${escaped} \\{\\n([^}]*)\\}`, 'm');
  const match = rule.exec(css);
  const line = `  ${prop}: ${value};`;
  if (!match) return `${css.trimEnd()}\n\n${selector} {\n${line}\n}\n`;
  const body = (match[1] ?? '')
    .split('\n')
    .filter((l) => l.trim() && !l.trim().startsWith(`${prop}:`));
  body.push(line);
  return css.replace(rule, `${selector} {\n${body.join('\n')}\n}`);
}

/**
 * Purpose: read a small JSON request body (Edit mode saves). Inputs: request.
 * Outputs: the parsed object. Throws Error when too large or not an object.
 * Side effects: reads the request stream.
 */
function readBody(req: IncomingMessage): Promise<Record<string, unknown>> {
  return new Promise((done, fail) => {
    let raw = '';
    req.on('data', (chunk: Buffer) => {
      raw += chunk.toString('utf8');
      if (raw.length > MAX_BODY) fail(new Error('too large'));
    });
    req.on('end', () => {
      try {
        const value: unknown = JSON.parse(raw);
        if (typeof value !== 'object' || value === null || Array.isArray(value)) {
          throw new Error('not an object');
        }
        done(value as Record<string, unknown>);
      } catch (error) {
        fail(error instanceof Error ? error : new Error('bad body'));
      }
    });
  });
}

/**
 * Purpose: the dev-only routes Edit mode saves through: POST /__edit/text {lang, key, value}
 *   and POST /__edit/style {selector, prop, value}. Refuses other methods and requests from
 *   another origin; the dev server itself listens on 127.0.0.1 only.
 * Inputs: none. Outputs: a Vite plugin (serve only).
 * Side effects: writes src/language-files/{en,hi}.json and src/styles/site-edits.css.
 */
function editModeSaver(): Plugin {
  const root = process.cwd();
  const send = (res: ServerResponse, status: number, body: object) => {
    res.statusCode = status;
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify(body));
  };
  const guard = (req: IncomingMessage): boolean => {
    const origin = req.headers.origin;
    return req.method === 'POST' && (!origin || origin === `http://${req.headers.host ?? ''}`);
  };
  return {
    name: 'facescan-edit-mode',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use('/__edit/text', (req, res) => {
        if (!guard(req)) return send(res, 403, { error: 'forbidden' });
        readBody(req)
          .then(({ lang, key, value }) => {
            if (
              !LANGUAGES.some((l) => l === lang) ||
              typeof key !== 'string' ||
              typeof value !== 'string'
            ) {
              throw new Error('bad request');
            }
            const file = resolve(root, 'src/language-files', `${String(lang)}.json`);
            writeFileSync(file, applyTextEdit(readFileSync(file, 'utf8'), key, value), 'utf8');
            send(res, 200, { saved: `src/language-files/${String(lang)}.json`, key });
          })
          .catch((error: unknown) =>
            send(res, 422, { error: error instanceof Error ? error.message : 'bad request' }),
          );
      });
      server.middlewares.use('/__edit/style', (req, res) => {
        if (!guard(req)) return send(res, 403, { error: 'forbidden' });
        readBody(req)
          .then(({ selector, prop, value }) => {
            if (
              typeof selector !== 'string' ||
              typeof prop !== 'string' ||
              typeof value !== 'string'
            ) {
              throw new Error('bad request');
            }
            const file = resolve(root, 'src/styles/site-edits.css');
            writeFileSync(
              file,
              applyStyleEdit(readFileSync(file, 'utf8'), selector, prop, value),
              'utf8',
            );
            send(res, 200, { saved: 'src/styles/site-edits.css', selector, prop });
          })
          .catch((error: unknown) =>
            send(res, 422, { error: error instanceof Error ? error.message : 'bad request' }),
          );
      });
    },
  };
}

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
      editModeSaver(),
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

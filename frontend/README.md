# frontend/

React + TypeScript + Vite installable PWA, mobile-first. Talks only to the FaceScan API.

Must not: call third-party APIs, store selfies (camera frames stay in memory and are sent once
to the API), contain secrets (`VITE_*` variables are public).

```bash
npm install && npm run dev   # http://localhost:5173
npm run lint && npm run typecheck && npm test
```

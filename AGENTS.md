# UNIVERSAL PRODUCTION & SECURITY GUARDRAILS

## 1. Code Transparency & Anti-Regression
- Before editing, state which file and function is being modified and its exact impact.
- Add a concise comment block above every new or updated function explaining Purpose, Inputs, Outputs, and Side Effects.
- NEVER delete or alter unrelated working code, routes, or state logic without explicit confirmation.

## 2. Security Layer (Zero-Trust)
- NEVER hardcode API keys, secrets, JWTs, or database URIs; strictly use environment variables.
- Validate and sanitize 100% of incoming payloads (body, query, params) using strict schema validators (e.g., Zod).
- Enforce authentication, authorization, and rate-limiting on sensitive endpoints; never return raw stack traces.

## 3. Server & API Architecture
- Strictly separate Routing, Controllers (request/response only), and Services (core business logic).
- Wrap all async handlers in centralized error-handling middleware with exact HTTP status codes.

## 4. Database & Models Layer
- Enforce strict schemas with explicit types, required constraints, defaults, and indexes on lookup fields.
- Prevent N+1 queries; use projections (.select) to exclude sensitive fields like password hashes by default.

## 5. Core Logic, State & Type Safety
- Maintain 100% strict TypeScript/type safety; using `any` or suppressing compiler errors is forbidden.
- Guard against edge cases: null, undefined, empty arrays, race conditions, and memory leaks.

## 6. AI Agent & LLM Integration Layer
- Enforce strict request timeouts, retry logic, and max-token caps on all external AI/API calls.
- Validate all LLM outputs against a strict JSON schema before parsing or storing in the database.

## 7. Data Integrity & No Data Loss
- The app NEVER deletes or modifies uploaded photos, events or user data.
- One exception (owner decision, 13 Nov 2026, DPDP right to erasure): a person may erase their
  OWN account and their own data (Privacy > Delete my account, typed confirmation). Event photos
  are never deleted; owners of events cannot self-delete; one audit line without personal data
  is kept. Nothing is ever deleted automatically or by anyone else.
- The only automatic cleanups allowed are temporary upload spool files and the 7-day attempt and candidate records already approved.
- A photo not reaching a user is a bug; losing data is never acceptable.

# FaceScan: instructions for AI coding agents and humans

FaceScan is an event-photo discovery web app: a guest takes a live selfie and finds their own
photos from an event. Proprietary code. First market: India (DPDP Act 2023).

## Security invariants (future code must never break these)
1. Every query touching photos, faces, clusters, matches or claims is scoped to one `event_id`,
   and Postgres row-level security is on.
2. A guest's own face search may cover all searchable events (milestone 7, owner decision
   12 Nov 2026), but each event is still searched separately, in its own transaction, with its
   own key and row-level security. Never one combined index across events.
3. Selfies and liveness frames are processed in memory only; never written to disk, storage or logs.
4. Face embeddings are stored only encrypted with the event's key and are never returned by any API.
5. Only guests can trigger a face search, and only for their own face. Organisers, photographers
   and admins cannot.
6. Photos are served only through short-lived signed URLs issued after a permission check;
   buckets stay private; EXIF is stripped from every derivative.
7. Logs never contain images, vectors, OTPs, tokens or email addresses.
8. Precision over recall: a photo is released only when the configured release rules pass.
9. Only models listed in THIRD_PARTY_LICENSES.md; never InsightFace `buffalo_*` packs; never
   scrape faces from the internet.
10. No paid service calls; every external dependency goes through `backend/app/interfaces`.

The face is a search query, never a password. One sign-in for everyone: a passkey (WebAuthn);
there are no passwords. Accounts are created with a name, an email (stored only as an HMAC for
lookup and AES-GCM ciphertext; shown only to admins for approvals) and a passkey (owner
decision 13 Nov 2026). The server decides role and status; the browser never does.

## Working rules
- **Ask before every technology, tool, library or service choice.** Give 2-3 options with a
  recommendation and the trade-offs, then wait for the owner's answer. Choices already confirmed
  in a task message do not need asking again.
- **Ask before adding any new dependency** (npm, PyPI, model). Pin exact versions.
- Ponytail applies (prefer the least code, reuse what exists). FaceScan's security invariants, tests, audit logging, consent and age checks, and the ask-before-choices rule always take priority. Never remove or weaken a security or privacy check, a test, or an input validation to reduce code.
- Local development runs natively on Windows 10 (PowerShell) and Linux. No Docker, WSL or make;
  scripts must not use bash-only syntax. Production deployment is decided in Phase 3.
- Scaffolded modules contain only a docstring and a TODO until a feature is implemented.
- Secrets live only in `.env` (git-ignored). Never hardcode keys, URLs with credentials or tokens.
- Validate every request payload with Pydantic schemas; never return stack traces.
- Strict typing: no `Any`, no `# type: ignore`, no `@ts-ignore`.
- The Phase 3 AI agent (`ai/agent/`) may only call public API tools, never face search or vectors.
- Sign-in: one passkey sign-in for all roles; the server sends each person to their
  dashboard from users.role + users.status. Registration (POST /auth/register): name, email,
  type guest / organiser / photographer (admin is refused); no account exists before the
  passkey verifies; guests start active, organisers and photographers pending until an admin
  approves them (/admin/accounts). Admins only from the server bootstrap
  (`uv run tasks staff-key --role admin`, refused while an active admin exists unless
  `--force`). Staff keys (single use, 7 days, only a SHA-256 stored) stay as a pre-approved
  invite. Suspended accounts are refused on every route except /auth/me, sign-out and delete.
  Server-side sessions in PostgreSQL (HttpOnly, Secure, SameSite=Strict cookies). Staff pages
  need a passkey session. Event ownership and photographer assignment come from
  event_members (role organiser / photographer, assigned_by) and are checked on every
  request, with row-level security underneath.
- Folders are lowercase. Python packages and modules use snake_case; multi-word non-Python
  folders use kebab-case.
- Name by responsibility, not technology: `interfaces/face_analyzer.py`, not `interfaces/onnx.py`.
  Adapters are named `<vendor>_<port>.py` (`local_filesystem_storage.py`, `env_master_key.py`).
- Backend module files have fixed roles: `router.py` (HTTP), `service.py` (business logic),
  `schemas.py` (API models), `models.py` (DB tables), `repository.py` (queries).
- React: components and pages are `PascalCase.tsx`, route components end in `Page`;
  hooks are `useSomething.ts`; utilities are `camelCase.ts`.
- Tests mirror source paths: `test_<module>.py`, `Component.test.tsx`.
- Migrations: `<yyyymmdd_hhmm>_<verb>_<object>.py`. ADRs: `NNNN-short-title.md`.
- Environment variables: `FACESCAN_<AREA>_<NAME>`, e.g. `FACESCAN_DB_URL`, `FACESCAN_STORAGE_ENDPOINT`.

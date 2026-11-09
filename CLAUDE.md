# FaceScan: instructions for AI coding agents and humans

FaceScan is an event-photo discovery web app: a guest takes a live selfie and finds their own
photos from an event. Proprietary code. First market: India (DPDP Act 2023).

## Security invariants (future code must never break these)
1. Every query touching photos, faces, clusters, matches or claims is scoped to one `event_id`,
   and Postgres row-level security is on.
2. No search across events, ever.
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
10. No paid service calls; every external dependency goes through `backend/app/ports`.

The face is a search query, never a password. Login is passkeys (WebAuthn) + email magic link.

## Working rules
- **Ask before every technology, tool, library or service choice.** Give 2-3 options with a
  recommendation and the trade-offs, then wait for the owner's answer. Choices already confirmed
  in a task message do not need asking again.
- **Ask before adding any new dependency** (npm, PyPI, model). Pin exact versions.
- Local development runs natively on Windows 10 (PowerShell) and Linux. No Docker, WSL or make;
  scripts must not use bash-only syntax. Production deployment is decided in Phase 3.
- Scaffolded modules contain only a docstring and a TODO until a feature is implemented.
- Secrets live only in `.env` (git-ignored). Never hardcode keys, URLs with credentials or tokens.
- Validate every request payload with Pydantic schemas; never return stack traces.
- Strict typing: no `Any`, no `# type: ignore`, no `@ts-ignore`.
- The Phase 3 AI agent (`ai/agent/`) may only call public API tools, never face search or vectors.
- Sign-in: email magic links + passkeys; server-side sessions in PostgreSQL (HttpOnly, Secure,
  SameSite=Strict cookies). Organisers and photographers need a passkey session. Event roles
  come from event_members and are checked on every request. Guest face search runs only in
  dev until a liveness adapter exists.

## Dev commands
Requirements: Git, [uv](https://docs.astral.sh/uv/) (installs Python 3.12), Node.js LTS, a native
PostgreSQL 15+ server. Commands are the same in PowerShell and on Linux (`scripts/tasks.py`).

| Command | What it does |
|---|---|
| `uv run tasks setup` | first-time setup: `.env` with random local secrets, `npm ci`, pre-commit, `var/storage` |
| `uv run tasks db-init` | create the `facescan` role and database (`database/setup/create_dev_database.sql`); asks for the PostgreSQL superuser password |
| `uv run tasks db-migrate` | apply database migrations (`alembic upgrade head`) as the `facescan` role |
| `uv run tasks dev` | run api (uvicorn --reload), worker and Vite together; Ctrl+C stops all three |
| `uv run tasks lint` | ruff + mypy (backend, ai, scripts), eslint + tsc (frontend), then `pre-commit run --all-files` (gitleaks secret scan, licence check, `.env` block); `--only python\|frontend`. Run before every commit |
| `uv run tasks format` | ruff format + prettier |
| `uv run tasks test` | pytest (backend, ai) + vitest (frontend); `--only python\|frontend` |
| `uv run tasks models` | download model weights listed in `ai/models/models.lock.json` and verify SHA-256 |
| `uv run tasks licences` | `scripts/check_licences.py`: fails on forbidden models, files or packages |
| `uv run --env-file .env facescan-ai <index\|match\|search\|label\|bench\|blur\|clean>` | face pipeline, matching engine, labeler and benchmark (see `ai/README.md`) |

Open the app at http://localhost:5173 (passkeys need the `localhost` host; the API is reached
through Vite's `/api` proxy). API health: http://127.0.0.1:8000/health. Magic links are printed
in the API terminal in dev. Put your email in `FACESCAN_AUTH_ORGANISER_EMAILS` to create events.
Never commit `.env` or the PostgreSQL superuser password.

## Folder map
```
config/      adapters.yaml (port -> adapter per default/region/plan), policies/<country>.yaml
frontend/    React + TS + Vite PWA (pages/, features/, components/ui/, lib/, i18n/)
backend/     FastAPI modular monolith; two processes: api (app.main) and worker (app.worker)
  app/core/      config, logging (redaction), security, errors, rate limiting
  app/db/        session, declarative base, event scope (RLS)
  app/ports/     one Protocol per external dependency
  app/adapters/  free implementations of the ports + registry.py
  app/modules/   identity, events, ingest, vision_pipeline, matching, gallery, consent, audit, admin
  app/jobs/      job type -> handler registry
ai/          facescan_ai: pure Python face library (no web, no database)
database/    alembic migrations, RLS policy templates, setup/ (dev role + database), seeds, schema.md
docs/        architecture + ADRs, security, legal, product, runbooks
scripts/     tasks.py (`uv run tasks`), check_licences.py
var/         local runtime data, git-ignored (storage/<bucket>/<event_id>/)
```

## Naming rules
- Folders are lowercase. Python packages and modules use snake_case; multi-word non-Python
  folders use kebab-case.
- Name by responsibility, not technology: `ports/face_embedder.py`, not `ports/onnx.py`.
  Adapters are named `<vendor>_<port>.py` (`local_filesystem_storage.py`, `env_master_key.py`).
- Backend module files have fixed roles: `router.py` (HTTP), `service.py` (business logic),
  `schemas.py` (API models), `models.py` (DB tables), `repository.py` (queries).
- React: components and pages are `PascalCase.tsx`, route components end in `Page`;
  hooks are `useSomething.ts`; utilities are `camelCase.ts`.
- Tests mirror source paths: `test_<module>.py`, `Component.test.tsx`.
- Migrations: `<yyyymmdd_hhmm>_<verb>_<object>.py`. ADRs: `NNNN-short-title.md`.
- Environment variables: `FACESCAN_<AREA>_<NAME>`, e.g. `FACESCAN_DB_URL`, `FACESCAN_STORAGE_ENDPOINT`.

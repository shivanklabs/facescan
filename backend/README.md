# backend/
FastAPI modular monolith. One codebase, two processes:
- `api`: `uvicorn app.main:create_app --factory`
- `worker`: `python -m app.worker` (claims jobs from the Postgres `jobs` table with
  `SELECT ... FOR UPDATE SKIP LOCKED`; retries with backoff; failed after the last attempt)

Must not: call paid services, import vendor SDKs outside `app/connectors/`, store selfies, log
biometrics or personal data. See security invariants in `/CLAUDE.md`.

## Database
`uv run tasks db-init` (once, as the PostgreSQL superuser), then `uv run tasks db-migrate`.
Every transaction sets its RLS scope (`app/db/event_isolation.py`); see `database/schema.md`.

## Sign-in and roles
- Email magic links (single use, 15 minutes, stored as a hash). In dev the console email adapter
  prints them in the API terminal; it refuses to run outside dev.
- Passkeys (py_webauthn): offered to guests after the first sign-in; required for organisers
  and photographers. Server-side sessions in PostgreSQL; the cookie `fs_session` is HttpOnly,
  Secure and SameSite=Strict, rotated at every sign-in; sign out and sign out everywhere.
- Only emails in `FACESCAN_AUTH_ORGANISER_EMAILS` can create events. Organisers add
  photographers by email. Roles come from `event_members` and are checked on every request.
- State-changing requests from another origin are refused (on top of SameSite=Strict).

## Main routes
| Route | Who |
|---|---|
| `POST /auth/magic-link`, `/auth/magic-link/verify`, `/auth/passkey/*`, `/auth/me`, `/auth/age`, `/auth/logout`, `/auth/logout-everywhere` | everyone |
| `POST /events`, `GET /events`, `GET /events/{id}` | organisers (create), members (read) |
| `POST /events/{id}/members`, `/invites`, `/invites/{iid}/revoke`, `/collections/{cid}/approve`, `GET /collections` | organisers |
| `POST /events/{id}/photos` | photographers, organisers |
| `POST /invites/{id}/resolve`, `/invites/{id}/join` | anyone with the invite link (join needs sign-in) |
| `GET /consent/notice`, `GET /events/{id}/guest-status`, `POST /events/{id}/consent`, `/consent/withdraw`, `GET /me/consents` | guests |
| `POST /events/{id}/search` (raw `application/x-facescan-frames`) | guests with consent, adults, dev only for now |
| `GET /events/{id}/gallery`, `GET /events/{id}/gallery/{pid}/download` | the guest, own matches only |

# backend/
FastAPI modular monolith. One codebase, two processes:
- `api`: `uvicorn app.main:create_app --factory`
- `worker`: `python -m app.worker` (claims jobs from the Postgres `jobs` table with
  `SELECT ... FOR UPDATE SKIP LOCKED`; retries with backoff; failed after the last attempt)

Must not: call paid services, import vendor SDKs outside `app/connectors/`, store selfies, log
biometrics or personal data. See security invariants in `/CLAUDE.md`.

## Database
`uv run tasks db-init` (once, as the PostgreSQL superuser), then `uv run tasks db-migrate`.
Every transaction sets its RLS scope (`app/db/event_isolation.py`); see `database/tables_guide.md`.

## Sign-in and roles
- One sign-in for everyone: a passkey. The server reads users.role (guest / organiser /
  photographer / admin) and users.status (active / pending / rejected / suspended) and sends
  each person to their dashboard. Registration: name + email + type (never admin) + passkey;
  organisers and photographers wait for an admin's approval. Staff keys still work as a
  pre-approved invite; the first admin comes only from `uv run tasks staff-key --role admin`.
- Passkeys (py_webauthn): the only sign-in for guests; required for organisers,
  photographers and admins. Server-side sessions in PostgreSQL; the cookie `fs_session` is HttpOnly,
  Secure and SameSite=Strict, rotated at every sign-in; sign out and sign out everywhere.
- Event creation needs an active organiser (or admin); event access needs an event_members
  row (organiser = owner, photographer = assigned by the owner); 404 hides other events.
- State-changing requests from another origin are refused (on top of SameSite=Strict).

## Main routes
| Route | Who |
|---|---|
| `POST /auth/register/options`, `POST /auth/register`, `/auth/passkey/*`, `/auth/staff-key`, `/auth/me`, `/auth/age`, `/auth/logout`, `/auth/logout-everywhere`, `/auth/delete-account` | everyone (register: guest / organiser / photographer only) |
| `/auth/staff-keys` | admins (organiser / admin keys), organisers (photographer keys for their event) |
| `GET /admin/accounts?status=&role=`, `POST /admin/accounts/{id}/status`, `GET /admin/health` | active admins (never their own or another admin's account) |
| `POST /events/{id}/photographers` (by email), `GET /events/{id}/photographers` | the event's organiser |
| `POST /events`, `GET /events`, `GET /events/{id}` | organisers (create), members (read) |
| `POST /events/{id}/members`, `/invites`, `/invites/{iid}/revoke`, `/collections/{cid}/approve`, `GET /collections` | organisers |
| `POST /events/{id}/photos` | photographers, organisers |
| `POST /invites/{id}/resolve`, `/invites/{id}/join` | anyone with the invite link (join needs sign-in) |
| `GET /consent/notice`, `POST /events/{id}/consent`, `/consent/withdraw`, `GET /me/consents` | guests |
| `POST /discover/search` (raw `application/x-facescan-frames`; one scan, every searchable event) | signed-in guests (staff and withdrawn events skipped) |
| `GET /discover/photos`, `POST /events/{id}/gallery/{pid}/not-me`, `GET /events/{id}/gallery/{pid}/download` | the guest, own matches only |

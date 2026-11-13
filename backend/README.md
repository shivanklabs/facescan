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
- Guests: passkey-only sign-up and sign-in (no email). Staff: one-time staff keys, then a
  passkey.
- Passkeys (py_webauthn): the only sign-in for guests; required for organisers,
  photographers and admins. Server-side sessions in PostgreSQL; the cookie `fs_session` is HttpOnly,
  Secure and SameSite=Strict, rotated at every sign-in; sign out and sign out everywhere.
- Event creation and admin rights come from redeemed staff keys (users flags).
- State-changing requests from another origin are refused (on top of SameSite=Strict).

## Main routes
| Route | Who |
|---|---|
| `/auth/guest/passkey/*`, `/auth/staff-key`, `/auth/staff-keys`, `/auth/passkey/*`, `/auth/me`, `/auth/age`, `/auth/logout`, `/auth/logout-everywhere` | everyone |
| `POST /events`, `GET /events`, `GET /events/{id}` | organisers (create), members (read) |
| `POST /events/{id}/members`, `/invites`, `/invites/{iid}/revoke`, `/collections/{cid}/approve`, `GET /collections` | organisers |
| `POST /events/{id}/photos` | photographers, organisers |
| `POST /invites/{id}/resolve`, `/invites/{id}/join` | anyone with the invite link (join needs sign-in) |
| `GET /consent/notice`, `GET /events/{id}/guest-status`, `POST /events/{id}/consent`, `/consent/withdraw`, `GET /me/consents` | guests |
| `POST /events/{id}/search` (raw `application/x-facescan-frames`) | guests with consent, adults, dev only for now |
| `GET /events/{id}/gallery`, `GET /events/{id}/gallery/{pid}/download` | the guest, own matches only |

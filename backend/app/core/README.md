# core/
Cross-cutting infrastructure. Must not contain business rules.

## Authorisation notes (moved from the old `security.py`)
Sign-in, sessions and role checks live in `login_sessions.py` and `request_checks.py`:
- every event route checks the caller's role in event_members inside the event's own RLS
  transaction (`require_role`);
- organisers and photographers need a strong sign-in session (passkey or Google);
- only guests can run a face search, never staff of the same event (invariant 5,
  `app/features/face_matching/service.py`).

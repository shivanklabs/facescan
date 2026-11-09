"""Authorisation notes.

Sign-in, sessions and role checks live in app/core/auth.py and app/core/deps.py:
- every event route checks the caller's role in event_members inside the event's own RLS
  transaction (require_role);
- organisers and photographers need a passkey session (PasskeyUser);
- only guests can run a face search, never staff of the same event (invariant 5,
  app/modules/matching/service.py).
The temporary dev admin token of milestone 3 has been removed.
"""

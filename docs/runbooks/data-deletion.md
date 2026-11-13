# Data deletion

FaceScan never deletes data by itself or on anyone else's behalf. The one exception is a
person erasing their own account (DPDP right to erasure; owner decision 13 Nov 2026).

## Delete my account (self-service)
- Where: Privacy page > "Delete my account"; the person types `DELETE`.
- API: `POST /auth/delete-account` with `{"confirm": "DELETE"}` (signed in).
- Refused (409) while the person organises an event: hand the event to another owner first.
- One transaction deletes the `users` row; `ON DELETE CASCADE` removes the person's passkeys,
  sessions, sign-in challenges, consents, matches, "Not me" links (cannot_links),
  user_templates, claims, claim/search/scene-search records, unfinished upload sessions and
  event memberships. Staff keys they made or used keep the row with the user set to NULL.
- Also removed: the account's email (hash + ciphertext) and display name.
- Kept: every event photo, face and cluster (they belong to the event), and one audit row
  `auth.account_deleted` with actor `system:account-erasure` and no id or personal data.
- The passkey may stay in the person's phone password manager; the goodbye screen tells them
  to remove it there (the server cannot).

## Withdrawing one event (not deletion)
- Privacy page > Turn off: matches are marked `withdrawn`, consent closed, new scans skip the
  event. Nothing is deleted; Turn back on restores it.

## Verification
- Tests: `backend/tests/integration/test_discover.py::test_delete_my_account_erases_my_data_and_keeps_event_photos`
  counts the person's rows in every event before and after, checks photo counts are
  unchanged, the passkey no longer signs in and the audit row carries no id.

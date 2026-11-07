# database/
Alembic migrations, RLS policy templates, dev seeds and the planned schema.

Must not: contain real personal data or real faces; disable RLS; add tables without `event_id`
(except `users` and `passkeys`). Migration names: `<yyyymmdd_hhmm>_<verb>_<object>.py`.

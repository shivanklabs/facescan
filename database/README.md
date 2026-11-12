# database/
Alembic migrations, RLS policy templates, dev database setup and the schema guide.

Must not: contain real personal data or real faces; disable RLS; add tables without `event_id`
(except `users` and `passkeys`). Migration names: `<yyyymmdd_hhmm>_<verb>_<object>.py`.

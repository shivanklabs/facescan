# database/setup/
One-time SQL for a native PostgreSQL server (no containers).

- `create_dev_database.sql`: creates the `facescan` role (no superuser, no `BYPASSRLS`) and
  database from the `FACESCAN_DB_*` values in `.env`. Run with `uv run tasks db-init`; psql asks
  for the PostgreSQL superuser password, which is never stored.

Must not: contain passwords, create extensions that are not needed, or grant `BYPASSRLS`.

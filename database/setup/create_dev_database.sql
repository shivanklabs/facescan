-- Creates the FaceScan development role and database on a native PostgreSQL server.
--
-- Run it with `uv run tasks db-init`, which loads .env into the environment and calls psql as
-- the PostgreSQL superuser (psql asks for that password; it is never stored).
-- Values come from the environment, not the command line, so the password is not visible in
-- the process list:
--   FACESCAN_DB_USER, FACESCAN_DB_PASSWORD, FACESCAN_DB_NAME
-- Requires psql 15 or newer (\getenv). Safe to re-run: existing objects are kept and the role's
-- password is reset to the value in .env.
--
-- Row-level security: the app role is NOBYPASSRLS and NOSUPERUSER, so the per-event RLS policies
-- (database/security_rules/event_isolation.sql, FORCE ROW LEVEL SECURITY) apply to it.

\set ON_ERROR_STOP on

\getenv db_user FACESCAN_DB_USER
\getenv db_password FACESCAN_DB_PASSWORD
\getenv db_name FACESCAN_DB_NAME

-- Refuse to run with missing values or the .env.example placeholder.
SELECT (:'db_user' = '' OR :'db_name' = '' OR :'db_password' = ''
        OR :'db_password' LIKE 'CHANGE_ME%') AS bad_input \gset
\if :bad_input
  \echo 'FACESCAN_DB_USER, FACESCAN_DB_NAME and FACESCAN_DB_PASSWORD must be set in .env (no CHANGE_ME).'
  \quit
\endif

SELECT format(
    'CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
    :'db_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'db_user') \gexec

SELECT format(
    'ALTER ROLE %I WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'db_user', :'db_password') \gexec

SELECT format(
    'CREATE DATABASE %I OWNER %I ENCODING %L TEMPLATE template0',
    :'db_name', :'db_user', 'UTF8')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db_name') \gexec

SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'db_name') \gexec

\echo 'FaceScan dev role and database are ready.'

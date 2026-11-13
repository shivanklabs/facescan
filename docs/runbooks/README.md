# runbooks/
Step-by-step operational procedures.

## First admin (bootstrap)
There is no public way to become an admin. On the server, with `.env` in place and the
database migrated:

    uv run tasks staff-key --role admin

It prints a one-time key (valid 7 days, works once; only its SHA-256 is stored). Open the site,
choose "Have a staff key?", paste it, then add a passkey when asked. Running the command again
is refused while an active admin exists; use `--force` only to add a second admin or when the
admin lost every passkey. Never paste keys into chat, tickets or git.

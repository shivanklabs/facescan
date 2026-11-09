# FaceScan

Event-photo discovery: guests find their own photos from an event by taking a live selfie.
Proprietary. See `CLAUDE.md` for security invariants, naming rules and the folder map.

## Requirements
Development runs natively on Windows 10 and Linux (no Docker, WSL or make).
- Git, Node.js LTS
- [uv](https://docs.astral.sh/uv/) (it installs Python 3.12: `uv python install 3.12`)
- PostgreSQL 15 or newer running locally (Windows: the official installer, running as a service;
  Linux: the distribution package). If `psql` is not on PATH, set `FACESCAN_DB_PSQL_DIR` in `.env`.

## Quick start (PowerShell or Linux shell)
```
uv run tasks setup      # .env with random local secrets, npm ci, pre-commit hooks
uv run tasks db-init    # creates the facescan role + database; asks for the postgres password
uv run tasks models     # downloads model weights (verified against models.lock.json)
uv run tasks dev        # api :8000 (auto-reload), worker, Vite :5173; Ctrl+C stops all three
```
Other tasks: `lint`, `format`, `test`, `licences` (`uv run tasks --help`).
Run `uv run tasks lint` before every commit: it ends with `pre-commit run --all-files`, which
includes a gitleaks secret scan (pre-commit hooks are not auto-installed when git uses a global
`core.hooksPath`).

- API health: http://127.0.0.1:8000/health
- Frontend: http://127.0.0.1:5173 (both dev servers listen on this machine only)
- Stored files: `var/storage/<bucket>/<event_id>/` (git-ignored), served only through signed URLs.

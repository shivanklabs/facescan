# FaceScan

Event-photo discovery: guests find their own photos from an event by taking a live selfie.
Proprietary. See `CLAUDE.md` for security invariants, naming rules and the folder map.

## Quick start
```bash
make setup      # creates .env from .env.example, installs Python (uv) and Node deps
make models     # downloads model weights (verified against models.lock.json)
make dev        # docker compose: postgres+pgvector, minio, api, worker, frontend
```
Requirements: Docker, GNU make, [uv](https://docs.astral.sh/uv/), Node 22+, Python 3.12.

- API health: http://localhost:8000/health
- Frontend: http://localhost:5173

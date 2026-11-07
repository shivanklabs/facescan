# backend/
FastAPI modular monolith. One codebase, two processes:
- `api`: `uvicorn app.main:create_app --factory`
- `worker`: `python -m app.worker` (polls the Postgres `jobs` table)

Must not: call paid services, import vendor SDKs outside `app/adapters/`, store selfies, log
biometrics or personal data. See security invariants in `/CLAUDE.md`.

# System design

```
browser (PWA) ──HTTPS──> api (FastAPI) ──> PostgreSQL 15+ (RLS per event)
                            │                 ▲
                            │                 │ jobs table
                            ▼                 │
              ObjectStorage port <──── worker (same codebase) ── facescan_ai (ONNX, CPU)
              (dev: local filesystem, signed URLs served by the api)
```

- **Modular monolith** (ADR 0001): modules identity, events, ingest, vision_pipeline, matching,
  gallery, consent, audit, admin. Two processes: api and worker.
- **Ports and adapters** (ADR 0003): every external dependency behind `app/ports`; adapters chosen
  via `settings/services.yaml`.
- **Face as query** (ADR 0002): selfies search one event; login is passkeys/magic links.
- **Encrypted in-memory search** (ADR 0004). No pgvector.
- **Storage**: `local_filesystem` adapter by default (`var/storage/<bucket>/<event_id>/`, random
  keys, HMAC-signed URLs with a short expiry, issued after the permission check).
  `s3_compatible` is a placeholder.

## Environments
- **Development**: native on Windows 10 and Linux, no containers. PostgreSQL runs as a local
  service; `uv run tasks dev` starts the api, worker and Vite.
- **CI**: GitHub Actions on Linux, same task commands.
- **Production**: the deployment method (and production storage) is decided in Phase 3.

Pipeline: upload -> EXIF strip + derivatives -> detect (YuNet) -> quality (OFIQ) -> align ->
embed (AuraFace, SFace second opinion) -> encrypt with event key -> cluster (HDBSCAN).
Guest search: consent -> liveness (challenge + MiniFASNet) -> embed selfie in memory -> search
event -> calibrated bands -> release rules -> signed URLs.

TODO: sequence diagrams, capacity estimates.

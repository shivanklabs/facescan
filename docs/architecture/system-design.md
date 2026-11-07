# System design

```
browser (PWA) ──HTTPS──> api (FastAPI) ──> Postgres 16 + pgvector (RLS per event)
                            │                 ▲
                            │                 │ jobs table
                            ▼                 │
                         MinIO (S3) <──── worker (same codebase) ── facescan_ai (ONNX, CPU)
```

- **Modular monolith** (ADR 0001): modules identity, events, ingest, vision_pipeline, matching,
  gallery, consent, audit, admin. Two processes: api and worker.
- **Ports and adapters** (ADR 0003): every external dependency behind `app/ports`; adapters chosen
  via `config/adapters.yaml`.
- **Face as query** (ADR 0002): selfies search one event; login is passkeys/magic links.
- **Encrypted in-memory search** (ADR 0004).

Pipeline: upload -> EXIF strip + derivatives -> detect (YuNet) -> quality (OFIQ) -> align ->
embed (AuraFace, SFace second opinion) -> encrypt with event key -> cluster (HDBSCAN).
Guest search: consent -> liveness (challenge + MiniFASNet) -> embed selfie in memory -> search
event -> calibrated bands -> release rules -> signed URLs.

TODO: sequence diagrams, capacity estimates.

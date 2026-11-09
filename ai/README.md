# ai/ (facescan_ai)
Pure Python face library on ONNX Runtime (CPU): detection, alignment, embedding, quality,
liveness, clustering, matching calibration, privacy blur.

Must not: contain web, database or storage code; download models at import time; load any model
not in `models/models.lock.json` and `THIRD_PARTY_LICENSES.md`; use InsightFace `buffalo_*`.

## Matching engine (milestone 2)
`index` also gates, calibrates and clusters the event:
quality gate (weak faces: small or low quality) -> per-event calibration from same-photo
impostor pairs -> HDBSCAN clusters of strong faces (cannot-link split, weak faces attached only
when clearly closest) -> quality-weighted prototypes. `match` compares the selfie with the
cluster prototypes, checks the best-vs-second margin, then checks every face of the winning
cluster with AuraFace and SFace. Bands: release, borderline (debug only), none.

```
uv run --env-file .env facescan-ai match <selfie.jpg> --event <name>   # one face only
uv run --env-file .env facescan-ai label <event>          # http://127.0.0.1:8765
uv run --env-file .env facescan-ai bench <event>          # or: bench <name> --synthetic
```
See `benchmark/README.md` for the dataset layout and reports.

## Offline pipeline (milestone 1)
Detect (YuNet, tiled image pyramid) -> align (5-point, 112x112) -> quality (basic, temporary)
-> embed (AuraFace 512-d primary, SFace 128-d second opinion). CPU only.

```
uv run tasks models                                             # download + verify weights
uv run --env-file .env facescan-ai index <photos_dir> --event <name>
uv run --env-file .env facescan-ai search <selfie.jpg> --event <name> --top 20
uv run facescan-ai blur <photo> <out.jpg>
uv run facescan-ai clean --event <name>
```
- `index` writes `benchmark/work/<event>.fsidx` (git-ignored), AES-256-GCM encrypted with
  `FACESCAN_BENCH_KEY` from `.env` (created by `uv run tasks setup`). No face crops are stored.
- A selfie must show exactly one face; `search` and `match` refuse zero or several faces.
  The selfie and its embedding stay in memory only. `search` prints raw cosine scores.
- `blur` uses a permissive detector ("when unsure, blur") and writes no EXIF.
- Options: `--min-face` (pixels, default 20), `--score` (default 0.7), `--threads`.

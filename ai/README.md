# ai/ (facescan_ai)
Pure Python face library on ONNX Runtime (CPU): detection, alignment, embedding, quality,
liveness, clustering, matching calibration, privacy blur.

Must not: contain web, database or storage code; download models at import time; load any model
not in `models/models.lock.json` and `THIRD_PARTY_LICENSES.md`; use InsightFace `buffalo_*`.

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
- `search` keeps the selfie and its embedding in memory only; output is raw cosine scores
  (release bands come in milestone 2). It uses the largest face in the selfie.
- `blur` uses a permissive detector ("when unsure, blur") and writes no EXIF.
- Options: `--min-face` (pixels, default 20), `--score` (default 0.7), `--threads`.

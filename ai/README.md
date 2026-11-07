# ai/ (facescan_ai)
Pure Python face library on ONNX Runtime (CPU): detection, alignment, embedding, quality,
liveness, clustering, matching calibration, privacy blur.

Must not: contain web, database or storage code; download models at import time; load any model
not in `models/models.lock.json` and `THIRD_PARTY_LICENSES.md`; use InsightFace `buffalo_*`.

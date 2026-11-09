# Third-party licences

Only the models listed here may be loaded (security invariant 9). `scripts/check_licences.py`
enforces the forbidden list. Every model must also appear in `ai/models/models.lock.json`.

## Models
| Model | Use | Source | Licence | Verified |
|---|---|---|---|---|
| YuNet `face_detection_yunet_2023mar.onnx` | face detection | OpenCV Zoo `models/face_detection_yunet` | MIT (model folder LICENSE) | 2026-11-08 |
| SFace `face_recognition_sface_2021dec.onnx` | recognition, second opinion | OpenCV Zoo `models/face_recognition_sface` | Apache-2.0 (model folder LICENSE) | 2026-11-08 |
| AuraFace-v1 `glintr100.onnx` | recognition, primary | Hugging Face `fal/AuraFace-v1` | Apache-2.0 (model card) | 2026-11-08 |
| Silent-Face MiniFASNetV2 / MiniFASNetV1SE | passive liveness | GitHub `minivision-ai/Silent-Face-Anti-Spoofing` | Apache-2.0 (repo) | 2026-11-08 |
| OFIQ | face image quality | GitHub `BSI-OFIQ/OFIQ-Project` | MIT for OFIQ code; see note | partial |

Notes
- **AuraFace repo also ships `scrfd_10g_bnkps.onnx`, `1k3d68.onnx`, `2d106det.onnx`,
  `genderage.onnx`.** Only `glintr100.onnx` is approved. `scripts/check_licences.py` fails if
  any of the others, or any `scrfd_*` / `buffalo_*` file, is present.
- **OFIQ**: OFIQ code is MIT. Its `LICENSE.md` table lists some bundled files as Apache 2.0 and
  two entries as "mixed". TODO: review each bundled model used by `quality/ofiq.py` before shipping.
- Silent-Face ships PyTorch `.pth` weights. TODO: convert to ONNX in-house and record the
  resulting SHA-256 in `models.lock.json`.

## Forbidden
- InsightFace `buffalo_*` model packs (non-commercial licence).
- Any model trained on faces scraped without consent where the licence forbids commercial use.
- Any dataset of faces scraped from the internet.

## Libraries (direct dependencies)
| Package | Licence |
|---|---|
| FastAPI, Pydantic, pydantic-settings, SQLAlchemy, Alembic, Uvicorn, httpx | MIT / BSD |
| httpx2 (dev only, Starlette test client) | BSD-3-Clause |
| gitleaks (pre-commit hook, dev only) | MIT |
| psycopg | LGPL-3.0 (dynamically linked, unmodified) |
| boto3 | Apache-2.0 |
| PyYAML | MIT |
| webauthn (py_webauthn) | BSD-3-Clause |
| onnxruntime | MIT |
| opencv-python-headless | Apache-2.0 (OpenCV) / MIT (wheel) |
| numpy, scikit-learn | BSD-3-Clause |
| Pillow | MIT-CMU (HPND) |
| typer (and its dependencies rich, markdown-it-py, mdurl: MIT; shellingham: ISC) | MIT |
| cryptography | Apache-2.0 OR BSD-3-Clause |
| React, React Router, Vite, vite-plugin-pwa, i18next | MIT |
| PostgreSQL | PostgreSQL licence |

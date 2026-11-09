# facescan_ai/
Library package. No web, database, storage or network code.

| Module | Role |
|---|---|
| `model_registry.py` | load ONNX models from `models.lock.json` after SHA-256 check (bytes hashed = bytes loaded) |
| `detection/` | YuNet decoding, tiled pyramid, NMS (`geometry.py` is pure maths) |
| `alignment/` | 5-point similarity transform to the 112x112 ArcFace template |
| `embedding/` | AuraFace and SFace embedders, cosine similarity |
| `quality/` | `FaceQuality` interface; `basic_quality.py` (temporary), `ofiq.py` (TODO) |
| `privacy/` | irreversible face blur |
| `pipeline.py` | detect -> align -> quality -> embed; selfie rule (exactly one face) |
| `matching/` | quality gate, calibration, margin, bands, matcher |
| `clustering/` | HDBSCAN, cannot-link/must-link, weak-face attachment, prototypes |
| `event_builder.py` | gate -> calibrate -> cluster an indexed event |
| `evaluation.py` | precision/recall and the false-release upper bound |
| `index_store.py` | encrypted per-event index (benchmark use) |
| `search.py` | rank one event's photos against one selfie |
| `cli.py` | `facescan-ai` command |

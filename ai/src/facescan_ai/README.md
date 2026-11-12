# facescan_ai/
Library package. No web, database, storage or network code.

| Module | Role |
|---|---|
| `model_loader.py` | load ONNX models from `models.lock.json` after SHA-256 check (bytes hashed = bytes loaded) |
| `face_detection/` | YuNet decoding, tiled pyramid, NMS (`geometry.py` is pure maths) |
| `face_alignment/` | (`align_face.py`) 5-point similarity transform to the 112x112 ArcFace template |
| `face_fingerprint/` | AuraFace and SFace embedders, cosine similarity |
| `face_quality/` | `FaceQuality` interface; `basic_quality.py` (temporary; OFIQ may replace it later) |
| `privacy/` | irreversible face blur |
| `face_pipeline.py` | detect -> align -> quality -> embed; selfie rule (exactly one face) |
| `face_matching/` | quality gate, calibration, winner margin, confidence levels, matcher |
| `people_grouping/` | `group_faces.py` (HDBSCAN), `not_me_rules.py` (cannot-link/must-link, weak faces), `person_average.py` |
| `event_builder.py` | gate -> calibrate -> cluster an indexed event |
| `accuracy_maths.py` | precision/recall and the false-release upper bound |
| `encrypted_face_index.py` | encrypted per-event index (benchmark use) |
| `cli.py` | `facescan-ai` command |

# Ponytail Audit Report: FaceScan

**Date:** 2026-11-10  
**Audit Scope:** Entire repository (`backend/`, `ai/`, `frontend/`, `database/`, `scripts/`, `config/`)  
**Status:** Report only; changes pending user decision.

---

### What this repo does
FaceScan is a privacy-first event photo discovery web application. Event guests take a live selfie from their phone and find their own photos from an event using encrypted face embeddings and calibrated dual-model release bands, compliant with India's DPDP Act 2023. Photographers upload event photos in batches, which are queued, indexed in the background, and served only through short-lived HMAC-signed URLs.

### Assumed load
- **Guests:** Hundreds of concurrent event attendees taking live camera selfies (3 to 5 frames) within the first 1–2 hours of an event gallery opening.
- **Photographers:** Upload batches of up to 50 high-resolution photos (up to 25 MB each, up to 60 MP).
- **Background Workers:** Dedicated worker processes running image inspection, face detection (YuNet), dual embedding extraction (AuraFace + SFace), HDBSCAN clustering, calibration, and encrypted storage.
- **Database:** PostgreSQL with Row-Level Security (RLS) enforcing strict per-event isolation across all queries.

---

## Findings

### Must fix (Bugs, Security, Scale bottlenecks)

1. **Redundant image decoding and memory allocation during photo inspection** (`backend/app/adapters/vision/facescan_ai_face_analyzer.py:L125-147`)
   - **What this is:** When a photographer uploads photos, `inspect_image` verifies image format, size limits, and dimensions before saving.
   - **Problem:** It opens the image with Pillow to check dimensions and decompression bombs, and then immediately calls `decode_image(data)` which opens Pillow a second time and unpacks the entire photo into a massive H × W × 3 uint8 BGR numpy array in memory—only to read `decoded.shape[:2]` and immediately discard the array. For 50 photos at up to 60 MP each, this allocates multiple gigabytes of temporary memory on the web server process during upload bursts.
   - **Fix:** Use Pillow's `ImageOps.exif_transpose(img).size` inside the first `Image.open` context to get the oriented width and height directly. Delete the second `decode_image` call in `inspect_image`.
   - **If we skip it:** Server memory spikes under concurrent multi-photo uploads, triggering potential out-of-memory worker crashes.

---

### Should fix (Duplication, Dead code, Native/Stdlib replacements)

2. **Legacy prototype search module** (`ai/src/facescan_ai/search.py:L1-63`)
   - **What this is:** An early Milestone 1 demonstration search module (`rank_photos`, `PhotoMatch`, `all_finite`) calculating raw cosine similarity.
   - **Problem:** Production matching uses `matcher.py` (which performs impostor calibration, release thresholding, borderline bands, and dual-model agreement). `search.py` is only called by the legacy `facescan-ai search` CLI command.
   - **Fix:** Inline the raw scoring logic directly into `ai/src/facescan_ai/cli.py` (or deprecate the legacy CLI subcommand) and delete `search.py`.
   - **If we skip it:** Future developers may confuse `search.py` with the production matching engine and bypass calibration safeguards.

3. **Dead single-frame matcher in adapter and port** (`backend/app/adapters/vision/facescan_ai_face_analyzer.py:L295-320`, `backend/app/ports/face_analyzer.py:L106-112, 161-169`)
   - **What this is:** A single-frame `match` method and `MatchBands` dataclass leftover from earlier milestone iterations.
   - **Problem:** Production guest searches strictly use `match_frames` (multi-frame selfies). `match` is never called by any endpoint, service, or job. Furthermore, lines 303–315 of `facescan_ai_face_analyzer.py` duplicate the index building logic of `_event_index` verbatim.
   - **Fix:** Remove `match` and `MatchBands` from `FaceAnalyzer` port and `FacescanAiFaceAnalyzer` adapter.
   - **If we skip it:** 43 lines of dead code and duplicated index-mapping logic remain in the core vision adapter.

4. **Duplicated EngineCalibration serialization** (`backend/app/adapters/vision/facescan_ai_face_analyzer.py:L61-86`, `ai/src/facescan_ai/index_store.py:L175-185, L228-239`)
   - **What this is:** Converting `EngineCalibration` objects to and from JSON/database dictionary representations.
   - **Problem:** Both modules hand-code identical dictionary mapping loops for `release_threshold`, `borderline_threshold`, `impostor_pairs`, `release_calibrated`, `borderline_calibrated`, and `impostor_max`.
   - **Fix:** Add `.to_dict()` and `.from_dict()` methods on `EngineCalibration` in `facescan_ai` and reuse them across both backend and AI index storage.
   - **If we skip it:** Any new calibration field requires coordinated manual edits in three separate mapping functions.

5. **Hand-written CBOR encoder in test helpers** (`backend/tests/helpers.py:L57-80`)
   - **What this is:** Helper functions `_cbor_head` and `cbor` that construct WebAuthn CBOR payloads for passkey integration tests.
   - **Problem:** Hand-rolled 24-line binary CBOR serializer when `cbor2` is already installed in the virtual environment as an official dependency of `py_webauthn`.
   - **Fix:** Replace `cbor(value)` with `cbor2.dumps(value)`.
   - **If we skip it:** We maintain a bespoke binary encoder that could fail on untested data types.

6. **Unused table model `Claim`** (`backend/app/modules/gallery/models.py:L14-34`, `backend/app/db/models.py:L10, 22`)
   - **What this is:** Database model for guests claiming unreleased event photos.
   - **Problem:** The photo claim feature is not yet built; there are no endpoints, queries, or services using this table.
   - **Fix:** Remove the model until the claim milestone is implemented, or retain only as a documented scaffold.
   - **If we skip it:** Schema migrations and test runs carry dead table definitions.

7. **Duplicated vector Base64 packing/unpacking** (`backend/app/modules/vision_pipeline/repository.py:L23-37`, `ai/src/facescan_ai/index_store.py:L136-144`)
   - **What this is:** Encoding and decoding NumPy float32 little-endian vector bytes to/from Base64 text.
   - **Problem:** `_pack`/`_unpack` in `repository.py` and `_encode_vector`/`_decode_vector` in `index_store.py` are identical implementations.
   - **Fix:** Export `encode_vector` and `decode_vector` from `facescan_ai` and import them in `backend/app/modules/vision_pipeline/repository.py`.
   - **If we skip it:** Identical low-level bitcast operations exist in two separate packages.

8. **Unused table model `EventSection`** (`backend/app/modules/events/models.py:L64-73`, `backend/app/db/models.py:L9, 21`)
   - **What this is:** Database table model for event sub-areas or schedule days.
   - **Problem:** No router, service, query, or migration logic uses `EventSection`.
   - **Fix:** Delete the model from `events/models.py` and `db/models.py`.
   - **If we skip it:** Dead schema definition continues to be imported by Alembic.

9. **Duplicated JPEG encoding between web_copy and full_copy** (`backend/app/adapters/vision/facescan_ai_face_analyzer.py:L166-185`)
   - **What this is:** Generating EXIF-stripped web-optimized previews (max 2048px, quality 85) and full-resolution downloads (quality 95).
   - **Problem:** Both methods decode image bytes with `decode_image`, call OpenCV's `cv2.imencode(".jpg", ...)`, verify boolean success, and cast to `bytes(...)`. `full_copy` is literally `web_copy` without scaling and with quality 95.
   - **Fix:** Combine into a shared private helper `_encode_clean_jpeg(data, quality=85, max_side=None)` or make `full_copy` call `web_copy(data, max_side=None)`.
   - **If we skip it:** Duplicate image encoding paths with identical error-handling branches.

10. **Duplicated face analysis logic in AI pipeline** (`ai/src/facescan_ai/pipeline.py:L116-122, L131-136`)
    - **What this is:** Face alignment, multi-engine embedding extraction, and quality assessment.
    - **Problem:** `analyse` and `analyse_selfie` repeat identical alignment and embedding dictionary comprehensions.
    - **Fix:** Extract a 3-line private method `_analyse_face(self, image: ImageBGR, face: DetectedFace) -> AnalysedFace`.
    - **If we skip it:** Changes to face alignment or embedding evaluation need editing in two methods in `pipeline.py`.

---

### Nice to have (Small speed-ups, Shorter forms, Cleanups)

11. **Redundant integration test setup and assertions** (`backend/tests/integration/test_guest_flow.py`, `backend/tests/unit/adapters/storage/test_local_filesystem_download.py:L27-35`)
    - **What this is:** Multi-step guest onboarding flows and signed URL tests.
    - **Problem:** Several tests repeat multi-step user creation, age check, and consent granting where existing helper fixtures (`authenticated_guest`) can be reused. `test_local_filesystem_download.py` creates redundant signed URLs for the same file.
    - **Fix:** Consolidate test fixtures and remove repetitive signed URL creations.
    - **If we skip it:** ~20 extra lines of test boilerplate.

12. **Scaffold placeholder pages in frontend** (`frontend/src/pages/{admin/AdminDashboardPage.tsx, guest/ClaimPhotoPage.tsx, organiser/ClaimQueuePage.tsx, photographer/ProcessingStatusPage.tsx}`)
    - **What this is:** Four separate files rendering static 4-line placeholder `<h1>` components.
    - **Problem:** Four separate files and routes for unbuilt features.
    - **Fix:** Keep as scaffold placeholders per AGENTS.md, or inline them directly into `frontend/src/app/router.tsx`.
    - **If we skip it:** 16 lines across 4 files.

13. **Hand-written Base64URL padding helpers in test suite** (`backend/tests/helpers.py:L82-87`)
    - **What this is:** Helpers `b64url` and `unb64url` for URL-safe Base64 conversions without padding.
    - **Problem:** Manual modulo-4 arithmetic (`"=" * (-len(text) % 4)`), while `webauthn.helpers` already exports `bytes_to_base64url` and `base64url_to_bytes`.
    - **Fix:** Re-export from `webauthn.helpers`.
    - **If we skip it:** 6 lines of hand-written Base64 string manipulation.

14. **Unused frontend environment file** (`frontend/src/lib/env.ts:L1-5`)
    - **What this is:** Exports `env.apiBase = '/api'`.
    - **Problem:** Never imported anywhere in the frontend. `frontend/src/lib/apiClient.ts` defines `const API_BASE = '/api';` locally.
    - **Fix:** Delete `frontend/src/lib/env.ts`.
    - **If we skip it:** 5 lines of dead file.

15. **Single-line wrapper function in job handler registry** (`backend/app/jobs/handlers.py:L14-17`)
    - **What this is:** Registry routing background job types to execution functions.
    - **Problem:** `_index_event(ctx, event_id, _payload)` wraps `index_event(ctx, event_id)` solely to drop the unused `_payload` argument.
    - **Fix:** Inline as a lambda or allow handlers to accept `*args`.
    - **If we skip it:** 4 lines of boilerplate.

---

## Ranked Summary by Lines Saved

| # | File and Line | Duplicated or Extra | Proposed Change | Lines Saved | Risk | Test Covered? |
|---|---|---|---|:---:|:---:|:---:|
| 1 | `ai/src/facescan_ai/search.py:1-63` | Legacy Milestone 1 prototype search | Inline into `cli.py` or delete; use `matcher.py` | 63 | Low | Yes (`test_quality_blur_search.py`) |
| 2 | `backend/app/adapters/vision/facescan_ai_face_analyzer.py:295-320`, `backend/app/ports/face_analyzer.py:106-112, 161-169` | Unused `match()` method, duplicate index building, dead `MatchBands` | Remove `match` and `MatchBands` | 43 | Low | Yes (`test_guest_flow.py` uses `match_frames`) |
| 3 | `backend/app/adapters/vision/facescan_ai_face_analyzer.py:61-86`, `ai/src/facescan_ai/index_store.py:175-185, 228-239` | Hand-written `EngineCalibration` dict packing and unpacking | Add `.to_dict()` and `.from_dict()` on `EngineCalibration` | 25 | Low | Yes (`test_matching.py`, `test_index_store.py`) |
| 4 | `backend/tests/helpers.py:57-80` | Hand-rolled CBOR encoder for WebAuthn tests | Replace with `cbor2.dumps()` (already installed) | 24 | Low | Yes (`test_auth.py`) |
| 5 | `backend/app/modules/gallery/models.py:14-34`, `backend/app/db/models.py:10, 22` | Unused `Claim` database table model | Remove until claim feature is built | 23 | Low | N/A (unreferenced model) |
| 6 | `backend/tests/integration/test_guest_flow.py`, `backend/tests/unit/adapters/storage/test_local_filesystem_download.py:27-35` | Repetitive test fixture setups and duplicate URL tests | Consolidate fixture re-use | 20 | Low | Yes (integration suite) |
| 7 | `frontend/src/pages/{admin,guest,organiser,photographer}/` | 4 separate scaffold files rendering static `<h1>` | Inline into `router.tsx` or keep as placeholder | 16 | Low | Yes (`router.test.tsx`) |
| 8 | `backend/app/modules/vision_pipeline/repository.py:23-37`, `ai/src/facescan_ai/index_store.py:136-144` | Duplicated float32 little-endian Base64 vector packing | Reuse `encode_vector` and `decode_vector` from `facescan_ai` | 15 | Low | Yes (`test_index_store.py`, `test_upload_and_index.py`) |
| 9 | `backend/app/adapters/vision/facescan_ai_face_analyzer.py:125-147` | Double Pillow open and full BGR numpy decode during image inspection | Use `ImageOps.exif_transpose(img).size` in Pillow | 14 | Low | Yes (`test_upload_and_index.py`) |
| 10 | `backend/app/modules/events/models.py:64-73`, `backend/app/db/models.py:9, 21` | Unused `EventSection` table model | Remove dead model | 12 | Low | N/A (unreferenced model) |
| 11 | `backend/app/adapters/vision/facescan_ai_face_analyzer.py:166-185` | Duplicated OpenCV JPEG decode/encode in `web_copy` and `full_copy` | Unify via `_encode_clean_jpeg` helper | 10 | Low | Yes (`test_guest_flow.py`) |
| 12 | `ai/src/facescan_ai/pipeline.py:116-122, 131-136` | Duplicated face alignment and embedding extraction | Extract `_analyse_face` helper | 8 | Low | Yes (`test_selfie_rule.py`) |
| 13 | `backend/tests/helpers.py:82-87` | Hand-written Base64URL padding logic | Use `webauthn.helpers.bytes_to_base64url` | 6 | Low | Yes (`test_auth.py`) |
| 14 | `frontend/src/lib/env.ts:1-5` | Unused env file (`apiClient.ts` defines its own base URL) | Delete unused file | 5 | Low | Yes (`router.test.tsx`) |
| 15 | `backend/app/jobs/handlers.py:14-17` | Single-line wrapper `_index_event` discarding payload | Inline lambda in handler registry | 4 | Low | Yes (`test_job_queue.py`) |

---

## Totals by Area

| Area | Estimated Lines Saved |
|---|:---:|
| **Backend** | 196 |
| **AI (`facescan_ai`)** | 71 |
| **Frontend** | 21 |
| **Scripts & Config** | 0 |
| **Total** | **288** |

*Note on Scripts & Config:* `scripts/tasks.py` and `scripts/check_licences.py` are strictly standard-library implementations with zero bloat. The YAML configurations in `config/` are minimal and clean.

---

## Intentional by Design (Do not delete)

The following components were audited and confirmed to be intentional architecture choices:
1. **Ports and Adapters (`backend/app/ports/`, `backend/app/adapters/`):** Clean protocol boundaries for Object Storage, Face Analyzer, Master Key, and Email. Free implementations are active in dev; paid/production adapters (e.g. S3-compatible, AWS KMS, Resend/SES) plug in later without changing business logic.
2. **Second Face Model (`SFace`):** Dual-model embedding architecture (`ai/src/facescan_ai/embedding/`). SFace provides secondary verification alongside AuraFace to enforce precision over recall and prevent incorrect photo releases.
3. **Multi-Frame Selfie Handling (`analyse_selfie_frames`, `match_frames`):** Requiring 3–5 live selfie frames prevents static spoofing and ensures all frames agree on a single cluster before releasing photos.
4. **Placeholders:**
   - `backend/app/adapters/storage/s3_compatible_storage.py`: Scaffold for Phase 3 S3 deployment.
   - `ai/src/facescan_ai/quality/ofiq.py`: Scaffold for ISO/IEC 29794-5 face quality assessment.
   - `ai/agent/README.md`: Scope and security boundary document for Phase 3 AI agent.

---

## Protected Components (Never weaken or remove)

The following security, privacy, and compliance mechanisms were verified and marked strictly protected:
1. **Row-Level Security (RLS) & Event Isolation:** `database/policies/rls_event_isolation.sql`, `backend/app/db/event_scope.py`, and `app.current_event_id` session variables guaranteeing no cross-event data leakage (Security Invariants 1 & 2).
2. **Per-Event Encryption:** `backend/app/core/crypto.py` (AES-256-GCM sealed vectors and cluster membership under per-event data keys, Invariant 4).
3. **Signed URLs & Permission Checks:** `backend/app/adapters/storage/local_filesystem_storage.py` and `backend/app/modules/gallery/service.py` issuing short-lived HMAC signed URLs with stripped EXIF derivatives only after role and consent checks (Invariant 6).
4. **Audit Logging & DPDP Compliance:** `backend/app/modules/audit/`, `backend/app/modules/consent/`, and `config/consent/face_search_notice.yaml` recording age declarations, notice versions, and consent timestamps.
5. **Rate Limiting:** In-memory sliding window limiter (`backend/app/core/rate_limit.py`) and persistent database hourly search attempt tracking (`backend/app/modules/matching/service.py`).
6. **Input Validation:** Pydantic schemas across all routes, Pillow decompression bomb limits (`MAX_PIXELS = 120_000_000`, `upload_max_pixels = 60_000_000`), and magic byte content sniffing.
7. **Security Tests & Licence Enforcement:** `backend/tests/security/` (`test_event_isolation.py`, `test_event_keys.py`, `test_no_biometrics_in_logs.py`), `scripts/check_licences.py`, and gitleaks secret scanning.

---

### Verdict
Healthy. The core security invariants and domain flows are robust and cleanly tested. The main opportunities are removing dead Milestone 1/2 code, eliminating a heavy memory allocation during batch uploads, and reusing existing dependencies (`cbor2`, `webauthn.helpers`).

**Lean:** -288 lines, 0 new dependencies (uses already installed `cbor2` and stdlib `ImageOps`).  
**Not checked:** Production deployment infrastructure (deferred to Phase 3) and live camera video stream capture on physical mobile devices.

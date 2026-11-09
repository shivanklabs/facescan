# Security invariants

The canonical list lives in `/CLAUDE.md` ("Security invariants"). Each invariant must have a test in `backend/tests/security/`.

## Enforcement so far
| Invariant | Enforced by | Tests |
|---|---|---|
| 1, 2 event scope | FORCEd row-level security on every event table; per-transaction scope (`app/db/event_scope.py`) | `backend/tests/security/test_event_isolation.py` |
| 3 selfies in memory | raw-body selfie read with a size cap; facescan_ai in-memory pipeline | `backend/tests/security/test_no_biometrics_in_logs.py`, `ai/tests/test_search_security.py` |
| 4 encrypted embeddings | AES-GCM under per-event keys wrapped by the KeyManager port; no vector fields in API models | `backend/tests/security/test_event_keys.py`, `backend/tests/integration/test_upload_and_index.py` |
| 5 guest-only search | TODO with login (the dev match endpoint is a temporary admin tool) | - |
| 6 signed URLs | HMAC-signed local storage URLs; EXIF stripped from web copies | `backend/tests/unit/adapters/storage/` |
| 7 no biometrics in logs | redaction filter; Pillow EXIF debug logs capped | security tests above |

## Temporary dev-only access
Until login is built, `FACESCAN_DEV_ADMIN_TOKEN` grants admin access to the event, upload and
dev match endpoints, only when `FACESCAN_ENV=dev`; the API refuses to start with it set
anywhere else (`app/core/security.py`, `backend/tests/unit/core/test_dev_token.py`).
Remove it when login lands.

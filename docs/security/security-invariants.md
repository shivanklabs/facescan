# Security invariants

The canonical list lives in `/CLAUDE.md` ("Security invariants"). Each invariant must have a test in `backend/tests/security/`.

## Enforcement so far
| Invariant | Enforced by | Tests |
|---|---|---|
| 1, 2 event scope | FORCEd row-level security on every event table; per-transaction scope (`app/db/event_scope.py`) | `backend/tests/security/test_event_isolation.py` |
| 3 selfies in memory | raw-body selfie read with a size cap; facescan_ai in-memory pipeline | `backend/tests/security/test_no_biometrics_in_logs.py`, `ai/tests/test_search_security.py` |
| 4 encrypted embeddings | AES-GCM under per-event keys wrapped by the KeyManager port; no vector fields in API models | `backend/tests/security/test_event_keys.py`, `backend/tests/integration/test_upload_and_index.py` |
| 5 guest-only search | guest role required, staff refused even with a guest role (`matching/service.py`) | `backend/tests/integration/test_guest_flow.py` |
| 6 signed URLs | HMAC-signed local storage URLs; EXIF stripped from web copies | `backend/tests/unit/adapters/storage/` |
| 7 no biometrics in logs | redaction filter; Pillow EXIF debug logs capped | security tests above |

## Guest search gate
There is no liveness check yet, so guest face search runs only when `FACESCAN_ENV=dev`
(`guest_search_allowed`), with a visible "Development: liveness not active" banner. Outside dev
it is refused until a liveness adapter is configured (`test_guest_search_is_disabled_outside_dev_without_liveness`).
The milestone-3 dev admin token has been removed.

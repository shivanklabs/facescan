# Schema (migration 0001)

All IDs are UUIDs (audit_log uses a bigint sequence). Timestamps are `timestamptz`.
Every table except `users` and `passkeys` has `event_id` with row-level security ENABLED and
FORCED (it binds the table owner too); the app connects as the `facescan` role (no superuser,
no BYPASSRLS). Models: `backend/app/**/models.py`; migration:
`database/migrations/versions/20261109_1843_create_core_tables.py`.

| Table | Purpose | Key columns |
|---|---|---|
| events | an event | id, event_id (= id, checked), name, country, status (draft/indexing/searchable/failed/closed), indexed_at |
| event_members | role of a user in an event | event_id, user_id, role (organiser/photographer/guest) |
| users | person account (global) | id, email_hash, email_encrypted, created_at, is_minor |
| passkeys | WebAuthn credentials (global) | id, user_id, credential_id, public_key, sign_count |
| event_sections | sub-areas/days of an event | id, event_id, name |
| collections | an upload batch; approval releases it for indexing | id, event_id, status (open/approved), approved_at |
| photos | uploaded photos | id, event_id, collection_id, bucket, object_key, web_object_key, content_type, byte_size, width, height, status (quarantined/approved/indexed/failed) |
| faces | detected faces | id, event_id, photo_id, cluster_id, attached_weak, bbox_x/y/w/h, quality, weak, embedding_encrypted (both engines + landmarks), key_version |
| clusters | groups of faces of one person | id, event_id, cluster_no, size, prototype_encrypted, key_version |
| event_calibrations | per-event thresholds and engine versions | id, event_id, payload_encrypted, key_version |
| matches | guest-to-photo candidate matches | id, event_id, user_id, photo_id, score_band, released_at |
| claims | guest claims a photo | id, event_id, user_id, photo_id, status, reviewed_by |
| consents | DPDP consent records | id, event_id, user_id, purpose, granted_at, withdrawn_at, guardian_user_id |
| user_templates | guest search template (opt-in) | id, event_id, user_id, embedding_encrypted, expires_at |
| event_keys | wrapped per-event data keys | id, event_id, key_version, wrapped_key (NULL = destroyed), master_key_id, destroyed_at |
| audit_log | append-only audit trail | id, event_id (NULL only for non-event actions), actor, action, target, details, at |
| jobs | Postgres job queue | id, event_id, type, payload, status (queued/running/done/failed), attempts, max_attempts, run_after, locked_by, locked_at, last_error |

## Row-level security
One policy per event table: `event_id = nullif(current_setting('app.event_id', true), '')::uuid`.
The app sets `app.event_id` per transaction (`app/db/event_scope.py`). With no scope set,
nothing is visible. Three narrow extra policies:
- `events`: SELECT in the `admin` scope (list event metadata only);
- `audit_log`: rows without an event, in the `admin` scope;
- `jobs`: SELECT/UPDATE in the `dispatch` scope (the worker claims jobs).
No scope other than the event's own can see photos, faces, clusters, keys or calibration.
`audit_log` is append-only (trigger). `SET row_security = off` fails for the app role.

## Encryption
Embeddings, landmarks, prototypes and calibration are AES-256-GCM ciphertext under the event's
data key; the associated data names the event, table and row. The data key is stored only
wrapped by the master key (KeyManager port). Destroying it (`wrapped_key` NULL) crypto-shreds
the event. Vector search happens in memory after decryption (ADR 0004). pgvector is not used.

# Schema (migrations 0001-0011)

All IDs are UUIDs (audit_log uses a bigint sequence). Timestamps are `timestamptz`.
Every event table has `event_id` with row-level security ENABLED and FORCED (it binds the
table owner too). Global tables (no `event_id` policy): `users`, `passkeys`, `sessions`,
`auth_challenges`, `magic_links`, `staff_keys`. The app connects as the `facescan` role (no
superuser, no BYPASSRLS). Models: `backend/app/**/models.py`; migrations:
`database/migrations/versions/`.

## People and sign-in (global)
| Table | Purpose | Key columns |
|---|---|---|
| users | person account | id, is_minor, age_answered_at, can_create_events, is_admin; email_hash/email_encrypted hold a random placeholder (anon_...@guest.local), never a real email (dev sign-in only uses a test address) |
| passkeys | WebAuthn credentials | id, user_id, credential_id, public_key, sign_count, last_used_at |
| sessions | server-side sessions | id, token_hash, user_id, event_id (opened from an invite), auth_method (passkey/dev strong; magic_link = staff key, weak), expires_at, revoked_at, last_seen_at |
| auth_challenges | WebAuthn challenges | id, challenge, purpose (incl. guest_register), user_id, pending_user_id (account created only after the passkey verifies), expires_at, used_at |
| staff_keys | one-time staff keys (FS-...) | id, key_hash (SHA-256), role (admin/organiser/photographer), event_id (photographer only), created_by, expires_at (7 days), used_at, used_by, revoked_at |
| magic_links | legacy email links; unused since the no-email decision, kept (no data deletion) | id, token_hash, email_hash, email_encrypted, next_path, expires_at, used_at |

## Events, people in events, uploads
| Table | Purpose | Key columns |
|---|---|---|
| events | an event | id, event_id (= id, checked), name, country, status (draft/indexing/searchable/failed/closed), starts_at, search_window_ends_at, indexed_at |
| event_members | role of a user in an event | event_id, user_id, role (organiser/photographer/guest) |
| event_invites | guest invite / QR links | id, event_id, secret_hash, created_by, expires_at, revoked_at |
| event_sections | sub-areas/days of an event | id, event_id, name, starts_at, ends_at |
| collections | an upload batch; approval releases it for indexing | id, event_id, status (open/approved), approved_at |
| upload_sessions | resumable chunked uploads | id, event_id, user_id, collection_id, filename, byte_size, total_chunks, expected_sha256, received_chunks, status |
| camera_offsets | per-camera clock correction | id, event_id, camera_model, camera_serial, offset_seconds |
| photos | uploaded photos | id, event_id, collection_id, section_id, bucket, object_key, web_object_key, content_type, byte_size, width, height, status (quarantined/approved/indexed/failed), sha256, phash, is_near_duplicate, taken_at, camera_make/model/serial, corrected_time |

## Faces and search
| Table | Purpose | Key columns |
|---|---|---|
| faces | detected faces | id, event_id, photo_id, bbox_x/y/w/h, detector_score, quality, weak, embedding_encrypted (both engines + landmarks), key_version |
| clusters | groups of faces of one person | id, event_id, cluster_no, size, payload_encrypted (prototypes + member and attached face ids), key_version |
| event_calibrations | per-event thresholds and engine versions | id, event_id, payload_encrypted, key_version |
| event_keys | wrapped per-event data keys | id, event_id, key_version, wrapped_key (NULL = destroyed), master_key_id, destroyed_at |
| matches | guest-to-photo matches | id, event_id, user_id, photo_id, score_band, source, released_at (unique per user + photo) |
| cannot_links | "Not me" marks; never shown to that person again | id, event_id, user_id, photo_id, face_id |
| search_attempts | face-search rate limits | id, event_id, user_id, created_at |
| user_templates | guest search template (opt-in) | id, event_id, user_id, embedding_encrypted, key_version, expires_at |
| scene_embeddings | encrypted scene vectors per photo | photo_id, event_id, ciphertext, nonce, engine_version |
| scene_search_candidates | scene-search results awaiting a claim (7-day cleanup) | id, event_id, user_id, photo_id, face_id |
| scene_search_attempts | scene-search rate limits (7-day cleanup) | id, event_id, user_id, created_at |
| claims | guest claims a photo | id, event_id, user_id, photo_id, face_id (SET NULL), status, reason, reviewed_at, reviewed_by |
| claim_attempts | claim rate limits (7-day cleanup) | id, event_id, user_id, created_at |
| consents | DPDP consent records ('notice', 'face_search') | id, event_id, user_id, purpose, notice_version, language, granted_at, withdrawn_at, guardian_user_id |

## Records and work
| Table | Purpose | Key columns |
|---|---|---|
| audit_log | append-only audit trail | id, event_id (NULL only for non-event actions), actor, action, target, details, at |
| jobs | Postgres job queue | id, event_id, type, payload, status (queued/running/done/failed), attempts, max_attempts, run_after, locked_by, locked_at, last_error |

## Account deletion
Deleting a `users` row (only by the person themself, Privacy > Delete my account) cascades to
their passkeys, sessions, challenges, memberships, consents, matches, cannot_links, claims,
templates, attempts and candidates. Photos, faces and clusters belong to the event and stay.
See `docs/runbooks/data-deletion.md`.

Job payloads hold ids only (enforced by `validate_payload` in the queue adapter), because the
dispatch scope can read every job.

## Row-level security
One policy per event table: `event_id = nullif(current_setting('app.event_id', true), '')::uuid`.
The app sets `app.event_id` per transaction (`app/db/event_isolation.py`). With no scope set,
nothing is visible. Three narrow extra policies:
- `events`: SELECT in the `admin` scope (list event metadata only);
- `audit_log`: rows without an event, in the `admin` scope;
- `jobs`: SELECT/UPDATE in the `dispatch` scope (the worker claims jobs).
- user scope (`app.user_id`): SELECT of the user's own event_members rows and consents, and the
  events they belong to ("my events", privacy settings).
No scope other than the event's own can see photos, faces, clusters, matches, keys or calibration.
`audit_log` is append-only (trigger). `SET row_security = off` fails for the app role.

## Encryption
Embeddings, landmarks, prototypes, cluster membership and calibration are AES-256-GCM
ciphertext under the event's
data key; the associated data names the event, table and row. The data key is stored only
wrapped by the master key (KeyManager port). Destroying it (`wrapped_key` NULL) crypto-shreds
the event. Vector search happens in memory after decryption (ADR 0004). pgvector is not used.

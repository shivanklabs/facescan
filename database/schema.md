# Planned schema (no migration yet)

All IDs are UUIDs. Every table except `users` and `passkeys` has `event_id` with RLS
(`database/policies/rls_event_isolation.sql`). Timestamps are `timestamptz`.

| Table | Purpose | Key columns |
|---|---|---|
| events | an event | id, event_id (= id), name, country, starts_at, search_window_ends_at, status |
| event_members | role of a user in an event | event_id, user_id, role (organiser/photographer/guest) |
| users | person account (global) | id, email_hash, email_encrypted, created_at, is_minor |
| passkeys | WebAuthn credentials (global) | id, user_id, credential_id, public_key, sign_count |
| event_sections | sub-areas/days of an event | id, event_id, name |
| photos | uploaded photos | id, event_id, section_id, object_key, status, taken_at |
| faces | detected faces | id, event_id, photo_id, bbox, quality, embedding_encrypted |
| clusters | groups of faces of one person | id, event_id, size, status |
| matches | guest-to-photo candidate matches | id, event_id, user_id, photo_id, score_band, released_at |
| claims | guest claims a photo | id, event_id, user_id, photo_id, status, reviewed_by |
| consents | DPDP consent records | id, event_id, user_id, purpose, granted_at, withdrawn_at, guardian_user_id |
| user_templates | guest search template (opt-in) | id, event_id, user_id, embedding_encrypted, expires_at |
| event_keys | wrapped per-event data keys | id, event_id, wrapped_key, key_version, destroyed_at |
| audit_log | append-only audit trail | id, event_id, actor_id, action, target, at |
| jobs | Postgres job queue | id, event_id, type, payload, status, attempts, run_after, locked_by |

Indexes on all lookup fields (event_id first). Embeddings are stored encrypted; vector search
happens in memory after decryption (ADR 0004). pgvector is not used.

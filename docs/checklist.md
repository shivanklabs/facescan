# FaceScan Test-Day Operational Checklist

This checklist guides organisers, photographers, and administrators through preparing, executing, and verifying a live event deployment of FaceScan.

---

## 1. Pre-Event Setup & Infrastructure
- [ ] **Environment Verification**: Ensure `.env` is configured with valid secrets (`FACESCAN_DB_URL`, `FACESCAN_STORAGE_SIGNING_KEY`, `FACESCAN_MASTER_KEY`).
- [ ] **Backup Verification**: Ensure `FACESCAN_BACKUP_DIR` is set to an external directory. Verify `uv run tasks backup` completes and produces a valid `manifest.json`.
- [ ] **Admin Authorization**: Configure `FACESCAN_AUTH_ADMIN_EMAILS` with the emails of platform administrators who hold passkey credentials.
- [ ] **Host Storage**: Check disk space via the Admin Dashboard (`/admin`) to confirm adequate capacity for event originals and web-size derivatives.

---

## 2. Event Creation & Organiser Configuration
- [ ] **Create Event**: Sign in with an organiser passkey and create the event with name and country (e.g. `IN`).
- [ ] **Configure Event Sections**: Define time-bounded sections (e.g. "Ceremony", "Reception", "Cocktail Hour") in Event Setup for scene search filters.
- [ ] **Set Search Window**: Configure an optional search window end date (`search_window_ends_at`). Confirm searches stop after the window while galleries and downloads remain active.
- [ ] **Print QR Codes**: In the Event Invite section, preview the QR code and click **Download QR Code for Printing** to prepare venue signage and table cards.
- [ ] **Age & Consent Policy**: Verify `settings/country-rules/in.yaml` has `minors_self_search: allowed` (or `restricted` if age verification is required).

---

## 3. Photographer Workflow & Ingestion
- [ ] **Camera Clock Calibration**: Take a test shot of a reference clock. In Photographer Tools, set the clock offset for each camera make/model to ensure correct chronological sequencing.
- [ ] **Chunked Resumable Upload**: Upload photo collections. If the connection drops, resume the upload; verified chunks are not re-transmitted.
- [ ] **Deduplication Check**:
  - Exact duplicates (matching SHA-256) are rejected at upload with an "already uploaded" notice.
  - Perceptual near-duplicates (pHash) are flagged on the Processing Status page without dropping files.
- [ ] **Processing Status & Retries**: Check `/photographer/events/{id}/status`. If any photo fails processing, inspect the failure and click **Retry Photo**.
- [ ] **Collection Approval**: Organiser reviews and approves collections, triggering background face detection, alignment, embedding extraction, and clustering.

---

## 4. Guest Experience & Core Verification
- [ ] **Invite Link Access**: Guest scans event QR code or opens invite link (`/e/:eventId?k=...`). No email or phone is requested.
- [ ] **Notice & Anonymous Session**: Guest reviews DPDP privacy notice and grants consent. An anonymous guest account (random UUID, no personal info) is created with a long-lived `HttpOnly, Secure, SameSite=Strict` session cookie (`fs_session`).
- [ ] **Face Search / Liveness**: Guest submits 3–5 selfie frames with live motion:
  - Real face passes and initiates matching.
  - Photo of a phone screen is refused by MiniFASNet / Silent-Face liveness.
  - Printed paper photo is refused by liveness check.
- [ ] **Borderline Search Handling**:
  - If a scan returns a borderline score, the app prompts for a second scan from a slightly different angle.
  - If still borderline, the app gracefully guides the guest to Scene Search and manual claim flow without releasing unverified photos.
- [ ] **Gallery Delivery & Original Quality**:
  - Matched photos appear in the guest's gallery with high-resolution download links.
  - Downloaded photos preserve 100% original dimensions, bytes, and stripped metadata.
- [ ] **Passkey Option for Return Visits**:
  - After first scan, a passkey offer is displayed.
  - On supported browsers, the guest can register a passkey for faster return visits.
  - If the browser lacks WebAuthn support (e.g. in-app browser), the offer is skipped without error.
- [ ] **Return Visits & Lost Sessions**:
  - On the same phone, visiting the site opens the gallery directly via the session cookie.
  - If the session is lost (cookie cleared), reopening the invite link creates a new anonymous guest account and scans again.
- [ ] **"Not me" Protection**:
  - Clicking "Not me" immediately removes the photo from the guest's view.
  - The match row is preserved as `not_me` (no data loss) and a `cannot_links` record prevents that face from ever being released to that guest.
- [ ] **Non-Destructive Consent Withdrawal**:
  - Guest clicks withdraw consent in Privacy Settings.
  - Gallery is hidden, matches are marked `withdrawn`, and this event's sessions are revoked.
  - If the guest returns and grants consent again, matches are restored back to `release`.
- [ ] **Staff Magic-Link Terminal Output**:
  - Organisers and admins receive sign-in magic links printed directly in the laptop terminal (`console` adapter). Guests cannot request magic links.

---

## 5. Organiser Claims & Admin Monitoring
- [ ] **Review Claims Queue**: Organiser reviews pending claims (`/organiser/events/{id}/claims`):
  - Displays anonymous guest identifiers (`Guest #1`, `Guest #2`, etc.) instead of emails.
  - Approve releases the photo with source `claim`.
  - Reject notifies the guest with audit logging.
- [ ] **Admin Health Monitoring**: Platform admin visits `/admin`:
  - Inspect free disk space and latest backup timestamp.
  - Verify per-event photo counts (quarantined, approved, indexed, failed, total).
  - Confirm indexing progress percentage reaches 100%.
  - Verify job failures (0), pending claims, and "Not me" counts.
  - Confirm dashboard strictly displays counts with zero photos, faces, or personal data.

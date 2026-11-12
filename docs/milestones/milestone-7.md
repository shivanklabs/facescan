# Milestone 7: Simple FaceScan website (approved by the owner, 12 Nov 2026)

Owner's goal (12 Nov 2026): a simple website with a beautiful, unique UI. A person logs in, does a
face scan, and either sees their photos (view as a gallery in the browser, download) or sees
"no photos found". Extra features come later.

## Owner's decisions (12 Nov 2026)
- Login: Google sign-in.
- Search: one scan searches **all events at once** (not one event per invite link).
- Scan: simple selfie + blink (the existing front-only mode with blink liveness).
- Approach: keep the existing engine (face AI, matching, encrypted storage, original-quality
  download); build a new simple frontend on top. Nothing is deleted; extra features are hidden.

## What the guest sees (4 screens)
1. **Welcome / Login**: one hero screen, "Continue with Google". A one-tap privacy line
   ("your selfie is never stored") with an "I agree" checkbox, shown only the first time.
2. **Face scan**: full-screen camera, oval guide, "blink once", automatic capture (1-2 s).
   Clear messages for dark, blurry or no-face frames.
3. **Searching**: short animated state while all events are searched.
4. **Result**:
   - Photos found: gallery grid grouped by event, tap to open full screen (swipe), download one
     photo or "download all" per event, "Not me" kept as a small menu item.
   - Nothing found: a friendly "no photos yet" screen with "scan again".
   - Return visit: logged-in users land straight on their gallery, with "scan again".

## What the owner sees (1 simple admin page)
- Sign in with Google; admins are the emails in `FACESCAN_AUTH_ADMIN_EMAILS`.
- Create event (name, date), drag-and-drop photo upload, a progress bar until "ready".
- Uploaded photos are approved automatically (no separate approval step).

## Backend changes
1. Google sign-in: verify the Google ID token on the server (RS256 check with the existing
   `cryptography` + `httpx` libraries against Google's public keys; no new Python package).
   Needs `FACESCAN_GOOGLE_CLIENT_ID` in `.env` (free, from Google Cloud Console).
   Email is stored hashed + encrypted, as the users table already does.
2. Search across all events: one endpoint loops over every searchable event and runs the
   existing per-event search (each event keeps its own key and row-level security). The user is
   made a guest of each event automatically; consent is the one-time agreement from screen 1.
3. Auto-approve uploads by the admin (config flag, default on).
4. Old screens and routes (invite links, organiser/photographer pages, claims, scene search)
   stay in the code but are removed from the new navigation.

## Rule changes this needs (owner must confirm)
- Security invariant 2 "No search across events, ever" becomes "a guest search may cover all
  searchable events; each event is still searched separately with its own key".
- Login becomes Google sign-in for everyone (magic link + passkey kept in code, hidden).
- Risk accepted by the owner: anyone holding a person's photo who passes the blink check could
  find that person's photos across all events.

## Design direction (proposal)
Dark, cinematic "photo-lab" look: deep charcoal background, warm amber accent, large serif
headings, glass cards, smooth camera-shutter animations; light mode supported; mobile-first.
No new UI library: plain CSS with design tokens.

## New dependencies
None (Google Identity Services is loaded as Google's own script tag, not an npm package).

## Done when
On the laptop and a phone: sign in with Google, blink scan, see own photos from two events,
open full screen, download the original; a person with no photos sees "no photos found";
admin creates an event and uploads photos that become searchable. Lint and tests pass.

## Build notes (12 Nov 2026)
- Routes: `/` welcome + Google button, `/scan`, `/photos`, `/studio` (owner). Older pages keep
  their paths (old home moved to `/staff`) and are not linked from the simple site.
- Backend: `IdentityProvider` port + `google_identity_provider.py` adapter (RS256 with
  `cryptography`, keys from Google's JWKS via httpx2, cached 1 h); `POST /auth/google`,
  `GET /auth/google/config`; migration 0009 allows `google` sessions; Google sessions count as
  strong sign-in for staff pages.
- `discover` module: `POST /discover/search?notice_version=` (one liveness + embedding, then
  each searchable event in its own transaction; staff are never searched in their own events;
  events being re-indexed or with an unusable key are skipped) and `GET /discover/photos`.
- `matching.service.guest_search` was split into `embed_selfie_frames` and `release_in_event`
  so both searches share the same release rules.
- Consent notice is now version `2026-11-draft-3` (text covers all events); people agree with
  one tick on the scan page.
- Owner setup: create an OAuth Web client in Google Cloud Console, put its id in
  `FACESCAN_GOOGLE_CLIENT_ID`, run `uv run tasks db-migrate`.

## Scan methods under comparison (12 Nov 2026)
The owner is testing three scan methods on real phones and will keep the most stable one.
The scan page has a chooser (remembered per device):
- **Live blink** (default): MediaPipe Face Landmarker (`@mediapipe/tasks-vision` 1.1.1,
  Apache-2.0) runs in the browser and captures the closed-eye frames at the moment of the
  blink. Model and wasm are served by FaceScan (lazy-loaded, ~4 MB gzip, not precached).
  Falls back to Quick burst when the model cannot start on a device.
- **Quick burst**: 5 frames ~140 ms apart while the person blinks.
- **Just look** (`method=look`): no blink challenge, only the Silent-Face anti-spoofing check.
  Allowed only when `FACESCAN_ENV=dev`; refused (403) elsewhere until the owner decides.
Server side: front-only liveness accepts the blink in any middle frame; matching uses only
the open-eye first and last frames. Dev-only email sign-in (`/auth/dev-login`, migration 0010)
stands in for Google until an OAuth client id exists.

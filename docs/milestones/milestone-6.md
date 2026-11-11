Milestone 6: finish the remaining MVP build in 4 parts. Every feature below is confirmed by me.

Before you start:
- If the previous task (Part C, phone test mode, backup, no-data-loss check, download quality, skipped tests, checklist, and my legal update: no email, anonymous guest accounts) is not finished and reported, finish it first.
- Save this prompt word for word as docs/milestones/milestone-6.md and commit it.

Rules for the whole milestone:
- Do Parts 1 to 4 in order. After each part: lint, licences and tests pass, commit, push, CI green with gh, then post a 3–5 line summary and continue to the next part without waiting for me. The app must work after every part, so I can stop and test at any point.
- The only new dependency allowed is qrcode.react (frontend). Everything else uses existing libraries or the standard library. If you think another one is needed, stop and ask.
- My rules still apply: a wrong person never gets a photo; the right person's photos are not missed; no data loss (never delete or modify uploads, events or user data; hide or mark instead); photos are delivered at original quality; no email and no identity data (email, name, phone) from guests; no outside service receives guest data except the Cloudflare tunnel connection.
- Decide small implementation details yourself and list them in the final report. Ask me only if you are blocked.
- Not in this milestone: accuracy tuning on real data, paid or legal launch items, blurring other people in released photos, feeding claims into must-link, guest-list mode (it needs guest emails), any email sending.

## Part 1: the two core rules
1. "Not me" button on every photo in the guest's gallery. The photo disappears from that guest's gallery at once; the match row is marked not_me (not deleted); that face is recorded as a cannot-link for this guest so it is never released to them again, including by later searches or claims; audit-logged; organisers and the admin page see only a count.
2. Borderline result: ask for a second guided scan from a slightly different angle. If it is still borderline, offer the scene search and claim flow. A borderline result never releases a photo.
3. Duplicate check at upload: an exact duplicate (same SHA-256 in the same event) is not stored a second time and the uploader sees "already uploaded". Near-duplicates (a perceptual hash written with Pillow and NumPy, no new library) are flagged on the processing status page, never dropped.

## Part 2: photographer and organiser tools
4. Resumable chunked upload with no new library: an upload id, chunks small enough for the Cloudflare tunnel, a checksum per chunk and a SHA-256 check at the end, resume after a dropped connection. Processing status page for photographers: counts per state, failures with a retry button. A file that fails processing stays stored and can be retried; nothing is lost.
5. Camera clock sync: read capture time, camera model and serial from EXIF on the server (the serial is never shown to guests or put in any download). The photographer sets an offset per camera; the corrected time is stored; photos without a time fall back to upload order.
6. Sections: the organiser creates sections (name, start, end). Photos are assigned by corrected time, otherwise to 1-hour slots. Scene search filters use them.
7. Search window: the organiser sets an end date. After it, no new face searches or claims start; galleries and downloads keep working; nothing is deleted.

## Part 3: access and consent
8. QR code: qrcode.react on the organiser's invite page, with a download for printing. Add its licence to THIRD_PARTY_LICENSES.md.
9. Age: my decision is no age restriction for now. Remove the under-18 block and the age question from the guest flow, and put the rule in config/policies/in.yaml as a policy value (for example minors_self_search: allowed) so it can be switched back on before launch without code changes. Update the tests to match.
10. Withdrawal: hide, do not delete. The guest's gallery is hidden, face search and claims stop for that guest in that event, their sessions for it end, and match rows are kept but marked withdrawn. Giving consent again restores them. Audit-logged. Update the existing withdrawal test.

## Part 4: safety and operations
11. Rate caps: check that per-account, per-IP and per-event caps exist for guest account creation, staff sign-in attempts, searches, claims and downloads (values in config), and add the missing ones. Client IPs only as short-lived hashed keys.
12. Alerts: hourly thresholds per event from config for searches, claims, new guest accounts, failed staff sign-ins, downloads, "Not me" reports and job failures, shown on the admin page (no email). Alerts never contain photos, face data or IP addresses.
13. Admin health page: admins are the staff emails in FACESCAN_AUTH_ADMIN_EMAILS. Per event: photos by state, indexing progress, job failures, pending claims, "Not me" count, recent alerts; plus free disk space and the time of the last backup. Counts only: no photos or faces.
14. Update the test-day checklist for everything new, run the full test suite, and give me the final report: what was built per item with commits, the CI result, anything skipped and why, and the small decisions you made.
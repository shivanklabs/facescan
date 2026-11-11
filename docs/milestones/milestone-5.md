Context: this repo is FaceScan; read AGENTS.md first. The product works only if two rules hold: (1) a wrong person never receives a photo, and (2) the right person's photos are not missed. Every part of this milestone serves those two rules. Before starting: CI must be green and the Ponytail cleanup committed; if not, stop and tell me.

Features confirmed by me for this milestone (build only these):
A. Guided face scan with liveness
B. Scene search for missing photos, with a safe release path
C. Organiser review queue for claims that fail the face check
Not in this milestone (ask me before adding): feeding accepted claims into must-link, blurring other people in released photos, guest-list mode, QR codes, guardian consent, phone HTTPS testing.

Rules: ask me before any new dependency, tool, model or service, including one-time conversion tools. Push after every commit once lint, licences and tests pass. Under-18s stay blocked from search.

## A. Guided face scan + liveness
1. Replace the 2-second capture with a guided scan of about 3-4 seconds: look straight, blink, turn slightly left, turn slightly right. Capture frames at each pose. Frames stay in memory only.
2. Liveness (confirmed choice): Silent-Face anti-spoofing models converted to ONNX in-house from the original Apache-2.0 weights. PyTorch may be used once, as a dev-only tool, only for the conversion; propose exactly how and ask me before adding it. Record the source, licence and SHA-256 in models.lock.json. Combine the model with the blink and head-turn challenge checks. Implement it behind the Liveness port and enable guest search only when this adapter is active (keep the existing rule for non-dev environments and record that it does not stop injection attacks).
3. Matching with several angles: the front frame and the side frames are matched separately. A photo is released only when the frames agree on the same person and every existing release rule passes. Side frames may help find side-angle photos but can never release a photo on their own against the front frame's verdict.
4. Front-only fallback (confirmed): if a guest cannot complete the head turns, allow a front-only scan with liveness by blink, under stricter release rules (configurable, stricter than the guided scan). Their side-angle photos are left to the claim flow.
5. Guidance on screen: good light, remove sunglasses and mask, face in the frame. Reject blurry or dark frames with a clear retry message.

## B. Scene search with safe release
1. Photo understanding (confirmed): SigLIP 2 (google/siglip2-base-patch16-224, Apache-2.0) run locally through ONNX. At index time, compute one image embedding per photo, encrypt it under the event key, and store it with the event. Search runs in memory per event, like faces; no pgvector. Propose the export and runtime toolchain (for example a tokenizer library) and ask me before adding anything.
2. Query understanding (confirmed): the guest types a description (English, Hindi or Hinglish). If a Gemini API key is set in .env, send only that typed text to Gemini to rewrite it into short English search phrases. Never send photos, selfies, face data, event or user ids, or emails to Gemini. Call the REST API with the existing HTTP client; ask me before adding an SDK, and ask me which Gemini model to use. If the key is missing or the call fails, search with the original text locally. The privacy notice must say that typed search text may be processed by Google; show a hint not to type names.
3. Candidates: search only within the guest's event, exclude photos already in their gallery, and show at most 12 candidates as low-resolution previews with every face blurred on the server. Unblurred images never reach the browser here. Section and time filters narrow the search.
4. "This is me": the guest taps one blurred face. A fresh guided scan (or front-only scan) runs, and a 1:1 check compares it with that one face. Use the same calibrated false-match target as the release band; do not relax it. Pass: the photo is added to the guest's gallery exactly like an automatic match. Fail: a claim goes to the organiser queue (C).
5. Limits: claims and scene searches per guest per event per day, from config, enforced in PostgreSQL. All claim steps are audit-logged without selfie or face data.

## C. Organiser review queue
1. A ClaimQueuePage for the event organiser: each pending claim shows the photo, the claimed face marked, the claimant's email, and the reason it reached the queue (face check failed or front-only scan).
2. Approve adds the photo to that guest's gallery; reject notifies the guest in their gallery. Both are audit-logged.
3. Organisers still never run face searches and never see who matched which photo outside this queue.

## Tests
- Liveness: a printed or screen-replayed photo test set (generated in memory from fixtures) is rejected; a scan without blink or head turn is rejected; front-only fallback applies the stricter rules.
- Multi-angle: side frames alone never release a photo.
- Scene search: never returns photos from another event; previews have every face blurred (detector finds no recognisable face, as in the existing blur regression test); no unblurred bytes in claim responses.
- Gemini: the outgoing request body contains only the query text; the feature works with no key.
- Claims: 1:1 threshold equals the release target; pass releases, fail queues; limits enforced; organiser approve and reject work; non-organisers cannot see the queue.
- No selfie or frame bytes on disk or in logs; no vectors or scores in guest responses.

Done when, on my laptop: a guest completes a guided scan (and a front-only scan) with the webcam; a photo of me shown on another phone's screen is refused, as well as a printed photo; the guest finds a missing photo by typing a description, claims it, passes the 1:1 check and sees it in the gallery; a failed check reaches the organiser queue and approval releases it. Lint, licences and tests pass on Windows and in CI; commits pushed. Finish with remaining TODOs and anything you want me to decide.

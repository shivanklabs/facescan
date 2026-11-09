# tests/
Unit tests use synthetic numbers and images only.

Real-face tests (`test_real_faces.py`, `test_search_security.py`) read consented photos from
`fixtures/local/` (git-ignored) as described by `fixtures/local/manifest.json`, and are skipped
when the manifest or the model weights are missing. Never commit face photos; never download
face photos from the internet.

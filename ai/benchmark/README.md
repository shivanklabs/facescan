# benchmark/
Accuracy benchmark for detection, matching and release rules.

Consent and labelling rules
- Only images from people who gave written, revocable consent for benchmarking. Never scraped
  images, never public face datasets with non-commercial licences.
- Datasets live in `datasets/` (git-ignored) and are deleted when consent is withdrawn.
- Labels follow `labels.schema.json`; labels reference opaque person IDs, never names.
- Reports in `reports/` contain only aggregate metrics, no images or vectors.
- `work/` (git-ignored) holds encrypted indexes written by `facescan-ai index`; delete them with
  `facescan-ai clean --event <name>` when no longer needed.

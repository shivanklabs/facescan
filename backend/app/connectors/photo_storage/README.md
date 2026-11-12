# connectors/photo_storage/
Adapters for ObjectStorage. Must not contain business rules.

- `local_filesystem_storage.py` (default): files under `var/storage/<bucket>/<event_id>/`
  (git-ignored) with random object keys. Downloads use HMAC-signed URLs that expire after
  `FACESCAN_STORAGE_URL_EXPIRY_MINUTES`; sign only after the permission check (invariant 6).
- `local_filesystem_download.py`: the API route `GET /storage/{bucket}/{event_id}/{key}` that
  checks the signature and expiry, then serves the file. Mounted only when
  `FACESCAN_STORAGE_SIGNING_KEY` is set.
- `s3_compatible_storage.py`: placeholder for production; the storage service is decided in
  Phase 3.

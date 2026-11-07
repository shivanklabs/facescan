#!/bin/sh
# Creates private buckets in local MinIO. Credentials come from the environment (.env).
set -eu
mc alias set local http://minio:9000 "$FACESCAN_STORAGE_ACCESS_KEY" "$FACESCAN_STORAGE_SECRET_KEY"
for bucket in "$FACESCAN_STORAGE_BUCKET_PHOTOS" "$FACESCAN_STORAGE_BUCKET_DERIVATIVES"; do
  mc mb --ignore-existing "local/$bucket"
  mc anonymous set none "local/$bucket"
done
echo "buckets ready"

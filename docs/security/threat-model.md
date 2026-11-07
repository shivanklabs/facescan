# Threat model (draft)

Assets: photos, face embeddings, selfies (transient), emails, consent records, event keys.

| Threat | Mitigation |
|---|---|
| Stranger searches for someone else's face | guest-only search, own face only, liveness challenge |
| Cross-event data leak | RLS on event_id, no cross-event search, isolation tests |
| Photo/presentation attack on liveness | browser challenge + MiniFASNet |
| Embedding theft | encrypted per event key; never returned by API; crypto-shredding |
| Bucket exposure | private buckets, short-lived signed URLs, EXIF stripped |
| Log leakage | redaction filter, no biometrics in logs test |
| Minor's data processed without guardian consent | minor_age policy, parental consent flow |

TODO: STRIDE per component, abuse cases for organisers/photographers.

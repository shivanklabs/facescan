# 0004. Encrypted in-memory face search

Status: accepted
Date: 2026-11-08

## Context and decision
Embeddings are biometric personal data. They are stored encrypted with a per-event data key. For a search, the event's embeddings are decrypted into worker memory and searched there; pgvector indexes are not used on plaintext vectors.

## Consequences
Destroying the event key crypto-shreds all embeddings. Search scales per event, which matches the product (no cross-event search). TODO: memory limits for very large events.

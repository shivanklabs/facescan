# 0002. The face is a search query, not a credential

Status: accepted
Date: 2026-11-08

## Context and decision
Face matching is probabilistic and faces are public. Using the face to log in would turn false matches into account takeover. Guests log in with passkeys or email magic links; the selfie is only used to search one event they are a member of.

## Consequences
No face login ever. Search requires an authenticated guest, consent and liveness.

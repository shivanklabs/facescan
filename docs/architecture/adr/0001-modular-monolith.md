# 0001. Modular monolith

Status: accepted
Date: 2026-11-08

## Context and decision
A small team with zero budget needs one deployable. We use one FastAPI codebase with strict module boundaries and two processes (api, worker). Job queue is a Postgres table. No microservices, no Redis, no Kafka.

## Consequences
Simple ops; modules can be extracted later if needed. Risk: boundary erosion, mitigated by fixed module file roles and review.

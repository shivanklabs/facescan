# 0003. Ports and adapters

Status: accepted
Date: 2026-11-08

## Context and decision
Budget is zero today but paid services (Rekognition, S3, KMS, SMS OTP) may be needed later. Every external dependency is a Protocol in app/ports; adapters live in app/adapters and are selected by config/adapters.yaml per default, region and plan.

## Consequences
Switching vendors is a config change. Business code never imports vendor SDKs.

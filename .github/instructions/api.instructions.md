---
applyTo: "openblade/api/**"
---

API routes must use explicit request and response models, never leak raw exceptions without context, and always operate through dependency-injected services or backends.
Keep endpoints simulator-friendly, deterministic, and easy to exercise with FastAPI's test client.
Expose safety-sensitive actions as deliberate workflows rather than hidden side effects.

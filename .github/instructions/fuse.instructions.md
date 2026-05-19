---
applyTo: "openblade/fuse/**"
---

FUSE-facing code should expose a read-mostly namespace over catalog data and delegate hydration to restore workflows.
Avoid hidden writes, keep cache state explicit, and prefer deterministic metadata views over implicit filesystem side effects.

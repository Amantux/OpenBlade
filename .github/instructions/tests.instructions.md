---
applyTo: "tests/**"
---

Tests should prove safety properties, not just happy paths.
Use the simulator for integration and end-to-end coverage.
Prefer clear invariants, explicit assertions, and deterministic fixtures.
When a bug is safety-relevant, add a regression test before or with the fix.

---
applyTo: "openblade/simulator/**"
---

Simulator code must be deterministic by default and fault-injectable when requested.
Model slots, cartridges, drives, changer operations, tape capacity, LTFS-like mounts, files, manifests, and errors.
Never import real hardware modules from simulator modules.
Expose a clean interface that matches the real backend contract.
Every simulator feature needs tests.

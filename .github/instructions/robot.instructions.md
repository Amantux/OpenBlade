---
applyTo: "openblade/{simulator,hardware}/**"
---

Robot and changer logic must preserve exclusivity: only one changer manipulation at a time and no duplicated cartridges.
Represent state transitions explicitly and reject illegal transitions with typed errors.
Treat unload-while-mounted and conflicting changer ownership as safety-critical failures.

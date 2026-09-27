---
title: Safety Gates
document_type: configuration
status: verified
last_verified: 2026-07-19
verified_against: [openblade/hardware/safety.py, openblade/domain/policies.py, AGENTS.md]
owners: [platform, security]
tags: [safety, configuration, agent]
---

# Safety Gates (`verified`)

These controls are **enforced by the controller** and **must never be bypassed** by
the agent. They are the reason many actions are denylisted.

| Gate | Rule | Enforced by |
|---|---|---|
| Real-hardware enable | `OPENBLADE_BACKEND=real` AND `OPENBLADE_REAL_HARDWARE_ENABLED=true` | `hardware/safety.py` → `RealHardwareDisabledError` |
| Format confirmation | tape format requires barcode confirmation + one-time safety token | `domain/policies.py` (FormatConfirmation/SafetyToken) |
| Unload guard | unload blocked while LTFS mounted or dirty | domain state machine |
| No `shell=True` | subprocess calls use `shell=False` | `hardware/` |
| Simulator-first | mock is default; real path is narrow + guarded | `bootstrap.py` |

## Agent implication
The agent may **observe** and **report** on these (via `get_safety_posture`), and
must **escalate** requests to change them. It must never set the hardware env vars,
supply a format token, force an unload, or move real media. See
[action policy](../agent/action-policy.md) denylist and [RB-HW-001](../runbooks/real-hardware-blocked.md).

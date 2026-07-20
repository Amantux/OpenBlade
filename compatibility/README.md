# Compatibility corpus — the external fidelity oracle

The single most important correction from the external review:

> **The emulator must not be the source of truth for the client that talks to the
> real i3.** Fidelity answers to a versioned corpus of official contracts +
> sanitized appliance captures + hardware-in-the-loop results.

This directory is that corpus. Each case is a self-contained JSON fixture; the
differential harness (`tests/compat/test_compatibility_corpus.py`) replays each
request against the emulator and compares.

## The two case sources — and why the distinction is load-bearing

- **`captured`** — recorded from a real appliance (or an official spec example).
  The emulator MUST match these exactly; a mismatch fails CI. These are the only
  cases that *prove* fidelity.
- **`inferred`** — derived from the emulator's own current behavior (or a doc we
  can't verify against hardware). They lock current behavior as a regression
  guard, but they do **not** prove fidelity — an inferred case passing means only
  "the emulator still does what it did," not "the emulator matches a real i3."

The harness reports the captured/inferred split so the fidelity gap is loud, never
hidden. Today there are **0 captured cases** → i3 wire fidelity is UNVERIFIED.

## Profiles (never one generic "i3-compatible" label)

| Profile dir | Appliance / surface | Firmware / rev |
|---|---|---|
| `scalar-i3-341g/` | Scalar i3 AML Web Services (`/aml/*`) | 341G family (Jan 2026) |
| `iblade-rev-a/` | Scalar-LTFS / Windows iBlade (`/iblade/*`) | Rev A (Sep 2017) |

The repo's local reference manuals are older (AML Web Services **Rev D 2019**,
iBlade **Rev A 2017**) than the current 341G firmware — so even doc-derived cases
against 341G are `inferred` until certified on hardware.

## Case schema

```json
{
  "id": "aml-login-success",
  "profile": "scalar-i3-341g",
  "source": "inferred",
  "firmware": "341G",
  "manual_ref": "Web Services Guide Rev D §2.5 (Table, login)",
  "fidelity_notes": "known/suspected divergences from a real appliance",
  "request": { "method": "POST", "path": "/aml/users/login", "json": { } },
  "expected": { "status": 200, "json_contains": { "code": 0 } }
}
```

`expected.json_contains` is a subset match; add `expected.json_equals` for an
exact body match (use for `captured` cases where the full body is authoritative).

## Adding a real appliance capture (the goal)

1. Capture the sanitized request/response on the rig (headers, status, body).
2. Save it here with `"source": "captured"` and the firmware.
3. Run the harness. If the emulator diverges, either the emulator is wrong (fix it)
   or the capture reveals a real contract we hadn't modeled (fix the emulator to
   match). The emulator is required to satisfy the corpus — not the reverse.

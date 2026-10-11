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

Use `tools/compat_capture.py` (replays each recipe's `request` block, fills
`expected` from the observed response, sanitizes, writes `<id>.json`).

On site, against the appliance:

```bash
python tools/compat_capture.py capture --base-url https://<i3-host> \
  --recipe compatibility/scalar-i3-341g/aml --out compatibility/scalar-i3-341g/aml/captured \
  --source captured --appliance-firmware <ver> --attest "<operator name>" --login <user>:<pass>
```

- `--source captured` is REFUSED (exit 2, nothing written) unless BOTH
  `--appliance-firmware` and `--attest` are given; they are recorded as
  `firmware` / `attested_by`. Never label emulator output `captured`.
- Redacted before writing (`sanitize()`, pure + unit-tested): session tokens
  (Bearer/Basic, cookie values, `session`/`token`-like JSON keys and XML tags),
  serials (`serial*` keys/tags), hosts/IPs in URLs and text, ISO/HTTP-date
  timestamps, and request passwords (`<redacted>` = the `--login` credential,
  `<redacted-invalid>` = any other). Review the diff anyway before committing.
- Only response keys the sanitizer left untouched go into
  `expected.json_contains`; the full scrubbed response is kept under `observed`.
- `captured` cases are ENFORCED by `tests/compat/test_compatibility_corpus.py`:
  if the emulator diverges, fix the emulator (or record a contract we hadn't
  modeled). The emulator must satisfy the corpus — not the reverse. Flip the
  `len(CAPTURED) == 0` guard in that file when the first capture lands.

Self-capture loop (emulator, `source: self-captured`, regression guard only):
`--diff` replays existing cases and prints drift without writing (exit 1 on
drift); then capture into `<profile>/<surface>/self-captured/`. Against a live
base URL, the module-global login limiter persists between cases — prefer a
fresh process per run.

## iblade-rev-a

`iblade-rev-a/` is a scaffold profile for the Rev A (2017) iBlade surface. All its
cases are `inferred`; see `iblade-rev-a/README.md`. The harness discovers it
automatically (it globs `compatibility/**/*.json`).

## Request/expectation keys beyond `json`

- `request.body` — raw request body string, sent verbatim (use for XML or malformed
  payloads). When present, `request.json` is ignored.
- `request.content_type` — sets the `Content-Type` header (overrides `request.headers`).
  Omit it entirely to test a missing Content-Type.
- `request.repeat` — send the request this many extra times before the asserted one
  (e.g. to trip the login rate limiter). The harness clears the limiter around each case.
- `expected.headers` — `{name: substring}`; each header must be present and contain the
  substring (`""` asserts presence only).

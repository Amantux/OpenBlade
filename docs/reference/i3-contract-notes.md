# Certified i3 contract values (from the Quantum Web Services manual)

These are the real documented values the OpenBlade emulator + client target, read
from the vendor manual (**Scalar i6k/i3/i6 RESTful Web Services API, 6-68185-01
Rev D**). The manual itself is copyrighted and gitignored (see
`docs/reference/quantum/`); this file records only the specific values we
implement, with citations — never the vendor text.

Source it locally with `docs/reference/quantum/fetch.sh` to re-verify.

## Element / segment coordinate (Figure 23: coordinate)

A coordinate is an **object**, not a reduced string:

```
{ "frame": 1, "rack": 1, "section": 2, "column": 1, "row": 1, "type": 4 }
```

- `section` — the module.
- `column` — magazine position (per the manual's magazine-eject description, column
  1 = left magazine … column 10 = right magazine).
- `row` — slot within the addressed unit.
- `type` — element type code (e.g. 2 = storage).

OpenBlade's `ScalarCoordinate` (`openblade/domain/scalar_coordinate.py`) already
matches this shape. The emulator historically reduced element coordinates to a
`"frame,bay,slot"` string; the faithful form is the full object.

## moveClass (moveMedium) — bit field

The current `moveClass` is a **bit field** (the older single-integer form with
`3 = Unload` is explicitly **deprecated** in the manual):

| Value | Meaning |
|---|---|
| 0 | Normal move (source + destination coordinates required) |
| 2 | Import (source only; IE slot in a partition) — *not supported on i3/i6* |
| 4 | Export (source only; storage slot in a partition) — *not supported on i3/i6* |
| 8 | Unload drive (source coordinate of the drive only; move to home/empty slot) |
| 16 | No-Eject (flag; combine with a move/unload, e.g. `24 = 8+16`) |
| 32 | Move to closest slot (combine with unload `8`) |
| ~~3~~ | **Deprecated** single-integer "Unload" — the emulator's legacy value |

So `OpenBlade` should send/accept `8` for unload (bit field), keeping `3` accepted
for backward compatibility with the emulator's legacy behavior.

## Timing / async

- Robotic operations are **asynchronous**: `202 Accepted` + poll for completion.
- `estimatedCompletionTime` is reported **in minutes** (e.g. for scan sessions).
- Session timeout is configurable (`sessionTimeout`; `localAccessTimeout` /
  `remoteAccessTimeout` per interface).

**Not in this manual:** tape data-transfer rates and per-move robotics durations.
The Web Services guide is a *control-plane* API spec; drive/robotics performance
figures live in hardware datasheets, not here. The emulator's latency profiles
therefore remain OpenBlade-chosen approximations, not doc-certified numbers — do
not present them as vendor-specified.

"""Hydrator shim for the protocol rig (NOT the real hydrator).

The real hydrator is the FUSE path (openblade/fuse): a read of an offline file
blocks inside the filesystem until tape recall completes. This rig exports a
plain directory, so an offline file is a stub whose content starts with
STUB_MAGIC followed by a JSON body {"state": "offline_on_tape", "mode": ...}.
A client asks for recall by creating `<root>/.ob-control/<relpath>.req`; then,
per the stub's mode:
  "delay:<s>"  -> after <s> seconds atomically replace the stub with the bytes
                  in `<root>/.ob-tape/<relpath>` (simulated tape recall),
  "never"      -> leave it offline (hydration timeout),
  "tape_error" -> write `.ob-control/<relpath>.err` = OFFLINE_TAPE_UNAVAILABLE.
Each recall is logged once to `.ob-control/hydration.log`; requests for a path
already in flight are coalesced (one hydration event per path).
"""

import json
import os
import sys
import threading
import time
from pathlib import Path

STUB_MAGIC = b"OPENBLADE-OFFLINE-STUB\n"


def _hydrate(root: Path, rel: str, mode: str, inflight: set[str]) -> None:
    target = root / rel
    if mode == "tape_error":
        (root / ".ob-control" / f"{rel}.err").write_text("OFFLINE_TAPE_UNAVAILABLE")
    elif mode.startswith("delay:"):
        time.sleep(float(mode.split(":", 1)[1]))
        tmp = target.with_name(f".{target.name}.hydrating")
        tmp.write_bytes((root / ".ob-tape" / rel).read_bytes())
        os.replace(tmp, target)
        with (root / ".ob-control" / "hydration.log").open("a") as log:
            log.write(f"hydrated {rel}\n")
    inflight.discard(rel)


def main(root: Path) -> None:
    control = root / ".ob-control"
    inflight: set[str] = set()
    while True:
        control.mkdir(parents=True, exist_ok=True)
        for req in sorted(control.rglob("*.req")):
            rel = str(req.relative_to(control))[: -len(".req")]
            req.unlink(missing_ok=True)
            data = (root / rel).read_bytes() if (root / rel).is_file() else b""
            if rel in inflight or not data.startswith(STUB_MAGIC):
                continue
            mode = json.loads(data[len(STUB_MAGIC) :])["mode"]
            inflight.add(rel)
            with (control / "hydration.log").open("a") as log:
                log.write(f"recall {rel} mode={mode}\n")
            threading.Thread(target=_hydrate, args=(root, rel, mode, inflight), daemon=True).start()
        time.sleep(0.1)


if __name__ == "__main__":
    main(Path(sys.argv[1]))

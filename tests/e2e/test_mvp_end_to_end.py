"""End-to-end MVP guard: the simulated-i3 archive/shard/restore/control-plane demo.

Runs scripts/mvp_e2e_demo.py as a subprocess; it asserts every stage internally
and exits non-zero on any failure, so a zero exit means the full pipeline works:
boot simulated i3 -> apply config -> shard + distribute + archive -> verify ->
restore byte-exact (STRIPE and BLOCK_STRIPE) -> drive robotics over HTTP.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEMO = _REPO_ROOT / "scripts" / "mvp_e2e_demo.py"


def test_mvp_end_to_end_demo_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(_DEMO)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, (
        f"MVP demo failed (exit {result.returncode}).\n"
        f"stdout tail:\n{result.stdout[-2000:]}\n"
        f"stderr tail:\n{result.stderr[-2000:]}"
    )
    assert "MVP END-TO-END: PASS" in result.stdout

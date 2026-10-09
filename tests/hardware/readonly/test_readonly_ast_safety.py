"""Plain-CI guard: tests/hardware/readonly/ must be read-only by construction.

Fails if any module there imports or calls a load/unload/move/format/mount
API. Resolution helpers ("resolve_*", "*dry_run*", "plan_*") stay allowed.
Not rig-gated.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

READONLY_DIR = Path(__file__).resolve().parent

FORBIDDEN_EXACT = frozenset(
    {
        "load", "unload", "move", "move_medium", "exchange", "transfer", "eject",
        "format", "mkltfs", "mount", "unmount", "umount", "ltfs", "erase",
        "run_inventory_job", "inventory",
    }
)  # fmt: skip
FORBIDDEN_PREFIXES = ("load_", "unload_", "move_", "format_", "mount_", "erase_", "mkltfs")
FORBIDDEN_SUBSTRINGS = ("_load", "_unload", "_move", "_format", "_mount", "_erase")
ALLOWED_MARKERS = ("dry_run", "resolve_", "plan_")
# Config loading reads a file, not a cartridge; exempt by exact name only.
ALLOWED_EXACT = frozenset({"load_config"})


def is_forbidden_name(name: str) -> bool:
    lowered = name.lower()
    if lowered in ALLOWED_EXACT or any(marker in lowered for marker in ALLOWED_MARKERS):
        return False
    return (
        lowered in FORBIDDEN_EXACT
        or lowered.startswith(FORBIDDEN_PREFIXES)
        or any(s in lowered for s in FORBIDDEN_SUBSTRINGS)
    )


def find_forbidden(source: str) -> list[str]:
    """Return ``line:name`` for every forbidden import or call in ``source``."""
    hits: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom | ast.Import):
            for alias in node.names:
                if is_forbidden_name(alias.name.rsplit(".", 1)[-1]):
                    hits.append(f"{node.lineno}:{alias.name}")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                if isinstance(func.value, ast.Constant) and isinstance(func.value.value, str):
                    continue  # "...".format(...) is string formatting, not a tape API
                name = func.attr
            else:
                continue
            if is_forbidden_name(name):
                hits.append(f"{node.lineno}:{name}")
    return hits


def _readonly_modules() -> list[Path]:
    return sorted(p for p in READONLY_DIR.glob("*.py") if p.name != Path(__file__).name)


def test_readonly_dir_has_modules_to_scan() -> None:
    assert len(_readonly_modules()) >= 2


@pytest.mark.parametrize("path", _readonly_modules(), ids=lambda p: p.name)
def test_readonly_module_calls_no_mutating_api(path: Path) -> None:
    assert find_forbidden(path.read_text()) == []


@pytest.mark.parametrize(
    "snippet",
    [
        "library.load(1, 'A00001L8')",
        "backend.unload(0)",
        "changer.move_medium(src, dst)",
        "ltfs.format_tape('A', '/dev/nst0')",
        "ltfs.format('A', confirmation)",
        "mount_ltfs('/dev/nst0', '/mnt')",
        "from openblade.hardware.ltfs import ltfs_mount",
        "from openblade.jobs.inventory import run_inventory_job",
        "library.inventory()",
        "svc.load_tape(1)",
    ],
)
def test_scanner_flags_forbidden_snippet(snippet: str) -> None:
    assert find_forbidden(snippet)


@pytest.mark.parametrize(
    "snippet",
    [
        "resolve_tape_by_id('X')",
        "LTFSCommandBackend.format_dry_run_plan('A', '/dev/nst0')",
        "'{}'.format(1)",
        "InventoryService(lib).snapshot()",
        "sg_inq(dev, runner, guard)",
        "from openblade.config import load_config",
    ],
)
def test_scanner_allows_read_only_snippet(snippet: str) -> None:
    assert find_forbidden(snippet) == []

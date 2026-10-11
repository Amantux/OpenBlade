"""Plain-CI guard: tests/hardware/readonly/ must be read-only by construction.

Fails if any module there (recursively) imports, calls or merely references a
load/unload/move/format/mount API, fetches one via ``getattr(x, "<name>")``,
puts a mutating verb into an argv list literal, or sends a non-GET request
other than the login/logout handshake. Resolution helpers stay allowed, matched
by prefix/suffix only ("resolve_*", "plan_*", "dry_run_*", "*_dry_run_plan").
Not rig-gated. The runtime guards in conftest.py (ReadOnlyViolation) back this
up for anything a static scan can't see.
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
FORBIDDEN_PREFIXES = ("load_", "unload_", "move_", "format_", "mount_", "erase_", "mkltfs_")
FORBIDDEN_SUBSTRINGS = ("_load", "_unload", "_move", "_format", "_mount", "_erase")
ALLOWED_PREFIXES = ("resolve_", "plan_", "dry_run_")
ALLOWED_SUFFIXES = ("_dry_run_plan",)
# Config loading reads a file, not a cartridge; exempt by exact name only.
ALLOWED_EXACT = frozenset({"load_config"})
# Whole argv items that make a command mutate media (mtx load, mkltfs, ltfs, ...).
FORBIDDEN_ARGV_WORDS = frozenset(
    {
        "load", "unload", "mkltfs", "ltfs", "mount", "umount", "unmount", "transfer",
        "move", "format", "eject", "erase", "exchange",
    }
)  # fmt: skip
WRITE_VERBS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
AUTH_PATHS = frozenset({"/aml/users/login", "/aml/auth/logout"})


def is_forbidden_name(name: str) -> bool:
    lowered = name.lower()
    if (
        lowered in ALLOWED_EXACT
        or lowered.startswith(ALLOWED_PREFIXES)
        or lowered.endswith(ALLOWED_SUFFIXES)
    ):
        return False
    return (
        lowered in FORBIDDEN_EXACT
        or lowered.startswith(FORBIDDEN_PREFIXES)
        or any(s in lowered for s in FORBIDDEN_SUBSTRINGS)
    )


def _str_const(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _write_request_hit(node: ast.Call) -> str | None:
    """Name a non-GET HTTP call unless it is a literal login/logout path."""
    func = node.func
    if not isinstance(func, ast.Attribute):
        return None
    args = list(node.args)
    if func.attr.upper() in WRITE_VERBS:
        verb = func.attr.upper()
    elif func.attr == "request" and args and _str_const(args[0]) is not None:
        verb = str(_str_const(args[0])).upper()
        args = args[1:]
        if verb not in WRITE_VERBS:
            return None
    else:
        return None
    path = _str_const(args[0]) if args else None
    if verb == "POST" and path in AUTH_PATHS:
        return None
    return f"{verb} {path if path is not None else '<dynamic path>'}"


def find_forbidden(source: str) -> list[str]:
    """Return ``line:what`` for every mutating reference in ``source``."""
    hits: list[str] = []
    for node in ast.walk(ast.parse(source)):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.ImportFrom | ast.Import):
            for alias in node.names:
                if is_forbidden_name(alias.name.rsplit(".", 1)[-1]):
                    hits.append(f"{line}:{alias.name}")
        elif isinstance(node, ast.Attribute):
            # Any reference, not just a call: `op = lib.load; op(1)` is caught here.
            # Bare names are checked only as call targets (below): a forbidden bare
            # name can only arrive via an import (flagged above) or a local binding
            # of a flagged value, and flagging every Name trips on locals such as
            # `inventory = InventoryService(...).snapshot()`.
            if _str_const(node.value) is not None:
                continue  # "...".format(...) is string formatting, not a tape API
            if is_forbidden_name(node.attr):
                hits.append(f"{line}:{node.attr}")
        elif isinstance(node, ast.List | ast.Tuple):
            for elt in node.elts:
                word = _str_const(elt)
                if word is not None and word.lower() in FORBIDDEN_ARGV_WORDS:
                    hits.append(f"{line}:argv {word!r}")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and is_forbidden_name(node.func.id):
                hits.append(f"{line}:{node.func.id}")
            if isinstance(node.func, ast.Name) and node.func.id in {"getattr", "setattr"}:
                attr = _str_const(node.args[1]) if len(node.args) > 1 else None
                if attr is not None and is_forbidden_name(attr):
                    hits.append(f"{line}:getattr {attr!r}")
            write = _write_request_hit(node)
            if write is not None:
                hits.append(f"{line}:{write}")
    return hits


def _readonly_modules() -> list[Path]:
    # The scanner scans its own source too: its snippets are whole-statement
    # strings, never argv-shaped single words, so they don't misfire.
    return sorted(READONLY_DIR.rglob("*.py"))


def test_readonly_dir_has_modules_to_scan() -> None:
    assert len(_readonly_modules()) >= 3


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
        # B3 bypasses: each must be flagged.
        "client.post('/aml/media/move', json={'src': 1})",
        "requests.put(url + '/format')",
        "runner.run(['mtx', '-f', d, 'load', '1', '0'])",
        "subprocess.run(['mkltfs', '--force'])",
        "getattr(lib, 'load')(1)",
        "op = lib.load\nop(1)",
        "lib.load_plan_now(1)",
        "backend.unload_dry_run(0)",
        "client.request('DELETE', '/aml/users/login')",
        "client.delete('/aml/partitions/1')",
        "appliance.request('POST', '/aml/physicalLibrary/elements/move', {})",
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
        "runner.run(['mtx', '-f', d, 'status'])",
        "client.get('/aml/partitions')",
        "client.request('GET', '/aml/physicalLibrary/elements')",
        "client.request('POST', '/aml/users/login', body)",
        "emulator.post('/aml/auth/logout')",
    ],
)
def test_scanner_allows_read_only_snippet(snippet: str) -> None:
    assert find_forbidden(snippet) == []

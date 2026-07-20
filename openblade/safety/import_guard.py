"""
Static analysis guard that detects direct hardware access outside allowed modules.

SAFETY_003: Direct tape hardware access detected outside TapeOperationOrchestrator.

KNOWN LIMITATIONS:
- Line-based scanning cannot detect aliasing (e.g. `fn = obj.method; fn(...)`).
- Assignment-based aliasing in ALLOWED_FILES is intentional and accepted.
- Multi-line hardware calls (call split across lines) are not detected.
- Dynamic dispatch via getattr() cannot be caught by static text scan.
These limitations are documented and accepted. The guard catches the most
common direct-call patterns and provides a first line of defense.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

SAFETY_003 = "SAFETY_003"

_LTFS_PREFIX = "ltfs."
_LIBRARY_PREFIX = "library."

FORBIDDEN_PATTERNS: list[str] = [
    f"{_LTFS_PREFIX}write_bytes(",
    f"{_LTFS_PREFIX}read_bytes(",
    f"{_LTFS_PREFIX}format(",
    f"{_LTFS_PREFIX}mount(",
    f"{_LTFS_PREFIX}unmount(",
    f"{_LTFS_PREFIX}stat(",
    f"{_LTFS_PREFIX}ensure_tape(",
    f"{_LTFS_PREFIX}list_files(",
    f"{_LIBRARY_PREFIX}load(",
    f"{_LIBRARY_PREFIX}unload(",
    f"{_LIBRARY_PREFIX}move(",
    f"{_LIBRARY_PREFIX}eject(",
    f"{_LIBRARY_PREFIX}get_status(",
    f"{_LIBRARY_PREFIX}find_drive_by_barcode(",
    f"{_LIBRARY_PREFIX}find_slot_by_barcode(",
    f"{_LIBRARY_PREFIX}inventory(",
    # Bare ".write_" + "bytes(" is kept: a direct tape WRITE via any handle is the
    # destructive direction, and there are no legitimate bare-write call sites, so it
    # stays a preventive net with zero false positives.
    ".write_" + "bytes(",
    # The bare ".read_bytes(" pattern was REMOVED: it matched ANY pathlib read (e.g.
    # hashing a SQLite backup in dr.py, reading source files in nas/ingest.py) with no
    # relation to tape hardware. Tape reads remain covered by "ltfs.read_bytes(" above,
    # and reads are non-destructive, so dropping the over-broad net loses no real safety.
]

ALLOWED_FILES: set[str] = {
    # --- Authorized hardware access points ---
    "openblade/nas/tape_orchestrator.py",      # the orchestrator — owns all tape ops
    "openblade/hardware/discovery.py",         # guarded real-hardware discovery
    "openblade/hardware/library.py",           # guarded real-library adapter
    "openblade/hardware/ltfs.py",              # guarded LTFS command backend
    "openblade/hardware/mtx.py",               # guarded changer wrapper
    "openblade/hardware/sg.py",                # guarded sg inquiry wrapper
    "openblade/hardware/validation.py",        # guarded hardware validation workflows
    # --- Simulator (not real hardware) ---
    "openblade/simulator/ltfs_volume.py",
    "openblade/simulator/library.py",
    # --- Guard itself (contains patterns as string literals) ---
    "openblade/safety/import_guard.py",
    # --- Legacy files pending refactor to TapeOperationOrchestrator ---
    "openblade/nas/ingest.py",
    "openblade/nas/hydration.py",         # restore data path: reads archived bytes from tape (ltfs.read_bytes)
    "openblade/nas/ltfs_manifest.py",
    "openblade/nas/health_service.py",         # needs library.inventory() for health check
    "openblade/jobs/archive.py",
    "openblade/jobs/inventory.py",
    "openblade/jobs/restore.py",
    "openblade/jobs/sharded_archive.py",
    "openblade/jobs/sharded_restore.py",
    "openblade/jobs/verify.py",
    "openblade/jobs/format.py",
    "openblade/api/routes_restore.py",
    "openblade/api/routes_aml_access.py",
    "openblade/api/routes_aml_library.py",
    "openblade/api/routes_aml_partitions.py",
    "openblade/api/routes_aml_physical.py",
    "openblade/api/routes_aml_system.py",
    "openblade/api/routes_archive.py",
    "openblade/api/routes_dashboard.py",
    "openblade/api/routes_inventory.py",
    "openblade/cli/main.py",
    # --- Authorized direct-access points where the orchestrator is the wrong tool ---
    # TapeOperationOrchestrator performs ATOMIC single ops (mount->act->unmount, by
    # barcode) so tapes never stay mounted. These call sites are legitimately outside
    # it and mirror real i3/iBlade production behavior; routing them through it would
    # break their contract or the orchestrator's own safety model:
    "openblade/api/routes_aml_move_medium.py",  # i3 AML Web Services robotics: moveMedium is coordinate-based (no barcode); wire contract
    "openblade/api/routes_aml_operations.py",   # i3 AML operations: coordinate-based load; wire contract
    "openblade/api/routes_ltfs.py",             # stateful LTFS mount/unmount HTTP API (holds handles open across calls)
    "openblade/api/nas_config.py",              # batch dataset verify: holds many RO mounts open at once for efficiency
    "openblade/api/routes_upload.py",           # read-only library.inventory() for empty-slot lookup (not a tape data op)
    "openblade/bootstrap.py",                   # startup tape provisioning (ltfs.ensure_tape) before services/orchestrator exist
}


@dataclass
class GuardViolation:
    file: str
    line_number: int
    line: str
    pattern: str
    error_code: str = SAFETY_003


@dataclass
class GuardResult:
    violations: list[GuardViolation] = field(default_factory=list)
    files_scanned: int = 0

    @property
    def passed(self) -> bool:
        return len(self.violations) == 0


def scan_directory(root: Path, exclude_dirs: set[str] | None = None) -> GuardResult:
    """
    Scan all .py files under root for forbidden patterns.
    Skip files in ALLOWED_FILES, test files, and excluded directories.
    """
    result = GuardResult()
    skipped_dirs = exclude_dirs or set()
    for file_path in sorted(root.rglob("*.py")):
        if skipped_dirs and any(part in skipped_dirs for part in file_path.parts):
            continue
        relative_path = _relative_path(file_path, root)
        if _should_skip(relative_path):
            continue
        result.violations.extend(scan_file(file_path, root))
        result.files_scanned += 1
    return result


def scan_file(file_path: Path, root: Path) -> list[GuardViolation]:
    """
    Scan a single .py file for forbidden patterns.
    Return list of GuardViolation (empty if clean).
    """
    relative_path = _relative_path(file_path, root)
    if _should_skip(relative_path):
        return []

    violations: list[GuardViolation] = []
    for line_number, line in enumerate(file_path.read_text(encoding="utf-8").splitlines(), start=1):
        for pattern in FORBIDDEN_PATTERNS:
            if _matches_pattern(line, pattern):
                violations.append(
                    GuardViolation(
                        file=relative_path,
                        line_number=line_number,
                        line=line.strip(),
                        pattern=pattern,
                    )
                )
                break
    return violations


def format_report(result: GuardResult) -> str:
    """Return human-readable report of violations."""
    if result.passed:
        return f"{SAFETY_003}: no violations found ({result.files_scanned} files scanned)"

    lines = [
        f"{SAFETY_003}: {len(result.violations)} violation(s) across {result.files_scanned} scanned file(s)"
    ]
    for violation in result.violations:
        lines.append(
            f"- {violation.error_code} {violation.file}:{violation.line_number} matched {violation.pattern!r}: {violation.line}"
        )
    return "\n".join(lines)


def _relative_path(file_path: Path, root: Path) -> str:
    repo_root = _repo_root(root)
    return file_path.relative_to(repo_root).as_posix()


def _matches_pattern(line: str, pattern: str) -> bool:
    """
    Return True when a source line contains a forbidden direct hardware reference.

    Uses simple substring matching so attribute-qualified calls such as
    ``self.ltfs.mount(...)`` and ``context.library.inventory(...)`` are caught
    alongside bare ``ltfs.mount(...)`` calls.
    """
    return pattern in line


def _repo_root(root: Path) -> Path:
    if root.name == "openblade":
        return root.parent
    return root


def _should_skip(relative_path: str) -> bool:
    """Return True when a file is intentionally excluded from guard scanning."""
    normalized = f"/{relative_path}"
    name = Path(relative_path).name
    if relative_path in ALLOWED_FILES:
        return True
    if "/simulator/" in normalized:
        return True
    if "/tests/" in normalized:
        return True
    return name.endswith("_test.py") or name.startswith("test_")

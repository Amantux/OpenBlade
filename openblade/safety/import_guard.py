"""
Static analysis guard that detects direct hardware access outside allowed modules.

SAFETY_003: Direct tape hardware access detected outside TapeOperationOrchestrator.
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
    f"{_LTFS_PREFIX}list_files(",
    f"{_LIBRARY_PREFIX}load(",
    f"{_LIBRARY_PREFIX}unload(",
    f"{_LIBRARY_PREFIX}move(",
    f"{_LIBRARY_PREFIX}eject(",
    f"{_LIBRARY_PREFIX}get_status(",
    ".write_" + "bytes(",
    ".read_" + "bytes(",
]

ALLOWED_FILES: set[str] = {
    # TapeOperationOrchestrator is the sole application-layer choke point for real tape ops.
    "openblade/nas/tape_orchestrator.py",
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
            if pattern in line:
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


def _repo_root(root: Path) -> Path:
    if root.name == "openblade":
        return root.parent
    return root


def _should_skip(relative_path: str) -> bool:
    normalized = f"/{relative_path}"
    name = Path(relative_path).name
    if relative_path in ALLOWED_FILES:
        return True
    if "/simulator/" in normalized:
        return True
    if "/tests/" in normalized:
        return True
    if name.endswith("_test.py") or name.startswith("test_"):
        return True
    return False

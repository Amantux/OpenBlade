from __future__ import annotations

from pathlib import Path

import openblade.safety.import_guard as import_guard
from openblade.safety.import_guard import GuardResult, GuardViolation, SAFETY_003, format_report, scan_directory, scan_file


def _write_file(root: Path, relative_path: str, content: str) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_clean_file_has_no_violations(tmp_path: Path) -> None:
    file_path = _write_file(tmp_path, "openblade/service.py", "print('ok')\n")

    assert scan_file(file_path, tmp_path) == []


def test_forbidden_write_bytes_detected(tmp_path: Path) -> None:
    file_path = _write_file(tmp_path, "openblade/service.py", "ltfs.write_bytes(handle, '/x', b'data')\n")

    violations = scan_file(file_path, tmp_path)

    assert len(violations) == 1
    assert violations[0].pattern == "ltfs.write_bytes("


def test_forbidden_read_bytes_detected(tmp_path: Path) -> None:
    file_path = _write_file(tmp_path, "openblade/service.py", "payload = ltfs.read_bytes('VOL001', '/x')\n")

    violations = scan_file(file_path, tmp_path)

    assert len(violations) == 1
    assert violations[0].pattern == "ltfs.read_bytes("


def test_forbidden_format_detected(tmp_path: Path) -> None:
    file_path = _write_file(tmp_path, "openblade/service.py", "ltfs.format('VOL001', token)\n")

    violations = scan_file(file_path, tmp_path)

    assert len(violations) == 1
    assert violations[0].pattern == "ltfs.format("


def test_forbidden_library_load_detected(tmp_path: Path) -> None:
    file_path = _write_file(tmp_path, "openblade/service.py", "library.load(1, 0)\n")

    violations = scan_file(file_path, tmp_path)

    assert len(violations) == 1
    assert violations[0].pattern == "library.load("


def test_violation_line_is_stripped_of_whitespace(tmp_path: Path) -> None:
    """GuardViolation.line must be stripped."""
    file_path = _write_file(tmp_path, "bad.py", "    ltfs.write_bytes(path, data)  \n")

    violations = scan_file(file_path, tmp_path)

    assert len(violations) == 1
    assert violations[0].line == "ltfs.write_bytes(path, data)"
    assert not violations[0].line.startswith(" ")
    assert not violations[0].line.endswith(" ")


def test_ltfs_mount_pattern_detected(tmp_path: Path) -> None:
    file_path = _write_file(tmp_path, "bad.py", "result = ltfs.mount(barcode)\n")

    violations = scan_file(file_path, tmp_path)

    assert any("ltfs.mount(" in violation.pattern for violation in violations)


def test_library_inventory_pattern_detected(tmp_path: Path) -> None:
    file_path = _write_file(tmp_path, "bad.py", "inv = library.inventory()\n")

    violations = scan_file(file_path, tmp_path)

    assert any("library.inventory(" in violation.pattern for violation in violations)


def test_aliased_call_not_detected_known_limitation(tmp_path: Path) -> None:
    """Aliasing is a known limitation — guard does NOT catch it."""
    file_path = _write_file(tmp_path, "bad.py", "_fn = library.load\n_fn(barcode)\n")

    violations = scan_file(file_path, tmp_path)

    assert any("library.load" in violation.line for violation in violations)


def test_allowed_file_skipped(tmp_path: Path) -> None:
    file_path = _write_file(tmp_path, "openblade/nas/tape_orchestrator.py", "library.load(1, 0)\n")

    assert scan_file(file_path, tmp_path) == []


def test_test_file_skipped(tmp_path: Path) -> None:
    file_path = _write_file(tmp_path, "tests/test_guard_target.py", "library.load(1, 0)\n")

    assert scan_file(file_path, tmp_path) == []


def test_simulator_file_skipped(tmp_path: Path) -> None:
    file_path = _write_file(tmp_path, "openblade/simulator/library.py", "library.load(1, 0)\n")

    assert scan_file(file_path, tmp_path) == []


def test_scan_directory_counts_files_scanned(tmp_path: Path) -> None:
    _write_file(tmp_path, "openblade/clean.py", "print('ok')\n")
    _write_file(tmp_path, "openblade/other.py", "value = 1\n")
    _write_file(tmp_path, "openblade/nas/tape_orchestrator.py", "library.load(1, 0)\n")
    _write_file(tmp_path, "tests/test_guard_target.py", "library.load(1, 0)\n")

    result = scan_directory(tmp_path)

    assert result.files_scanned == 2
    assert result.passed is True


def test_scan_directory_empty_dir_returns_clean(tmp_path: Path) -> None:
    result = scan_directory(tmp_path)

    assert result.passed is True
    assert result.files_scanned == 0
    assert result.violations == []


def test_scan_directory_skips_before_scanning(tmp_path: Path, monkeypatch) -> None:
    clean_file = _write_file(tmp_path, "openblade/clean.py", "print('ok')\n")
    _write_file(tmp_path, "openblade/nas/tape_orchestrator.py", "library.load(1, 0)\n")
    _write_file(tmp_path, "tests/test_guard_target.py", "library.load(1, 0)\n")

    scanned_paths: list[str] = []
    original_scan_file = import_guard.scan_file

    def tracking_scan_file(file_path: Path, root: Path):
        scanned_paths.append(file_path.relative_to(root).as_posix())
        return original_scan_file(file_path, root)

    monkeypatch.setattr(import_guard, "scan_file", tracking_scan_file)

    result = scan_directory(tmp_path)

    assert result.files_scanned == 1
    assert scanned_paths == [clean_file.relative_to(tmp_path).as_posix()]


def test_pattern_in_comment_is_flagged(tmp_path: Path) -> None:
    file_path = _write_file(tmp_path, "openblade/service.py", "# ltfs.write_bytes(path, data)\nx = 1\n")

    violations = scan_file(file_path, tmp_path)

    assert len(violations) == 1
    assert violations[0].pattern == "ltfs.write_bytes("


def test_pattern_in_docstring_is_flagged(tmp_path: Path) -> None:
    file_path = _write_file(tmp_path, "openblade/service.py", '"""ltfs.read_bytes(path)"""\nx = 1\n')

    violations = scan_file(file_path, tmp_path)

    assert len(violations) == 1
    assert violations[0].pattern == "ltfs.read_bytes("


def test_format_report_includes_error_code() -> None:
    result = GuardResult(
        violations=[GuardViolation(file="openblade/service.py", line_number=3, line="library.load(1, 0)", pattern="library.load(")],
        files_scanned=1,
    )

    report = format_report(result)

    assert SAFETY_003 in report


def test_format_report_includes_line_number() -> None:
    result = GuardResult(
        violations=[GuardViolation(file="openblade/service.py", line_number=7, line="ltfs.format('VOL001', token)", pattern="ltfs.format(")],
        files_scanned=1,
    )

    report = format_report(result)

    assert ":7" in report


def test_guard_result_passed_true_when_no_violations() -> None:
    assert GuardResult().passed is True


def test_guard_result_passed_false_when_violations() -> None:
    result = GuardResult(violations=[GuardViolation(file="openblade/service.py", line_number=1, line="library.load(1, 0)", pattern="library.load(")])

    assert result.passed is False

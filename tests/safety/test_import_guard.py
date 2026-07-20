"""
Safety test: SAFETY_003 — no direct hardware access outside allowed modules.
Run with: pytest tests/safety/test_import_guard.py
"""


def test_no_direct_hardware_access_in_codebase():
    """
    Scan the entire openblade/ source tree.
    Fails with SAFETY_003 if any disallowed file contains direct hardware calls.
    """
    import pathlib

    from openblade.safety.import_guard import SAFETY_003, format_report, scan_directory

    root = pathlib.Path(__file__).parent.parent.parent / "openblade"
    result = scan_directory(root)

    if not result.passed:
        report = format_report(result)
        raise AssertionError(
            f"{SAFETY_003}: Direct tape hardware access detected outside allowed modules.\n{report}"
        )


def _flagged(line: str) -> bool:
    from openblade.safety.import_guard import FORBIDDEN_PATTERNS, _matches_pattern

    return any(_matches_pattern(line, pattern) for pattern in FORBIDDEN_PATTERNS)


def test_guard_still_catches_real_tape_hardware_access():
    # Tape data/robotics calls must remain forbidden after the pattern tightening.
    assert _flagged("        self.ltfs.write_bytes(handle, path, data)")
    assert _flagged("    result = context.ltfs.format(barcode, confirmation)")
    assert _flagged("        data = self.ltfs.read_bytes(barcode, tape_path)")
    assert _flagged("        result = context.library.load(slot_id, drive_id)")
    assert _flagged("    for slot in context.library.inventory().slots:")


def test_guard_no_longer_flags_plain_pathlib_reads():
    # The over-broad bare ".read_bytes(" pattern was removed: a plain filesystem read
    # (e.g. hashing a SQLite backup in openblade/dr.py) is not tape access. The
    # destructive-direction ".write_bytes(" net is kept (see test below).
    assert not _flagged("    return hashlib.sha256(path.read_bytes()).hexdigest()")
    assert not _flagged("    content = source.read_text()")


def test_guard_still_catches_bare_write_bytes():
    # A direct tape WRITE via any handle name stays forbidden (preventive net).
    assert _flagged("        self._backend.write_bytes(handle, data)")


def test_mount_handling_files_stay_read_only():
    # routes_ltfs.py and nas_config.py hold LTFS mount handles but are allowlisted as
    # direct-access points. Pin them read-only: a future data-write/format/RW-mount
    # endpoint added here MUST route through the orchestrator, so its addition should
    # trip this test rather than pass silently under the file-level allowlist.
    import pathlib

    repo_root = pathlib.Path(__file__).parent.parent.parent
    for rel in ("openblade/api/routes_ltfs.py", "openblade/api/nas_config.py"):
        text = (repo_root / rel).read_text(encoding="utf-8")
        for forbidden in ("ltfs.write_bytes(", "ltfs.format(", "MountMode.READ_WRITE"):
            assert forbidden not in text, f"{rel} must stay read-only; found {forbidden!r}"

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


def test_guard_no_longer_flags_plain_pathlib_io():
    # The over-broad bare ".read_bytes("/".write_bytes(" patterns were removed: plain
    # filesystem I/O (e.g. hashing a SQLite backup in openblade/dr.py) is not tape access.
    assert not _flagged("    return hashlib.sha256(path.read_bytes()).hexdigest()")
    assert not _flagged("    dest_path.write_bytes(data)")
    assert not _flagged("    content = source.read_text()")

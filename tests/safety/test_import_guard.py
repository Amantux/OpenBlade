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

"""Registry of public operational error codes."""

from __future__ import annotations

from openblade.nas.types import ErrorCodeEntry

KNOWN_ERROR_CODES: list[ErrorCodeEntry] = [
    ErrorCodeEntry(
        code="SAFETY_001",
        severity="error",
        title="Tape operation without orchestrator",
        description="A tape hardware call was made outside TapeOperationOrchestrator.",
        action="Route all tape operations through TapeOperationOrchestrator.execute().",
    ),
    ErrorCodeEntry(
        code="SAFETY_002",
        severity="error",
        title="Format without confirmation",
        description="A tape format was attempted without confirmed_format=True.",
        action="Set extras={'confirmed_format': True} in TapeOpRequest.",
    ),
    ErrorCodeEntry(
        code="SAFETY_003",
        severity="error",
        title="Forbidden import pattern",
        description="Direct hardware access detected in a non-orchestrator module.",
        action="Use TapeOperationOrchestrator for all tape operations.",
    ),
    ErrorCodeEntry(
        code="AUTH_001",
        severity="error",
        title="Authentication required",
        description="Request requires authentication.",
        action="Log in via POST /aml/users/login and include session cookie.",
    ),
    ErrorCodeEntry(
        code="AUTH_002",
        severity="error",
        title="Insufficient permissions",
        description="Authenticated user lacks required RBAC permission.",
        action="Request a role with the required permission from an admin.",
    ),
    ErrorCodeEntry(
        code="NAS_001",
        severity="error",
        title="Archive lifecycle failure",
        description="One or more steps of the post-write archive lifecycle failed.",
        action="Check archive lifecycle logs and retry the failed step.",
    ),
    ErrorCodeEntry(
        code="NAS_002",
        severity="warning",
        title="Missing tape in set",
        description="A tape required for restore is not available.",
        action="Load the required tape barcode into the library.",
    ),
    ErrorCodeEntry(
        code="CAT_001",
        severity="warning",
        title="Manifest missing",
        description="Tape is present but manifest.json not found in /.openblade/.",
        action="Run catalog rebuild to attempt recovery, or re-archive the tape.",
    ),
    ErrorCodeEntry(
        code="CAT_002",
        severity="error",
        title="Manifest corrupt",
        description="manifest.json exists but could not be parsed.",
        action="Use a versioned manifest to restore, or rebuild from catalog shard.",
    ),
    ErrorCodeEntry(
        code="SYS_001",
        severity="error",
        title="Database unreachable",
        description="The catalog database could not be reached.",
        action="Check db_url configuration and database file permissions.",
    ),
]

"""OpenBlade version metadata."""

from __future__ import annotations

import sys

OPENBLADE_VERSION = "0.2.0"


def get_version_info() -> dict[str, str]:
    """Return public version metadata (no internal runtime details)."""
    return {
        "version": OPENBLADE_VERSION,
        "git_commit": "unknown",
        "build_date": "unknown",
    }


def get_full_version_info() -> dict[str, str]:
    """Return full version metadata including runtime details (for authenticated use)."""
    return {
        **get_version_info(),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "environment": "development",
    }

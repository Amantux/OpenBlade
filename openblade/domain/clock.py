"""Naive-UTC timestamps for catalog rows.

The catalog stores naive datetimes that mean UTC. ``datetime.utcnow()`` is
deprecated since Python 3.12; this keeps the same value without the warning.
"""

from __future__ import annotations

from datetime import UTC, datetime


def naive_utcnow() -> datetime:
    """Current UTC time as a naive datetime (catalog convention)."""
    return datetime.now(UTC).replace(tzinfo=None)

"""Canonical NAS tape-path layout.

The on-tape location of an archived NAS file is derived from its dataset name and
relative path. Ingest (writing) and hydration (reading back) MUST agree on this
formula byte-for-byte, or a restore silently reads the wrong key. Keeping the one
formula here makes that agreement structural instead of a convention two modules
independently re-encode.
"""

from __future__ import annotations

from pathlib import PurePosixPath


def dataset_tape_path(dataset_name: str, relative_path: str) -> PurePosixPath:
    """Return the on-tape path for a file: ``/<dataset name>/<relative path>``."""
    return PurePosixPath("/") / dataset_name / relative_path

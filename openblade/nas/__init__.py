"""NAS configuration models and services."""

from openblade.nas.types import (
    CacheDriveConfig,
    NasShareDefinition,
    SourceStreamConfig,
    StoragePolicy,
)

__all__ = [
    "CacheDriveConfig",
    "NasService",
    "NasShareDefinition",
    "SourceStreamConfig",
    "StoragePolicy",
]


def __getattr__(name: str):
    if name == "NasService":
        from openblade.nas.service import NasService

        return NasService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

"""Active library context helpers for AML requests."""

from __future__ import annotations

from contextvars import ContextVar, Token
from urllib.parse import urlparse, urlunparse

from openblade.catalog.models import LibraryInstance
from openblade.catalog.repository import CatalogRepository

_ACTIVE_LIBRARY_ID: ContextVar[str] = ContextVar("openblade_active_library_id", default="")
_LIBRARY_PROFILES_BY_PORT: dict[int, dict[str, int]] = {
    8010: {
        "drive_count": 3,
        "slot_count": 24,
        "occupied_slot_count": 21,
        "active_job_count": 2,
        "alerts_count": 0,
    },
    8011: {
        "drive_count": 2,
        "slot_count": 18,
        "occupied_slot_count": 14,
        "active_job_count": 1,
        "alerts_count": 1,
    },
    8012: {
        "drive_count": 1,
        "slot_count": 12,
        "occupied_slot_count": 8,
        "active_job_count": 0,
        "alerts_count": 0,
    },
}
_DEFAULT_LIBRARY_PROFILE: dict[str, int] = {
    "drive_count": 1,
    "slot_count": 12,
    "occupied_slot_count": 12,
    "active_job_count": 0,
    "alerts_count": 0,
}
_SERVICE_HOSTS_BY_PORT = {
    8010: "emulator-1",
    8011: "emulator-2",
    8012: "emulator-3",
}


def set_active_library_id(value: str) -> Token[str]:
    return _ACTIVE_LIBRARY_ID.set(value.strip())


def reset_active_library_id(token: Token[str]) -> None:
    _ACTIVE_LIBRARY_ID.reset(token)


def get_active_library_id() -> str:
    return _ACTIVE_LIBRARY_ID.get().strip()


def get_active_library(repo: CatalogRepository) -> LibraryInstance | None:
    # Returns None when no library header is present.
    # Callers should treat None as "use system default" — NOT as "all libraries".
    # True multi-library aggregation is a v2 feature.
    active_library_id = get_active_library_id()
    if not active_library_id:
        return None
    try:
        library = repo.get_library_instance(int(active_library_id))
    except ValueError:
        return None
    if library is not None and library.enabled:
        return library
    return None


def _get_default_library(repo: CatalogRepository) -> LibraryInstance | None:
    enabled_libraries = [library for library in repo.list_library_instances() if library.enabled]
    if enabled_libraries:
        return enabled_libraries[0]
    return None


def get_library_profile(library: LibraryInstance | None) -> dict[str, int]:
    if library is None:
        return dict(_DEFAULT_LIBRARY_PROFILE)
    parsed = urlparse(library.emulator_url)
    if parsed.port and parsed.port in _LIBRARY_PROFILES_BY_PORT:
        return dict(_LIBRARY_PROFILES_BY_PORT[parsed.port])
    return dict(_DEFAULT_LIBRARY_PROFILE)


def resolve_emulator_url(emulator_url: str) -> str:
    parsed = urlparse(emulator_url.rstrip("/"))
    if parsed.hostname in {"localhost", "127.0.0.1"} and parsed.port in _SERVICE_HOSTS_BY_PORT:
        return urlunparse(parsed._replace(netloc=f"{_SERVICE_HOSTS_BY_PORT[parsed.port]}:8010"))
    return emulator_url.rstrip("/")


def get_active_emulator_url(repo: CatalogRepository) -> str:
    library = get_active_library(repo) or _get_default_library(repo)
    if library is not None:
        return resolve_emulator_url(library.emulator_url)
    return resolve_emulator_url("http://localhost:8010")

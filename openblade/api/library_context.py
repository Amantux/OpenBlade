"""Active library context helpers for AML requests."""

from __future__ import annotations

from contextvars import ContextVar, Token
from urllib.parse import urlparse, urlunparse

from openblade.catalog.models import LibraryInstance
from openblade.catalog.repository import CatalogRepository

_ACTIVE_LIBRARY_ID: ContextVar[str] = ContextVar("openblade_active_library_id", default="")
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
    active_library_id = get_active_library_id()
    if active_library_id:
        try:
            library = repo.get_library_instance(int(active_library_id))
        except ValueError:
            library = None
        if library is not None and library.enabled:
            return library

    enabled_libraries = [library for library in repo.list_library_instances() if library.enabled]
    if enabled_libraries:
        return enabled_libraries[0]
    return None


def resolve_emulator_url(emulator_url: str) -> str:
    parsed = urlparse(emulator_url.rstrip("/"))
    if parsed.hostname in {"localhost", "127.0.0.1"} and parsed.port in _SERVICE_HOSTS_BY_PORT:
        return urlunparse(parsed._replace(netloc=f"{_SERVICE_HOSTS_BY_PORT[parsed.port]}:8010"))
    return emulator_url.rstrip("/")


def get_active_emulator_url(repo: CatalogRepository) -> str:
    library = get_active_library(repo)
    if library is not None:
        return resolve_emulator_url(library.emulator_url)
    return resolve_emulator_url("http://localhost:8010")

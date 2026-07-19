"""Wiring tests: config selects the Web Services robotics backend."""

from __future__ import annotations

import pytest

from openblade.bootstrap import _create_scalar_http_library
from openblade.config import BackendMode, OpenBladeConfig
from openblade.domain.errors import RealHardwareDisabledError
from openblade.hardware.scalar_http import ScalarHttpLibraryBackend


def _config(**overrides: object) -> OpenBladeConfig:
    base: dict[str, object] = {
        "backend": BackendMode.REAL,
        "real_hardware_enabled": True,
        "robotics_transport": "webservices",
    }
    base.update(overrides)
    return OpenBladeConfig(**base)  # type: ignore[arg-type]


def test_webservices_transport_requires_scalar_url() -> None:
    with pytest.raises(RealHardwareDisabledError):
        _create_scalar_http_library(_config(scalar_url=None))


def test_webservices_transport_builds_scalar_http_backend() -> None:
    library = _create_scalar_http_library(
        _config(scalar_url="https://library.example/", scalar_user="admin")
    )

    assert isinstance(library, ScalarHttpLibraryBackend)

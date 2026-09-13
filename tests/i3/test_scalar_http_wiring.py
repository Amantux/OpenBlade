"""Wiring tests: config selects the Web Services robotics backend."""

from __future__ import annotations

import pytest

from openblade.bootstrap import _create_scalar_http_library
from openblade.config import BackendMode, OpenBladeConfig
from openblade.domain.errors import RealHardwareDisabledError
from openblade.domain.policies import RealHardwareGuard
from openblade.hardware.runner import SafeRunner
from openblade.hardware.scalar_http import ScalarHttpLibraryBackend


def _config(**overrides: object) -> OpenBladeConfig:
    base: dict[str, object] = {
        "backend": BackendMode.REAL,
        "real_hardware_enabled": True,
        "robotics_transport": "webservices",
    }
    base.update(overrides)
    return OpenBladeConfig(**base)  # type: ignore[arg-type]


def _guard() -> RealHardwareGuard:
    return RealHardwareGuard(
        config_backend="real",
        config_real_hardware_enabled=True,
        operator_acknowledgment="test",
    )


def test_webservices_transport_requires_scalar_url() -> None:
    with pytest.raises(RealHardwareDisabledError):
        _create_scalar_http_library(_config(scalar_url=None), SafeRunner(dry_run=True), _guard())


def test_webservices_transport_builds_scalar_http_backend() -> None:
    library = _create_scalar_http_library(
        _config(scalar_url="https://library.example/", scalar_user="admin"),
        SafeRunner(dry_run=True),
        _guard(),
    )

    assert isinstance(library, ScalarHttpLibraryBackend)
    # The drive correlation is wired but deferred: building it probes local SCSI,
    # which a robotics-only deployment must not be forced to do at startup.
    assert library._correlation_factory is not None
    assert library._correlation is None

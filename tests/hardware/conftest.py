from __future__ import annotations

import os
from importlib import import_module
from pathlib import Path

import pytest

from openblade.bootstrap import create_context, reset_context
from openblade.config import load_config
from openblade.domain.policies import RealHardwareGuard
from openblade.hardware.mtx import MtxChangerBackend
from openblade.hardware.runner import SafeRunner
from openblade.hardware.safety import require_real_hardware


def pytest_configure(config):
    config.addinivalue_line("markers", "real_hardware: mark test as requiring real tape hardware")


def _skip_if_no_real_hardware():
    if not (
        os.environ.get("OPENBLADE_BACKEND") == "real"
        and os.environ.get("OPENBLADE_REAL_HARDWARE_ENABLED") == "true"
    ):
        pytest.skip(
            "Real hardware not enabled. Set OPENBLADE_BACKEND=real and "
            "OPENBLADE_REAL_HARDWARE_ENABLED=true"
        )


def _real_hardware_guard() -> RealHardwareGuard:
    config = load_config()
    return require_real_hardware(config)


@pytest.fixture(autouse=False)
def real_hardware_guard():
    _skip_if_no_real_hardware()


@pytest.fixture
def runner():
    return SafeRunner(dry_run=False)


@pytest.fixture
def changer_device():
    _skip_if_no_real_hardware()
    return os.environ.get("OPENBLADE_CHANGER_DEVICE", "/dev/sg0")


@pytest.fixture
def drive_devices():
    _skip_if_no_real_hardware()
    devs = os.environ.get("OPENBLADE_DRIVE_DEVICES", "/dev/nst0")
    return [d.strip() for d in devs.split(",") if d.strip()]


@pytest.fixture
def scratch_barcodes():
    _skip_if_no_real_hardware()
    barcodes_str = os.environ.get("OPENBLADE_SCRATCH_BARCODES", "")
    if not barcodes_str:
        pytest.skip("No scratch barcodes configured (OPENBLADE_SCRATCH_BARCODES)")
    return [b.strip() for b in barcodes_str.split(",") if b.strip()]


@pytest.fixture
def scratch_barcode(scratch_barcodes):
    return scratch_barcodes[0]


@pytest.fixture
def real_library_backend(real_hardware_guard):
    changer = os.environ.get("OPENBLADE_CHANGER_DEVICE", "/dev/sg0")
    try:
        module = import_module("openblade.hardware.library")
        backend_cls = module.RealLibraryBackend
        try:
            return backend_cls(changer_device=changer)
        except TypeError:
            return backend_cls(changer)
    except Exception:
        return MtxChangerBackend(
            device=changer,
            guard=_real_hardware_guard(),
            runner=SafeRunner(dry_run=False),
        )


@pytest.fixture
def real_app_context(real_hardware_guard):
    config = load_config()
    try:
        context = create_context(config)
    except NotImplementedError:
        pytest.skip("Real AppContext is not implemented in this repository yet")
    reset_context(context)
    return context


@pytest.fixture
def default_db_path():
    db_url = os.environ.get("OPENBLADE_DB_URL", load_config().db_url)
    if not db_url.startswith("sqlite:///"):
        pytest.skip(f"SQLite-backed catalog required, got {db_url!r}")
    return Path(db_url.removeprefix("sqlite:///"))


@pytest.fixture
def tmp_mount_dir(tmp_path):
    mount = tmp_path / "ltfs_mount"
    mount.mkdir()
    return mount

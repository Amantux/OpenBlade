from pathlib import PurePosixPath

import pytest

from openblade.catalog.db import get_session, init_db
from openblade.catalog.repository import CatalogRepository
from openblade.config import OpenBladeConfig, load_config
from openblade.domain.errors import (
    BarcodeMismatchError,
    ChecksumMismatchError,
    FormatRequiresConfirmationError,
    RealHardwareDisabledError,
    TapeMountedError,
)
from openblade.domain.models import MountMode
from openblade.domain.policies import FormatConfirmation, RealHardwareGuard, SafetyToken
from openblade.fuse.filesystem import CatalogFilesystem
from openblade.hardware.mtx import MtxChangerBackend
from openblade.hardware.safety import require_real_hardware
from openblade.jobs.archive import ArchiveRequest, run_archive_job
from openblade.jobs.restore import RestoreRequest, run_restore_job
from openblade.simulator.faults import FaultConfig, FaultType
from openblade.simulator.library import MockLibraryBackend
from openblade.simulator.ltfs_volume import MockLTFSBackend
from openblade.simulator.scenarios import one_drive_twenty_slots_five_cartridges


def _catalog() -> CatalogRepository:
    init_db("sqlite:///:memory:")
    return CatalogRepository(get_session())


def test_no_real_hardware_command_executes_in_default_config() -> None:
    with pytest.raises(RealHardwareDisabledError):
        require_real_hardware(load_config())


def test_mtx_backend_refuses_without_valid_guard() -> None:
    with pytest.raises(RealHardwareDisabledError):
        MtxChangerBackend("/dev/sg0", RealHardwareGuard("mock", False, ""))


def test_format_without_confirmation_raises() -> None:
    library = MockLibraryBackend()
    library.seed_slots(["PHO001L8"])
    ltfs = MockLTFSBackend(library)
    with pytest.raises(FormatRequiresConfirmationError):
        ltfs.format("PHO001L8", None)


def test_format_with_mismatched_barcode_raises() -> None:
    library = MockLibraryBackend()
    library.seed_slots(["PHO001L8"])
    ltfs = MockLTFSBackend(library)
    with pytest.raises(BarcodeMismatchError):
        ltfs.format(
            "PHO001L8", FormatConfirmation("PHO002L8", SafetyToken.generate("format", "PHO001L8"))
        )


def test_unload_while_mounted_raises() -> None:
    library = MockLibraryBackend()
    library.seed_slots(["PHO001L8"])
    library.load(1, 0)
    ltfs = MockLTFSBackend(library)
    ltfs.format(
        "PHO001L8", FormatConfirmation("PHO001L8", SafetyToken.generate("format", "PHO001L8"))
    )
    ltfs.mount("PHO001L8", MountMode.READ_ONLY)
    with pytest.raises(TapeMountedError):
        library.unload(0, 1)


def test_direct_hardware_calls_raise_when_disabled() -> None:
    with pytest.raises(RealHardwareDisabledError):
        require_real_hardware(OpenBladeConfig())


def test_archive_does_not_mark_archived_until_verified(tmp_path) -> None:
    catalog = _catalog()
    library, _ = one_drive_twenty_slots_five_cartridges()
    faulty = MockLTFSBackend(
        library, fault_config=FaultConfig.with_fault(FaultType.CHECKSUM_MISMATCH)
    )
    barcode = str(library.inventory().slots[0].barcode)
    library.load(1, 0)
    faulty.format(barcode, FormatConfirmation(barcode, SafetyToken.generate("format", barcode)))
    library.unload(0, 1)
    group = catalog.create_volume_group("photos")
    catalog.add_barcode_to_volume_group(group.id, barcode)
    source = tmp_path / "source"
    source.mkdir()
    (source / "bad.txt").write_text("bad")
    job = catalog.create_job("archive", {})
    with pytest.raises(ChecksumMismatchError):
        run_archive_job(ArchiveRequest(source, "photos"), library, faulty, catalog, job.id)
    record = catalog.get_file_record("/photos/bad.txt")
    assert record is not None
    assert record.instances[-1].state == "pending"


def test_restore_uses_readonly_mount_only(tmp_path) -> None:
    catalog = _catalog()
    library, ltfs = one_drive_twenty_slots_five_cartridges()
    barcode = str(library.inventory().slots[0].barcode)
    library.load(1, 0)
    ltfs.format(barcode, FormatConfirmation(barcode, SafetyToken.generate("format", barcode)))
    handle = ltfs.mount(barcode, MountMode.READ_WRITE)
    source = tmp_path / "payload.txt"
    source.write_text("restore me")
    ltfs.write_file(handle, source, PurePosixPath("/photos/payload.txt"))
    ltfs.unmount(handle)
    library.unload(0, 1)
    group = catalog.create_volume_group("photos")
    import hashlib

    record = catalog.create_file_record(
        "/photos/payload.txt",
        source.stat().st_size,
        hashlib.sha256(source.read_bytes()).hexdigest(),
        group.id,
    )
    instance = catalog.create_file_instance(record.id, barcode, "/photos/payload.txt")
    catalog.mark_instance_archived(instance.id)
    modes = []
    original_mount = ltfs.mount

    def tracking_mount(barcode: str, mode: MountMode):
        modes.append(mode)
        return original_mount(barcode, mode)

    ltfs.mount = tracking_mount
    job = catalog.create_job("restore", {})
    destination = tmp_path / "out"
    destination.mkdir()
    run_restore_job(
        RestoreRequest("/photos/payload.txt", destination), library, ltfs, catalog, job.id
    )
    assert modes == [MountMode.READ_ONLY]


def test_fuse_write_blocked(tmp_path) -> None:
    catalog = _catalog()
    group = catalog.create_volume_group("photos")
    catalog.create_file_record("/photos/a.txt", 1, "abc", group.id)
    fs = CatalogFilesystem(catalog, cache_dir=str(tmp_path / "cache"))
    with pytest.raises(PermissionError):
        fs.write("/photos/a.txt", b"x")


def test_format_endpoint_requires_token(app_context) -> None:
    from fastapi.testclient import TestClient

    from openblade.api.main import app
    from openblade.bootstrap import reset_context

    reset_context(app_context)
    client = TestClient(app)
    login = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert login.status_code == 200
    session_id = login.cookies.get("sessionID")
    assert session_id is not None
    barcode = next(item["barcode"] for item in client.get("/cartridges/").json() if not item["barcode"].startswith("CLN"))
    response = client.post(
        "/cartridges/format/confirm",
        json={"barcode": barcode, "token": "bad-token"},
        headers={
            "Cookie": f"sessionID={session_id}",
            "X-Openblade-Service-Token": "openblade-controller-dev-token-do-not-expose",
        },
    )
    assert response.status_code == 400

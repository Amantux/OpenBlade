from __future__ import annotations

from pathlib import Path, PurePosixPath

from openblade.bootstrap import create_context
from openblade.config import BackendMode, OpenBladeConfig
from openblade.domain.models import Barcode, MountMode, MountState
from openblade.domain.policies import RealHardwareGuard
from openblade.hardware.discovery import LibraryDiscovery, ScsiDevice
from openblade.hardware.library import RealLibraryBackend
from openblade.hardware.ltfs import RealLTFSBackend
from openblade.hardware.mtx import SAMPLE_MTX_LOADED, MtxChangerBackend
from openblade.hardware.runner import SafeRunner


def _discovery() -> LibraryDiscovery:
    return LibraryDiscovery(
        changers=[
            ScsiDevice(
                host=6,
                bus=0,
                target=0,
                lun=0,
                device_type="mediumx",
                vendor="IBM",
                model="03584L22",
                revision="0060",
                sg_device="/dev/sg0",
                block_device="/dev/smc0",
            )
        ],
        drives=[
            ScsiDevice(
                host=6,
                bus=0,
                target=1,
                lun=0,
                device_type="tape",
                vendor="IBM",
                model="ULTRIUM-TD8",
                revision="H3S4",
                sg_device="/dev/sg1",
                block_device="/dev/st0",
            ),
            ScsiDevice(
                host=6,
                bus=0,
                target=2,
                lun=0,
                device_type="tape",
                vendor="IBM",
                model="ULTRIUM-TD8",
                revision="H3S4",
                sg_device="/dev/sg2",
                block_device="/dev/st1",
            ),
        ],
        sg_map={"/dev/sg0": "/dev/smc0", "/dev/sg1": "/dev/st0", "/dev/sg2": "/dev/st1"},
    )


def test_real_library_backend_maps_inventory_from_mtx_fixture(tmp_path: Path) -> None:
    del tmp_path
    config = OpenBladeConfig(
        backend=BackendMode.REAL,
        real_hardware_enabled=True,
        hardware_dry_run=True,
    )
    guard = RealHardwareGuard("real", True, "ack")
    changer = MtxChangerBackend(
        device="/dev/sg0",
        guard=guard,
        runner=SafeRunner(dry_run=True),
        sample_status_output=SAMPLE_MTX_LOADED,
    )
    backend = RealLibraryBackend(config=config, discovery=_discovery(), changer=changer)

    inventory = backend.inventory()

    assert inventory.library_id == "sg0"
    assert len(inventory.drives) == 2
    assert len(inventory.slots) == 4
    assert str(inventory.drives[0].barcode) == "PHO001L8"
    assert str(inventory.slots[1].barcode) == "PHO002L8"
    assert backend.drive_device(0) == "/dev/st0"


class _FakeLibrary:
    def __init__(self) -> None:
        self.states: list[tuple[int, MountState]] = []

    def find_drive_by_barcode(self, barcode: str) -> int | None:
        return 0 if Barcode(barcode).value == "PHO001L8" else None

    def drive_device(self, drive_id: int) -> str:
        assert drive_id == 0
        return "/dev/st0"

    def set_drive_mount_state(self, drive_id: int, mount_state: MountState) -> None:
        self.states.append((drive_id, mount_state))


def test_real_ltfs_backend_supports_capability_validation_in_dry_run(tmp_path: Path) -> None:
    library = _FakeLibrary()
    backend = RealLTFSBackend(
        library=library,  # type: ignore[arg-type]
        guard=RealHardwareGuard("real", True, "ack"),
        runner=SafeRunner(dry_run=True),
        mount_root=tmp_path / "ltfs",
    )

    handle = backend.mount("PHO001L8", MountMode.READ_WRITE)
    instance = backend.write_bytes(handle, PurePosixPath("/verify/ping.txt"), b"hello")
    stat = backend.stat(handle, PurePosixPath("/verify/ping.txt"))
    dest = tmp_path / "roundtrip.txt"
    read_result = backend.read_file(handle, PurePosixPath("/verify/ping.txt"), dest)
    unmount_result = backend.unmount(handle)

    assert instance.barcode.value == "PHO001L8"
    assert stat.size_bytes == 5
    assert read_result.success is True
    assert dest.read_bytes() == b"hello"
    assert unmount_result.success is True
    assert library.states == [
        (0, MountState.MOUNTED_RW),
        (0, MountState.UNMOUNTED),
    ]


def test_create_context_supports_guarded_real_hardware_dry_run(tmp_path: Path) -> None:
    context = create_context(
        OpenBladeConfig(
            backend=BackendMode.REAL,
            real_hardware_enabled=True,
            hardware_dry_run=True,
            db_url=f"sqlite:///{tmp_path / 'real-context.db'}",
            ltfs_mount_root=str(tmp_path / "ltfs-root"),
        )
    )

    assert type(context.library).__name__ == "RealLibraryBackend"
    assert type(context.ltfs).__name__ == "RealLTFSBackend"
    assert context.library.inventory().library_id == "sg0"

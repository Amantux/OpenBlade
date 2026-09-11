"""RealLibraryBackend drive mapping on a three-drive library (no real hardware).

A fake SafeRunner replays `mtx status` and `sg_inq` output, so the whole
construction path — discovery, changer, correlation — runs exactly as it will at
the i3, including the refusal when the declared serial map disagrees with the
attached drives.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from openblade.config import BackendMode, OpenBladeConfig, parse_drive_serial_map
from openblade.domain.errors import DriveCorrelationError
from openblade.hardware.discovery import (
    LibraryDiscovery,
    find_tape_changers,
    find_tape_drives,
    parse_lsscsi,
)
from openblade.hardware.library import RealLibraryBackend
from openblade.hardware.mtx import SAMPLE_MTX_THREE_DRIVES
from openblade.hardware.runner import CommandResult, SafeRunner

DEVICES = ("/dev/nst0", "/dev/nst1", "/dev/nst2")
# Device order is NOT element order: nst0 is DTE 2, nst1 is DTE 0, nst2 is DTE 1.
SERIALS = {"/dev/nst0": "SER-C", "/dev/nst1": "SER-A", "/dev/nst2": "SER-B"}
SERIAL_MAP = "SER-A:0,SER-B:1,SER-C:2"

LSSCSI_THREE_DRIVES = """
[6:0:0:0]    mediumx QUANTUM  Scalar i3        0060  /dev/smc0  /dev/sg3
[6:0:1:0]    tape    IBM      ULTRIUM-TD8      H3S4  /dev/nst0  /dev/sg4
[6:0:2:0]    tape    IBM      ULTRIUM-TD8      H3S4  /dev/nst1  /dev/sg5
[6:0:3:0]    tape    IBM      ULTRIUM-TD8      H3S4  /dev/nst2  /dev/sg6
"""


class FakeRunner(SafeRunner):
    """Replays mtx/sg_inq output for a three-drive library."""

    def __init__(self, serials: dict[str, str]) -> None:
        super().__init__(dry_run=False)
        self.serials = serials

    def run(
        self,
        args: list[str],
        timeout: int | None = None,
        redact_args: list[int] | None = None,
    ) -> CommandResult:
        if args[0] == "mtx":
            return self._ok(args, SAMPLE_MTX_THREE_DRIVES)
        if args[0] == "sg_inq":
            serial = self.serials.get(args[-1], "")
            return self._ok(
                args,
                "standard INQUIRY:\n"
                "    length=96 (0x60)   Peripheral device type: tape\n"
                " Vendor identification: IBM\n"
                " Product identification: ULTRIUM-TD8\n"
                " Product revision level: H3S4\n"
                + (f" Unit serial number: {serial}\n" if serial else ""),
            )
        raise AssertionError(f"unexpected command {args!r}")

    @staticmethod
    def _ok(args: list[str], stdout: str) -> CommandResult:
        return CommandResult(
            args=list(args), returncode=0, stdout=stdout, stderr="", elapsed_seconds=0.0
        )


def _discovery() -> LibraryDiscovery:
    devices = parse_lsscsi(LSSCSI_THREE_DRIVES)
    return LibraryDiscovery(
        changers=find_tape_changers(devices),
        drives=find_tape_drives(devices),
        sg_map={},
    )


def _config(
    tmp_path: Path, *, serial_map: str = "", devices: tuple[str, ...] = DEVICES
) -> OpenBladeConfig:
    return OpenBladeConfig(
        backend=BackendMode.REAL,
        real_hardware_enabled=True,
        changer_device="/dev/sg3",
        drive_devices=devices,
        drive_serial_map=parse_drive_serial_map(serial_map),
        ltfs_mount_root=str(tmp_path / "ltfs"),
    )


def _backend(config: OpenBladeConfig, serials: dict[str, str] | None = None) -> RealLibraryBackend:
    return RealLibraryBackend(
        config=config,
        runner=FakeRunner(serials if serials is not None else SERIALS),
        discovery=_discovery(),
    )


def test_three_drives_are_visible_in_inventory(tmp_path: Path) -> None:
    inventory = _backend(_config(tmp_path, serial_map=SERIAL_MAP)).inventory()
    assert [drive.drive_id for drive in inventory.drives] == [0, 1, 2]


def test_verified_map_binds_elements_to_the_right_devices(tmp_path: Path) -> None:
    backend = _backend(_config(tmp_path, serial_map=SERIAL_MAP))

    assert backend.correlation.verified is True
    assert backend.drive_device(0) == "/dev/nst1"
    assert backend.drive_device(1) == "/dev/nst2"
    assert backend.drive_device(2) == "/dev/nst0"


def test_mismatched_map_refuses_to_construct_the_backend(tmp_path: Path) -> None:
    """The safety guard: a stale/incorrect map must never degrade to a guess."""
    with pytest.raises(DriveCorrelationError):
        _backend(_config(tmp_path, serial_map="SER-A:0,SER-B:1,SER-GONE:2"))


def test_positional_fallback_warns_and_stays_unverified(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="openblade.hardware.correlation"):
        backend = _backend(_config(tmp_path))

    assert backend.correlation.verified is False
    assert backend.drive_device(1) == "/dev/nst1"
    assert any("DRIVE ORDER UNVERIFIED" in record.getMessage() for record in caplog.records)


def test_configured_devices_win_over_discovery_order(tmp_path: Path) -> None:
    """OPENBLADE_DRIVE_DEVICES is authoritative; discovery order is only a fallback."""
    reordered = ("/dev/nst2", "/dev/nst1", "/dev/nst0")
    backend = _backend(_config(tmp_path, devices=reordered))
    assert backend.correlation.devices_in_drive_order() == list(reordered)


def test_discovery_order_is_used_when_no_devices_are_configured(tmp_path: Path) -> None:
    backend = _backend(_config(tmp_path, devices=()))
    assert backend.correlation.devices_in_drive_order() == list(DEVICES)


def test_unknown_drive_element_raises_keyerror(tmp_path: Path) -> None:
    backend = _backend(_config(tmp_path, serial_map=SERIAL_MAP))
    with pytest.raises(KeyError):
        backend.drive_device(7)

from __future__ import annotations

"""Safe LTFS command helpers for real hardware."""

import re
from dataclasses import dataclass

from openblade.domain.models import OperationResult
from openblade.domain.policies import DryRunPlan, FormatConfirmation, RealHardwareGuard
from openblade.hardware.runner import SafeRunner

SAMPLE_LTFS_DEVICE_LIST = """
[2026-05-19 12:00:00] LTFS14000I Device list:
[2026-05-19 12:00:00] LTFS14001I  0: /dev/st0 (IBM ULTRIUM-TD8)
[2026-05-19 12:00:00] LTFS14001I  1: /dev/st1 (IBM ULTRIUM-TD8)
"""

SAMPLE_LTFS_FORMAT_DRY_RUN = """
Tape barcode: PHO001L8
Tape capacity: 12000000000 bytes
WORM: No
Format would write: LTFS label, index partition, data partition
"""

_DEVICE_RE = re.compile(
    r"LTFS14001I\s+(?P<index>\d+):\s+(?P<device>/dev/\S+)\s+\((?P<description>.+)\)"
)


@dataclass(frozen=True)
class LTFSDevice:
    index: int
    device: str
    description: str


def parse_ltfs_device_list(output: str) -> list[LTFSDevice]:
    """Parse device-list output from LTFS tools."""
    devices: list[LTFSDevice] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _DEVICE_RE.search(line)
        if match is None:
            continue
        devices.append(
            LTFSDevice(
                index=int(match.group("index")),
                device=match.group("device"),
                description=match.group("description"),
            )
        )
    return devices


class LTFSCommandBackend:
    """Minimal LTFS command backend with explicit safety gates."""

    @staticmethod
    def device_list(runner: SafeRunner, guard: RealHardwareGuard) -> list[LTFSDevice]:
        guard.validate()
        if runner.dry_run:
            return parse_ltfs_device_list(SAMPLE_LTFS_DEVICE_LIST)
        result = runner.run(["ltfs", "-o", "device_list"], timeout=60)
        result.raise_on_error()
        return parse_ltfs_device_list(result.stdout)

    @staticmethod
    def format_dry_run_plan(barcode: str, device: str) -> DryRunPlan:
        return DryRunPlan(
            operation="format",
            target=f"format {barcode} on {device}",
            affected_barcodes=[barcode],
            warnings=[
                "Destructive operation.",
                "Writes LTFS metadata and rewrites tape structure.",
                SAMPLE_LTFS_FORMAT_DRY_RUN.strip(),
            ],
            is_destructive=True,
            estimated_duration_seconds=120,
        )

    @staticmethod
    def format_tape(
        barcode: str,
        device: str,
        confirmation: FormatConfirmation,
        guard: RealHardwareGuard,
        runner: SafeRunner,
    ) -> OperationResult:
        guard.validate()
        confirmation.validate(barcode)
        args = ["mkltfs", "-d", device, "-n", barcode, "--force"]
        if runner.dry_run:
            return OperationResult(
                True,
                "dry-run format",
                {"barcode": barcode, "device": device, "args": args},
            )
        result = runner.run(args, timeout=300)
        return OperationResult(
            result.success,
            "formatted" if result.success else "format failed",
            {"stdout": result.stdout, "stderr": result.stderr, "args": args},
        )

    @staticmethod
    def mount_readonly(
        device: str,
        mount_point: str,
        guard: RealHardwareGuard,
        runner: SafeRunner,
    ) -> OperationResult:
        guard.validate()
        args = ["ltfs", mount_point, "-o", f"devname={device},ro"]
        if runner.dry_run:
            return OperationResult(
                True,
                "dry-run mount readonly",
                {"device": device, "mount_point": mount_point, "args": args},
            )
        result = runner.run(args, timeout=300)
        return OperationResult(
            result.success,
            "mounted readonly" if result.success else "readonly mount failed",
            {"stdout": result.stdout, "stderr": result.stderr, "args": args},
        )

    @staticmethod
    def mount_readwrite(
        device: str,
        mount_point: str,
        guard: RealHardwareGuard,
        runner: SafeRunner,
    ) -> OperationResult:
        guard.validate()
        args = ["ltfs", mount_point, "-o", f"devname={device},rw"]
        if runner.dry_run:
            return OperationResult(
                True,
                "dry-run mount readwrite",
                {"device": device, "mount_point": mount_point, "args": args},
            )
        result = runner.run(args, timeout=300)
        return OperationResult(
            result.success,
            "mounted readwrite" if result.success else "readwrite mount failed",
            {"stdout": result.stdout, "stderr": result.stderr, "args": args},
        )

    @staticmethod
    def unmount(
        mount_point: str,
        guard: RealHardwareGuard,
        runner: SafeRunner,
    ) -> OperationResult:
        guard.validate()
        args = ["umount", mount_point]
        if runner.dry_run:
            return OperationResult(
                True,
                "dry-run unmount",
                {"mount_point": mount_point, "args": args},
            )
        result = runner.run(args, timeout=120)
        return OperationResult(
            result.success,
            "unmounted" if result.success else "unmount failed",
            {"stdout": result.stdout, "stderr": result.stderr, "args": args},
        )

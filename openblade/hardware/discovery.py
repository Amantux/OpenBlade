from __future__ import annotations

"""Hardware discovery parsers for lsscsi and sg_map."""

import re
from dataclasses import dataclass

from openblade.domain.policies import RealHardwareGuard
from openblade.hardware.runner import SafeRunner

SAMPLE_LSSCSI_FULL = """
[0:0:0:0]    disk    ATA      WDC WD40EFAX-68J  0A83  /dev/sda
[6:0:0:0]    mediumx IBM      03584L22         0060  /dev/smc0  /dev/sg0
[6:0:1:0]    tape    IBM      ULTRIUM-TD8      H3S4  /dev/st0   /dev/sg1
[6:0:2:0]    tape    IBM      ULTRIUM-TD8      H3S4  /dev/st1   /dev/sg2
"""

SAMPLE_LSSCSI_NO_CHANGER = """
[0:0:0:0]    disk    ATA      WDC WD40EFAX     0A83  /dev/sda
"""

SAMPLE_SG_MAP_FULL = """
/dev/sg0  /dev/smc0
/dev/sg1  /dev/st0
/dev/sg2  /dev/st1
"""

_ADDRESS_RE = re.compile(
    r"^\[(?P<host>\d+):(?P<bus>\d+):(?P<target>\d+):(?P<lun>\d+)\]\s+(?P<rest>.+)$"
)
_REST_RE = re.compile(
    r"^(?P<device_type>\S+)\s+(?P<vendor>\S+)\s+(?P<model>.+?)\s{2,}"
    r"(?P<revision>\S+)\s{2,}(?P<devices>/dev/.+)$"
)


@dataclass(frozen=True)
class ScsiDevice:
    host: int
    bus: int
    target: int
    lun: int
    device_type: str
    vendor: str
    model: str
    revision: str
    sg_device: str | None
    block_device: str | None


@dataclass(frozen=True)
class LibraryDiscovery:
    changers: list[ScsiDevice]
    drives: list[ScsiDevice]
    sg_map: dict[str, str]


def parse_lsscsi(output: str) -> list[ScsiDevice]:
    """Parse output from `lsscsi -g`."""
    devices: list[ScsiDevice] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _ADDRESS_RE.match(line)
        if match is None:
            continue
        rest_match = _REST_RE.match(match.group("rest").strip())
        if rest_match is None:
            continue
        device_paths = rest_match.group("devices").split()
        block_device = device_paths[0] if device_paths else None
        sg_device = device_paths[1] if len(device_paths) > 1 else None
        devices.append(
            ScsiDevice(
                host=int(match.group("host")),
                bus=int(match.group("bus")),
                target=int(match.group("target")),
                lun=int(match.group("lun")),
                device_type=rest_match.group("device_type"),
                vendor=rest_match.group("vendor"),
                model=rest_match.group("model").strip(),
                revision=rest_match.group("revision"),
                sg_device=sg_device,
                block_device=block_device,
            )
        )
    return devices


def parse_sg_map(output: str) -> dict[str, str]:
    """Parse output from `sg_map`."""
    mapping: dict[str, str] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2:
            mapping[parts[0]] = parts[1]
    return mapping


def find_tape_changers(devices: list[ScsiDevice]) -> list[ScsiDevice]:
    return [device for device in devices if device.device_type == "mediumx"]


def find_tape_drives(devices: list[ScsiDevice]) -> list[ScsiDevice]:
    return [device for device in devices if device.device_type == "tape"]


def discover_library(runner: SafeRunner, guard: RealHardwareGuard) -> LibraryDiscovery:
    """Discover tape changers, drives, and sg mappings."""
    guard.validate()
    if runner.dry_run:
        devices = parse_lsscsi(SAMPLE_LSSCSI_FULL)
        return LibraryDiscovery(
            changers=find_tape_changers(devices),
            drives=find_tape_drives(devices),
            sg_map=parse_sg_map(SAMPLE_SG_MAP_FULL),
        )

    lsscsi_result = runner.run(["lsscsi", "-g"], timeout=30)
    lsscsi_result.raise_on_error()
    devices = parse_lsscsi(lsscsi_result.stdout)

    sg_map_result = runner.run(["sg_map"], timeout=30)
    sg_mapping = parse_sg_map(sg_map_result.stdout) if sg_map_result.success else {}

    return LibraryDiscovery(
        changers=find_tape_changers(devices),
        drives=find_tape_drives(devices),
        sg_map=sg_mapping,
    )

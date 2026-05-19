"""Simulator drive primitives."""

from dataclasses import dataclass

from openblade.domain.models import Barcode, DriveState, MountState


@dataclass
class MockDrive:
    drive_id: int
    barcode: Barcode | None = None
    drive_state: DriveState = DriveState.EMPTY
    mount_state: MountState = MountState.UNMOUNTED

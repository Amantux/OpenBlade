"""Protocol interfaces that both simulator and real hardware must implement."""

from pathlib import Path, PurePosixPath
from typing import Protocol, runtime_checkable

from openblade.domain.models import (
    DriveStatus,
    FileInstance,
    LibraryInventory,
    LTFSFileStat,
    MountHandle,
    MountMode,
    OperationResult,
    SlotState,
)
from openblade.domain.policies import FormatConfirmation


@runtime_checkable
class LibraryBackend(Protocol):
    def inventory(self) -> LibraryInventory: ...
    def load(self, source_slot: int, drive_id: int) -> OperationResult: ...
    def unload(self, drive_id: int, target_slot: int) -> OperationResult: ...
    def move(self, source_slot: int, target_slot: int) -> OperationResult: ...
    def get_drive(self, drive_id: int) -> DriveStatus: ...
    def get_slot(self, slot_id: int) -> SlotState: ...


@runtime_checkable
class LTFSBackend(Protocol):
    def format(self, barcode: str, confirmation: FormatConfirmation) -> OperationResult: ...
    def mount(self, barcode: str, mode: MountMode) -> MountHandle: ...
    def unmount(self, handle: MountHandle) -> OperationResult: ...
    def write_file(
        self, handle: MountHandle, source: Path, dest: PurePosixPath
    ) -> FileInstance: ...
    def read_file(
        self, handle: MountHandle, source: PurePosixPath, dest: Path
    ) -> OperationResult: ...
    def stat(self, handle: MountHandle, path: PurePosixPath) -> LTFSFileStat: ...

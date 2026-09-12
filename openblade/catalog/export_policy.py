"""What is lost when a cartridge leaves the library.

Exporting media is the one operation in this product that makes archived data
unreachable without deleting anything: the bytes are fine, they are just in
someone's hand. Every restore path already refuses a cartridge whose catalog
state is ``exported`` (jobs/restore.py, jobs/sharded_restore.py), so the moment
an export succeeds those files stop being restorable.

This module answers "what is on it?" once, so the mailslot service (which
previews) and the tape orchestrator (which refuses) cannot disagree.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from openblade.catalog.repository import CatalogRepository
from openblade.domain.models import FileInstanceState

ARCHIVED_INSTANCE_STATES = frozenset(
    {FileInstanceState.ARCHIVED.value, FileInstanceState.VERIFIED.value}
)

# A `pending` instance is a write in flight: the archive job has claimed a tape
# path on this cartridge and has not finished. Taking the media out from under
# it is at least as bad as exporting finished data, so it counts too -- reported
# separately, because "3 files, 1 still being written" is a different sentence
# from "3 files".
_PENDING_INSTANCE_STATES = frozenset({FileInstanceState.PENDING.value})

# How many catalog paths to name in the refusal. Enough to recognise what the
# cartridge is, bounded so a 1,073-file tape does not produce a 60 KB error.
_SAMPLE_LIMIT = 5


@dataclass
class ExportAssessment:
    """Everything that makes an export consequential, for one barcode."""

    barcode: str
    volume_group: str | None = None
    archived_files_on_cartridge: int = 0
    pending_files_on_cartridge: int = 0
    bytes_on_cartridge: int = 0
    sample_paths: list[str] = field(default_factory=list)
    # Sibling cartridges in the same volume group that also carry archived data.
    # CONTEXT ONLY -- deliberately NOT part of `carries_data`. This used to
    # refuse too: the reasoning was "a block_stripe file is split across tapes,
    # so removing any member of the group may break it". That reasoning is
    # wrong, because a shard IS a file instance: a cartridge holding part of a
    # striped file has instances of its own and is caught by the real check. A
    # cartridge with zero instances carries nothing, and refusing to rotate a
    # blank scratch tape out of an active group -- with the false sentence
    # "still carries archived data" -- taught operators that `--force` is the
    # normal way to export. A guard that cries wolf is a guard that gets
    # bypassed. (Adversarial review finding.)
    volume_group_barcodes_with_data: list[str] = field(default_factory=list)

    @property
    def carries_data(self) -> bool:
        """True when THIS cartridge holds archived or in-flight file instances."""
        return self.archived_files_on_cartridge > 0 or self.pending_files_on_cartridge > 0

    def refusal_message(self) -> str:
        """Operator-facing refusal naming exactly what would go out of the door."""
        parts = [
            f"Cartridge {self.barcode} still carries archived data: "
            f"{self.archived_files_on_cartridge} file instance(s), "
            f"{self.bytes_on_cartridge} bytes"
        ]
        if self.volume_group is not None:
            parts.append(f"volume group {self.volume_group}")
        if self.sample_paths:
            shown = ", ".join(self.sample_paths)
            more = self.archived_files_on_cartridge - len(self.sample_paths)
            parts.append(f"e.g. {shown}" + (f" (+{more} more)" if more > 0 else ""))
        if self.pending_files_on_cartridge:
            parts.append(
                f"{self.pending_files_on_cartridge} write(s) still in flight to it"
            )
        if self.volume_group_barcodes_with_data:
            parts.append(
                "other cartridges in that group hold data too "
                f"({', '.join(self.volume_group_barcodes_with_data)}), so a "
                "striped file may lose a shard"
            )
        return (
            "; ".join(parts)
            + ". Exporting makes these unrestorable until the cartridge is "
            "imported again. Pass --force if that is what you mean."
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "barcode": self.barcode,
            "volumeGroup": self.volume_group,
            "archivedFilesOnCartridge": self.archived_files_on_cartridge,
            "pendingFilesOnCartridge": self.pending_files_on_cartridge,
            "bytesOnCartridge": self.bytes_on_cartridge,
            "samplePaths": list(self.sample_paths),
            "volumeGroupBarcodesWithData": list(self.volume_group_barcodes_with_data),
            "carriesData": self.carries_data,
        }


def assess_export(catalog: CatalogRepository, barcode: str) -> ExportAssessment:
    """Summarise the archived data that leaves with ``barcode``."""
    assessment = ExportAssessment(barcode=barcode)

    instances = catalog.list_instances_for_barcode(barcode)
    archived = [
        instance for instance in instances if instance.state in ARCHIVED_INSTANCE_STATES
    ]
    assessment.archived_files_on_cartridge = len(archived)
    assessment.pending_files_on_cartridge = sum(
        1 for instance in instances if instance.state in _PENDING_INSTANCE_STATES
    )
    for instance in archived:
        record = catalog.get_file_record_by_id(instance.file_record_id)
        if record is None:
            continue
        assessment.bytes_on_cartridge += record.size_bytes or 0
        if len(assessment.sample_paths) < _SAMPLE_LIMIT:
            assessment.sample_paths.append(record.path)

    cartridge = catalog.get_cartridge(barcode)
    if cartridge is None or cartridge.volume_group_id is None:
        return assessment

    for group in catalog.list_volume_groups():
        if group.id == cartridge.volume_group_id:
            assessment.volume_group = group.name
            break

    # Siblings come from a fresh cartridge query rather than `group.cartridges`:
    # the volume group may already be in SQLAlchemy's identity map with a
    # collection loaded before the newest cartridge was linked, and a stale
    # "no siblings" answer here reads as "safe to export".
    for sibling in catalog.list_cartridges():
        if sibling.volume_group_id != cartridge.volume_group_id:
            continue
        if sibling.barcode == barcode:
            continue
        if any(
            instance.state in ARCHIVED_INSTANCE_STATES
            for instance in catalog.list_instances_for_barcode(sibling.barcode)
        ):
            assessment.volume_group_barcodes_with_data.append(sibling.barcode)
    assessment.volume_group_barcodes_with_data.sort()

    return assessment

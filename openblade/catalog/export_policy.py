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

# How many catalog paths to name in the refusal. Enough to recognise what the
# cartridge is, bounded so a 1,073-file tape does not produce a 60 KB error.
_SAMPLE_LIMIT = 5


@dataclass
class ExportAssessment:
    """Everything that makes an export consequential, for one barcode."""

    barcode: str
    volume_group: str | None = None
    archived_files_on_cartridge: int = 0
    bytes_on_cartridge: int = 0
    sample_paths: list[str] = field(default_factory=list)
    # Sibling cartridges in the same volume group that also carry archived data.
    # A block_stripe file is split ACROSS tapes, so removing any one member of
    # the group can break restores of files whose other shards stay behind.
    volume_group_barcodes_with_data: list[str] = field(default_factory=list)

    @property
    def carries_data(self) -> bool:
        return self.archived_files_on_cartridge > 0 or bool(
            self.volume_group_barcodes_with_data
        )

    def refusal_message(self) -> str:
        """Operator-facing refusal naming exactly what would go out of the door."""
        parts = [f"Cartridge {self.barcode} still carries archived data"]
        if self.archived_files_on_cartridge:
            parts.append(
                f"{self.archived_files_on_cartridge} file instance(s), "
                f"{self.bytes_on_cartridge} bytes on this cartridge"
            )
        if self.volume_group is not None:
            parts.append(f"volume group {self.volume_group}")
        if self.volume_group_barcodes_with_data:
            parts.append(
                "other cartridges in that group also hold data "
                f"({', '.join(self.volume_group_barcodes_with_data)}) -- a sharded "
                "file may span them"
            )
        if self.sample_paths:
            shown = ", ".join(self.sample_paths)
            more = self.archived_files_on_cartridge - len(self.sample_paths)
            parts.append(f"e.g. {shown}" + (f" (+{more} more)" if more > 0 else ""))
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
            "bytesOnCartridge": self.bytes_on_cartridge,
            "samplePaths": list(self.sample_paths),
            "volumeGroupBarcodesWithData": list(self.volume_group_barcodes_with_data),
            "carriesData": self.carries_data,
        }


def assess_export(catalog: CatalogRepository, barcode: str) -> ExportAssessment:
    """Summarise the archived data that leaves with ``barcode``."""
    assessment = ExportAssessment(barcode=barcode)

    archived = [
        instance
        for instance in catalog.list_instances_for_barcode(barcode)
        if instance.state in ARCHIVED_INSTANCE_STATES
    ]
    assessment.archived_files_on_cartridge = len(archived)
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
